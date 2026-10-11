# CANIS v4 — Deck & Shell print-time options

Goal: get the two long prints (deck, shell) down from their baselines, keeping the
**same Bambu P1S 0.4 nozzle** and **without changing any fit**. Everything below was
verified by actually re-slicing headless in BambuStudio 2.07 (real P1S machine +
0.20 Standard process + Bambu PLA Basic).

## Baselines (your GUI)
| Part | Baseline | Weight |
|---|---|---|
| Deck | 3h57 | 146 g |
| Shell | 4h48 | 206 g |

## Key finding — 2h30 one-piece is impossible on a 0.4 nozzle
Every slicer lever was tested. Walls are already 2, speeds already maxed, and
0.30 mm is the nozzle's max layer height. The **deck's model alone is 2h26 before
its required support** (the 4 mm plate floats over the skirt cavity and must be
supported). The **shell is simply big** — 214×173 mm footprint, **92 mm tall**.
So one-piece floors are ~3h15 (deck) and ~3h45 (shell); the only way under 2h30 is
to split a part into two pieces (each < 2h30).

## ⚠ CLI-vs-GUI note (important)
The verification was done with the BambuStudio **command-line** slicer, which runs
**~6–12 % HIGHER** than the GUI (my CLI repro: deck 4h17, shell 5h26 vs your GUI
3h57 / 4h48). **The GUI times below are what you'll actually see.** If you re-slice
yourself, use the exact settings in the last section — slicing at the default
0.20 mm is what produced the surprise "2h40" on a shell half.

---

## Final decisions & ready-to-print files (`printables/`)

### SHELL — one-piece, trimmed
| File | GUI time | Notes |
|---|---|---|
| `shell_onepiece.gcode.3mf` | **~4h20** (from 4h48) | 0.22 mm layer, no seam, surface unchanged |

Layer 0.22 mm (barely different from 0.20, so the cover stays clean), 10 % infill,
2 walls, 4 top / 3 bottom, **no support**, crown-down.

### DECK — two options, pick at print time
| Option | File(s) | GUI time | Trade |
|---|---|---|---|
| **One-piece** (recommended) | `deck_onepiece.gcode.3mf` | **~3h15** | no seam through the electronics platform |
| **Split** | `deck_A.gcode.3mf` + `deck_B.gcode.3mf` | **~1h50 each** | under 2h30, but a glued seam |

One-piece deck: 0.30 mm, 8 % infill, 2 walls, 3 top / 3 bottom, support
**"on build plate only"**, 5 mm brim. Support-bound at ~3h15 — cannot reach 2h30
one-piece.

### Fallback — split shell (only if you ever need shell halves < 2h30)
STLs `stl_v4/shell_A.stl` + `stl_v4/shell_B.stl`, ~2h05 GUI each at
0.28 mm / 8 % / no support / **5 mm brim**, crown-down. Not the chosen path — no
gcode shipped for these by default.

---

## Fit & integrity — why nothing breaks
The split halves are a **single planar cut on the already-verified print-oriented
STL** (via `../../split_half.scad`), not a CAD re-derive, so
each half's outer surface is byte-identical to the original. Verified:

- `deck_A + deck_B = 205.0 mm` == original deck width
- `shell_A + shell_B = 214.4 mm` == original shell width
- all halves: `manifold = yes`, `number_of_parts = 1` (no auto-repair)
- Z extents identical to originals (deck 50.0, shell 92.0)

So every mating interface — shelf screws, chute, sensor-plate bosses, wheel arches,
deck seating — is unchanged. Your already-printed parts still fit.

## Assembly notes (if you split)
- **Deck seam** crosses the central Pi-cradle + 4 mm plate. Glue the full 165 mm
  plate edge (CA or thin epoxy) and run a glue fillet on the **underside** (belly,
  clear of the strap-mounted power bank) for stiffness. This is the weaker of the
  two seams — it carries the electronics — which is why one-piece is recommended.
- **Shell seam** (fallback only) is transverse at x=0: front half = sensor face +
  camera bosses + paw; rear half = hopper fill-port + CANIS wordmark + gills. Keeps
  all precision features intact; crosses only open crown vent slots and the
  wheel-arch openings (side-wall bond interrupted near the bottom, ample above).
- Print both halves of a part in the **same filament** so the seam is invisible.

## GUI settings to reproduce (if you re-slice the STL yourself)
| Part | STL | Orientation | Layer | Infill | Walls | Top/Bot | Support | Brim |
|---|---|---|---|---|---|---|---|---|
| Shell (one-piece) | `shell_print.stl` | crown-down, **auto-orient OFF** | 0.22 | 10 % | 2 | 4/3 | **off** | no |
| Deck (one-piece) | `deck.stl` | as-is (skirt down) | 0.30 | 8 % | 2 | 3/3 | **on, "build plate only"** | 5 mm |
| Deck half ×2 | `deck_A/B.stl` | as-is | 0.30 | 10 % | 2 | 4/3 | on, build-plate-only | 5 mm |
| Shell half ×2 | `shell_A/B.stl` | crown-down, auto-orient OFF | 0.28 | 8 % | 2 | 4/3 | off | 5 mm |

Originals (`deck.gcode.3mf`, `shell.gcode.3mf`, `deck.stl`, `shell_print.stl`) are
untouched. Full per-part reference: `../../SLICER_SETTINGS_P1S.md`.
