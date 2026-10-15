"""End-to-end: sample STL -> geometry analysis -> rules engine plan."""

from pathlib import Path
from types import SimpleNamespace

from cnc_assistant.planner.adapter import plan_for_job, to_part_features
from cnc_assistant.services.geometry import analyse_stl

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "sample_plate.stl"


def analysis():
    return analyse_stl(SAMPLE, step_down_mm=0.3, tool_dia_mm=3.175, tool_flute_length_mm=12)


def test_sample_features_become_grouped_holes_and_pockets():
    part, engraving = to_part_features(analysis())
    holes = {(h.diameter_mm, h.depth_mm): h for h in part.holes}

    assert holes[(4.0, 6.0)].count == 4  # corner mounting holes grouped
    assert (2.0, 6.0) in holes  # too-small hole still listed
    assert (10.0, 2.0) in holes  # counterbore
    assert (5.0, 6.0) in holes  # hole under the counterbore
    assert {(p.width_mm, p.height_mm, p.depth_mm) for p in part.pockets} == {(24.0, 14.0, 2.0), (80.0, 6.0, 3.0)}
    assert engraving == []
    assert (part.dim_x_mm, part.dim_y_mm, part.dim_z_mm) == (80.0, 50.0, 6.0)


def test_plan_for_job_references_feature_numbers():
    a = analysis()
    job = SimpleNamespace(
        name="Sample",
        analysis=a,
        cut_from_stock=None,  # older jobs: treated as True
        stock_thickness_mm=None,
        tool=SimpleNamespace(name="2F", tool_type="flat_end_mill", cut_dia_mm=3.175, flutes=2,
                             material="carbide", flute_length_mm=12.0),
        material=SimpleNamespace(name="Acrylic", category="acrylic", spindle_rpm=10000, feed_rate_mmpm=150,
                                 plunge_rate_mmpm=30, step_down_mm=0.3, step_over_mm=1.4),
    )
    plan = plan_for_job(job)

    types = [op["type"] for op in plan["operations"]]
    assert types[0] == "drilling" and types[-1] == "outline"
    referenced = sorted(n for op in plan["operations"] for n in op["feature_refs"])
    assert referenced == list(range(1, len(a["features"]) + 1))  # every feature is covered exactly once
    assert plan["planner"] == "rules_engine"
