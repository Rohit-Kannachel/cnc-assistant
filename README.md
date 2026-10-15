# CNC Assistant

A local Flask web app for planning jobs on a **Genmitsu 3018 Pro** (GRBL). It keeps a tool library and a material library with feeds and speeds, analyses uploaded STL parts, and plans the operations with a rules engine. Later phases will add toolpaths and GRBL gcode output, and possibly a learned/model-based planner for edge cases.

See [cnc_assistant_handoff.md](cnc_assistant_handoff.md) for the original design and roadmap.

**Status:**
- Phase 1 is done: tool library (with photos) and material library, seeded with your 3.175mm carbide end mill and acrylic settings.
- Phase 2 is done: STL upload and geometry analysis (see [How the STL analysis works](#how-the-stl-analysis-works)).
- Phase 3 (rules-based) is done: operation planning with the rules engine (see [The operation planner](#the-operation-planner)).
- Phase 4 is done: toolpaths and a downloadable GRBL `.nc` file, with a top-view toolpath preview (see [G-code](#g-code)).

Try it with the sample part [samples/sample_plate.stl](samples/sample_plate.stl): an 80 × 50 × 6 mm plate with through holes, a pocket, a counterbore and a rebate.

---

## Setup

The app is a normal Python package (`pyproject.toml`), so it installs the same way in a venv or a conda environment, on Windows or Linux. You need Python 3.10 or newer.

### Option A: conda

```bash
conda env create -f environment.yml
conda activate cnc_assistant
```

`environment.yml` creates a Python 3.12 environment and runs `pip install -e .` for you.

### Option B: venv

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Linux / macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` installs the dependencies plus the app in "editable" mode, so code changes take effect without reinstalling. (`pip install -r requirements.txt` installs only the dependencies, if you ever need that.)

### Configure

Copy the example env file and fill it in:

```bash
cp .env.example .env        # PowerShell: Copy-Item .env.example .env
```

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (not used yet; for a future model-based planner) |
| `CLAUDE_MODEL` | Model for a future model-based planner (default `claude-sonnet-5-5`) |
| `SECRET_KEY` | Flask secret key; any long random string |
| `CNC_DATA_DIR` | Where the database, uploads and gcode go (default `instance`, relative to where you start the app) |
| `CNC_HOST` / `CNC_PORT` | Bind address and port (default `127.0.0.1:5000`) |

The `.env` file is found by searching upward from the directory you run the app in, so run it from the project folder.

---

## Run

```bash
cnc-assistant            # add --debug for auto-reload while developing
```

or with Flask's own CLI:

```bash
flask --app cnc_assistant run --debug
```

Then open http://localhost:5000. On first start the app creates the SQLite database in `instance/` and seeds the default tool and material.

### On a Linux machine (later)

For something longer-running than the development server, install the optional server extra and use gunicorn:

```bash
pip install -e ".[server]"
gunicorn -w 2 -b 0.0.0.0:5000 "cnc_assistant:create_app()"
```

Set `CNC_HOST=0.0.0.0` if you use `cnc-assistant` and want to reach it from another computer on your network.

---

## How the STL analysis works

Code: [services/geometry.py](src/cnc_assistant/services/geometry.py).

1. **Load and position.** trimesh loads the STL (scaled ×25.4 if you pick inches) and moves it so X/Y start at the part's lower-left corner and Z = 0 is the top surface, with depths going negative, the way you'd zero the machine.
2. **Find floors.** Every upward-facing flat face (the top, pocket floors, shoulders) sits at a fixed depth. Between two neighbouring floor depths a 2.5D part's cross-section doesn't change, so the part is sliced once in the middle of each band. These bands are the **depth levels**.
3. **Find features.** At each level, the part's outline minus the material is what needs removing. Each separate piece is a feature: a **through hole**, **blind hole**, **pocket**, **through cutout**, or **step** (open to the side). It's followed down as long as the whole opening stays open, so a counterbore shows up as a wide shallow hole plus a narrower through hole.
4. **Check.** Warnings cover a mesh with gaps, a part bigger than the 300 × 180 bed, a likely inches/mm mix-up, a part standing on its side, sloped or undercut surfaces (not 2.5D), a part deeper than the flute length, and openings too small for the tool.
5. **Passes.** The material's step-down gives the list of Z passes to full depth.

The job page shows a shaded top view (darker = deeper, white = through) with numbered features, plus the feature and level tables. If you change the job's tool, material or units, the analysis runs again.

To rebuild the sample part: `python scripts/make_sample_stl.py` (needs `pip install -e ".[dev]"`).

The part's Z axis is taken from the STL as exported: whatever is +Z in Inventor is "up" on the machine.

---

## The operation planner

After every analysis the job is planned by the **rules engine**, [planner/rules_engine.py](src/cnc_assistant/planner/rules_engine.py). It's a plain Python module with no Flask or database code. Its rules, in order:

1. **Drilling** first, one operation per hole size (plunge if the hole matches the tool, helical if it's bigger, flagged if it's smaller).
2. **Roughing** if a pocket is deeper than 4 × step-down, or the part is thicker than 6 × step-down.
3. **Pocketing** for each pocket size (uses the material's step-over).
4. **Trace** for engraving: non-through features 0.5 mm deep or less.
5. **Contour** finish if the part has sloped/curved walls, or after roughing.
6. **Outline** last, with 4–12 tabs (one per ~28 mm of outline, 2.5 mm wide), if *Cut free from stock* is ticked.

[planner/adapter.py](src/cnc_assistant/planner/adapter.py) converts the app's data into the engine's inputs. It groups identical holes and pockets and records which feature numbers each operation covers, so toolpath generation can find their positions later. Steps and through cutouts are planned as pockets.

Edits made to the original engine during integration are marked `CHANGED:` in the file.

Run the engine on its built-in demo parts:

```bash
python -m cnc_assistant.planner.rules_engine
```

Run the tests (engine rules, sample STL end-to-end, and the G-code safety simulation):

```bash
pip install -e ".[dev]"
pytest
```

---

## G-code

After planning, the job's G-code is generated automatically. **Download G-code (.nc)** is at the top of the job page and in its G-code section.

**Stock & work origin.** Set these in the *Stock & work origin* panel on the job page. Saving re-plans and regenerates the G-code.

| Setting | Options | Default |
|---|---|---|
| Stock X × Y × thickness | any size in mm | part + 5 mm on every side; the part's own thickness |
| Part position | centred, or the part's front-left corner at X, Y from the stock's front-left corner | centred |
| X0 Y0 | a stock corner (front-left, front-right, back-left, back-right), the stock centre, the part's front-left corner, or the part centre | stock front-left |
| Z0 | top of the stock, or bottom of the stock (the spoilboard surface) | top of the stock |

"Front" is the side nearest you (lowest Y), "left" is lowest X.

The page shows:
- a sketch of the stock, the part and the origin,
- the toolpath preview drawn in work coordinates.

It warns if:
- the part runs off the stock,
- there's under one tool diameter of stock beside the part,
- the stock is bigger than the bed,
- the stock is thicker than the part (the part's top is set level with the stock top and there's no facing yet, so the part comes out at stock thickness),
- the stock is thinner than the part.

How it works ([services/setup.py](src/cnc_assistant/services/setup.py)): toolpaths are generated around the part as before, then every move is shifted into your work coordinates when the G-code is written. The file header records the stock, the part position and where to zero.

**Other setup notes:**
- Through cuts go 0.2 mm below the stock, so use a spoilboard.
- With a new file, do an air cut first (zero Z a few mm higher than normal).

**How operations become moves** ([services/toolpath.py](src/cnc_assistant/services/toolpath.py)):

| Operation | Moves |
|---|---|
| drilling | Hole the same size as the tool: peck straight down one step-down at a time, lifting out between pecks. Up to 2.5 × the tool: helical bore (0.4 mm per turn) at the milling feed, cleared outward. Bigger *through* hole: cut round the wall only; the middle drops out as a slug on the last pass. |
| pocketing / trace | Nested offset loops cleared inside-out, each depth entered with a 3° ramp. Big through cutouts are cut round the wall only (slug drops), like big holes. |
| roughing | The same on pockets/steps/cutouts, leaving 0.2 mm on the walls. Does nothing if there are none. |
| contour | One finishing loop around each pocket/step wall per depth. |
| outline | Around the outside, last, with tabs: Z is held up over each tab once the cut gets deeper than the tab. |

Cutting direction is climb milling for a clockwise (M3) spindle. Operations the plan flags as impossible (the tool is bigger than the hole) are **left out** of the file and listed on the page.

[services/gcode.py](src/cnc_assistant/services/gcode.py) writes the moves as GRBL G-code (G21/G90, `G0`/`G1`, and `G2`/`G3` arcs wherever the path follows a circle; ASCII-only comments) and draws the toolpath preview. Arcs keep files small (the 150 mm lid: 1,855 lines instead of 38,000) and motion smooth, as in Fusion's output.

**Safety tests.** [tests/test_gcode.py](tests/test_gcode.py) generates G-code for the sample part, reads it back, and simulates the cut on a 0.2 mm height map ([tests/gcode_sim.py](tests/gcode_sim.py)). It fails if:
- a rapid move hits material,
- the part is gouged,
- an end mill plunges straight down more than one step-down,
- a feature isn't cut to depth,
- or the tab count is wrong.

Run `pytest` after changing anything in toolpaths or G-code.

---

## Project layout

```
cnc_assistant/
├── pyproject.toml          # package metadata + dependencies (source of truth)
├── requirements.txt        # same dependencies, for plain `pip install -r`
├── environment.yml         # conda environment
├── .env.example            # copy to .env
├── samples/                # sample_plate.stl to try the app with
├── scripts/                # make_sample_stl.py
├── tests/                  # pytest: rules engine, planner adapter, G-code simulation
└── src/cnc_assistant/
    ├── __init__.py         # create_app() factory
    ├── cli.py              # `cnc-assistant` command
    ├── config.py           # settings from env vars / .env, machine limits
    ├── extensions.py       # SQLAlchemy instance
    ├── db_upgrade.py       # adds new optional columns / converts renamed values in an existing database
    ├── seed.py             # default tool + material
    ├── models/             # Tool, Material, Job
    ├── planner/            # rules_engine.py (the rules) + adapter.py (app data -> engine)
    ├── routes/             # Flask blueprints (main, tools, materials, jobs)
    ├── services/           # logic without Flask: geometry.py (STL analysis), toolpath.py, gcode.py, images.py
    ├── templates/          # Jinja2 HTML
    └── static/             # CSS (and JS later)
```

Runtime data (`instance/`: database, tool photos, uploads, gcode) and `.env` are git-ignored.

### Changing the database schema

There are no full migrations. On startup the app creates any missing tables (`db.create_all()`) and then adds any new **optional (nullable)** columns you've added to a model (`db_upgrade.py`), keeping your data. Anything else (renaming or removing a column, changing its type, adding a required column) still means deleting `instance/cnc_assistant.db` and restarting. The defaults are re-seeded automatically.
