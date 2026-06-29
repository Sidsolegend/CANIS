// ============================================================================
//  CANIS — TWO-TIER chassis, enclosure & mounts  (OpenSCAD)  — clean rebuild
//  Lower deck: Pi 5, L298N, buck, motors, castor, dispenser.
//  Upper shelf (on 4 standoffs): Arduino, breadboard, ESP32.
//  Shell: clean dome + paw. Sensors on a SEPARATE plate. No ribs-through-holes,
//  no offset dispenser, no intersecting internal mounts. Bambu P1S / PLA.
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
// 4 standoffs — verified clear on BOTH tiers:
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

// ---- dispenser / sensors / drive ----
throat_d=15; hop_top=56; hop_h=70; hop_wall=2;
disp_x = -72;                                // throat on the deck (rear)
sg_l=23; sg_w=12.5; sg_h=23; sg_tab=32; sg_screw=2.4;
us_eye_d=16.2; us_pitch=26; pir_d=23.6; cam_d=9; cam_hx=21; cam_hy=12.5; cam_s=2.2;
wheel_d=68; wheel_t=26; axle_x=22; track=deck_w+12;
castor_x=-deck_l/2+18; castor_bc=24; castor_hole=4.3; castor_spacer_h=9;
m3=3.3; m3p=2.8;

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
      translate([0,0,deck_t]) standoffs(POSTS,post_h);           // shelf standoffs
    }
    // through holes (full depth, clear of skirt/cradle)
    thruhole(disp_x,0,throat_d, -(skirt_h+1), deck_t+1);                       // throat
    for(p=POSTS) thruhole(p[0],p[1],m3p,-(skirt_h+1),post_h+1);                // shelf screws up into posts
    for(a=[45:90:359]) thruhole(castor_x+castor_bc/2*cos(a),castor_bc/2*sin(a),castor_hole,-(skirt_h+1),deck_t+1);
    for(sx=[-1,1],sy=[-1,1]) thruhole(axle_x+sx*16,sy*(deck_w/2-10),m3,-(skirt_h+1),deck_t+1);  // motor brackets
    for(p=disp_pins()) thruhole(disp_x+p[0],p[1],m3,-(skirt_h+1),deck_t+1);                      // funnel feet
    for(p=sb_pins())   thruhole(p[0],p[1],m3,-(skirt_h+1),deck_t+1);                              // servo bracket feet
    for(c=[[55,0],[-40,0]]) translate([c[0],c[1],-(skirt_h+1)]) hull(){ thruhole(0,-10,7,0,skirt_h+deck_t+2); thruhole(0,10,7,0,skirt_h+deck_t+2);} // cable
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
    // rear notch so the dispenser hopper passes through
    hull(){ translate([notch_x-1,0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps); translate([notch_x-40,0,-eps]) cylinder(r=notch_r,h=shelf_t+2*eps); }
    for(p=POSTS) thruhole(p[0],p[1],m3,-eps,shelf_t+eps);        // screw to standoffs
    for(c=[[-5,0],[-5,30],[-5,-30]]) thruhole(c[0],c[1],8,-eps,shelf_t+eps);  // wire pass-throughs (tidy column)
  }
}

// ============================== DISPENSER (clean funnel ONLY) ===============
sb_y = 34;   // servo bracket sits beside the funnel (its own part, enclosed)
function disp_pins()=[for(a=[90:120:359])[(throat_d/2+6)*cos(a),(throat_d/2+6)*sin(a)]];  // 3 feet
module dispenser(){
  difference(){
    union(){
      cylinder(d=throat_d+24,h=4);                      // round base
      translate([0,0,4]) difference(){                  // funnel
        hull(){ rr(throat_d+2*hop_wall,throat_d+2*hop_wall,3,eps); translate([0,0,hop_h]) rr(hop_top,hop_top,6,eps); }
        translate([0,0,-eps]) hull(){ rr(throat_d,throat_d,2,eps); translate([0,0,hop_h+2*eps]) rr(hop_top-2*hop_wall,hop_top-2*hop_wall,4,eps);}
      }
    }
    translate([0,0,-eps]) cylinder(d=throat_d,h=6);     // clean throat
    for(p=disp_pins()) thruhole(p[0],p[1],m3,-eps,4+2*eps);
  }
}
// servo holder — SEPARATE piece, screws to the deck beside the funnel
module servo_bracket(){
  difference(){
    union(){
      rr(sg_l+14,sg_w+10,3,4);
      translate([0,0,4]) difference(){
        rr(sg_l+6,sg_w+6,2,sg_h+8);
        translate([0,0,6]) rr(sg_l,sg_w,1,sg_h+4);
        for(s=[-1,1]) translate([s*sg_tab/2,0,sg_h+6]) cylinder(d=sg_screw,h=10);
      }
    }
    for(sx=[-1,1]) translate([sx*(sg_l/2+4.5),0,-eps]) cylinder(d=m3,h=6);
  }
}
function sb_pins()=[[disp_x-(sg_l/2+4.5),sb_y],[disp_x+(sg_l/2+4.5),sb_y]];
module gate(){ difference(){ hull(){ cylinder(d=10,h=3); translate([sb_y,0,0]) cylinder(d=throat_d+6,h=3);} cylinder(d=5.6,h=10,$fn=6); translate([0,0,2]) cylinder(d=8,h=4);} }
module lid(){ difference(){ union(){ cylinder(d=hop_top+6,h=3); translate([0,0,-5]) cylinder(d=hop_top-2*hop_wall-0.6,h=5.5);} translate([0,0,3]) scale([1,1,.4]) sphere(d=hop_top*.5);} }

// ============================== CASTOR SPACER ===============================
module castor(){ difference(){ rr(44,44,6,castor_spacer_h); for(a=[45:90:359]) thruhole(castor_bc/2*cos(a),castor_bc/2*sin(a),castor_hole,-eps,castor_spacer_h+eps); translate([0,0,1.6]) cylinder(d=22,h=castor_spacer_h); thruhole(0,0,11,-eps,castor_spacer_h+eps);} }

// ============================== SHELL =======================================
shell_clr=1.5; wall=2.4; skirt_drop=16; body_h=74; head_h=108;
head_cx=deck_l/2-42; head_fx=head_cx+34; face_cz=head_h*0.46;
paw_cam=28; paw_us=4; paw_pir=-23;
module shell_body(g=0,t=0){ hull(){
  translate([0,0,-skirt_drop]) rr(deck_l+2*shell_clr+g,deck_w+2*shell_clr+g,fillet+2,eps);
  translate([-10,0,body_h+t]) rr(deck_l-24+g,deck_w-20+g,fillet+8,eps);
  translate([head_cx,0,head_h+t]) rr(70+g,deck_w-34+g,26,eps); } }
module paw_holes(){ translate([head_fx,0,face_cz]) rotate([0,-14,0]){
  translate([-1.5,0,(paw_cam+paw_pir)/2]) hull() for(sy=[-1,1],sz=[-1,1]) translate([0,sy*24,sz*((paw_cam-paw_pir)/2+8)]) rotate([0,90,0]) cylinder(r=7,h=3,center=true); // bezel
  translate([0,0,paw_cam]) rotate([0,90,0]) cylinder(d=cam_d+1,h=30,center=true);
  for(sx=[-1,1],sy=[-1,1]) translate([0,sx*cam_hx/2,paw_cam+sy*cam_hy/2]) rotate([0,90,0]) cylinder(d=cam_s,h=30,center=true);
  for(s=[-1,1]) translate([0,s*us_pitch/2,paw_us]) rotate([0,90,0]) cylinder(d=us_eye_d,h=30,center=true);
  translate([0,0,paw_pir]) rotate([0,90,0]) cylinder(d=pir_d,h=30,center=true); } }
// 2 bosses for the separate sensor plate
function plate_bosses()=[[0,paw_cam+8],[0,paw_pir-8]];
module shell(){
  difference(){
    union(){
      difference(){ shell_body(0,0); translate([0,0,-2]) shell_body(-2*wall,-wall); }
      translate([head_fx,0,face_cz]) rotate([0,-14,0]) for(b=plate_bosses())
        translate([-wall-3,b[0],b[1]]) rotate([0,90,0]) difference(){ cylinder(d=6,h=7,center=true); cylinder(d=m3p,h=9,center=true);}
    }
    translate([0,0,-skirt_drop-2]) shell_body(-2*wall+0.4,-wall);                  // open bottom
    paw_holes();
    for(s=[-1,1]) translate([axle_x,s*(deck_w/2+shell_clr),0]) rotate([90,0,0]) cylinder(d=wheel_d+10,h=2*wall+6,center=true); // wheel arches
    translate([disp_x,0,body_h]) cylinder(d=hop_top+8,h=head_h,center=true);       // hopper opening (funnel only)
    for(i=[-2:2]) translate([i*8,40,body_h-3]) cube([3.5,40,20],center=true);       // fan grille
    // CANIS — deep, clearly-readable deboss on the flat rear wall
    translate([-deck_l/2-shell_clr-2,0,body_h*.42]) rotate([0,5,0]) rotate([90,0,90]) mirror([1,0,0]) linear_extrude(5) text("CANIS",size=15,halign="center",valign="center",font="Liberation Sans:style=Bold");
  }
}

// ============================== SENSOR PLATE (modular) ======================
// Flat plate: mounts the 3 sensors in the paw layout, screws to the 2 shell bosses.
module sensor_plate(){
  difference(){
    union(){
      rr(60,(paw_cam-paw_pir)+34,4,2.6);
      // camera standoffs (top) — solid; far from the PIR
      for(sx=[-1,1],sy=[-1,1]) translate([sx*cam_hx/2,paw_cam+sy*cam_hy/2,0]) difference(){ cylinder(d=5,h=6); translate([0,0,-eps]) cylinder(d=1.6,h=8);}
      // ultrasonic: 2 short locating nubs at the board's L/R edges (no full frame)
      for(s=[-1,1]) translate([s*(us_pitch/2+us_eye_d/2+2),paw_us,0]) cylinder(d=4,h=5);
      // PIR ring (bottom) — solid; far from the camera/ultrasonic
      translate([0,paw_pir,0]) difference(){ cylinder(d=pir_d+6,h=6); translate([0,0,-eps]) cylinder(d=pir_d+0.6,h=8);}
    }
    // sensor business-end clearance holes through the plate
    translate([0,paw_cam,-eps]) cylinder(d=cam_d+2,h=4);
    for(s=[-1,1]) translate([0,paw_us+0,-eps]) translate([s*us_pitch/2,0,0]) cylinder(d=us_eye_d+1,h=4);
    translate([0,paw_pir,-eps]) cylinder(d=pir_d+1,h=4);
    for(b=plate_bosses()) thruhole(b[0],b[1],m3,-eps,4);   // mount screws to shell bosses
  }
}

// ============================== VIEWS =======================================
module wheel(x,y){ color([.15,.15,.15]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d,h=wheel_t,center=true); color([.8,.2,.1]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d*.55,h=wheel_t+1,center=true); }
show_shell=true;
module assembly(){
  color([.85,.85,.88]) deck();
  translate([disp_x,0,deck_t]) color([.55,.6,.65]) dispenser();
  translate([disp_x,sb_y,deck_t]) color([.5,.55,.6]) servo_bracket();
  translate([castor_x,0,-castor_spacer_h]) color([.7,.7,.7]) castor();
  color([.1,.1,.1,.85]) translate([8,0,deck_t+eps]) rr(pi_l,pi_w,4,30);          // Pi ghost
  translate([0,0,deck_t+post_h]) color([.82,.84,.88]) shelf();                    // upper shelf
  for(c=UPPER) color([.2,.5,.8,.9]) translate([c[3],c[4],deck_t+post_h+shelf_t+eps]) rr(c[1],c[2],3,12);  // shelf board ghosts
  wheel(axle_x,track/2); wheel(axle_x,-track/2);
  if(show_shell){ color([.86,.88,.9,.45]) translate([0,0,deck_t]) shell(); translate([disp_x,0,deck_t+body_h-2]) color([.2,.2,.2]) lid(); }
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
    color([.55,.75,.95,.95]) translate([disp_x,0,1]) cylinder(d=throat_d+24,h=2.5);       // funnel base
    color([.6,.6,.65,.95]) translate([disp_x,sb_y,1]) rr(sg_l+14,sg_w+10,3,2.5);          // servo bracket
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
  translate([320,210,0]) servo_bracket();
  translate([380,210,0]) gate();
  translate([255,290,0]) lid();
  translate([330,290,0]) castor();
  translate([400,290,0]) sensor_plate();
}

if(part=="deck") deck();
else if(part=="shelf") shelf();
else if(part=="shell") shell();
else if(part=="dispenser") dispenser();
else if(part=="servo_bracket") servo_bracket();
else if(part=="gate") gate();
else if(part=="lid") lid();
else if(part=="castor") castor();
else if(part=="sensor_plate") sensor_plate();
else if(part=="layout") layout();
else if(part=="all") all_parts();
else assembly();
