// ============================================================================
//  dispenser_split.scad  --  2-PIECE version of the v4 treat dispenser
// ============================================================================
//  WHY: canis_v4.scad's dispenser() seals the SG90 inside a pocket with no way
//  to insert it (only a 12mm shaft hole reaches the bottom).  This splits the
//  same geometry horizontally at z=20 into:
//
//     dispenser_base  -- open-top cup; the servo drops in from the top and
//                         rests on the floor, wires exit a side slot.  A ring
//                         GROOVE in its top rim is the socket for the top.
//     dispenser_top   -- funnel + flap chamber; a thin SPIGOT ring on its
//                         underside press-fits down into the base's groove.
//                         Outer walls butt flush (58mm OD, invisible seam).
//
//  The joint is a thin ring, NOT a wide bore, so the top keeps the ORIGINAL
//  dispenser interior -> the only bridge is the ~24.5mm servo-pocket ceiling,
//  exactly as the original (which prints fine).  Both pieces print flat with
//  NO support: base open-side up, top funnel-mouth up.
//
//  Nothing here edits canis_v4.scad -- it `use`s it, so the pieces are the
//  exact verified geometry, just cut in two + the ring joint added.
//
//  ASSEMBLY:
//    1. Drop the SG90 into the base, shaft UP, wires out the side slot.
//       (Trim the servo's two mounting tabs -- the pocket fits the bare body.)
//    2. Power the servo to CLOSED (80 deg) and press the flap onto the horn so
//       the pad parks over the gate hole.  DRY-FIT FIRST (see note below).
//    3. Press dispenser_top straight down until the outer walls meet flush.
//    4. Screw the base's 3 feet to the chassis as before; treats drop through
//       the gate hole -> chute, unchanged.
//
//  FIT: the spigot is a ~0.1mm press into the groove -- firm hand-push, holds
//  by friction.  Too tight -> sand the spigot OD; want it permanent -> a drop
//  of glue on the outer seam.  The joint carries almost no load.
//
//  !! SERVO NOTE: an SG90's output shaft is NOT centred on its body (~5mm off).
//  This housing centres the BODY and assumes a centred shaft, so dry-fit the
//  flap and confirm the pad fully covers the gate at CLOSED before committing.
//  This is inherited from canis_v4.scad, not the split.  If the pad misses,
//  tell me your servo's shaft-to-body-back distance and I'll offset the pocket.
// ============================================================================

use <canis_v4.scad>
$fn = 96;
eps = 0.02;

// --- constants copied from canis_v4.scad (verified via echo 2026-07-11) ---
hous_d = 58;          // round housing OD  = 2*(flap_r+flap_pad/2)+6

// --- joint geometry (ring socket in base / spigot on top) ---
gsplit = 20;          // horizontal cut plane (below the flap at z=28)
geng   = 5;           // spigot / groove engagement depth (z=15..20)
gro    = 27;          // groove outer radius in the base (rim inner edge)
gri    = 24;          // groove inner radius
spo    = 26.9;        // spigot outer radius  -> 0.1mm press against groove OD
spi    = 25;          // spigot inner radius  -> 1mm clearance inside (register on OD)
wire_w = 6;           // servo-wire exit slot width

module below(zc){ translate([0,0,zc-200]) cylinder(d=400,h=200); }   // z <= zc
module above(zc){ translate([0,0,zc])     cylinder(d=400,h=200); }   // z >= zc

// ---------------------------------------------------------------- BASE (cup)
module dispenser_base(){
  difference(){
    intersection(){ dispenser(); below(gsplit); }
    // ring-groove socket cut into the top rim (opens upward -> prints clean)
    translate([0,0,gsplit-geng])
      difference(){ cylinder(r=gro,h=geng+1); cylinder(r=gri,h=geng+1); }
    // servo-wire exit slot, -x side, through the bottom wall
    translate([-hous_d/2,0,5.5]) cube([hous_d,wire_w,7],center=true);
  }
}

// ---------------------------------------------------------------- TOP (lid)
module dispenser_top(){
  union(){
    intersection(){ dispenser(); above(gsplit); }
    // thin spigot ring hanging below the split, plugs into the base groove
    translate([0,0,gsplit-geng])
      difference(){ cylinder(r=spo,h=geng+eps); cylinder(r=spi,h=geng+eps); }
  }
}

// ---- select part: -D part="base" / "top" / "assembly" ----------------------
part = "assembly";
if(part=="base")     dispenser_base();
else if(part=="top") translate([0,0,-(gsplit-geng)]) dispenser_top();  // spigot rim to z=0 for printing
else {
  color([.55,.6,.7])    dispenser_base();
  color([.85,.5,.2,.6]) dispenser_top();
}
