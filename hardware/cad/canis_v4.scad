// ============================================================================
//  CANIS v4 - TWO-TIER chassis, enclosure & mounts  (OpenSCAD)
//  Lower deck: Pi 5, L298N, buck, motors, castor, dispenser.
//  Upper shelf (on 4 standoffs): Arduino, breadboard, ESP32.
//  SHELL v4: sleek low wedge - soft radii (calm, non-threatening for a fearful
//  dog), ~45deg self-supporting crown taper, rounded rear haunch carrying the
//  treat-hopper collar, recessed sensor face, scalloped wheel fenders, raked
//  side cooling gills, paw-print deboss. Same fit interfaces as v2/v3
//  (deck 205x165, sensor coords, plate bosses, wheel arches, hopper).
//  Bambu P1S / PLA.
//
//  RENDER one part: set `part` below (F5/F6) or -D 'part="deck"'.
//  Parts:  deck  shelf  shell  dispenser  gate  lid  castor  sensor_plate
//  Views:  layout (both tiers, real outlines)  ·  assembly
// ============================================================================
part = "assembly";
$fn = 48;
eps = 0.02;

// ---- core sizes ----
deck_l = 205; deck_w = 165; deck_t = 4;     // lower deck (no ribs; 4mm + skirt)
skirt_h = 8; skirt_t = 3; fillet = 9;
shelf_l = 195; shelf_w = 165; shelf_t = 3;  // upper shelf
post_h = 42;                                 // standoff height (clears the Pi)
// 4 standoffs - verified clear on BOTH tiers:
POSTS = [[84,74],[84,-74],[-25,74],[-25,-74]];

// ---- components (size_x, size_y, cx, cy, mount) ----
pi_l=92; pi_w=63;
LOWER = [
  ["Pi 5",        pi_l, pi_w,  8,   0, "cradle"],
  ["L298N",       44, 44,   -74,  54, "board"],
  ["LM2596 buck", 44, 22,   -74, -54, "board"],
];
UPPER = [
  ["Breadboard",  84, 55,    20,  45, "stick"],
  ["Arduino Uno", 68, 53,    20, -45, "board"],
  ["ESP32",       56, 29,    30,   0, "board"],
];

// ---- dispenser (FLAP GATE) / sensors / drive ----
// SG90 stands UPRIGHT; a printed FLAP (paddle) on its horn sweeps under a fixed
// GATE hole. CLOSED = flap covers the hole (one treat held at the throat); servo
// swings the flap ~80deg AWAY -> the treat drops through -> deck -> chute -> floor.
// Matches the tested firmware (OPEN=0 / CLOSED=80). "Just add the SG90 + its horn."
treat_d  = 9;                       // CONFIRM: small treats ~6-9mm -> design for 9mm
hop_top  = 54; hop_h = 60; hop_wall = 2;     // funnel (hopper): mouth, height, wall
throat_d = treat_d + 5;             // ~14: throat passes ONE treat at a time
gate_d   = treat_d + 6;             // ~15: exit hole the treat drops through
flap_r   = 16;                      // shaft -> gate-hole centre = flap swing radius (keeps housing ~58mm)
flap_th  = 3;                       // flap paddle thickness
flap_pad = gate_d + 5;              // pad dia that covers the gate when CLOSED
disp_x   = -72;                     // servo axis on the deck (rear)
sg_l=23; sg_w=12.5; sg_h=23; sg_screw=2.4;   // SG90 body
flap_z   = 4 + sg_h + 1;            // flap sits just above the upright servo
hous_d   = 2*(flap_r + flap_pad/2) + 6;      // housing dia: swept flap + walls
chute_mt = gate_d/2+5;              // chute mount-hole half-spacing (shared deck<->chute)
us_eye_d=16.2; us_pitch=26; pir_d=23.6; cam_d=9; cam_hx=21; cam_hy=12.5; cam_s=2.2;
srm_d=24; us_yaw=32;   // SR04M-2 transducer hole (paw centre) ; L/R HC-SR04 aim angle (deg outward)
wheel_d=68; wheel_t=26; axle_x=22; track=deck_w+12;
castor_x=-deck_l/2+18; castor_bc=24; castor_hole=4.3; castor_spacer_h=9;
m3=3.3; m3p=2.8;

// ---- power bays (PARAMETRIC placeholders - edit to your exact parts) ----
aa_l=58; aa_w=31; aa_h=16; aa_c=[80,0];            // [5] 4xAA motor pack: open-front cradle on the deck (front), long axis fore-aft
bank_l=100; bank_w=65; bank_h=25; bank_c=[12,0];   // [6] Pi power bank: UNDER the deck (belly), strap-mounted
lbl_h=0.7;                                          // engrave depth for the numbered labels
// NUMBER KEY (engraved on deck/shelf):
//   1 Pi 5   2 L298N   3 buck   4 dispenser(servo)   5 4xAA motor pack   6 Pi power bank (UNDER)
//   7 Arduino   8 ESP32   9 breadboard   10 sensors (camera+3 ultrasonic+PIR, on the shell paw)

// ---- helpers ----
module rr(l,w,r,h){ linear_extrude(h) offset(r) square([max(.01,l-2*r),max(.01,w-2*r)],center=true); }
module thruhole(x,y,d,from,to){ translate([x,y,from]) cylinder(d=d,h=to-from); }
module standoffs(pts,h){ for(p=pts) translate([p[0],p[1],0]) difference(){ cylinder(d=7,h=h); translate([0,0,-1]) cylinder(d=m3p,h=h+2);} }

// a board's 4 mounting standoffs (open underneath)
module board_mt(sx,sy){ for(ix=[-1,1],iy=[-1,1]) translate([ix*(sx/2-4),iy*(sy/2-4),0]) difference(){ cylinder(d=6.5,h=5); translate([0,0,-1]) cylinder(d=m3p,h=7);} }
// breadboard locator lip (open centre)
module stick_mt(sx,sy){ difference(){ rr(sx+5,sy+5,4,2.6); translate([0,0,-eps]) rr(sx+0.8,sy+0.8,2,4);} }
// component = [name, sx, sy, cx, cy, mount]
module place(list){ for(c=list){ if(c[5]=="board") translate([c[3],c[4],0]) board_mt(c[1],c[2]); if(c[5]=="stick") translate([c[3],c[4],0]) stick_mt(c[1],c[2]); } }

// engrave a number label into a top surface (z = surface height)
module lbl(n,x,y,z,rot=0){ translate([x,y,z-lbl_h]) rotate(rot) linear_extrude(lbl_h+eps) text(str(n),size=8,halign="center",valign="center",font="Liberation Sans:style=Bold"); }
// 4xAA holder cradle: floor + 3 walls, open +y end to slide the pack in
// walls 0.5*pack height (was 0.6): the cradle sits in front of the sensor
// plate and its wall top must stay under the SR04M ring (ring bottom z=13)
module aa_cradle(){ w=2.4; h=aa_h*0.5; difference(){
  rr(aa_l+2*w, aa_w+2*w, 3, h);
  translate([0,0,2]) rr(aa_l, aa_w, 1, h);
  // open one end, FULL height: the previous centred cut (z 2 +/- h/2) left a
  // 2.8mm lintel spanning the 50mm opening (an unprintable floating bridge)
  // and only a 4.8mm-tall slot the 16mm AA pack could never slide through.
  translate([0,(aa_w+2*w)/2,2+h/2]) cube([aa_l-8, 2*w+2, h+eps], center=true);
} }

// ============================== LOWER DECK ==================================
module deck(){
  difference(){
    union(){
      rr(deck_l,deck_w,fillet,deck_t);
      translate([0,0,-(skirt_h-deck_t)]) difference(){          // perimeter skirt
        rr(deck_l,deck_w,fillet,skirt_h);
        translate([0,0,-eps]) rr(deck_l-2*skirt_t,deck_w-2*skirt_t,max(.5,fillet-skirt_t),skirt_h+2*eps);
      }
      // Pi cradle (fence)
      translate([8,0,0]) difference(){
        rr(pi_l+8,pi_w+8,5,deck_t+10);
        translate([0,0,deck_t]) rr(pi_l+1.4,pi_w+1.4,3,12);
        for(s=[-1,1]) translate([s*(pi_l/2),0,deck_t+6]) cube([16,30,16],center=true);  // port gaps
      }
      translate([0,0,deck_t]) place(LOWER);                      // board standoffs
      translate([aa_c[0],aa_c[1],deck_t]) rotate(-90) aa_cradle();  // [5] 4xAA cradle, long axis fore-aft, opens to the front
      translate([0,0,deck_t]) standoffs(POSTS,post_h);           // shelf standoffs
    }
    // through holes (full depth, clear of skirt/cradle)
    for(p=POSTS) thruhole(p[0],p[1],m3p,-(skirt_h+1),post_h+1);                // shelf screws up into posts
    for(a=[45:90:359]) thruhole(castor_x+castor_bc/2*cos(a),castor_bc/2*sin(a),castor_hole,-(skirt_h+1),deck_t+1);
    // countersinks: FLAT-HEAD castor bolts sit flush so the dispenser housing (which
    // covers these holes) still seats flat on the deck - v2/v3 needed nuts here.
    for(a=[45:90:359]) translate([castor_x+castor_bc/2*cos(a),castor_bc/2*sin(a),deck_t-2.2]) cylinder(d1=4.3,d2=9.6,h=2.21);
    for(sx=[-1,1],sy=[-1,1]) thruhole(axle_x+sx*16,sy*(deck_w/2-10),m3,-(skirt_h+1),deck_t+1);  // motor brackets
    for(p=disp_pins()) thruhole(disp_x+p[0],p[1],m3,-(skirt_h+1),deck_t+1);                      // dispenser feet
    thruhole(exit_x,0,gate_d+2,-(skirt_h+1),deck_t+1);                                          // treat exit (drops to chute below)
    // chute screws Y-spaced (v2/v3 X-spacing put one screw inside the wire slot and
    // the flange over the castor spacer)
    for(s=[-1,1]) thruhole(exit_x,s*chute_mt,m3,-(skirt_h+1),deck_t+1);
    // wire-routing slots (between tiers + around components)
    for(c=[[50,-45],[-40,0],[-40,42]]) translate([c[0],c[1],-(skirt_h+1)]) hull(){ thruhole(0,-9,7,0,skirt_h+deck_t+2); thruhole(0,9,7,0,skirt_h+deck_t+2);}
    // [6] strap slots for the power bank held UNDER the deck (velcro/zip strap)
    for(sx=[bank_c[0]-32, bank_c[0]+32], sy=[-1,1]) translate([sx, sy*(bank_w/2+5), deck_t/2]) cube([11,2.8,deck_t+6],center=true);
    // engraved NUMBER labels (key at top of file)
    lbl(1,8,-44,deck_t); lbl(2,-50,30,deck_t); lbl(3,-50,-40,deck_t);
    lbl(4,-72,-37,deck_t); lbl(5,aa_c[0],-38,deck_t); lbl(6,40,66,deck_t);
  }
}

// ============================== UPPER SHELF =================================
notch_x=-shelf_l/2; notch_r=34;
module shelf(){
  difference(){
    union(){
      rr(shelf_l,shelf_w,fillet,shelf_t);
      translate([0,0,shelf_t]) place(UPPER);
    }
    // rear notch so the dispenser hopper passes through - extended forward: the
    // funnel's sloped wall reaches x~-45 at shelf height (v2/v3 notch cut it off)
    hull(){ translate([notch_x-1,0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps); translate([notch_x-40,0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps); translate([-38,0,-eps]) cylinder(r=20,h=shelf_t+2*eps); }
    for(p=POSTS) thruhole(p[0],p[1],m3,-eps,shelf_t+eps);        // screw to standoffs
    for(c=[[-5,0],[-5,30],[-5,-30]]) thruhole(c[0],c[1],8,-eps,shelf_t+eps);  // wire pass-throughs (tidy column)
    lbl(7,75,-45,shelf_t); lbl(8,75,0,shelf_t); lbl(9,75,45,shelf_t);          // 7 Arduino  8 ESP32  9 breadboard
  }
}

// ============================== DISPENSER (flap gate) =======================
//  HOW IT WORKS: the SG90 stands UPRIGHT inside the round housing, shaft up. A
//  printed FLAP (paddle) presses on the horn and sits just under the throat. The
//  funnel feeds kibble down a one-treat throat; the flap holds that treat over a
//  GATE hole. The servo swings the flap ~80deg clear -> the treat drops through
//  the gate -> down through the deck -> out the CHUTE at the rear.
function disp_pins()=[for(a=[90:120:359])[(hous_d/2-4)*cos(a),(hous_d/2-4)*sin(a)]];  // 3 mount feet
gate_c   = [flap_r,0];                             // gate-hole centre (local)
exit_x   = disp_x + flap_r;                        // where the treat drops through the deck
disp_top = flap_z + flap_th + 3;
module dispenser(){      // housing + funnel (SG90 + flap go INSIDE)
  difference(){
    union(){
      cylinder(d=hous_d, h=disp_top);                                     // round housing
      translate([0,0,disp_top-eps]) difference(){                         // funnel: mouth centred, throat over the gate
        hull(){ translate(gate_c) cylinder(d=throat_d+2*hop_wall,h=eps); translate([0,0,hop_h]) rr(hop_top,hop_top,6,eps);}
        hull(){ translate(gate_c) translate([0,0,-eps]) cylinder(d=throat_d,h=eps); translate([0,0,hop_h+eps]) rr(hop_top-2*hop_wall,hop_top-2*hop_wall,4,eps);}
      }
    }
    translate([0,0,2+(sg_h+3)/2]) cube([sg_l+1.5,sg_w+1.5,sg_h+3],center=true); // servo body cavity (shaft centred)
    cylinder(d=12,h=disp_top+2,center=true);                              // shaft + horn clearance up the centre
    translate([0,0,flap_z-0.5]) cylinder(d=2*(flap_r+flap_pad/2)+1.5,h=flap_th+1.5); // flap sweep chamber
    translate(gate_c) translate([0,0,flap_z+flap_th-eps]) cylinder(d=throat_d+1,h=hop_h); // throat -> onto the flap
    translate(gate_c) translate([0,0,-eps]) cylinder(d=gate_d, h=flap_z+1);              // gate hole (under flap -> through deck)
    for(p=disp_pins()) translate([p[0],p[1],-eps]) cylinder(d=m3,h=8);    // mount feet
  }
}
module flap(){          // the gate paddle; presses on the SG90 horn, sweeps under the throat
  difference(){
    hull(){ cylinder(d=12,h=flap_th); translate(gate_c) cylinder(d=flap_pad,h=flap_th); } // hub -> arm -> pad over the gate
    cylinder(d=5.8,h=flap_th+1,$fn=6);                                    // horn cross-socket (trim to fit the SG90 spline)
    translate([0,0,flap_th-2.2]) cylinder(d=9,h=3);                       // horn body recess
  }
}
// ===== CHUTE v4.2 - OPEN-TOP WEDGE (prints flat, ~no support) =====
// v1-v4.1 was a CLOSED tube running nearly horizontal: the slicer packed TREE
// supports along its whole underside AND *inside* the tube, where they can't be
// removed. This is an OPEN channel cut into a FLAT-BOTTOMED wedge: printed
// flat-bottom-down (part="chute_print") it sits on an 18x84mm base with the
// channel opening up -> ~2% overhang (just the flange bridging the entry, a
// short self-supporting bridge), ZERO interior supports. The internal floor is
// sloped so a treat still slides out; the flat outer bottom is only for printing
// and mounting. Same flange + 2 Y-spaced screws + gate bore -> the DECK is
// UNCHANGED. Exits at the +Y side, clearing the castor, wheel and belly bank.
cw_iw=treat_d+5; cw_wall=2; cw_fl=2; cw_wh=8;   // inner width / wall / floor / side-wall height
cw_ow=cw_iw+2*cw_wall;
cw_ang=10; cw_yb=-16; cw_ym=66;                 // slide angle / back edge / muzzle (Y = exit dir)
function cw_ftop(y)=cw_fl+(cw_ym-y)*tan(cw_ang);// floor-top height above the flat bottom (high at back)
cw_H=cw_ftop(cw_yb)+cw_wh;                       // tallest section (back), ~24mm
module cw_slab(w,z0,z1){ translate([0,0,(z0+z1)/2]) cube([w,0.1,max(.01,z1-z0)],center=true); }
module chute_wedge(){                            // flat bottom at z=0 -> PRINT orientation
  difference(){
    union(){
      hull(){ translate([0,cw_yb,0]) cw_slab(cw_ow,0,cw_H);            // wedge outer, flat bottom
              translate([0,cw_ym,0]) cw_slab(cw_ow,0,cw_ftop(cw_ym)+cw_wh); }
      translate([0,0,cw_H-3]) rr(22,2*chute_mt+9,3,3);                 // flange on top (bolts to deck)
    }
    hull(){ translate([0,-6,0]) cw_slab(cw_iw,cw_ftop(-6),cw_H+25);    // open sloping channel (open muzzle)
            translate([0,cw_ym+6,0]) cw_slab(cw_iw,cw_ftop(cw_ym),cw_ftop(cw_ym)+cw_wh+25); }
    translate([0,0,cw_H-13]) cylinder(d=gate_d+2,h=20);                // gate drop bore
    for(s=[-1,1]) translate([0,s*chute_mt,cw_H-4]) cylinder(d=m3,h=6); // mount holes (match deck)
  }
}
// USE orientation: flange up at the deck underside, wedge hanging below (flange
// top aligned to v1's z=1.5 so the existing placement translate stays the same).
module chute(){ translate([0,0,1.5-cw_H]) chute_wedge(); }
// plug is TAPERED: the funnel keeps narrowing below the rim (hull toward the
// offset throat), so a straight plug binds ~2.5mm in (v2/v3 defect).
module lid(){ difference(){ union(){ cylinder(d=hop_top+6,h=3); translate([0,0,-5]) cylinder(d1=hop_top-2*hop_wall-8,d2=hop_top-2*hop_wall-0.6,h=5.5);} translate([0,0,3]) scale([1,1,.4]) sphere(d=hop_top*.5);} }

// ============================== CASTOR SPACER ===============================
// d33 round with flats at x=+/-14 (v2/v3 was a 44x44 square that overlapped the
// deck skirt wall by 7mm and sat in the chute's path); same bolt circle & hub.
module castor(){ difference(){
  intersection(){ cylinder(d=33,h=castor_spacer_h); translate([0,0,castor_spacer_h/2]) cube([28,34,castor_spacer_h+2],center=true); }
  for(a=[45:90:359]) thruhole(castor_bc/2*cos(a),castor_bc/2*sin(a),castor_hole,-eps,castor_spacer_h+eps);
  translate([0,0,1.6]) cylinder(d=22,h=castor_spacer_h); thruhole(0,0,11,-eps,castor_spacer_h+eps);} }

// ============================== SHELL v4 (sleek wedge) ======================
// Low convex wedge: soft vertical lower walls, ~45deg self-supporting crown
// taper, and a rounded rear "haunch" that carries the treat-hopper collar so
// the funnel reads as a designed fill-port, not a hole. Recessed rounded front
// sensor face (same coords/bosses as v2/v3 -> sensor plate still fits),
// scalloped wheel fenders, raked side cooling gills over the L298N bay,
// paw-print deboss on the crown, CANIS wordmark debossed on the tail.
shell_clr=1.5; wall=2.4; skirt_drop=16;
// INNER surface must clear the deck (205x165): footprint = deck + 2*(wall+clr)
sh_bl=deck_l+2*(wall+shell_clr); sh_bw=deck_w+2*(wall+shell_clr);   // 212.8x172.8
sh_vh=52;            // vertical wall height (crown taper starts here)
sh_th=76;            // total height at the crown
sh_r=10;             // soft body corner radius (friendly, not slab-like)
top_l=170; top_w=126; top_r=18; top_dx=-4;            // crown facet (~45deg taper)
haunch_c=[disp_x,0]; haunch_d=72;                     // rounded rear shoulder (hopper), concentric with the fill-port
hop_hole=64;         // funnel pass-through (clears the square mouth corners)
front_x=sh_bl/2;                   // front wall plane (+x)
// v4.1 sensor bay: camera top-centre, SR04M-2 centre, PIR BESIDE the SR04M.
// The v4 vertical stack (pir_z=9) was physically impossible: PIR(d23.6) and
// SR04M(d24) centres sat 15mm apart (sensors overlap by ~9mm) and the PIR's
// lower half was behind the deck slab.
cam_z=40; srm_z=24;                // sensor heights on the front face.
// KEEP cam_z=40. The camera bore (d cam_d+1=10, bottom=cam_z-5) and the SR04M
// bore (d 24, top=srm_z+12=36) are close: at cam_z=40 they OVERLAP by 1mm and
// merge into one clean opening (always manifold). Nudging cam_z to 41 makes the
// two cuts EXACTLY tangent -> a single non-manifold edge at (y=0,z=36) that
// Bambu flags; 42 does the same on the plate (cam vs SR04M clearance holes).
// The plate is only ~21mm tall (crown taper caps it), so the camera standoffs
// can't move up to dodge this either. 40 is the one value clean for both parts.
pir_z=24; pir_y=26;                // PIR beside the SR04M (bores clear by 1.6mm)
module top_slice(g=0){
  // hull(), not union(): a plain union of the rect + haunch circle meets at a
  // non-tangent seam (their boundaries cross, not blend) - the fill-port hole
  // cut through that seam came out with a creased, "melted" edge instead of a
  // clean round collar. hull() blends the two into one smooth outline.
  hull(){
    translate([top_dx,0,0]) rr(top_l+g, top_w+g, top_r, eps);
    translate([haunch_c[0],haunch_c[1],0]) cylinder(d=haunch_d+g, h=eps);
  }
}
module shell_body(g=0,t=0){ hull(){
  translate([0,0,-skirt_drop]) rr(sh_bl+g, sh_bw+g, sh_r, eps);
  translate([0,0,sh_vh])       rr(sh_bl+g, sh_bw+g, sh_r, eps);
  translate([0,0,sh_th+t])     top_slice(g);
} }
module paw_holes(){    // recessed rounded face panel + camera/SR04M-2/PIR bores
  translate([front_x-0.4,13,28]) rotate([0,90,0])
    linear_extrude(2.8,center=true) offset(7) square([44,58],center=true);   // covers cam + SRM + side PIR
  translate([front_x,0,cam_z]) rotate([0,90,0]) cylinder(d=cam_d+1,h=20,center=true);
  for(sx=[-1,1],sy=[-1,1]) translate([front_x,sx*cam_hx/2,cam_z+sy*cam_hy/2]) rotate([0,90,0]) cylinder(d=cam_s,h=20,center=true);
  translate([front_x,0,srm_z]) rotate([0,90,0]) cylinder(d=srm_d,h=20,center=true);   // [10c] SR04M-2 centre
  translate([front_x,pir_y,pir_z]) rotate([0,90,0]) cylinder(d=pir_d,h=20,center=true);   // PIR (beside the SR04M)
}
// (y,z) on the vertical front wall - diagonal pair at y=±38: outside the AA
// cradle's width, clear of the deck, and clear of both sensor rings
function plate_bosses()=[[-38,13],[38,47]];
// [10L/10R] angled HC-SR04 eye-pairs bored through the front corners (L/R bearing)
us_side_x=front_x-22; us_side_y=66;
module side_us(s){ translate([us_side_x, s*us_side_y, srm_z]) rotate([0,0,s*us_yaw])
  for(e=[-1,1]) translate([0,e*us_pitch/2,0]) rotate([0,90,0]) cylinder(d=us_eye_d,h=70,center=true); }
module paw2d(){        // paw print, toes forward (+x), toes clearly separated from the pad
  scale([1.15,1]) circle(d=16);
  for(a=[-42,-14,14,42]) rotate(a) translate([15.5,0]) scale([1.35,1]) circle(d=6.8);
}
module shell(){
  difference(){
    union(){
      difference(){ shell_body(0,0); translate([0,0,-2]) shell_body(-2*wall,-wall); }
      // bosses 10.5 long (0.5 embedded): the plate mounts on their ends at
      // x=front_x-wall-10, so its 6mm rings/standoffs stop 1.4mm short of the
      // wall (the v4 7mm bosses left the ring tips 2.1mm INSIDE the wall)
      translate([front_x,0,0]) for(b=plate_bosses())
        translate([-wall-4.75,b[0],b[1]]) rotate([0,90,0]) difference(){ cylinder(d=6,h=10.5,center=true); cylinder(d=m3p,h=12.5,center=true);}
    }
    translate([0,0,-skirt_drop-2]) shell_body(-2*wall+0.4,-wall);                   // open bottom
    paw_holes();
    side_us(1); side_us(-1);                                                        // L/R angled HC-SR04 openings
    for(s=[-1,1]) translate([axle_x,s*(sh_bw/2),-deck_t]){                          // wheel arches, centred on the real axle
      rotate([90,0,0]) cylinder(d=wheel_d+12,h=2*wall+8,center=true);
      translate([0,s*0.3,0]) rotate([90,0,0]) cylinder(d=wheel_d+24,h=3,center=true); // shallow fender scallop (1.2mm)
    }
    translate([disp_x,0,sh_vh-10]) cylinder(d=hop_hole,h=sh_th);                    // funnel pass-through (haunch)
    translate([disp_x,0,sh_th-2]) cylinder(d1=hop_hole,d2=hop_hole+7,h=2.2);        // chamfered fill-port rim
    for(i=[0:5]) translate([8+(i-2.5)*9,0,sh_th-6]) hull(){                          // crown vent slots (over the Pi fan)
      for(e=[-1,1]) translate([0,e*15,0]) cylinder(d=4,h=8);
    }
    for(s=[-1,1],i=[0:2]) translate([-76+i*9, s*(sh_bw/2), 19+i*7]) rotate([90,0,0]) // raked side gills (L298N/buck bay)
      hull(){ for(e=[-1,1]) translate([e*10,0,0]) cylinder(d=3.6,h=12,center=true); }
    translate([44,0,sh_th-0.8]) linear_extrude(2) paw2d();                          // paw deboss on the crown
    // CANIS - deboss on the tail face (1.2mm)
    translate([-sh_bl/2-0.5,0,27]) rotate([90,0,90]) mirror([1,0,0]) linear_extrude(1.7) text("CANIS",size=14,halign="center",valign="center",font="Liberation Sans:style=Bold");
  }
}

// ============================== SENSOR PLATE (modular) ======================
// Flat plate behind the paw: mounts CAMERA (top) + SR04M-2 centre transducer +
// PIR (bottom), screws to the 2 shell bosses. The 2 HC-SR04 mount on the angled
// shell side openings (side_us), NOT on this plate.
// plate-local y coords: plate centre sits at shell z=(cam_z+pir_z)/2 when mounted
// plate-local: x = shell y, y = shell z - paw_mid. Plate 88x42, centred at
// shell z=30: bottom edge at z=13 (above the AA-cradle walls at z=12 - the
// v4 60x65 plate ran 12mm BELOW deck level and clipped the crown taper),
// top edge at z=51 (clear of the crown taper at the plate's depth).
paw_mid=30; paw_cam=cam_z-paw_mid; paw_us=srm_z-paw_mid; paw_pir=pir_z-paw_mid;
plate_W=88; plate_H=42;
module sensor_plate(){
  difference(){
    union(){
      rr(plate_W,plate_H,4,2.6);
      // camera standoffs (top centre). d=4.4 (was 5): the two BOTTOM standoffs
      // sit ~0.4mm over the edge of the SR04M-2 clearance bore, so the bore cut
      // clips a small crescent off each standoff base. This is harmless (still
      // manifold, standoff still bonds to the plate) but d=4.4 shrinks the clip
      // for a cleaner base. It is NOT the old "cam_z+1" move - that nudged the
      // camera up and introduced a non-manifold tangency; reverted (see cam_z).
      for(sx=[-1,1],sy=[-1,1]) translate([sx*cam_hx/2,paw_cam+sy*cam_hy/2,0]) difference(){ cylinder(d=4.4,h=6); translate([0,0,-eps]) cylinder(d=1.6,h=8);}
      // SR04M-2 locating ring (centre)
      translate([0,paw_us,0]) difference(){ cylinder(d=srm_d+6,h=6); translate([0,0,-eps]) cylinder(d=srm_d+0.6,h=8);}
      // PIR ring - BESIDE the SR04M (bores clear by 1.6mm; outer walls merge)
      translate([pir_y,paw_pir,0]) difference(){ cylinder(d=pir_d+6,h=6); translate([0,0,-eps]) cylinder(d=pir_d+0.6,h=8);}
    }
    // sensor business-end clearance holes through the plate
    translate([0,paw_cam,-eps]) cylinder(d=cam_d+2,h=4);
    translate([0,paw_us,-eps]) cylinder(d=srm_d+1,h=4);                 // SR04M-2 clearance
    translate([pir_y,paw_pir,-eps]) cylinder(d=pir_d+1,h=4);
    for(b=plate_bosses()) thruhole(b[0],b[1]-paw_mid,m3,-eps,4);   // mount screws to shell bosses
    // FULL-DEPTH re-cuts, AFTER the union: bores cut only inside each
    // feature's own difference() get re-filled by overlapping solids (v4
    // shipped with the PIR bore and all four camera screw bores blocked).
    translate([0,paw_us,-1]) cylinder(d=srm_d+0.6,h=9);
    translate([pir_y,paw_pir,-1]) cylinder(d=pir_d+0.6,h=9);
    for(sx=[-1,1],sy=[-1,1]) translate([sx*cam_hx/2,paw_cam+sy*cam_hy/2,-1]) cylinder(d=1.6,h=9);
  }
}
// the plate at its mounted position (against the boss ends, rings facing the
// wall) - used by the fitchecks; v4 never fit-checked the plate at all
module plate_on(){ translate([0,0,deck_t]) translate([front_x-wall-10-2.6,0,paw_mid]) rotate([90,0,90]) sensor_plate(); }

// ============================== FIT CHECK ===================================
// intersection of the placed shell with everything inside it - must be EMPTY.
module fitcheck(){
  intersection(){
    translate([0,0,deck_t]) shell();
    union(){
      deck();
      translate([0,0,deck_t+post_h]) shelf();
      for(c=UPPER) translate([c[3],c[4],deck_t+post_h+shelf_t]) rr(c[1],c[2],3,12);   // shelf board envelopes
      translate([8,0,deck_t]) rr(pi_l,pi_w,4,30);                                     // Pi envelope
      translate([disp_x,0,deck_t]) dispenser();
      translate([exit_x,0,-1.5]) chute();
      for(sy=[-1,1]) translate([axle_x,sy*track/2,0]) rotate([90,0,0]) cylinder(d=wheel_d,h=wheel_t,center=true);  // wheels
      translate([aa_c[0],aa_c[1],deck_t]) rotate(-90) aa_cradle();                    // AA cradle
    }
  }
}
// pairwise part-vs-part interference at assembly positions - must also be EMPTY.
// (parts that legitimately TOUCH share a face -> zero-volume intersection.)
pairsel=-1;   // -1 = all pairs; 0..8 = single pair (for isolating a failure)
module fitcheck2(){
  if(pairsel<0||pairsel==0) ds(){ deck(); translate([0,0,deck_t+post_h]) shelf(); }
  if(pairsel<0||pairsel==1) ds(){ deck(); translate([disp_x,0,deck_t]) dispenser(); }
  if(pairsel<0||pairsel==2) ds(){ translate([0,0,deck_t+post_h]) shelf(); translate([disp_x,0,deck_t]) dispenser(); }
  if(pairsel<0||pairsel==3) ds(){ translate([disp_x,0,deck_t]) dispenser(); translate([disp_x,0,deck_t+flap_z]) flap(); }
  if(pairsel<0||pairsel==4) ds(){ deck(); translate([exit_x,0,-1.5]) chute(); }
  if(pairsel<0||pairsel==5) ds(){ translate([disp_x,0,deck_t]) dispenser(); translate([disp_x,0,deck_t+disp_top+hop_h]) lid(); }
  if(pairsel<0||pairsel==6) ds(){ deck(); translate([castor_x,0,-castor_spacer_h]) castor(); }
  if(pairsel<0||pairsel==7) ds(){ translate([exit_x,0,-1.5]) chute(); translate([castor_x,0,-castor_spacer_h]) castor(); }
  if(pairsel<0||pairsel==8) ds(){ translate([exit_x,0,-1.5]) chute(); translate([0,0,deck_t]) shell(); }
  if(pairsel<0||pairsel==9) ds(){ deck(); plate_on(); }                                  // plate clears the deck
  if(pairsel<0||pairsel==10) ds(){ translate([0,0,deck_t]) shell(); plate_on(); }        // plate clears wall + crown (boss-end contact only)
}
module ds(){ intersection(){ children(0); children(1); } }

// ============================== VIEWS =======================================
module wheel(x,y){ color([.15,.15,.15]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d,h=wheel_t,center=true); color([.8,.2,.1]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d*.55,h=wheel_t+1,center=true); }
show_shell=true;
module assembly(){
  color([.85,.85,.88]) deck();
  translate([disp_x,0,deck_t]) color([.55,.6,.65]) dispenser();
  translate([disp_x,0,deck_t+flap_z]) color([.85,.5,.2]) flap();                  // gate flap
  color([.1,.1,.1,.7]) translate([disp_x-6,0,deck_t+3]) cube([sg_l,sg_w,sg_h],center=false?false:true);   // servo ghost (inside)
  translate([exit_x,0,-1.5]) color([.6,.6,.65]) chute();                           // exit chute (flange top against deck underside)
  translate([castor_x,0,-castor_spacer_h]) color([.7,.7,.7]) castor();
  color([.1,.1,.1,.85]) translate([8,0,deck_t+eps]) rr(pi_l,pi_w,4,30);          // Pi ghost
  translate([0,0,deck_t+post_h]) color([.82,.84,.88]) shelf();                    // upper shelf
  for(c=UPPER) color([.2,.5,.8,.9]) translate([c[3],c[4],deck_t+post_h+shelf_t+eps]) rr(c[1],c[2],3,12);  // shelf board ghosts
  wheel(axle_x,track/2); wheel(axle_x,-track/2);
  if(show_shell){ color([.86,.88,.9,.45]) translate([0,0,deck_t]) shell(); translate([disp_x,0,deck_t+disp_top+hop_h-3]) color([.2,.2,.2]) lid(); }
}
// real-outline map of BOTH tiers side by side (lower left, upper right)
module tier_map(list,ox,plate_l,plate_w){
  translate([ox,0,0]){
    color([.9,.9,.92]) rr(plate_l, plate_w, fillet, 1);
    for(c=list) let(
      mt=c[5],
      osx = mt=="cradle"? pi_l+8 : mt=="stick"? c[1]+5 : c[1],
      osy = mt=="cradle"? pi_w+8 : mt=="stick"? c[2]+5 : c[2],
      col = mt=="cradle"?[.1,.1,.1,.85]:mt=="stick"?[.95,.8,.2,.85]:[.3,.7,.45,.85] ){
        color(col) translate([c[3],c[4],1]) rr(osx,osy,3,2);
        color([1,1,1]) translate([c[3],c[4],3.4]) linear_extrude(.6) text(c[0],size=8,halign="center",valign="center",font="Liberation Sans:style=Bold");
    }
    for(p=POSTS) color([.85,.4,.1]) translate([p[0],p[1],1]) cylinder(d=7,h=2.5);
  }
}
module layout(){
  tier_map(LOWER,-(deck_l/2+15),deck_l,deck_w);
  translate([-(deck_l/2+15),0,0]){       // lower extras
    color([.55,.75,.95,.95]) translate([disp_x,0,1]) cylinder(d=hous_d,h=2.5);            // dispenser housing
    color([.85,.5,.2]) translate([exit_x,0,1.1]) cylinder(d=gate_d+2,h=2.5);              // treat exit
    for(sx=[-1,1],sy=[-1,1]) color([.2,.2,.2]) translate([axle_x+sx*16,sy*(deck_w/2-10),1]) cylinder(d=5,h=2.5);
  }
  tier_map(UPPER,(deck_l/2+15),shelf_l,shelf_w);
  translate([(deck_l/2+15),0,0]) color([.95,.55,.55,.8]) translate([notch_x+10,0,1]) cylinder(r=notch_r,h=2.5);   // notch
}

// every printable part laid flat on one plate -> a single combined STL to split
module all_parts(){
  deck();
  translate([265,0,0]) shelf();
  translate([0,225,0]) shell();
  translate([255,210,0]) dispenser();
  translate([330,210,0]) flap();
  translate([380,210,0]) chute();
  translate([255,300,0]) lid();
  translate([330,300,0]) castor();
  translate([400,300,0]) sensor_plate();
}

// cross-section through the dispenser (y=0 plane) to verify throat/flap/gate/servo
module dispcut(){
  intersection(){
    union(){
      dispenser();
      color([.85,.5,.2]) translate([0,0,flap_z]) flap();                 // flap in CLOSED position
      color([.15,.15,.15]) translate([0,0,2+sg_h/2]) cube([sg_l,sg_w,sg_h],center=true); // SG90 ghost
      color([.4,.8,.4]) translate([flap_r,0,flap_z+flap_th+treat_d/2+0.5]) sphere(d=treat_d); // a treat on the flap
    }
    translate([-100,0,-50]) cube([200,100,200]);                         // keep +y half -> reveals the x-z section
  }
}
if(part=="deck") translate([0,0,skirt_h-deck_t]) deck();       // skirt rim on the bed (z=0)
else if(part=="shelf") shelf();
else if(part=="shell") shell();
// shell prints UPSIDE DOWN: the "45deg self-supporting crown" is only the OUTER
// skin - the inner cavity ceiling is a flat ~165x121 slab at z~90 (an impossible
// bridge upright). Crown-down, the flat crown is the first layer, the 45deg
// taper becomes a printable 45deg flare, and the open bottom faces up.
else if(part=="shell_print") translate([0,0,sh_th]) rotate([180,0,0]) shell();
else if(part=="dispenser") dispenser();
else if(part=="flap") flap();
else if(part=="dispcut") dispcut();
else if(part=="chute") chute();
else if(part=="chute_print") chute_wedge();   // flat bottom on the bed, channel opening up -> ~no support
else if(part=="lid") lid();
// lid prints UPSIDE DOWN: as modeled the taper plug is down and the d60 disc
// rim appears at z=5 as an 8mm floating ring (same failure as the LYRA top).
// Disc-face down, the plug tapers inward going up - fully self-supporting.
else if(part=="lid_print") translate([0,0,3]) rotate([180,0,0]) lid();
else if(part=="castor") castor();
else if(part=="sensor_plate") sensor_plate();
else if(part=="fitcheck") fitcheck();
else if(part=="fitcheck2") fitcheck2();
else if(part=="layout") layout();
else if(part=="all") all_parts();
else assembly();
