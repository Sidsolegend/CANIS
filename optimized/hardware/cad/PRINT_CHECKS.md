# CANIS v4 — STL print verification (2026-07-03)

Every `stl_v4/` part was checked with `print_check.py` (mesh integrity +
voxel-column support analysis: bed contact, and any material whose underside
starts in mid-air, split into *bridge* — anchored on all sides, printable —
vs *cantilever* — open-ended, the failure mode that killed two LYRA case
prints). All parts are **manifold, watertight, single-shell**. Findings and
fixes below. Bambu P1S / PLA / 0.2 mm assumed.

## Print plan (what to slice)

| Part (file) | Orientation | Supports | Notes |
|---|---|---|---|
| `deck.stl` | as exported | **YES — build-plate-only** | the perimeter skirt drops 8 mm, so the whole 199×159 underside is a 4 mm-high closed cavity roof — unbridgeable. Supports fill the skirt cavity (4 mm tall) and peel off as one sheet. |
| `shelf.stl` | as exported | none | 100% bed contact, clean. |
| `shell_print.stl` | as exported (crown DOWN) | none needed | see "shell" below. Expect minor droop on gill-slot roofs and the 2.8 mm sensor-recess ledge (hidden). Brim recommended — tall part. |
| `dispenser.stl` | as exported (upright) | none *possible* | funnel walls are all ≤30° (self-supporting). One caveat: the flap-sweep chamber has a Ø53 flat internal ceiling at z=32 that prints as an internal bridge — expect 0.5–1 mm droop, and the flap has only ~1 mm top clearance. If the flap rubs, sand its top face or raise `disp_top` 1.5 mm and reprint. |
| `flap.stl` | as exported | none | clean. |
| `chute_print.stl` | as exported (flange down) | **YES — tree/organic** | the trough climbs at ~15° above horizontal, so ~1400 mm² of its underside is open cantilever. No orientation avoids this; scars are on the outside of the trough. |
| `lid_print.stl` | as exported (disc DOWN) | none | see "lid" below. |
| `castor.stl` | as exported | none | clean. |
| `sensor_plate.stl` | as exported | none | 99% bed contact. The 0.2 mm counterbore ledges inside the SR04M/PIR bores droop trivially — cosmetic. |

Do **not** slice `shell.stl`, `lid.stl`, or `chute.stl` — those are
assembly-pose models kept for reference/fitchecks; the `*_print` variants
(and `deck.stl`, which is now exported pre-dropped to z=0) are the print files.

## Failures found & fixed (in `canis_v4.scad`)

1. **`lid` printed plug-down** (as modeled): the Ø60 disc rim appeared at
   z=5 as an 8 mm floating ring — the exact LYRA-top failure. Added
   `lid_print`: disc-face on the bed, plug tapers inward going up, dimple
   becomes a first-layer recess. Verified: 80% bed contact, zero cantilever.
2. **`shell` was unprintable upright.** The "45° self-supporting crown" is
   only the outer skin — the inner cavity ceiling is a **flat ~165×121 mm
   slab at ~90 mm height** (hull-of-slices always tops out flat). Added
   `shell_print` (crown-down): the flat crown becomes the first layer, the
   45° taper becomes a 45° flare, the open bottom faces up. Verified: 53%
   bed contact and no large cantilever bands.
3. **`sensor_plate` bores were blocked.** The SR04M and PIR locating rings
   (Ø30 / Ø29.6, only 15 mm apart) cross each other's bores, and the plate
   slab refilled the four camera-standoff screw bores — every bore was cut
   inside its own feature `difference()` *before* the union, so neighbouring
   solids re-filled them (PIR bore solid z=4–6; camera screw bores solid
   through the plate — the PIR sensor and M2 screws could not seat). Fixed
   with full-depth re-cuts after the union. Verified by ray probes: all
   bores open end-to-end.

### v4.1 sensor-bay redesign (2026-07-04)

Opening the bores exposed the real defect: the v4 sensor stack was
**physically impossible** — PIR (Ø23.6) and SR04M (Ø24) centres 15 mm apart
(the devices overlap by ~9 mm), the PIR's lower half sat behind the deck
slab, the 60×65 plate ran 12 mm below deck level and clipped the crown
taper, its rings/standoffs reached 2.1 mm *into* the front wall, and the
plate was never in any fitcheck. v4.1 changes (all in `canis_v4.scad`):

- **PIR moved beside the SR04M** (`pir_y=26`, same height z=24) — bores
  clear by 1.6 mm; the shell face and recess panel updated to match.
- **Plate resized to 88×42**, centred at z=30: bottom edge z=13 (above the
  AA-cradle walls), top edge z=51 (clear of the crown at the plate's depth).
- **Bosses lengthened to 10.5 mm** and moved to a diagonal pair at (±38,
  13/47) — outside the AA cradle's width; ring/standoff tips now stop
  1.4 mm short of the wall.
- **AA cradle walls lowered to 0.5× pack height** (top z=12) so the SR04M
  ring (bottom z=13) clears them.
- **New fitchecks** `pairsel=9` (plate vs deck) and `pairsel=10` (plate vs
  shell) — both render EMPTY.
4. **AA cradle "open end" wasn't open.** The cut was `center=true` at z=2,
   leaving a floating 2.8 mm lintel across the 50 mm opening (unprintable
   bridge) and only a 4.8 mm-tall slot — the 16 mm-tall AA pack could never
   slide in. Now cut full-height from the floor. Verified gone.
5. **`deck` exported at z=−4** (skirt below the origin). Now pre-dropped so
   the skirt rim sits at z=0.

## Re-checking after edits

```bash
python3 print_check.py stl_v4/<part>.stl
```
`FAIL` lines with large `cantilever=` areas are real problems. Known
harmless flags: thin sloped walls (funnel, shell taper) and hole roofs
read as cantilever because the flood-fill leaks through the opening —
judge by the geometry before panicking.
