// ============================================================================
//  shelf_plain.scad  --  stripped-down upper shelf
// ============================================================================
//  The canis_v4 shelf() has: engraved numbers 7/8/9, a raised stick-mount tray,
//  board standoff posts, screw holes, and wire pass-throughs.  Per request this
//  is JUST the flat plate + the rear hopper notch -- no labels, no raised mounts,
//  no holes.  Mount boards / bolt it down using your own holes (metal-chassis
//  grid).  Uses canis_v4.scad's rr() so the outline is byte-identical; the file
//  itself is untouched.
// ============================================================================
use <canis_v4.scad>
$fn = 48;
eps = 0.02;

// --- from canis_v4.scad (verified) ---
shelf_l = 195; shelf_w = 165; shelf_t = 3; fillet = 9;
notch_x = -shelf_l/2; notch_r = 34;

module shelf_plain(){
  difference(){
    rr(shelf_l, shelf_w, fillet, shelf_t);                       // flat plate
    // rear hopper notch (verbatim from canis_v4 shelf())
    hull(){
      translate([notch_x-1, 0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps);
      translate([notch_x-40,0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps);
      translate([-38,     0,-eps])   cylinder(r=20,    h=shelf_t+2*eps);
    }
  }
}
shelf_plain();
