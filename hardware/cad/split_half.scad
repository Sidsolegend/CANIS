// split_half.scad - cut a print-oriented STL into a left/right half at a plane.
// Used to make deck_A/B and shell_A/B (each a sub-2h30 print job). The cut is a
// pure planar intersection of the already-verified mesh, so each half's outer
// surface is byte-identical to the original -> all mating interfaces unchanged.
//
//   deck  (centre x=0):    openscad -o deck_A.stl  -D 'in_file="stl_v4/deck.stl"'        -D seam=0    -D cy=0 -D keep=0 split_half.scad
//                          openscad -o deck_B.stl  -D 'in_file="stl_v4/deck.stl"'        -D seam=0    -D cy=0 -D keep=1 split_half.scad
//   shell (centre x=-0.8): openscad -o shell_A.stl -D 'in_file="stl_v4/shell_print.stl"' -D seam=-0.8 -D cy=0 -D keep=0 split_half.scad
//                          openscad -o shell_B.stl -D 'in_file="stl_v4/shell_print.stl"' -D seam=-0.8 -D cy=0 -D keep=1 split_half.scad
//
// keep=0 keeps x<seam, keep=1 keeps x>seam. cy = model Y centre of the part.

in_file="in.stl"; seam=0; keep=0; big=1000; cy=0;
intersection(){
  import(in_file, convexity=12);
  if(keep==0) translate([seam-big, cy-big/2, -1]) cube([big,big,big+2]);
  else        translate([seam,     cy-big/2, -1]) cube([big,big,big+2]);
}
