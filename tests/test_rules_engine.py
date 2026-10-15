"""Checks for the rules engine, using the same hand-written parts as its demo.

Run with:  pytest
"""

import pytest

from cnc_assistant.planner import rules_engine as engine


def plan(name):
    for case_name, part, tool, material in engine.demo_inputs():
        if case_name == name:
            return engine.plan_job(case_name, part, tool, material)
    raise KeyError(name)


def op_types(p):
    return [op.op_type.value for op in p.operations]


@pytest.mark.parametrize("name", [n for n, *_ in engine.demo_inputs()])
def test_outline_is_always_last_with_tabs(name):
    ops = plan(name).operations
    assert ops[-1].op_type == engine.OperationType.OUTLINE
    assert ops[-1].tabs and engine.TAB_MIN <= ops[-1].tab_count <= engine.TAB_MAX
    assert [op.order for op in ops] == list(range(1, len(ops) + 1))


def test_drilling_comes_first():
    assert op_types(plan("Acrylic Bracket"))[0] == "drilling"
    assert op_types(plan("Enclosure Lid"))[0] == "drilling"


def test_hole_bigger_than_tool_uses_helical_entry():
    drill = plan("Acrylic Bracket").operations[0]
    assert drill.entry_type == "helical"  # 4mm hole, 3.175mm tool


def test_pocket_uses_material_step_over():
    pocket = next(op for op in plan("Enclosure Lid").operations if op.op_type == engine.OperationType.POCKETING)
    assert pocket.step_over_mm == 1.5  # from the acrylic Material, not the 45% ratio


def test_engraving_adds_trace():
    assert "trace" in op_types(plan("Engraved Name Plate"))


def test_deep_mdf_roughs_and_warns_about_flute_length():
    p = plan("Deep MDF Part")
    assert op_types(p)[0] == "roughing"
    assert any("flute length" in w for w in p.global_warnings)


def test_oversized_part_warns_both_axes():
    warnings = plan("Oversized Part").global_warnings
    assert any("X dimension" in w for w in warnings)
    assert any("Y dimension" in w for w in warnings)


def test_tiny_hole_warns_globally_and_on_the_op():
    p = plan("Part With Tiny Hole")
    assert any("smaller than tool" in w for w in p.global_warnings)
    assert p.operations[0].warnings


def test_hole_matching_tool_size_is_detected_with_tolerance():
    tool = engine.Tool("t", engine.ToolType.FLAT_END_MILL, 3.175, 2)
    part = engine.PartFeatures(10, 10, 3, holes=[engine.Hole(diameter_mm=3.17, depth_mm=3)])
    warnings = engine.check_global_warnings(part, tool)
    assert any("exactly matches" in w for w in warnings)
    assert not any("smaller than tool" in w for w in warnings)


def test_unknown_flute_length_is_not_reported():
    tool = engine.Tool("t", engine.ToolType.FLAT_END_MILL, 3.175, 2, flute_length_mm=None)
    part = engine.PartFeatures(10, 10, 30)
    assert not any("flute length" in w for w in engine.check_global_warnings(part, tool))


def test_no_outline_when_not_cutting_from_stock():
    _, part, tool, material = engine.demo_inputs()[2]
    part.cut_from_stock = False
    assert "outline" not in op_types(engine.plan_job("lid", part, tool, material))


def test_plan_serialises_to_json():
    import json

    data = plan("Enclosure Lid").to_dict()
    json.dumps(data)
    assert data["operations"][0]["type"] == "drilling"
    assert "feature_refs" in data["operations"][0]


def test_big_through_hole_becomes_a_tabbed_inside_profile():
    tool = engine.Tool("t", engine.ToolType.FLAT_END_MILL, 3.175, 2)
    mat = engine.Material("a", engine.MaterialType.ACRYLIC, 10000, 150, 50, 0.5, 1.4)
    part = engine.PartFeatures(150, 150, 10, holes=[
        engine.Hole(20.0, 10.0, through=True, feature_refs=[2]),   # big, through -> profile
        engine.Hole(4.0, 10.0, through=True, feature_refs=[3]),    # small, through -> bored
        engine.Hole(20.0, 4.0, through=False, feature_refs=[4]),   # big but blind -> bored
    ])
    p = engine.plan_job("lid", part, tool, mat)
    kinds = [(op.op_type.value, op.feature_refs) for op in p.operations]
    assert ("profile", [2]) in kinds
    assert ("drilling", [3]) in kinds and ("drilling", [4]) in kinds
    profile = next(op for op in p.operations if op.op_type == engine.OperationType.PROFILE)
    assert profile.tabs and profile.tab_count >= engine.INSIDE_TAB_MIN
    # Inside profiles come after the interior work and before the outline frees the part.
    assert [op.op_type.value for op in p.operations][-2:] == ["profile", "outline"]
