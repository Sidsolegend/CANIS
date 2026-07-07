# CANIS — print notes (STL check before tomorrow's print)

Read-only geometry check on `hardware/cad/stl/` and `hardware/cad/stl_v2/` — no CAD files were changed, this is guidance only.

## Individual part STLs: safe to print as-is

Every individual part in `stl/` (v1) and `stl_v2/` (v2) is a single manifold shell — no floating geometry. Print from whichever version matches the dispenser mechanism you're actually building:

- **v1** (`hardware/cad/stl/`, per `BUILD_GUIDE.md`) — rotating-disc dispenser (`disc.stl`).
- **v2** (`hardware/cad/stl_v2/`, per `BUILD_GUIDE_v2.md`) — flap-gate dispenser (`flap.stl` instead of `disc.stl`).
- **v3** (`hardware/cad/stl_v3/`) — only `shell.stl` exists; incomplete part set, not a full build option yet.

These are not interchangeable — pick one build guide and print all parts from the matching folder.

## Skip this file

`canis_all.stl` (all 9 parts combined on one plate) has multiple shells at inconsistent Z origins — several genuinely float 4–30mm above the others in that file. `BUILD_GUIDE.md` already says to ignore it if printing individual files. Don't print it; print the individual per-part STLs instead.
