// ============================================================================
//  CANIS — final parametric chassis, enclosure & mounts  (OpenSCAD)
//  Hybrid build: 3D-printed deck + product shell + dispenser + castor mount,
//  reusing the existing metal motor brackets.
//  Printer: Bambu Lab P1S (256 bed) · Material: PLA.
//
//  Design language: clean / minimal (Apple-ish). No "face" — sensors sit behind
//  one discreet recessed front strip. Every project component has a real,
//  supported home on the deck (see COMPONENTS list + `layout` view).
//
//  RENDER:  set `render_part`, F5 preview / F6 render.
//           render_part = "layout"  -> top-down map of where every board goes.
//  EXPORT:  openscad -D 'render_part="deck"' -o stl/deck.stl canis.scad   (etc.)
//  Parts:   deck dispenser gate castor_mount motor_bracket shell lid
//           plus views: assembly · layout
//
//  >>> Numbers tagged // CONFIRM are worth a second measurement. <<<
// ============================================================================

render_part = "assembly";
$fn = 48;

// ----------------------------------------------------------------------------
//  CORE DIMENSIONS
// ----------------------------------------------------------------------------
deck_l       = 248;   // front<->back  (sized so every pad/cradle/tower clears)
deck_w       = 190;   // left<->right
deck_t       = 3.4;   // plate thickness (load-bearing; ribbed underneath)
deck_fillet  = 12;
skirt_h      = 9;     // perimeter rib depth (stiffens the plate)
skirt_t      = 2.6;
// +X = FRONT. Origin = deck centre.

// --- Raspberry Pi 5 in CanaKit case -----------------------------------------
pi_case_l    = 92;    // measured (long axis along X)
pi_case_w    = 63;    // measured
pi_x         = 0;     // centred; rear is clear for the dispenser
pi_y         = 0;
cradle_wall  = 3;
cradle_h     = 12;
case_clr     = 0.8;

// --- SG90 servo (dispenser gate) --------------------------------------------
sg_body_l    = 23.0; sg_body_w = 12.5; sg_body_h = 23.0;
sg_tab_span  = 32.0; sg_screw_d = 2.4; sg_horn_clr = 6;

// --- Treat / kibble ---------------------------------------------------------
treat_d      = 8;     // measured (8mm round kibble)
throat_d     = treat_d + 7;

// --- Dispenser hopper -------------------------------------------------------
hop_top      = 56; hop_h = 70; hop_wall = 2;
disp_base_l  = 62; disp_base_w = 58; disp_base_t = 4;
disp_base_off= -15;   // base shifted toward the servo tower (rear of the throat)

// --- Front sensors (mounted behind the PAW) ---------------------------------
// Apertures (holes in the shell front):
us_eye_d     = 16.2;  // HC-SR04 transducer cans
us_eye_pitch = 26;    // their fixed centre-to-centre (= the two "beans")
pir_dome_d   = 23.6;  // HC-SR501 lens
cam_lens_d   = 9;     // camera aperture          // CONFIRM camera (Pi cam / USB)
cam_hole_x   = 21; cam_hole_y = 12.5; cam_screw_d = 2.2;
// Real PCB outlines (drive the internal mounts + clearance checks):
us_pcb_w  = 45; us_pcb_h  = 20;   // HC-SR04 board
pir_pcb_w = 33; pir_pcb_h = 25;   // HC-SR501 board (incl. dome housing)
cam_pcb_w = 25; cam_pcb_h = 24;   // camera module   // CONFIRM once camera chosen
// Paw layout (face-local mm): camera top, 2 ultrasonic beans, PIR main pad.
// Spaced so the three PCBs DON'T collide behind the face.
paw_cam_z = 28; paw_us_z = 4; paw_pir_z = -23;

// --- Rear castor ------------------------------------------------------------
// The castor (wheel + holder) is a bought 25 mm-tall unit. The deck underside
// sits ~one wheel-radius (34 mm) off the floor, so we print a short SPACER to
// make up the difference and keep the deck level.
castor_bc       = 24;  castor_hole = 4.3;     // CONFIRM your castor's bolt pattern
castor_assy_h   = 25;  // measured: full castor wheel + holder height
castor_spacer_h = 9;   // = ride height (34) - castor (25). CONFIRM after assembly.

// --- Drive (assembly preview + bracket) -------------------------------------
wheel_d      = 68; wheel_t = 26;            // measured
track        = deck_w + 10;
motor_body_w = 39; motor_body_l = 40;       // measured  // CONFIRM bracket
axle_x       = 18;  // drive axle forward of centre (CoG between wheels & castor)

// --- Fasteners / helpers ----------------------------------------------------
m3_clr = 3.3; m3_pilot = 2.8; zip_w = 4; zip_g = 2.2;

// ----------------------------------------------------------------------------
//  COMPONENT LAYOUT  — single source of truth for what sits where.
//  [ name, size_x, size_y, centre_x, centre_y, mount ]
//  mount: "cradle" Pi · "board" pad+corner bosses+zip · "stick" pad+lip (taped)
//  Verified collision-free on the 248x190 deck — the `layout` view draws the
//  REAL pad/cradle/dispenser outlines (not bare footprints) so overlaps show.
// ----------------------------------------------------------------------------
COMPONENTS = [
    ["Pi 5",        pi_case_l, pi_case_w, pi_x,  pi_y, "cradle"],  // centre
    ["Arduino Uno", 54, 69,    84,  44,  "board"],   // front, +y
    ["Breadboard",  55, 84,    84, -44,  "stick"],   // front, -y
    ["L298N",       44, 44,   -82,  62,  "board"],   // rear-left
    ["ESP32",       56, 29,   -82, -62,  "board"],   // rear-right
    ["LM2596 buck", 44, 22,   -25, -66,  "board"],   // -y lane
];
// Loose bits that just need wiring room (shown on the map, no rigid mount):
//   2x buzzer · button · 1k resistor · capacitors  -> live on the breadboard.

// ----------------------------------------------------------------------------
//  HELPERS
// ----------------------------------------------------------------------------
module rrect(l,w,r,h){ linear_extrude(h) offset(r) square([max(.01,l-2*r),max(.01,w-2*r)],center=true); }
// cut full depth (through plate AND any rib below) so nothing blocks a hole/screw
module bolt(x,y,d,t){ translate([x,y,-(skirt_h+2)]) cylinder(d=d,h=skirt_h+t+4); }

// shared hole patterns (deck <-> bolt-on parts) -----------------------------
castor_x = -deck_l/2 + 20;
function castor_holes()=[for(a=[45:90:359])[castor_x+castor_bc/2*cos(a),castor_bc/2*sin(a)]];
disp_x   = -deck_l/2 + 46;   // throat position (base + tower fit behind it, clear of Pi)
function disp_holes_local()=[[14,0],[gate_axis_x,9],[gate_axis_x,-9]];  // tripod: funnel + tower lobes
function motor_holes()=[for(sx=[-1,1],sy=[-1,1])[axle_x+sx*16,sy*(deck_w/2-10)]];
// shell screw bosses in verified-clear perimeter gaps (front gap, 2 rear, 2 sides)
function shell_holes()=[[116,6],[-118,60],[-118,-60],[0,90],[0,-90]];

// board mount = 4 open standoffs (board bolts on / tapes; deck stays open under it)
pad_h = 5;
module board_pad(sx,sy){
    for(ix=[-1,1],iy=[-1,1])
        translate([ix*(sx/2-4),iy*(sy/2-4),0]) difference(){
            cylinder(d=6.5,h=pad_h);
            translate([0,0,-1]) cylinder(d=m3_pilot,h=pad_h+2);
        }
}
// breadboard locator = a thin perimeter lip only (open centre; board adheres to deck)
module stick_pad(sx,sy){
    difference(){
        rrect(sx+5,sy+5,4,2.6);
        translate([0,0,-1]) rrect(sx+0.8,sy+0.8,2,5);          // hollow -> open centre
    }
}
module place_mounts(){
    for(c=COMPONENTS){
        nm=c[0]; sx=c[1]; sy=c[2]; cx=c[3]; cy=c[4]; mt=c[5];
        if(mt=="board") translate([cx,cy,deck_t]) board_pad(sx,sy);
        if(mt=="stick") translate([cx,cy,deck_t]) stick_pad(sx,sy);
        // "cradle" handled in deck() directly
    }
}

// ----------------------------------------------------------------------------
//  PART: DECK
// ----------------------------------------------------------------------------
module deck(){
    difference(){
        union(){
            rrect(deck_l,deck_w,deck_fillet,deck_t);                       // plate
            translate([0,0,-(skirt_h-deck_t)])                            // skirt
                difference(){
                    rrect(deck_l,deck_w,deck_fillet,skirt_h);
                    translate([0,0,-1]) rrect(deck_l-2*skirt_t,deck_w-2*skirt_t,max(.5,deck_fillet-skirt_t),skirt_h+2);
                }
            for(rx=[-100,-45,15,85])                                      // transverse ribs (clear of throat)
                translate([rx,0,-(skirt_h-deck_t)]) cube([2.6,deck_w-2*skirt_t,skirt_h],center=true);
            // Pi case cradle (drop-in fence, long axis along X)
            translate([pi_x,pi_y,deck_t]) difference(){
                rrect(pi_case_l+2*cradle_wall+2*case_clr,pi_case_w+2*cradle_wall+2*case_clr,5,cradle_h);
                translate([0,0,-1]) rrect(pi_case_l+2*case_clr,pi_case_w+2*case_clr,3,cradle_h+2);
                for(sx=[-1,1]) translate([sx*(pi_case_l/2),0,cradle_h/2]) cube([cradle_wall*4,30,cradle_h+2],center=true);
            }
            place_mounts();                                               // every board's pad
        }
        // --- subtractions ---
        for(p=castor_holes()) bolt(p[0],p[1],castor_hole,deck_t);
        for(p=disp_holes_local()) bolt(disp_x+p[0],p[1],m3_clr,deck_t);
        for(p=motor_holes())  bolt(p[0],p[1],m3_clr,deck_t);
        for(p=shell_holes())  bolt(p[0],p[1],m3_clr,deck_t);              // shell screws from below
        translate([disp_x,0,-(skirt_h+2)]) cylinder(d=throat_d+4,h=skirt_h+deck_t+4);   // treats fall clean through
        // cable pass-throughs in the OPEN gaps fore & aft of the Pi (clear of the cradle)
        for(c=[[54,0],[-55,0]]) translate([c[0],c[1],-(skirt_h+2)]) hull(){
            translate([0,-10,0]) cylinder(d=7,h=skirt_h+deck_t+4); translate([0,10,0]) cylinder(d=7,h=skirt_h+deck_t+4); }
    }
}

// ----------------------------------------------------------------------------
//  PART: DISPENSER  (hopper + servo gate)
// ----------------------------------------------------------------------------
gate_axis_x = -(throat_d/2 + 18);   // servo tower BEHIND the throat (clears funnel + throat)
module dispenser(){
    difference(){
        union(){
            // base: ONE clean peanut covering the funnel lobe + the tower lobe
            linear_extrude(disp_base_t) hull(){
                circle(d=throat_d+22);
                translate([gate_axis_x,0]) offset(5) square([sg_body_l-2,sg_body_w-2],center=true);
            }
            translate([0,0,disp_base_t]) difference(){    // funnel hopper over the throat
                hull(){ rrect(throat_d+2*hop_wall,throat_d+2*hop_wall,3,1);
                        translate([0,0,hop_h-1]) rrect(hop_top,hop_top,6,1); }
                translate([0,0,-1]) hull(){ rrect(throat_d,throat_d,2,1);
                        translate([0,0,hop_h+1]) rrect(hop_top-2*hop_wall,hop_top-2*hop_wall,4,1); }
            }
            translate([gate_axis_x,0,disp_base_t]) difference(){       // servo tower
                rrect(sg_body_l+6,sg_body_w+6,2,sg_horn_clr+sg_body_h);
                translate([0,0,sg_horn_clr]) rrect(sg_body_l,sg_body_w,1,sg_body_h+2);
                for(s=[-1,1]) translate([s*sg_tab_span/2,0,sg_horn_clr+sg_body_h-2]) cylinder(d=sg_screw_d,h=8);
            }
        }
        translate([0,0,-1]) cylinder(d=throat_d,h=disp_base_t+2);
        for(p=disp_holes_local()) bolt(p[0],p[1],m3_clr,disp_base_t);
    }
}
module gate(){
    difference(){
        hull(){ cylinder(d=10,h=3); translate([-gate_axis_x,0,0]) cylinder(d=throat_d+6,h=3); }
        cylinder(d=5.6,h=10,$fn=6); translate([0,0,2]) cylinder(d=8,h=4);
    }
}

// ----------------------------------------------------------------------------
//  PART: CASTOR SPACER  (short leveller between deck and the 25mm castor)
//  Top face bolts up to the deck (castor_holes). Bottom face takes the castor.
// ----------------------------------------------------------------------------
module castor_mount(){
    difference(){
        rrect(44,44,6,castor_spacer_h);
        // 4 through-holes (deck side <-> castor side share the bolt circle)
        for(a=[45:90:359]) translate([castor_bc/2*cos(a),castor_bc/2*sin(a),-1])
            cylinder(d=castor_hole,h=castor_spacer_h+2);
        // central clearance for the castor's stem / to save plastic
        translate([0,0,1.6]) cylinder(d=22,h=castor_spacer_h);
        translate([0,0,-1]) cylinder(d=11,h=castor_spacer_h+2);
    }
}

// ----------------------------------------------------------------------------
//  PART: MOTOR BRACKET  (optional — only if you retire the metal ones)
// ----------------------------------------------------------------------------
module motor_bracket(){
    bw=24; bl=40;
    difference(){
        union(){ rrect(bl,bw,3,3); translate([bl/2-3,0,0]) rrect(3,bw,2,26); }
        for(sy=[-1,1]) bolt(0,sy*(bw/2-6),m3_clr,3);
        translate([bl/2-3,0,18]) rotate([0,90,0]){ cylinder(d=7,h=8,center=true);
            for(s=[-1,1]) translate([0,s*9,0]) cylinder(d=m3_clr,h=8,center=true); }
    }
}

// ----------------------------------------------------------------------------
//  PART: SHELL  (the product enclosure)  — z=0 is the deck top.
// ----------------------------------------------------------------------------
shell_clr=1.2; shell_wall=2.4; skirt_drop=16; body_h=46; head_h=94;
head_cx = deck_l/2 - 44;
head_front_x = head_cx + 36;
face_cz = head_h*0.46;              // the PAW: camera pad + 2 sensor "beans" + PIR

module shell_body(grow=0,top=0){
    hull(){
        translate([0,0,-skirt_drop]) rrect(deck_l+2*shell_clr+grow,deck_w+2*shell_clr+grow,deck_fillet+2,1);
        translate([-10,0,body_h+top]) rrect(deck_l-26+grow,deck_w-22+grow,deck_fillet+8,1);
        translate([head_cx,0,head_h+top]) rrect(70+grow,deck_w-34+grow,26,1);
    }
}
module front_face(){   // the PAW — recessed bezel + apertures sized to the real sensors
    translate([head_front_x,0,face_cz]) rotate([0,-14,0]){
        // recessed rounded bezel (the paw outline), tall enough for the whole cluster
        translate([-1.5,0,(paw_cam_z+paw_pir_z)/2]) hull() for(sy=[-1,1],sz=[-1,1])
            translate([0,sy*24,sz*((paw_cam_z-paw_pir_z)/2+8)]) rotate([0,90,0]) cylinder(r=7,h=3,center=true);
        // CAMERA — top
        translate([0,0,paw_cam_z]) rotate([0,90,0]) cylinder(d=cam_lens_d+1,h=30,center=true);
        for(sx=[-1,1],sy=[-1,1]) translate([0,sx*cam_hole_x/2,paw_cam_z+sy*cam_hole_y/2]) rotate([0,90,0]) cylinder(d=cam_screw_d,h=30,center=true);
        // TWO ULTRASONIC — the beans
        for(s=[-1,1]) translate([0,s*us_eye_pitch/2,paw_us_z]) rotate([0,90,0]) cylinder(d=us_eye_d,h=30,center=true);
        // PIR — main pad
        translate([0,0,paw_pir_z]) rotate([0,90,0]) cylinder(d=pir_dome_d,h=30,center=true);
    }
}
// internal mounts behind the paw — MINIMAL, far apart, can't touch:
//   camera = 4 standoffs up top · PIR = one ring at the bottom · ultrasonic = its two
//   cans push through the snug bean holes (held by the holes + a dab of glue; no rails).
module sensor_mounts(){
    translate([head_front_x,0,face_cz]) rotate([0,-14,0]){
        for(sy=[-1,1],sz=[-1,1])                                   // camera standoffs (top)
            translate([-shell_wall-3, sy*cam_hole_x/2, paw_cam_z+sz*cam_hole_y/2]) rotate([0,90,0])
                difference(){ cylinder(d=5,h=6,center=true); cylinder(d=1.6,h=8,center=true); }
        translate([-shell_wall-3,0,paw_pir_z]) rotate([0,90,0])      // PIR ring (bottom)
            difference(){ cylinder(d=pir_dome_d+5,h=6,center=true); cylinder(d=pir_dome_d+0.8,h=9,center=true); }
    }
}
// ghost PCBs — to PROVE the three boards fit behind the paw without colliding
module ghost_sensors(){
    translate([head_front_x,0,face_cz]) rotate([0,-14,0]){
        color([.2,.7,.3]) translate([-shell_wall-2,0,paw_us_z])  cube([1.6,us_pcb_w,us_pcb_h],center=true);
        color([.3,.5,.9]) translate([-shell_wall-2,0,paw_pir_z]) cube([1.6,pir_pcb_w,pir_pcb_h],center=true);
        color([.9,.3,.3]) translate([-shell_wall-2,0,paw_cam_z]) cube([1.6,cam_pcb_w,cam_pcb_h],center=true);
    }
}
module shell(){
    difference(){
        union(){
            difference(){ shell_body(0,0); translate([0,0,-2]) shell_body(-2*shell_wall,-shell_wall); }
            for(p=shell_holes()) translate([p[0],p[1],0]){
                cylinder(d=8.5,h=20);
                dx = deck_l/2+shell_clr-abs(p[0]);   // gap to nearest x-wall
                dy = deck_w/2+shell_clr-abs(p[1]);   // gap to nearest y-wall
                if(dx<dy) translate([sign(p[0])*(dx/2+1),0,10]) cube([dx+4,8.5,20],center=true);
                else      translate([0,sign(p[1])*(dy/2+1),10]) cube([8.5,dy+4,20],center=true);
            }
            sensor_mounts();                                                          // real sensor holders
        }
        for(p=shell_holes()) translate([p[0],p[1],-1]) cylinder(d=m3_pilot,h=20);
        translate([0,0,-skirt_drop-2]) shell_body(-2*shell_wall+0.4,-shell_wall);     // open bottom
        front_face();                                                                 // the paw (sensors)
        for(s=[-1,1]) translate([axle_x,s*(deck_w/2+shell_clr),0]) rotate([90,0,0]) cylinder(d=wheel_d+10,h=2*shell_wall+6,center=true); // wheel arches
        hull(){                                                                        // hopper opening (funnel + servo tower)
            translate([disp_x,0,body_h]) cylinder(d=hop_top+7,h=head_h,center=true);
            translate([disp_x+gate_axis_x,0,body_h]) cylinder(d=sg_body_l+14,h=head_h,center=true);
        }
        for(i=[-2:2]) translate([i*7,0,body_h-3]) cube([3.2,46,20],center=true);       // fan grille
        // CANIS brand — deboss into the rear wall. Tool starts OUTSIDE the wall and
        // tilts ~4deg to follow the tumblehome, so it engraves crisply & evenly.
        translate([-deck_l/2-shell_clr-1.2,0,body_h*0.40]) rotate([0,4,0])
            rotate([90,0,90]) mirror([1,0,0])
                linear_extrude(3.8) text("CANIS",size=13,halign="center",valign="center",
                                         font="Liberation Sans:style=Bold");
    }
}
module lid(){
    difference(){
        union(){ cylinder(d=hop_top+6,h=3); translate([0,0,-5]) cylinder(d=hop_top-2*hop_wall-0.6,h=5.5); }
        translate([0,0,3]) scale([1,1,0.4]) sphere(d=hop_top*0.5);
    }
}

// ----------------------------------------------------------------------------
//  VIEWS
// ----------------------------------------------------------------------------
module ghost_wheel(x,y){
    color([.15,.15,.15]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d,h=wheel_t,center=true);
    color([.85,.2,.1]) translate([x,y,0]) rotate([90,0,0]) cylinder(d=wheel_d*.55,h=wheel_t+1,center=true);
}
show_shell = true;
module assembly(){
    color([.92,.92,.94]) deck();
    translate([disp_x,0,deck_t]) color([.6,.6,.62]) dispenser();
    translate([castor_x,0,-castor_spacer_h]) color([.7,.7,.7]) castor_mount();
    color([.1,.1,.1,.85]) translate([pi_x,pi_y,deck_t+1]) rrect(pi_case_l,pi_case_w,4,30);
    ghost_wheel(axle_x,track/2); ghost_wheel(axle_x,-track/2);
    if(show_shell){
        color([.86,.87,.9,.5]) translate([0,0,deck_t]) shell();
        translate([disp_x,0,deck_t+body_h-2]) color([.2,.2,.2]) lid();
    }
}
// top-down map: every component as a labelled footprint — proves it all fits
// top-down map drawing the REAL outline of every mount, so overlaps actually show
module layout(){
    color([.9,.9,.92]) deck();
    // dispenser: real base + servo tower + funnel mouth
    color([.55,.75,.95,.9]) translate([disp_x,0,deck_t+0.2]){
        linear_extrude(1) hull(){ circle(d=throat_d+22);                      // real peanut base
            translate([gate_axis_x,0]) offset(5) square([sg_body_l-2,sg_body_w-2],center=true); }
        translate([gate_axis_x,0,1.1]) rrect(sg_body_l+6,sg_body_w+6,2,1.2);  // servo tower
        translate([0,0,1.1]) circle(d=throat_d+4);                            // throat
    }
    color([.8,.8,.8,.7]) translate([castor_x,0,deck_t+0.2]) circle(d=40);
    for(p=motor_holes()) color([.2,.2,.2]) translate([p[0],p[1],deck_t]) cylinder(d=5,h=1.2);
    for(p=shell_holes()) color([.85,.4,.1]) translate([p[0],p[1],deck_t]) cylinder(d=8.5,h=1.2);
    for(c=COMPONENTS){
        sx=c[1]; sy=c[2]; cx=c[3]; cy=c[4]; mt=c[5];
        // REAL outline: cradle = fence outer, stick = pad+lip, board = pad
        osx = mt=="cradle"? pi_case_l+2*cradle_wall+2*case_clr : mt=="stick"? sx+5 : sx;
        osy = mt=="cradle"? pi_case_w+2*cradle_wall+2*case_clr : mt=="stick"? sy+5 : sy;
        col = mt=="cradle"?[.1,.1,.1,.85]:mt=="stick"?[.95,.8,.2,.85]:[.3,.7,.45,.85];
        color(col) translate([cx,cy,deck_t+1]) rrect(osx,osy,3,3);
        color([1,1,1]) translate([cx,cy,deck_t+5]) linear_extrude(0.6)
            text(c[0],size=7,halign="center",valign="center",font="Liberation Sans:style=Bold");
    }
}

module sensors_check(){   // shell (translucent) + ghost PCBs in their mounts
    color([.7,.75,.82,.4]) shell();
    ghost_sensors();
}

if      (render_part=="deck")          deck();
else if (render_part=="sensors")       sensors_check();
else if (render_part=="dispenser")     dispenser();
else if (render_part=="gate")          gate();
else if (render_part=="castor_mount")  castor_mount();
else if (render_part=="motor_bracket") motor_bracket();
else if (render_part=="shell")         shell();
else if (render_part=="lid")           lid();
else if (render_part=="layout")        layout();
else                                   assembly();
