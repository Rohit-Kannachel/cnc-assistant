"""A tiny material-removal simulator for checking generated gcode in tests.

The stock is a height map (top surface Z per grid cell, starting at 0). The gcode is
parsed line by line and the flat end mill's footprint is swept along every move:
  - feed moves (G1, and G2/G3 arcs in the XY plane) lower the height map under the
    tool to the tool's Z,
  - rapid moves (G0) must never have material above the tool tip (a crash),
  - straight-down feed moves into material deeper than `max_plunge` are recorded.
"""

import math
import re

import numpy as np

WORD = re.compile(r"([GXYZIJFSM])(-?\d+(?:\.\d+)?)")


class Sim:
    def __init__(self, x_range, y_range, tool_dia, res=0.2, top=0.0):
        """top: Z of the stock's top surface in the program's coordinates (0 if Z0 is the stock top)."""
        self.res = res
        self.x0, self.y0 = x_range[0], y_range[0]
        nx = int((x_range[1] - x_range[0]) / res) + 1
        ny = int((y_range[1] - y_range[0]) / res) + 1
        self.height = np.full((ny, nx), float(top))
        self.r = tool_dia / 2
        # For crash/plunge checks use a slightly smaller radius, so cells the cutter's edge
        # only grazes (at walls and tab ends) don't count as hits.
        self.r_inner = self.r - 1.5 * res
        self.k = int(math.ceil(self.r / res)) + 1
        self._offsets = np.arange(-self.k, self.k + 1) * res
        self.crashes = []
        self.max_plunge = 0.0
        self.feed_with_spindle_off = 0
        self.arcs = 0

    def xs(self):
        return self.x0 + np.arange(self.height.shape[1]) * self.res

    def ys(self):
        return self.y0 + np.arange(self.height.shape[0]) * self.res

    def _window(self, x, y):
        """The height-map cells around the tool, plus masks of the cells under the tool
        (full radius) and well inside it (inner radius), using the exact tool position."""
        ci = int(round((x - self.x0) / self.res))
        cj = int(round((y - self.y0) / self.res))
        k = self.k
        j0, j1, i0, i1 = cj - k, cj + k + 1, ci - k, ci + k + 1
        if j0 < 0 or i0 < 0 or j1 > self.height.shape[0] or i1 > self.height.shape[1]:
            raise AssertionError(f"tool left the simulated stock at X{x:.2f} Y{y:.2f}")
        cx = self.x0 + ci * self.res - x  # offset of the window's centre cell from the tool
        cy = self.y0 + cj * self.res - y
        dx = self._offsets[None, :] + cx
        dy = self._offsets[:, None] + cy
        d2 = dx * dx + dy * dy
        return self.height[j0:j1, i0:i1], d2 <= self.r * self.r, d2 <= self.r_inner * self.r_inner

    def run(self, gcode):
        pos = {"X": None, "Y": None, "Z": None}
        spindle = False
        for n, raw in enumerate(gcode.splitlines(), start=1):
            line = re.sub(r"\(.*?\)", "", raw).strip()
            if not line:
                continue
            words = dict(WORD.findall(line))
            if words.get("M") == "3":
                spindle = True
            elif words.get("M") in ("5", "2", "30"):
                spindle = False
            g = words.get("G")
            if g not in ("0", "1", "2", "3"):
                continue
            new = {a: float(words[a]) if a in words else pos[a] for a in "XYZ"}
            if None in new.values() or None in pos.values():
                pos = new
                continue
            if g in ("2", "3"):
                self.arcs += 1
                self._arc(pos, new, float(words.get("I", 0)), float(words.get("J", 0)),
                          ccw=(g == "3"), line_no=n, spindle=spindle)
            else:
                self._move(pos, new, rapid=(g == "0"), line_no=n, spindle=spindle)
            pos = new

    def _arc(self, a, b, i, j, *, ccw, line_no, spindle):
        """Split a G2/G3 arc (centre = start + I,J; Z linear in angle) into short feed moves."""
        cx, cy = a["X"] + i, a["Y"] + j
        r0 = math.hypot(a["X"] - cx, a["Y"] - cy)
        r1 = math.hypot(b["X"] - cx, b["Y"] - cy)
        # GRBL rejects an arc (error 33) when the start and end radii differ by more than
        # 0.005 mm and by more than 0.1% of the radius. We hold ourselves to the stricter 0.005.
        assert abs(r0 - r1) <= 0.005, f"line {line_no}: arc radius mismatch {r0:.4f} vs {r1:.4f} (GRBL error 33)"
        t0 = math.atan2(a["Y"] - cy, a["X"] - cx)
        t1 = math.atan2(b["Y"] - cy, b["X"] - cx)
        sweep = (t1 - t0) if ccw else (t0 - t1)
        sweep %= 2 * math.pi
        if sweep < 1e-9:
            sweep = 2 * math.pi
        n = max(2, int(math.ceil(r0 * sweep / (self.res / 2))))
        prev = a
        for k in range(1, n + 1):
            t = t0 + (sweep * k / n if ccw else -sweep * k / n)
            pt = {"X": cx + r0 * math.cos(t), "Y": cy + r0 * math.sin(t), "Z": a["Z"] + (b["Z"] - a["Z"]) * k / n}
            self._move(prev, pt, rapid=False, line_no=line_no, spindle=spindle)
            prev = pt

    def _move(self, a, b, *, rapid, line_no, spindle):
        dx, dy, dz = b["X"] - a["X"], b["Y"] - a["Y"], b["Z"] - a["Z"]
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        steps = max(1, int(math.ceil(length / (self.res / 2))))
        vertical = abs(dx) < 1e-9 and abs(dy) < 1e-9
        if not rapid and not spindle:
            self.feed_with_spindle_off += 1
        if vertical and not rapid and dz < 0:
            w, _, inner = self._window(b["X"], b["Y"])
            top = w[inner].max()
            self.max_plunge = max(self.max_plunge, top - b["Z"])
        for i in range(1, steps + 1):
            t = i / steps
            x, y, z = a["X"] + dx * t, a["Y"] + dy * t, a["Z"] + dz * t
            w, disk, inner = self._window(x, y)
            if rapid:
                over = w[inner].max() - z
                if over > 0.01:
                    self.crashes.append(f"line {line_no}: rapid into material by {over:.2f} mm at X{x:.2f} Y{y:.2f}")
                    return
            else:
                w[disk] = np.minimum(w[disk], z)

    def at(self, x, y):
        """Stock top under a single point."""
        ci = int(round((x - self.x0) / self.res))
        cj = int(round((y - self.y0) / self.res))
        return self.height[cj, ci]
