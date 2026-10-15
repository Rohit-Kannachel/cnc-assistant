# CNC Assistant App — Project Handoff Document

## Context & Purpose

The user owns a **Genmitsu 3018 Pro CNC router** and wants to build a local Flask + Python web app to assist with CNC machining workflow. The app will:

1. Maintain a **tool library** (their end mills, bits, etc.)
2. Maintain a **material + feeds & speeds library** (acrylic, wood, etc. with matching cut parameters)
3. Accept an **STL file upload** of a part they want to machine
4. Use **geometry analysis** to understand the part
5. Use the **Claude API** to reason about the part and generate an **operation plan** (what cuts to make, in what order, with what parameters)
6. Generate a **GRBL-compatible .nc / gcode file** ready to send to the 3018

The user is comfortable with Flask and Python and wants to be able to maintain the code themselves. Keep the codebase clean, well-commented, and as simple as possible while still being capable.

---

## Machine Specs (Genmitsu 3018 Pro)

- **Bed size:** 300 x 180mm
- **Firmware:** GRBL
- **Spindle:** Fixed speed or basic PWM (not servo — RPM is set manually on the machine or via S command)
- **Controller software:** Candle (GRBLControl) or UGS (Universal Gcode Sender)
- **Capabilities:** Simple 2.5D operations — outlines, pockets, drilling. No ATC, no coolant system.
- **Gcode output needed:** Standard GRBL `.nc` or `.gcode` file

---

## User's Current Tool

- **Type:** 2-flute tungsten-carbide flat nose spiral end mill
- **Shank diameter:** 3.175mm
- **Cutting diameter:** 3.175mm
- **Notes:** Not ideal for acrylic (single-flute preferred) but usable with conservative settings

---

## Confirmed Cut Parameters for Acrylic with This Tool

These have been discussed and agreed with the user — use as defaults in the material library:

| Parameter | Value |
|---|---|
| Spindle RPM | 10,000 |
| Feed Rate (XY) | 150–300 mm/min (start at 150) |
| Plunge Rate (Z) | 25–50 mm/min |
| Step Down (Z per pass) | 0.2–0.4mm |
| Step Over | 1.2–1.6mm (~40–50% of bit diameter) |
| Entry type | Ramp preferred, not straight plunge |

**White powdery chips = good. Melting/re-welding = slow spindle or speed up feed.**

---

## Technology Stack (Confirmed)

| Component | Technology | Notes |
|---|---|---|
| Backend | **Flask** (Python) | User's preferred framework |
| Database | **SQLite** via **SQLAlchemy** | Simple, local, no server needed |
| Geometry analysis | **trimesh** | STL loading, bounding box, Z-slicing |
| 2D toolpath math | **shapely** | Tool radius offset, pocket passes, stepover |
| Gcode output | **Custom Python** | Write GRBL gcode directly — no library needed |
| AI planner | **Anthropic Python SDK** | Claude reasons about operations given geometry + tool + material |
| Frontend | **HTML + Jinja2 + vanilla JS** | Keep it simple, no heavy framework |
| 3D viewer (later) | **three.js via CDN** | In-browser STL preview |

### Python dependencies (requirements.txt to create)
```
flask
flask-sqlalchemy
trimesh
shapely
numpy
anthropic
```

**Note on STEP files:** Trimesh does NOT support STEP natively. The user is fine with **STL only** for now. STEP support can be added later via `cadquery` if needed.

---

## Confirmed Library Research (already done — do not redo)

### trimesh (v5.1.1) — verified working
- Loads STL files
- `mesh.bounds` → bounding box
- `mesh.extents` → X, Y, Z dimensions
- `mesh.volume` → volume
- `mesh.is_watertight` → mesh validity check
- `mesh.section_multiplane()` → slices mesh into Z-layers (step-down passes)
- Does NOT support STEP

### shapely (v2.2.0) — verified working
- `polygon.buffer(tool_radius)` → outside contour offset
- `polygon.buffer(-tool_radius)` → inside pocket offset
- Loop of `buffer(-stepover)` → generates all pocket clearing passes
- Tab generation possible via geometry subtraction

### Gcode — write directly
GRBL gcode is plain text. No library needed. Example:
```
G21        ; mm mode
G90        ; absolute positioning
G0 Z5      ; safe height
G0 X0 Y0   ; move to start
G1 Z-0.3 F40  ; plunge
G1 X30 F200   ; cut
G0 Z5      ; retract
M5         ; spindle off
M30        ; program end
```

---

## Project Structure to Create

```
cnc_assistant/
│
├── app.py                      # Flask app entry point, registers blueprints
├── config.py                   # Config: DB path, API key (from env var), machine limits
├── requirements.txt
├── README.md
│
├── models/                     # SQLAlchemy models
│   ├── __init__.py
│   ├── tool.py                 # Tool library model
│   ├── material.py             # Material + feeds/speeds model
│   └── job.py                  # Job/part record model
│
├── routes/                     # Flask blueprints
│   ├── __init__.py
│   ├── tools.py                # CRUD routes for tool library
│   ├── materials.py            # CRUD routes for material library
│   ├── jobs.py                 # Upload STL, view job
│   └── planner.py              # Claude API call, return operation plan
│
├── services/                   # Business logic (no Flask here)
│   ├── __init__.py
│   ├── geometry.py             # trimesh: load STL, extract features
│   ├── toolpath.py             # shapely: generate 2D toolpaths from features
│   ├── gcode.py                # Write GRBL gcode from toolpath data
│   └── claude_planner.py       # Build prompt, call Claude API, parse response
│
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               # Base layout with nav
│   ├── index.html              # Dashboard / home
│   ├── tools/
│   │   ├── index.html          # List all tools
│   │   ├── new.html            # Add tool form
│   │   └── edit.html           # Edit tool form
│   ├── materials/
│   │   ├── index.html          # List all materials
│   │   ├── new.html            # Add material form
│   │   └── edit.html           # Edit material form
│   └── jobs/
│       ├── index.html          # List all jobs
│       ├── new.html            # Upload STL, select tool + material
│       └── detail.html         # Job detail: geometry summary, operation plan, gcode download
│
├── static/
│   ├── css/
│   │   └── style.css
│   └── js/
│       └── viewer.js           # (later) three.js STL viewer
│
└── uploads/                    # STL file storage (gitignore this)
```

---

## Database Models (design spec)

### Tool
```python
class Tool(db.Model):
    id            = int (PK)
    name          = str          # e.g. "2-flute carbide 3.175mm"
    tool_type     = str          # flat_end_mill | ball_end_mill | v_bit | drill
    shank_dia_mm  = float        # shank diameter
    cut_dia_mm    = float        # cutting diameter
    flutes        = int
    material      = str          # carbide | hss | cobalt
    flute_length_mm = float      # usable cutting length
    notes         = str (optional)
    created_at    = datetime
```

### Material
```python
class Material(db.Model):
    id              = int (PK)
    name            = str        # e.g. "Acrylic / Plexiglass"
    category        = str        # plastic | wood | aluminium | foam | pcb
    # Feeds & speeds (per tool diameter — store as JSON or separate table later)
    spindle_rpm     = int
    feed_rate_mmpm  = float      # XY feed rate mm/min
    plunge_rate_mmpm = float     # Z plunge rate mm/min
    step_down_mm    = float      # depth per pass
    step_over_mm    = float      # lateral pass spacing
    notes           = str (optional)
    created_at      = datetime
```

### Job
```python
class Job(db.Model):
    id              = int (PK)
    name            = str
    stl_filename    = str        # stored filename in uploads/
    tool_id         = FK → Tool
    material_id     = FK → Material
    # Geometry facts (populated after analysis)
    dim_x_mm        = float
    dim_y_mm        = float
    dim_z_mm        = float
    is_flat         = bool       # True if part height < 5mm (2.5D candidate)
    is_watertight   = bool
    # AI output
    operation_plan  = str (JSON) # Claude's structured operation plan
    gcode_filename  = str (optional) # generated gcode file
    status          = str        # pending | analysed | planned | gcode_ready
    created_at      = datetime
```

---

## Geometry Analysis Service (services/geometry.py)

Should extract and return a dict like this from an STL file:

```python
{
    "filename": "part.stl",
    "dimensions": {"x": 30.0, "y": 20.0, "z": 5.0},  # mm
    "volume_mm3": 2800.0,
    "is_watertight": True,
    "is_flat": True,           # z < 8mm → likely a 2.5D part
    "face_count": 1200,
    "z_layers": [              # suggested step-down layers given step_down param
        {"z": -0.3, "pass": 1},
        {"z": -0.6, "pass": 2},
        # ...
    ],
    "warnings": []             # e.g. ["Part may be larger than machine bed (300x180mm)"]
}
```

Key logic in geometry.py:
- Load with `trimesh.load(filepath)`
- Check `mesh.is_watertight` — warn if False
- Use `mesh.extents` for dimensions
- Compare extents to machine bed (300x180mm) and warn if too big
- If `z_extent < 8mm` → flag as flat/2.5D
- Use `mesh.section_multiplane()` to generate Z slices at step_down intervals

---

## Claude Planner Service (services/claude_planner.py)

### What to send Claude

Build a structured prompt with:
- Machine: Genmitsu 3018, GRBL, 300x180mm bed
- Tool: (from tool library record)
- Material: (from material library record) with feeds/speeds
- Part geometry: (from geometry analysis dict)

### What to ask Claude to return

Ask for a JSON response with this structure:
```json
{
  "operation_summary": "Brief plain-English description of what will be done",
  "warnings": ["any concerns about the job"],
  "operations": [
    {
      "order": 1,
      "type": "drill | pocket | outline | engrave",
      "description": "human readable description",
      "tool_entry": "ramp | helical | plunge",
      "feed_rate": 200,
      "plunge_rate": 40,
      "step_down": 0.3,
      "step_over": 1.5,
      "depth": 5.0,
      "tabs": true,
      "tab_count": 4,
      "tab_width_mm": 3.0,
      "tab_height_mm": 1.0
    }
  ],
  "recommended_order_rationale": "Why this operation order was chosen"
}
```

### System prompt for Claude (use this)
```
You are a CNC machining assistant specialising in hobby desktop CNC routers, specifically the Genmitsu 3018 Pro running GRBL firmware. 

You will receive:
- Machine specifications
- Tool properties from the user's tool library
- Material properties and feeds/speeds from their material library  
- Geometry analysis of an STL part they want to machine

Your job is to produce a structured JSON operation plan. Be conservative with feeds and speeds — this is a budget router with limited rigidity. Always recommend ramped entry over straight plunging for flat end mills. Recommend tabs on any outline/profile cut. Order operations logically: interior features and holes first, pockets second, final profile outline last.

Respond ONLY with valid JSON matching the schema provided. No preamble, no explanation outside the JSON.
```

---

## Gcode Generation (services/gcode.py)

### GRBL gcode basics to implement
```
G21     ; metric (mm)
G90     ; absolute coordinates
G17     ; XY plane
F{feed} ; set feed rate
S{rpm}  ; set spindle speed
M3      ; spindle on (clockwise)
G4 P2   ; dwell 2 seconds (let spindle spin up)
G0 Z5   ; safe height rapid
G0 X{x} Y{y}  ; rapid to position
G1 Z{z} F{plunge}  ; feed down (plunge)
G1 X{x} Y{y} F{feed}  ; feed move
G0 Z5   ; retract to safe height
M5      ; spindle off
M30     ; program end
```

### Ramp entry implementation
Instead of `G1 Z-0.3 F40` (straight plunge), ramp over the first 5mm of cut:
```
; Ramp entry over 5mm at 3° angle
G1 X5 Z-0.3 F100  ; move forward 5mm while descending 0.3mm
```

### Tab implementation
When generating outline pass, at tab positions: raise Z by tab_height before the tab segment, lower again after.

---

## Build Phases — Do These in Order

### Phase 1 (Start here)
- Flask app skeleton with blueprints
- SQLite + SQLAlchemy setup
- Tool library: full CRUD (list, add, edit, delete)
- Material library: full CRUD with feeds/speeds fields
- Seed with one default tool (the user's 3.175mm 2-flute carbide) and one default material (acrylic with confirmed parameters above)
- Basic nav UI

### Phase 2
- STL file upload
- Geometry analysis service (trimesh)
- Display analysis results: dimensions, warnings, flat vs 3D

### Phase 3
- Claude API integration
- Send geometry + tool + material → get operation plan JSON
- Display operation plan in UI in human-readable form

### Phase 4
- Gcode generation service (shapely + custom gcode writer)
- Download .nc file
- Basic gcode preview (display as text)

### Phase 5
- three.js STL viewer in browser
- Toolpath visualisation overlay

---

## Important Notes for the Developer

1. **API key:** Store `ANTHROPIC_API_KEY` as an environment variable, never hardcoded. Load with `os.environ.get('ANTHROPIC_API_KEY')` in config.py.

2. **Claude model to use:** `claude-sonnet-4-6` — confirmed available and appropriate for this task.

3. **STL uploads:** Store in `uploads/` directory inside the project. Generate a UUID filename to avoid collisions. Keep the original filename in the DB for display.

4. **Machine bed check:** Always warn if part extents exceed 300mm (X) or 180mm (Y). The 3018 cannot cut what doesn't fit.

5. **STEP files:** Not supported in this phase. If user uploads a non-STL file, show a clear error: "Only STL files are supported. Please export as STL from your CAD software."

6. **The user uses Autodesk Inventor** as their CAD tool. They export STL from there.

7. **Kiri:Moto** is the user's current CAM tool (browser-based, free). This app is intended to eventually replace that workflow.

8. **Keep the UI simple** — this is a local tool for one user, not a SaaS product. Functionality over beauty, but make it clean and usable.

9. **Tab generation is important** — acrylic will fly loose if the outline cut completes without tabs. Always recommend tabs on profile/outline operations.

10. **Single-flute bits are better for acrylic** but the user currently has a 2-flute. App should note this in the tool library (field: `recommended_for` or just in notes).

---

## Quick Start Commands (to include in README)

```bash
# Clone / enter project
cd cnc_assistant

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set API key
export ANTHROPIC_API_KEY=your_key_here

# Initialise database
flask db init  # or python3 -c "from app import db; db.create_all()"

# Run
flask run
# Open http://localhost:5000
```

---

## Summary

Begin with **Phase 1** — get the Flask skeleton, database models, and tool/material CRUD working with a clean UI. Seed the database with the user's existing tool and acrylic parameters. Make sure the app runs locally with `flask run` before moving to geometry analysis.

The user will be running this on their own laptop alongside their CNC machine. Keep it self-contained and easy to understand.
