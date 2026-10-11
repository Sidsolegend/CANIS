# CANIS v4 - verified BambuStudio slicer settings (Bambu P1S / PLA / 0.20 mm)

Verified 2026-07-09 by actually slicing every part in **BambuStudio 2.07** with the
real *Bambu Lab P1S 0.4 nozzle* machine + *0.20mm Standard* process + *Bambu PLA
Basic* filament. Every part slices to valid gcode. BambuStudio's own mesh check
reports all parts `manifold = yes, number_of_parts = 1` → **no auto-repair**, so no
repair-induced distortion. All STLs are byte-for-geometry identical to a fresh
re-export from `canis_v4.scad` (not stale). Bed = 256×256×250 mm; 18×28 mm
purge-exclusion at the front-left corner (centered parts clear it).

##  FASTER DECK & SHELL - print-time options

Verified by headless re-slicing on the real P1S profile. **My CLI runs ~6–12%
HIGHER than your GUI** (your GUI baselines: deck 3h57, shell 4h48; my CLI repro:
deck 4h17, shell 5h26) - so the GUI numbers below are what you'll actually see.
Neither part can reach 2h30 one-piece on a 0.4 nozzle (walls already 2, speeds
maxed, 0.30mm is the nozzle limit; the deck's model alone is 2h26 before its
required support). Ready files in `printables/`.

### SHELL - one-piece, trimmed (chosen)
`shell_onepiece.gcode.3mf` - **my-CLI 4h40 → GUI ~4h20** (down from 4h48). Layer
**0.22mm** (barely changed from 0.20, so the cover surface stays clean), infill
10%, 2 walls, top4/bottom3, **no support**, crown-down. No seam.
*(Split-shell STLs `shell_A/B.stl` remain as a fallback if you ever want <2h30
halves - slice at 0.28mm/8%/no-support/**5mm brim**, ~2h05 GUI each. The seam is
transverse at x=0, keeps all sensor/mount features intact, crosses only open
crown vents + the wheel-arch openings.)*

### DECK - TWO options provided (pick at print time)
| Option | File(s) | GUI time | Trade |
|---|---|---|---|
| **One-piece** | `deck_onepiece.gcode.3mf` | ~3h15 | no seam through the electronics platform (recommended) |
| **Split** | `deck_A.gcode.3mf` + `deck_B.gcode.3mf` | ~1h50 each | under 2h30, but a glued seam |

One-piece deck: 0.30mm / 8% / top3 / bottom3 / build-plate-only support / 5mm
brim. Stays support-bound at ~3h15 (the 4mm plate over the skirt cavity always
needs support) - cannot reach 2h30 as one piece.

**If you split the deck** (cut at x=0, `split_half.scad`): both halves
`manifold=yes, 1 part`, and `deck_A+deck_B = 205.0` == original, so all mating
interfaces are unchanged. The seam crosses the central Pi-cradle + 4mm plate - glue the full 165mm plate edge and run a glue fillet on the deck **underside**
(clear of the strap-mounted bank). Same filament for both halves → invisible seam.

### GUI settings to reproduce (if you re-slice the STL yourself)
- **shell** (`shell_print.stl`, keep crown-down, **auto-orient OFF**): 0.22mm
  layer, 10% infill, 2 wall loops, 4 top / 3 bottom, support **off**.
- **deck one-piece** (`deck.stl`): 0.30mm layer, 8% infill, 2 walls, 3 top /
  3 bottom, support **on, "on build plate only"**, 5mm brim.

## Per-part settings

| Slice this file | Orientation | Supports | Brim | Other |
|---|---|---|---|---|
| `castor.stl` | as-is (flat) | none | no | - |
| `sensor_plate.stl` | as-is (back flat down) | none | no | ≥3 walls (thin inter-bore walls) |
| `shelf.stl` | as-is (plate down) | none | **~5 mm** | large thin plate → brim stops corner-lift |
| `chute_print.stl` | as-is (flat bottom down, channel up) | none | optional | Warning: benign "floating cantilever" warning |
| `deck.stl` | as-is (skirt-rim down) | **ON - "on build plate only"** | **~5 mm** | 42 mm standoffs print upright, fine |
| `shell_print.stl` | as-is (**crown DOWN**) | none | optional (5–8 mm) | Warning: benign "floating regions" warning; ≥3 walls |

## MUST-DO (or the print fails / prints the wrong file)

1. **Deck → enable supports = "on build plate only" + a brim.**
   The 4 mm top plate floats over a 199 mm cavity that cannot be bridged.
   Warning: BambuStudio slices the deck **with supports OFF and gives NO warning** - the
   slicer will not protect you. Verified: with build-plate supports it slices clean
   and the support amount is modest (fills the 4 mm gap, peels as a sheet).
2. **Slice the `_print` variants** - `shell_print.stl`, `chute_print.stl`.
   NOT `shell.stl` / `chute.stl` (assembly-pose reference models).
3. **Turn OFF auto-orient / "lay flat"** so the baked-in pose is kept
   (shell must stay crown-down).

## Warnings that are SAFE to ignore (they still produce full gcode)

- `chute_print` → *"has floating cantilever"* = the flange bridging a ~14 mm gap.
  PLA bridges that cleanly (slicer lays it as a Bridge). Glance at the flange
  underside after printing (it's on the treat path).
- `shell_print` → *"has floating regions"* = wheel-arch tops, sensor-bore roofs,
  crown-facet corners. Self-support crown-down. Optional: "support on overhangs"
  only if you want those faces cleaner (cosmetic).

## Bed placement

Deck (205 mm wide) and shell (214 mm wide) nearly fill the bed → **one part per
plate, centered.** If Auto-arrange refuses or shoves them to an edge, just drag to
the middle. Small parts (castor / sensor_plate / chute_print) can share a plate.

## Post-print (not print blockers)

- Ream the 4× **Ø1.6 mm** camera pilots (sensor_plate) and 4× **Ø2.2 mm** camera
  screw holes (shell) for M2.
- All text (deck/shelf number labels, shell paw + CANIS wordmark) is recessed
  deboss - prints crisp, no action.

## Design note surfaced during verification (not a print issue)

`chute_print` has two deck-bolt holes (y = ±12.5). The **front one (y=+12.5)
lands inside the open channel slot** - no flange material around it, so only the
**rear bolt actually clamps** the chute to the deck. Prints fine; revisit in CAD
if two-point clamping was intended.
