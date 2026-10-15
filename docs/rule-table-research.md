# Optimising the rule table: research notes

Research and measurements for improving `planner/rules_engine.py` and `services/toolpath.py`.

**Applied so far:**
- acrylic defaults from the Fusion programs (150 mm/min, plunge 50, step-down 0.5),
- bores at the milling feed with a 0.4 mm/turn helix (R3),
- a feature with an island is never a hole (R3),
- big through holes and cutouts cut as an inside **profile** op with tabs holding the middle (R2),
- 0.2 mm break-through,
- tabs every ~28 mm, 2.5 mm wide,
- G2/G3 arc output.

Everything else below is still the list to choose from. The biggest remaining item is **R4**: on the lid, the groove is still cut three times (roughing, pocketing, contour: about 145 of 232 min).

Time estimates come from the app's own estimator: feed moves at their feed rate, rapids at 1000 mm/min, no acceleration. Real runs will be somewhat longer, especially with lots of short segments, but the *ratios* between options hold.

---

## 1. What happened on `lid_trial`

The part is a Ø150 × 10 mm disc with:
- a 4 mm-wide ring groove (Ø110–118, 4 mm deep),
- a Ø20 through hole in the centre.

| Op | What the plan did | Est. time |
|---|---|---|
| 1 drilling | The ring groove was classed as a **Ø118 blind hole**, because its outer edge is round. It was cut at the *drilling* feed of 45 mm/min. | 229 min |
| 2 drilling | The Ø20 through hole was **cleared completely** with nested rings, also at 45 mm/min. | 157 min |
| 3–4 roughing, contour | Added because the part is thicker than 6 × step-down. They produced **no moves**: there are no pockets to rough. | 0 |
| 5 outline | 34 passes × 0.3 mm at 120 mm/min | 139 min |
| **Total** | | **526 min** |

Prototyping the fixes below on the same part:

| Change | Total |
|---|---|
| A. Current plan | 526 min |
| B. Cut the Ø20 hole as an **inside profile** and let the slug drop | 413 min |
| C. B + holes and grooves at the **milling** feed (150), not the drill feed (45) | 196 min |
| D. C + **feeds from chip load** (600 mm/min, 0.5 mm step-down) | **31 min** |
| E. Aggressive: 900 mm/min, 1.0 mm step-down (needs test cuts first) | 11 min |

Strategy fixes (B, C) are safe wins on their own. The biggest single lever is feed rate (D), and it's also a *quality* fix for acrylic, as explained below.

---

## 1b. Known-good reference: your Fusion 360 programs (acrylic, worked well)

Five GRBL programs from Fusion 360 (Personal) for a similar part were parsed: `trial_disc*.nc` from June. The part:
- Ø80 disc, 5 mm stock, cut from a sheet,
- an outer shoulder (about r 31–40) lowered about 2 mm,
- a 4 mm groove (Ø65–73) to about 3.65 mm deep,
- a Ø15 through bore in the centre.

The tool was a 3.175 mm flat end mill.

| Setting | Fusion (worked) | This app today |
|---|---|---|
| Spindle | 10,000 RPM | 10,000 |
| Cutting feed | **150 mm/min** everywhere (chip load 0.0075 mm/tooth) | 150 (pockets), 120 (outline), 127 (roughing), **45 (holes)** |
| Plunge feed | **50 mm/min** | 30 |
| Step-down: contour, pocket | **0.5 mm**; a later "deep" version tried **1.0** (contour) and **0.75** (pocket) | 0.3 |
| Step-down: adaptive | levels 0.2/0.3 apart (about 0.5 mm roughing with an intermediate level) | n/a |
| Break-through below stock | **0.2 mm**, later **0.35 mm** | 0.1 |
| Through hole (Ø15) | **Bore**: helical at r = 3.9 mm (to full depth), then at the final r = 5.9 mm. The Ø4.6 core drops as a slug. Pitch 0.38–0.52 mm/rev (about 1° ramp). Milling feed 150. | Area cleared with rings at 45 mm/min, 3° ramp |
| Groove | 2D Pocket: two loops (inner and outer wall), 0.5 mm steps | Same idea, but run as "drilling" at 45 if it has a round edge |
| Shoulder / step | **2D Adaptive** clearing | Ring clearing |
| Outline | 2D Contour, climb, small lead-in arc, plunge outside the part | Ramp along the path |
| Tabs | **9 tabs** on a 261 mm path (every 40°), about 5.8 mm path gap, so a **~2.6 mm bridge**, **~1.0 mm tall** from the cut bottom, square (straight up/down) | 5 tabs (1 per 50 mm), 3 mm bridge, 1.0 mm tall |
| Motion | **G2/G3 arcs** for circles (a contour is about 200 lines) | Line segments only (tens of thousands of lines) |
| Total time (est., at programmed feeds) | 20–154 min per program | |

Fusion Personal also turns rapids into moves at the cutting feed (noted in the file header), so the real runs were slower than these estimates.

**What this means for the rules:**
- **150 mm/min at 10k RPM is proven on your acrylic and machine.** R1's faster feeds are an *option to test* (section 4), not a fix for a known problem. Keep 150 as the default until a test ladder says otherwise.
- **Step-down 0.5 mm is proven.** The 0.3 in the material library is more cautious than needed. The 1.0 / 0.75 "deep" attempt is worth checking: if it cut cleanly, step-down by engagement (R5) is confirmed.
- **Holes and bores should use the milling feed.** Fusion bores at 150, not 45, which confirms R3.
- **Through bores leave a slug, as R2 suggests.** Fusion cut a helix at one radius, then finished at the final radius.
- **Tabs:** more and narrower than ours (9 × 2.6 mm vs 5 × 3 mm) on a small disc. Consider spacing them by angle or about 30 mm of path, with a minimum of 6.
- **Break-through:** 0.2–0.35 mm, not 0.1.
- **Output arcs:** G2/G3 for circular moves gives far smaller files and smoother motion on GRBL.

---

## 2. Recommendations, ranked by impact

### R1. Set feeds from chip load. Current acrylic feeds are far too slow, which risks melting.

**Why.** Feed = RPM × flutes × chip load.
- Chip-load guidance for acrylic with a 1/8" (3.175 mm) tool is about **0.002–0.005" per tooth (0.05–0.13 mm)** ([MIT Fablab calculator, Carbide 3D forum](https://community.carbide3d.com/t/cutting-plexiglas-pmma/242)).
- A cast-acrylic user suggests about 0.002" for a 1/8" cutter as the floor, to avoid rubbing ([Carbide 3D](https://community.carbide3d.com/t/o-flute-feed-speed-in-acrylic/19190)).
- Your current 150 mm/min at 10,000 RPM with 2 flutes is **0.0075 mm/tooth (0.0003")**, roughly 7–15× below that range.
- Acrylic **melts when chips are too thin** to carry heat away. The counter-intuitive fix is to feed *faster* or slow the spindle ([CNC Router Store](https://www.cncrouterstore.ca/pages/why-acrylic-melts-cnc); "go too slow, and you'll get a lot of melting": [Carbide 3D](https://community.carbide3d.com/t/cut-1-8-acrylic-in-one-pass/2267)).
- A 2-flute 1/8" tool at 10k RPM and 1200 mm/min (about 0.06 mm/tooth) has been reported as working, described as the high end of the range, in the Carbide 3D acrylic threads listed under Sources.

The engine's own `CHIP_LOAD[ACRYLIC] = 0.015` mm is also 3–5× below the published range.

**The 3018's limits.**
- The machine's top feed is set by GRBL's `$110`/`$111`. On 3018s these are commonly around 1000–1100 mm/min ([Ed Nisley's 3018 config](https://softsolder.com/2019/08/22/cnc-3018-pro-grbl-configuration/), [LightBurn forum](https://forum.lightburnsoftware.com/t/recently-my-3018-prover-behaves-a-bit-odd/81080/5)). Send `$$` in Candle to see yours.
- The frame is flexible, so keep the chip load up and **take shallower cuts rather than feeding slower**. Push too deep and the bit deflects and chatters, or rubs and burns ([Easel 3018 guide](https://easel.com/partners/machines/sainsmart-3018-pro-diy)).

**Rule change.**
- Store a **chip load** per material and tool (or per material and tool diameter) in the library.
- Derive feed as `rpm × flutes × chipload`, capped at the machine's max feed (add `MAX_FEED_MM_MIN` to config, from `$110`).
- Plunge feed ≈ ⅓ of feed.
- Ramps can use the full feed, since a 2–3° ramp barely loads the tool vertically.
- **Warn** if the computed chip load is below the material's minimum. That's the "this will melt" warning.

**Before trusting it:** run a test ladder on scrap (section 4).

### R2. Through holes and cutouts: profile the edge and drop the slug, don't clear the area

**Why.** Clearing a through opening turns all of its area into chips. An **inside profile** (one loop at the wall, offset by the tool radius) only cuts a ring one tool wide. On the lid's Ø20 hole that's 157 → 44 min at the same feed. The bigger the opening, the bigger the win.

**The catch is the loose slug.** Once it's cut free it can catch the tool and be thrown. Standard ways to hold it ([Laguna: onion skinning](https://info.lagunatools.com/onion-skinning-for-small-parts-on-your-cnc), [Dallas Makerspace](https://talk.dallasmakerspace.org/t/multicam-cnc-onion-skin-request-for-comment/24997)):
- **Onion skin:** stop about 0.3–0.5 mm above the bottom, then sand or snap it out. AXYZ suggests about 0.5 mm in Z for acrylic ([AXYZ](https://www.axyz.com/6-easy-steps-get-best-finish-cutting-acrylic/)).
- **Small tabs** on the inside profile.
- Double-sided tape under the slug area, a common acrylic workholding method.

**Rule change.** New op type `profile_inside`:
- **Through hole or cutout bigger than ~2.5 × tool diameter** → `profile_inside` with tabs or onion skin (a material or job option).
- **Smaller through holes** → helical bore. The slug would be smaller than the tool anyway, so it's simply cut away.
- **Blind features** still need clearing.

### R3. Holes and grooves: use the right strategy and the right feed

**Why.** The engine runs every hole at the *drilling* feed `min(plunge × 1.5, feed × 0.4)`. That suits a straight plunge, but a **helical bore is side milling** and should use the milling feed. Changing that alone took the lid's groove from 229 → 71 min.

**Misclassification.** The ring groove had a round outer edge plus an island, so it was called a "blind hole". **Anything with an island is not a hole.** It's a pocket, or a *groove* when its width is under about 2 × tool diameter.

**Rule change:**

| Feature | Strategy |
|---|---|
| hole ≈ tool size (±0.05) | peck drill (plunge feed) |
| tool < hole ≤ ~2.5 × tool | helical bore at **milling** feed |
| hole > ~2.5 × tool, through | `profile_inside` (R2) |
| hole > ~2.5 × tool, blind | pocket clearing |
| feature with islands, width < 2 × tool | groove: follow the centre-line / offset loops at milling feed |
| feature with islands, wider | pocket clearing |

**Helix size.** The helix *path* diameter should be ≥ 1.1–1.2 × the tool diameter for a clean helical entry ([UF Helical Machining Guidebook](https://mae.ufl.edu/designlab/Advanced%20Manufacturing/Helical_Machining_Guidebook.pdf)). Our Ø4 holes with a 3.175 tool have a 0.8 mm path. That works with a centre-cutting end mill, but it's close to a plunge, so a slower ramp or peck is worth considering for holes under about 1.5 × the tool.

### R4. Fix the roughing and contour triggers; use "rough + full-depth finish" for edge quality

**Why.**
- `needs_roughing` fires for any part thicker than 6 × step-down (1.8 mm in acrylic), so nearly every part gets a roughing op and a contour op.
- On parts with no pockets (the lid, the plain plate) they produce no moves. They're just noise in the plan.
- Where there *are* pockets, they make the tool cut the same area three times: roughing, pocketing, then contour.

What CAM practice actually uses roughing and finishing for in acrylic is **edge quality on profiles** ([AXYZ](https://www.axyz.com/6-easy-steps-get-best-finish-cutting-acrylic/), [EOA](https://www.eoasaw.com/wp-content/uploads/Premium-Finishes-when-Routing-Acrylic.pdf)):
- Rough the profile **0.2 mm oversize radially**, stopping about **0.5 mm short in Z**, so the part is still held.
- Then make **one full-depth finishing pass** at a slightly lower feed.
- This works best with small tools like yours. One source saw only a marginal gain on 3/8" and bigger tools.

**Rule change:**
- Drop the "thickness > 6 × step-down" roughing trigger.
- For pockets, make the pocketing op leave 0.2 mm on the walls, then one finishing loop per wall at the end. Remove the separate roughing op.
- For outlines and inside profiles, add an optional **finish pass**: allowance 0.2 mm, full depth, one loop.

### R5. Step-down depends on how much of the tool is engaged

**Why.**
- A **full-width cut** (slotting) is the hardest work for a flexible machine. Outlines, profiles and narrow grooves are all full-width cuts.
- Clearing a pocket with a 40% step-over engages less than half the tool, so it can take a deeper cut at the same load.
- Common rule-of-thumb guidance in the profile and onion-skin guides I found is a **pass depth of no more than half the tool diameter**. The 3018 will probably want less.
- Amana reduces feed by 25% at 2 × D depth and 50% at 3 × D ([Amana plastic chart](https://www.amanatool.com/pub/media/productattachments/Plastic-Edge-Rounding-Speed-Feed-Chart-v6.pdf)).

**Rule change.** Give step-down per op type as a fraction of tool diameter, then tune it with test cuts. Starting points for the 3018 with a 3.175 tool:
- **slotting** (outline, profile, groove): 0.15–0.25 × D, i.e. 0.5–0.8 mm
- **partial-width clearing** (40% step-over): 0.3 × D, about 1 mm
- **finish pass:** full depth, with 0.2 mm radial engagement

### R6. Adaptive / trochoidal clearing for larger pockets (later)

**Why.**
- Constant, light radial engagement (around 5–10% step-over) with deep cuts keeps the cutting force steady. That suits less rigid machines, and the tool "never gets buried" ([hobby-machinist](https://www.hobby-machinist.com/goto/post?id=580715), [Carbide 3D](https://community.carbide3d.com/t/trochoidal-milling-is-amazing/6063)).
- Too high a step-over welds plastic onto a small tool. One user dropped to 5% to fix it ([V1 Engineering](https://forum.v1engineering.com/t/min-width-for-adaptive-trochoidal-slotting/19659)).
- It's a roughing strategy and needs a finishing pass.

**Rule change:** none yet. It's the big upgrade once pockets get large, and a good candidate for the "model handles edge cases" phase.

### R7. Tabs: shape, placement and count

**Why:**
- **Shape:** square tabs leave vertical witness marks where the tool stops and restarts. **Triangular or ramped ("3D") tabs** avoid them ([Vectric tabs](https://docs.vectric.com/docs/Spark/V1.0/ESP/Help/page/toolpath-tabs), [Onshape forum](https://forum.onshape.com/discussion/29092/cam-studio-tabs)).
- **Count and placement:** use few tabs, at stress points, with **one near the end of the cut** ([graphics-pro](https://graphics-pro.com/feature/how-to-use-holding-tabs/)).
- **Acrylic:** it's brittle, so keep tabs thin enough to snap cleanly and test-break one on scrap.

**Rule change:**
- Ramp into and out of each tab over about 1–2 mm of path instead of moving Z straight up and down. In the toolpath code this means sloping the tab ends.
- Make sure one tab falls near where the final pass ends.
- Our tab gap already adds the tool diameter to the bridge width, which matches the Makercam guidance.

### R8. Cut direction: climb to rough, optional conventional "spring" finish

**Why.** The usual hobby advice is to rough with climb milling, then finish with a light conventional pass. Deflection from the climb cut leaves slightly oversized parts and holes, and the finish pass removes it ([Carbide 3D thread](https://community.carbide3d.com/t/climb-vs-conventional-definitive-guidelines-anyone/15374), [Tormach](https://blog.tormach.com/climb-milling-versus-conventional-milling-sneaky-cnc-tricks)). Sources disagree for acrylic specifically: AXYZ recommends climb, and EOA says "depends, test" ([EOA](https://www.eoasaw.com/wp-content/uploads/Premium-Finishes-when-Routing-Acrylic.pdf)).

**Rule change:** make the direction of the finish pass (R4) a material setting. Default to climb, and test conventional on the 3018.

### R9. Ordering and air-cutting

- **Don't cut air.**
  - The hole under a counterbore currently starts at Z0, even though the counterbore has already removed the top 2 mm.
  - Profile and outline passes could start at the deepest level already cut.
- **Ordering:** group by depth, then by nearest neighbour between features with the same tool. That's FeatureCAM's approach ([Autodesk FeatureCAM ordering](https://help.autodesk.com/cloudhelp/2025/ENU/FCAM/files/GUID-1F342BFE-AF1F-475A-91DF-3BE25C1A8EC6.htm)). Optimal ordering is a travelling-salesman problem, so use a greedy heuristic.
- Inside features before the outline (already done). The part must stay held until last.

### R10. Tooling hint

A **single-flute (O-flute) upcut** is the usual recommendation for acrylic: better chip evacuation and less melting ([CNC Router Store](https://www.cncrouterstore.ca/pages/why-acrylic-melts-cnc), [IAPD](https://digital.iapd.org/?p=14256)). The flute count changes the *feed* needed for a given chip load, not the chip load target ([Carbide 3D](https://community.carbide3d.com/t/o-flute-feed-speed-in-acrylic/19190)). One user found 1/8" O-flutes deflected on their machine, so test it on the 3018.

An **air blast** to clear chips noticeably improves acrylic finish.

---

## 3. Draft of a revised rule table

| Feature (from the analysis) | Condition | Operation | Feed | Step-down |
|---|---|---|---|---|
| hole | dia ≈ tool | peck drill | plunge | 1 × step-down per peck |
| hole | tool < dia ≤ 2.5 × tool | helical bore | milling feed | slotting depth |
| hole / cutout, through | > 2.5 × tool | **inside profile + onion skin/tabs** | milling feed | slotting depth |
| hole / pocket, blind | > 2.5 × tool | pocket clear (leave 0.2) + wall finish | milling feed | clearing depth |
| any feature with islands | width < 2 × tool | groove (loops) | milling feed | slotting depth |
| step (open to edge) | any | pocket clear + wall finish | milling feed | clearing depth |
| shallow (≤ 0.5 mm), not through | any | trace / engrave | 0.5 × feed | single pass |
| outline | always (if cut from stock) | profile, rough 0.2 oversize, finish full depth, ramped tabs | milling feed; finish ~0.7× | slotting depth |
| roughing op | only for large pockets (area ≫ tool), later adaptive | | | |

Milling feed = `rpm × flutes × chipload`, capped at machine max.

---

## 4. Calibrating feeds on your 3018 (test ladder)

Use a scrap piece of the same acrylic, 10,000 RPM, 3.175 mm 2-flute, air blast if you have one.

1. Cut short straight slots (e.g. 30 mm), 0.5 mm deep. Use one feed per slot: **300, 450, 600, 800, 1000** mm/min. The chip loads are 0.015, 0.023, 0.03, 0.04, 0.05 mm/tooth.
2. Look at the chips and walls:
   - **White powdery chips / clean curls = good.**
   - **Melted strings or re-welded slot = too slow.**
   - **Chatter or a jagged wall = too fast or too deep.**
3. Take the best feed and repeat at 0.8 mm and 1.0 mm deep.
4. Put the winner into the material library as the chip load and step-down. With R1 in place, every job then uses it.

This ladder is easy to generate as G-code: an obvious small feature to add (a "test cut" button on the material page).

---

## 5. What a model could later take on

The rules above handle the common 2.5D cases. Good candidates for a learned or model-based layer:
- choosing onion skin vs tabs, and where to put tabs,
- adaptive clearing parameters,
- ordering,
- recognising engraving and text,
- non-round cutouts with tight internal corners (where the tool radius limits what can be cut),
- adjusting feeds from your logged results ("this job melted", "this one chattered").

Logging per-job outcomes from now on would give that model something to learn from.

---

## Sources

- [Carbide 3D: Cutting Plexiglas / PMMA](https://community.carbide3d.com/t/cutting-plexiglas-pmma/242)
- [Carbide 3D: Cut 1/8" acrylic in one pass](https://community.carbide3d.com/t/cut-1-8-acrylic-in-one-pass/2267)
- [Carbide 3D: Fusion 360 & acrylic](https://community.carbide3d.com/t/fusion-360-acrylic/50801)
- [Carbide 3D: O flute feed & speed in acrylic](https://community.carbide3d.com/t/o-flute-feed-speed-in-acrylic/19190)
- [Carbide 3D: Climb vs conventional](https://community.carbide3d.com/t/climb-vs-conventional-definitive-guidelines-anyone/15374)
- [Carbide 3D: Trochoidal milling is amazing](https://community.carbide3d.com/t/trochoidal-milling-is-amazing/6063)
- [MIT Fablab feeds & speeds calculator](https://pub.pages.cba.mit.edu/feed_speeds)
- [BU EPIC lab feeds and speeds](https://www.bu.edu/epic/lab-resources/)
- [CNC Router Store: Why is my acrylic melting?](https://www.cncrouterstore.ca/pages/why-acrylic-melts-cnc)
- [AXYZ: 6 steps to the best finish cutting acrylic](https://www.axyz.com/6-easy-steps-get-best-finish-cutting-acrylic/)
- [EOA: Premium finishes when routing acrylic (PDF)](https://www.eoasaw.com/wp-content/uploads/Premium-Finishes-when-Routing-Acrylic.pdf)
- [IAPD: acrylic routing tooling](https://digital.iapd.org/?p=14256)
- [Amana plastic speed/feed chart (PDF)](https://www.amanatool.com/pub/media/productattachments/Plastic-Edge-Rounding-Speed-Feed-Chart-v6.pdf)
- [Easel: SainSmart Genmitsu 3018 guide](https://easel.com/partners/machines/sainsmart-3018-pro-diy)
- [Ed Nisley: CNC 3018-Pro GRBL configuration](https://softsolder.com/2019/08/22/cnc-3018-pro-grbl-configuration/)
- [LightBurn forum: 3018 ProVer settings](https://forum.lightburnsoftware.com/t/recently-my-3018-prover-behaves-a-bit-odd/81080/5)
- [Laguna: onion skinning small parts](https://info.lagunatools.com/onion-skinning-for-small-parts-on-your-cnc)
- [Dallas Makerspace: onion skin discussion](https://talk.dallasmakerspace.org/t/multicam-cnc-onion-skin-request-for-comment/24997)
- [UF Helical Machining Guidebook (PDF)](https://mae.ufl.edu/designlab/Advanced%20Manufacturing/Helical_Machining_Guidebook.pdf)
- [Tungaloy: helical interpolation](https://tungaloy.com/?p=747457)
- [Hobby-machinist: adaptive clearing on a light mill](https://www.hobby-machinist.com/goto/post?id=580715)
- [V1 Engineering: minimum width for adaptive slotting](https://forum.v1engineering.com/t/min-width-for-adaptive-trochoidal-slotting/19659)
- [Vectric: toolpath tabs](https://docs.vectric.com/docs/Spark/V1.0/ESP/Help/page/toolpath-tabs)
- [Onshape forum: CAM Studio tabs](https://forum.onshape.com/discussion/29092/cam-studio-tabs)
- [Graphics Pro: how to use holding tabs](https://graphics-pro.com/feature/how-to-use-holding-tabs/)
- [Tormach: climb vs conventional milling](https://blog.tormach.com/climb-milling-versus-conventional-milling-sneaky-cnc-tricks)
- [Autodesk FeatureCAM: machining order](https://help.autodesk.com/cloudhelp/2025/ENU/FCAM/files/GUID-1F342BFE-AF1F-475A-91DF-3BE25C1A8EC6.htm)
