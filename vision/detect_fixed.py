#!/usr/bin/env python3
"""
CANIS - FIXED room camera detection (mode C, `fixed_room`)
==========================================================
Camera architecture C of canis_config.h "Camera architecture A/B/C"
(docs/MOBILE_UPGRADE_STATUS.md §2/§5b): a FIXED camera watches the room from a
known mount pose; the Pi is off-board and stationary. Per frame:

    YOLO   -> the DOG            (no marker on the dog - learned detector)
    ArUco  -> the ROBOT's marker (rigid, flat, rock-solid fiducial)
    geometry -> both positions on the FLOOR in the ROOM frame
             -> robot->dog DISTANCE and ROBOT-frame BEARING

Modes A/B (robot's-eye) live in detect_yolo.py; this file exists because C's
geometry is genuinely different (room-frame localization, not a camera-axis
bearing). Everything that is NOT geometry is REUSED from the proven modules:
JsonPoster [OPT-6a], camera sources + reconnect [OPT-6b], DetectionGrace
[OPT-12], FrozenFrameGuard [OPT-13], BlurSkipMonitor [C6], the YOLO detector,
and the side-mapping LAW.

ROOM-FRAME GEOMETRY MODEL (documented so every number is auditable):
  Room frame: origin on the FLOOR directly below the camera lens; +Z up;
  +Y = horizontal projection of the optical axis (into the room); +X right.
  The camera sits at (0, 0, CAM_HEIGHT_M), pitched DOWN by TILT_DEG, no roll.
    pixel (u, v) -> camera ray ((u-cx)/f, (v-cy)/f, 1),  f from HFOV
    rotate by the tilt -> room-frame ray -> intersect a horizontal plane:
      DOG   at the bbox BOTTOM-CENTER pixel, plane z = 0        (paws on floor)
      ROBOT at the marker corners,          plane z = MARKER_HEIGHT_M
  Robot heading: the marker is mounted FLAT (facing up) with its printed TOP
  edge (ArUco corner 0 -> corner 1) facing the robot's FRONT; the floor
  projection of (top-edge midpoint - bottom-edge midpoint) is the forward
  vector. MEASURE CAM_HEIGHT_M and TILT_DEG on the real mount - they multiply
  straight into every distance the CBF layer will trust.

WIRE CONTRACT (docs/INTERFACE_CONTRACT.md §3 - same fields, same LAW):
  POST /detect JSON, identical body shape to detect_yolo.py, so the ESP32,
  controller, and dashboard are camera-mode-agnostic:
    zone         "{zone}-{side}" when the robot pose is known, else "{zone}"
    dog_present  always true when we POST (silence = no dog)
    bearing_deg  ROBOT-frame bearing to the dog: 0.0 = dead ahead of the
                 robot, negative = to the robot's LEFT (same sign convention
                 as the contract). NOTE: §3 words bearing as "camera frame"
                 because A/B are robot's-eye where camera frame == robot
                 frame; for a fixed camera only the ROBOT frame preserves the
                 left/right semantics the ESP32's zoneToDir() depends on.
                 OMITTED (optional field) when the marker isn't localized.
    distance_m   ROOM-frame robot->dog range (floor-plane Euclidean) - a
                 measured triangle, not the monocular bbox heuristic; null
                 when robot or dog floor position is unavailable.
    confidence   YOLO confidence for the dog (node-side gate >= 0.60, LAW)
    node_id      default "fixedcam-<zone>"
    ts           epoch seconds at frame capture
  Side mapping (LAW): left if bearing_deg < -10.0, right if > +10.0, else
  center - applied to the ROBOT-frame bearing. POST only while a dog is
  reported; never post dog_present:false. Marker visible but no dog = silence.

Config precedence: CLI arg > env var > default (canis_config.h mirror named):
    --camera pi|usb|stream                 (fixed camera backend; "stream"
                                            reuses the ESP32-CAM MJPEG source)
    --stream-url / CANIS_ESP32CAM_URL      (only for --camera stream)
    --zone / CANIS_ZONE                    (default "kitchen")
    --model / CANIS_YOLO_MODEL             (same default as detect_yolo.py)
    --conf / CANIS_YOLO_CONF_MIN           (default 0.60 - contract gate)
    --hfov / CANIS_CAM_HFOV_DEG            (default 66.0 - CANIS_FIXED_CAM_HFOV_DEG)
    --cam-height / CANIS_FIXED_CAM_HEIGHT_M   (default 2.0)
    --tilt / CANIS_FIXED_CAM_TILT_DEG      (default 30.0, degrees DOWN)
    --marker-id / CANIS_ROBOT_MARKER_ID    (default 17 - CANIS_ROBOT_MARKER_ID;
                                            keep distinct from dog collar ids)
    --marker-height / CANIS_ROBOT_MARKER_HEIGHT_M  (default 0.18)
    --marker-grace / CANIS_MARKER_GRACE_S  (default 2.0 - hold last robot pose)
    --imgsz / CANIS_YOLO_IMGSZ             (default 320)
    --node-id / CANIS_NODE_ID              (default "fixedcam-<zone>")
    --url / CANIS_DETECT_URL               (default http://127.0.0.1:5000/detect)
    --token / CANIS_TOKEN
    --directional / --no-directional / CANIS_DIRECTIONAL=0|1   (default on)

Test the channel without camera/model:   python3 detect_fixed.py --test --side left
Logic check, zero heavy deps (stub detector + stub marker, no cv2/model/net):
    python3 detect_fixed.py --selftest
"""

import argparse
import math
import os
import time
from collections import namedtuple

from vision_logic import (DetectionGrace, FrozenFrameGuard,   # [OPT-12/13]
                          BlurSkipMonitor)                    # [C6]
# Reuse the mode-A/B engineering wholesale - detect_yolo has no heavy imports
# at module top (cv2/ultralytics are guarded), so this import is selftest-safe.
from detect_yolo import (JsonPoster, YoloDogDetector, Esp32CamStreamSource,
                         focal_px, side_from_bearing, zone_label, _env_bool,
                         _default_model_path, FRAME_SIZE, CAM_INDEX,
                         PROCESS_EVERY, POST_EVERY_S, READ_FAIL_MAX,
                         RECONNECT_MAX_BACKOFF, GRACE_S, FROZEN_MAX,
                         BLUR_WINDOW_S, BLUR_ALERT_RATIO, YOLO_CONF_MIN,
                         YOLO_IMGSZ, ESP32CAM_URL_DEFAULT)

# ---------------- Mode C tunables (canis_config.h mirrors; CLI wins) ----------------
HFOV_DEG_DEFAULT   = float(os.environ.get("CANIS_CAM_HFOV_DEG", "66.0"))      # CANIS_FIXED_CAM_HFOV_DEG
CAM_HEIGHT_M       = float(os.environ.get("CANIS_FIXED_CAM_HEIGHT_M", "2.0")) # CANIS_FIXED_CAM_HEIGHT_M
CAM_TILT_DEG       = float(os.environ.get("CANIS_FIXED_CAM_TILT_DEG", "30.0"))# CANIS_FIXED_CAM_TILT_DEG
ROBOT_MARKER_ID    = int(os.environ.get("CANIS_ROBOT_MARKER_ID", "17"))       # CANIS_ROBOT_MARKER_ID
MARKER_HEIGHT_M    = float(os.environ.get("CANIS_ROBOT_MARKER_HEIGHT_M", "0.18"))
# The marker sits on a RIGID robot, so brief ArUco dropouts (motion blur, a
# person walking through) are bridged by holding the last pose. Kept short:
# a MOVING robot makes a held pose stale - bearing/distance error grows with
# hold time, so this trades a little staleness for a lot fewer null-distance
# frames. DetectionGrace is reused as the holder (its "zone" is our pose).
MARKER_GRACE_S     = float(os.environ.get("CANIS_MARKER_GRACE_S", "2.0"))
# Room-frame sanity clamp: a floor intersection farther than this is a
# grazing-ray artifact (pixel near the horizon), not a measurement.
ROOM_DIST_MAX_M    = 20.0

Geom = namedtuple("Geom", "frame_w frame_h hfov_deg tilt_deg cam_h_m marker_h_m")


# ---------------- Pure room-frame geometry (unit-tested by --selftest) ----------------
def ray_dir_room(u, v, geom):
    """Room-frame direction of the ray through pixel (u, v). Camera frame is
    OpenCV (x right, y down, z forward); the camera pitches DOWN by tilt_deg."""
    f = focal_px(geom.frame_w, geom.hfov_deg)
    xc = (u - geom.frame_w / 2.0) / f
    yc = (v - geom.frame_h / 2.0) / f
    t = math.radians(geom.tilt_deg)
    return (xc,                                       # X: right
            math.cos(t) - math.sin(t) * yc,           # Y: into the room
            -math.sin(t) - math.cos(t) * yc)          # Z: up

def intersect_height(u, v, geom, target_h_m):
    """(X, Y) where the pixel ray crosses the horizontal plane z=target_h_m,
    or None when the ray is at/above the horizon (never reaches the plane)."""
    dx, dy, dz = ray_dir_room(u, v, geom)
    if dz >= -1e-9:                       # parallel to or away from the plane
        return None
    t = (target_h_m - geom.cam_h_m) / dz
    if t <= 0:
        return None
    x, y = dx * t, dy * t
    if math.hypot(x, y) > ROOM_DIST_MAX_M:            # grazing-ray artifact
        return None
    return (x, y)

def dog_floor_point(cx_px, bottom_px, geom):
    """Dog position on the floor from the bbox bottom-center (the paws)."""
    return intersect_height(cx_px, bottom_px, geom, 0.0)

def marker_pose_from_corners(corners_px, geom):
    """Robot pose from the 4 ArUco corner pixels (order: 0 top-left, 1 top-
    right, 2 bottom-right, 3 bottom-left of the PRINTED pattern; marker flat,
    top edge facing the robot's front). Returns ((X, Y), heading_deg) with
    heading measured in the room frame (atan2 convention, +Y = 90.0), or None."""
    pts = [intersect_height(u, v, geom, geom.marker_h_m) for (u, v) in corners_px]
    if any(p is None for p in pts):
        return None
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    front = ((pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0)
    back = ((pts[3][0] + pts[2][0]) / 2.0, (pts[3][1] + pts[2][1]) / 2.0)
    fx, fy = front[0] - back[0], front[1] - back[1]
    if math.hypot(fx, fy) < 1e-6:                      # degenerate projection
        return None
    return ((cx, cy), math.degrees(math.atan2(fy, fx)))

def wrap_deg(a):
    """Wrap an angle to [-180, 180)."""
    return (a + 180.0) % 360.0 - 180.0

def robot_frame_bearing_deg(robot_xy, heading_deg, dog_xy):
    """Bearing of the dog FROM the robot, in the robot's frame. Contract sign
    convention: 0.0 = dead ahead, NEGATIVE = to the robot's LEFT."""
    ang = math.degrees(math.atan2(dog_xy[1] - robot_xy[1],
                                  dog_xy[0] - robot_xy[0]))
    return -wrap_deg(ang - heading_deg)   # CCW(+) in room math = robot's left = negative

def room_distance_m(robot_xy, dog_xy):
    """Floor-plane robot->dog distance in the room frame."""
    return math.hypot(dog_xy[0] - robot_xy[0], dog_xy[1] - robot_xy[1])


# ---------------- Per-frame combine (pure; the live loop and selftest share it) ----------------
def frame_report(dog, robot_pose, geom, zone, directional):
    """dog = (cx_px, bottom_px, conf) for an already conf-gated dog, or None.
    robot_pose = ((X, Y), heading_deg) or None (marker not localized).
    Returns (zone_label, bearing_deg | None, distance_m | None), or None when
    there is no dog to report (contract: silence = no dog)."""
    if dog is None:
        return None
    cx_px, bottom_px, _conf = dog
    dog_xy = dog_floor_point(cx_px, bottom_px, geom)
    if robot_pose is None or dog_xy is None:
        # Dog seen but the robot->dog triangle can't be built: still a valid
        # contract POST - plain zone, bearing omitted, distance null. NEVER
        # fake a number (LAW), and never go silent about a visible dog.
        return zone, None, None
    robot_xy, heading = robot_pose
    b = robot_frame_bearing_deg(robot_xy, heading, dog_xy)
    d = room_distance_m(robot_xy, dog_xy)
    return zone_label(zone, side_from_bearing(b), directional), b, round(d, 2)


def build_payload(zone, bearing_deg, distance_m, confidence, node_id, ts):
    """Contract §3 body; bearing_deg is OPTIONAL and omitted when the robot
    pose is unknown (never fabricate a bearing). dog_present always true."""
    p = {
        "zone": zone,
        "dog_present": True,
        "distance_m": distance_m if distance_m is None else round(float(distance_m), 2),
        "confidence": round(float(confidence), 3),
        "node_id": node_id,
        "ts": round(float(ts), 3),
    }
    if bearing_deg is not None:
        p["bearing_deg"] = round(float(bearing_deg), 1)
    return p


# ---------------- Detectors ----------------
class YoloDogBottomDetector(YoloDogDetector):
    """Same guarded-import YOLO detector, but reports the bbox BOTTOM pixel
    (paws-on-floor contact point) instead of the bbox height - fixed-camera
    geometry ranges by floor intersection, not by apparent size.
    Returns (found, cx_px, bottom_px, confidence)."""

    def detect(self, frame_bgr):
        res = self.model(frame_bgr, imgsz=self.imgsz, conf=self.conf_min,
                         verbose=False)[0]
        best = None
        for b in res.boxes:
            if int(b.cls[0]) not in self._dog_cls:
                continue
            conf = float(b.conf[0])
            if conf < self.conf_min:
                continue
            if best is None or conf > best[3]:
                x1, _y1, x2, y2 = (float(v) for v in b.xyxy[0])
                best = (True, (x1 + x2) / 2.0, y2, conf)
        return best if best else (False, 0.0, 0.0, 0.0)


class StubFixedScene:
    """--selftest scene: scripted (dog, marker) observations, zero heavy deps.
    The dog walks image-left -> far-center -> image-right in front of a robot
    parked mid-frame FACING AWAY from the camera (so image-left is the
    robot's LEFT); then a sub-threshold detection, a marker dropout, and a
    dog dropout - exercising gate, null-distance fallback, and grace."""

    # Marker centered at (320, 300), 20 px square, top edge = far edge (v=290)
    # -> robot faces +Y (away from the camera), heading ~ 90 deg.
    MARKER = ((310.0, 290.0), (330.0, 290.0), (330.0, 310.0), (310.0, 310.0))

    def __init__(self):
        self._script = [
            # (dog_found, cx, bottom, conf, marker_found)
            (True,   60.0, 350.0, 0.91, True),    # image-left  -> robot LEFT
            (True,  320.0, 260.0, 0.88, True),    # ahead of the robot -> center
            (True,  580.0, 350.0, 0.86, True),    # image-right -> robot RIGHT
            (True,  320.0, 260.0, 0.40, True),    # below conf gate -> silence
            (True,  320.0, 260.0, 0.87, False),   # marker lost -> plain zone, null dist
            (False,   0.0,   0.0, 0.00, False),   # dog gone -> grace hold, then clear
        ]
        self._i = 0

    def observe(self):
        r = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return r


# ---------------- CLI ----------------
def parse_args():
    ap = argparse.ArgumentParser(
        description="CANIS fixed-room-camera detector (camera mode C: YOLO dog "
                    "+ ArUco robot marker -> room-frame distance/bearing)")
    ap.add_argument("--camera", choices=["pi", "usb", "stream"], default="pi",
                    help="fixed camera backend; 'stream' pulls an MJPEG URL "
                         "(an ESP32-CAM used as the FIXED camera)")
    ap.add_argument("--stream-url", default=ESP32CAM_URL_DEFAULT,
                    help="MJPEG URL for --camera stream (env CANIS_ESP32CAM_URL)")
    ap.add_argument("--zone", default=os.environ.get("CANIS_ZONE", "kitchen"))
    ap.add_argument("--model", default=_default_model_path(),
                    help="ultralytics model: NCNN export dir or .pt (env CANIS_YOLO_MODEL)")
    ap.add_argument("--conf", type=float, default=YOLO_CONF_MIN,
                    help="node-side confidence gate (env CANIS_YOLO_CONF_MIN, default 0.60)")
    ap.add_argument("--hfov", type=float, default=HFOV_DEG_DEFAULT,
                    help="fixed camera horizontal FOV, deg (env CANIS_CAM_HFOV_DEG)")
    ap.add_argument("--cam-height", type=float, default=CAM_HEIGHT_M,
                    help="lens height above the floor, m (env CANIS_FIXED_CAM_HEIGHT_M)")
    ap.add_argument("--tilt", type=float, default=CAM_TILT_DEG,
                    help="downward camera pitch from horizontal, deg "
                         "(env CANIS_FIXED_CAM_TILT_DEG)")
    ap.add_argument("--marker-id", type=int, default=ROBOT_MARKER_ID,
                    help="DICT_4X4_50 id of the ROBOT's marker (env "
                         "CANIS_ROBOT_MARKER_ID; keep distinct from dog collar ids)")
    ap.add_argument("--marker-height", type=float, default=MARKER_HEIGHT_M,
                    help="marker plane height above the floor, m "
                         "(env CANIS_ROBOT_MARKER_HEIGHT_M)")
    ap.add_argument("--marker-grace", type=float, default=MARKER_GRACE_S,
                    help="hold the last robot pose this long after marker loss, s "
                         "(env CANIS_MARKER_GRACE_S)")
    ap.add_argument("--imgsz", type=int, default=YOLO_IMGSZ,
                    help="inference size; MUST match the NCNN export (env CANIS_YOLO_IMGSZ)")
    ap.add_argument("--node-id", default=os.environ.get("CANIS_NODE_ID", ""),
                    help="reporter id for /detect (default fixedcam-<zone>)")
    ap.add_argument("--url", default=os.environ.get("CANIS_DETECT_URL",
                                                    "http://127.0.0.1:5000/detect"))
    ap.add_argument("--token", default=os.environ.get("CANIS_TOKEN", ""))
    dgroup = ap.add_mutually_exclusive_group()
    dgroup.add_argument("--directional", dest="directional", action="store_true")
    dgroup.add_argument("--no-directional", dest="directional", action="store_false")
    ap.set_defaults(directional=_env_bool("CANIS_DIRECTIONAL", True))
    ap.add_argument("--test", action="store_true",
                    help="send ONE fake full-contract detection and exit (no camera/model)")
    ap.add_argument("--side", choices=["left", "center", "right"], default="center",
                    help="side to fake in --test mode")
    ap.add_argument("--selftest", action="store_true",
                    help="run the room-frame geometry + pipeline on a scripted stub "
                         "scene - PASSes with NO cv2/model/camera/network")
    return ap.parse_args()


# ---------------- Self-test (no camera, no model, no cv2, no network) ----------------
def run_selftest():
    w, h = FRAME_SIZE
    geom = Geom(w, h, 66.0, 30.0, 2.0, 0.2)
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))
        if not cond:
            fails.append(name)

    print("[selftest] 1/3 room-frame geometry")
    p = intersect_height(w / 2.0, h / 2.0, geom, 0.0)
    expect_y = geom.cam_h_m / math.tan(math.radians(geom.tilt_deg))
    check("optical-axis floor hit at H/tan(tilt)",
          p is not None and abs(p[0]) < 1e-9 and abs(p[1] - expect_y) < 1e-6,
          f"{p} vs (0, {expect_y:.3f})")
    flat = Geom(w, h, 66.0, 0.0, 2.0, 0.2)          # horizontal camera
    check("pixel at the horizon -> None", intersect_height(w / 2.0, h / 2.0, flat, 0.0) is None)
    check("pixel above the horizon -> None", intersect_height(w / 2.0, 100.0, flat, 0.0) is None)
    check("wrap_deg", wrap_deg(190.0) == -170.0 and wrap_deg(-190.0) == 170.0
          and wrap_deg(0.0) == 0.0)
    # Robot at (0,2) facing +Y (heading 90): a dog at (-1,2) is on its LEFT.
    check("robot-frame bearing sign LAW (negative = left)",
          abs(robot_frame_bearing_deg((0.0, 2.0), 90.0, (-1.0, 2.0)) + 90.0) < 1e-9
          and abs(robot_frame_bearing_deg((0.0, 2.0), 90.0, (1.0, 2.0)) - 90.0) < 1e-9
          and abs(robot_frame_bearing_deg((0.0, 2.0), 90.0, (0.0, 3.0))) < 1e-9)
    check("room distance", abs(room_distance_m((0.0, 2.0), (3.0, 6.0)) - 5.0) < 1e-9)
    pose = marker_pose_from_corners(StubFixedScene.MARKER, geom)
    check("marker pose recovered (centered, facing away -> heading ~90)",
          pose is not None and abs(pose[0][0]) < 1e-6 and pose[0][1] > 0
          and abs(pose[1] - 90.0) < 1.0,
          f"{pose}")

    print("[selftest] 2/3 stub scene pipeline (YOLO+marker -> gate -> geometry -> grace -> payload)")
    scene = StubFixedScene()
    grace = DetectionGrace(GRACE_S)
    marker_hold = DetectionGrace(0.0)     # no hold in the stub: dropout is immediate
    posted, now = [], 1000.0
    for _ in range(7):
        dog_found, cx, bottom, conf, marker_found = scene.observe()
        pose = marker_hold.update(
            marker_found,
            marker_pose_from_corners(StubFixedScene.MARKER, geom) if marker_found else None,
            now)
        gated = dog_found and conf >= YOLO_CONF_MIN            # node-side gate (LAW)
        report = frame_report((cx, bottom, conf), pose, geom,
                              "kitchen", True) if gated else None
        z = grace.update(gated, report[0] if report else None, now)
        if z is not None and report is not None:
            posted.append(build_payload(z, report[1], report[2], conf,
                                        "fixedcam-selftest", now))
        elif z is not None:
            posted.append({"held": z})
        now += 0.5
    zones = [p["zone"] for p in posted if "zone" in p]
    check("scripted walk labeled in the ROBOT frame (left/center/right)",
          zones[:3] == ["kitchen-left", "kitchen-center", "kitchen-right"], f"{zones}")
    check("sub-threshold detection (conf .40) NOT posted",
          not any(p.get("confidence") == 0.4 for p in posted))
    with_dist = [p for p in posted if p.get("distance_m") is not None]
    check("room-frame distances computed and sane (0.3-20 m)",
          len(with_dist) == 3 and all(0.3 < p["distance_m"] < ROOM_DIST_MAX_M
                                      for p in with_dist),
          f"{[p.get('distance_m') for p in posted if 'zone' in p]}")
    lost = [p for p in posted if "zone" in p and p["zone"] == "kitchen"]
    check("marker lost -> plain zone, distance null, bearing OMITTED",
          len(lost) == 1 and lost[0]["distance_m"] is None
          and "bearing_deg" not in lost[0])
    check("grace held the last zone after dog loss",
          any(p.get("held") == "kitchen" for p in posted))
    check("grace cleared past window (silence = no dog)",
          grace.update(False, None, now + GRACE_S + 1) is None)
    p0 = next(p for p in posted if "zone" in p)
    check("payload carries the contract fields",
          all(k in p0 for k in ("zone", "dog_present", "bearing_deg", "distance_m",
                                "confidence", "node_id", "ts"))
          and p0["dog_present"] is True)

    print("[selftest] 3/3 environment (informational, does not gate PASS)")
    try:
        import cv2  # noqa: F401
        has_aruco = hasattr(cv2, "aruco")
        print(f"  [ok] cv2 importable (aruco module: {'yes' if has_aruco else 'MISSING'})")
    except ImportError:
        print("  [--] cv2 NOT installed - fine for selftest; needed to run live")
    try:
        import ultralytics  # noqa: F401
        print(f"  [ok] ultralytics importable (v{ultralytics.__version__})")
    except ImportError:
        print("  [--] ultralytics NOT installed - fine for selftest; needed live")
    print(f"  [--] geometry defaults: hfov={HFOV_DEG_DEFAULT} cam_h={CAM_HEIGHT_M}m "
          f"tilt={CAM_TILT_DEG}deg marker_id={ROBOT_MARKER_ID} "
          f"marker_h={MARKER_HEIGHT_M}m")

    print(f"[selftest] {'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}")
    return not fails


# ---------------- Main loop ----------------
def main():
    args = parse_args()
    node_id = args.node_id or f"fixedcam-{args.zone}"
    poster = JsonPoster(args.url, args.token.strip())

    if args.selftest:
        raise SystemExit(0 if run_selftest() else 1)

    if args.test:
        bearing = {"left": -20.0, "center": 0.0, "right": 20.0}[args.side]
        z = zone_label(args.zone, args.side, args.directional)
        poster._post(build_payload(z, bearing, 1.5, 0.99, node_id, time.time()))
        return

    # Heavy imports live HERE (guarded) - selftest/test never reach them.
    import cv2
    from detect import PiCamSource, UsbCamSource, frame_signature, is_sharp

    if not hasattr(cv2, "aruco"):
        raise SystemExit("cv2.aruco missing - install opencv-contrib "
                         "(sudo apt install -y python3-opencv provides it on Pi OS).")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    aruco = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    detector = YoloDogBottomDetector(args.model, conf_min=args.conf, imgsz=args.imgsz)

    if args.camera == "stream":
        src = Esp32CamStreamSource(args.stream_url)
        src_desc = f"stream {args.stream_url}"
    elif args.camera == "pi":
        src = PiCamSource(FRAME_SIZE)
        src_desc = "pi"
    else:
        src = UsbCamSource(CAM_INDEX, FRAME_SIZE)
        src_desc = "usb"

    print(f"CANIS FIXED-room vision up [{src_desc}]. zone={args.zone!r} node={node_id!r} "
          f"model={args.model!r} conf>={args.conf} hfov={args.hfov} "
          f"cam_h={args.cam_height}m tilt={args.tilt}deg robot_marker={args.marker_id} "
          f"marker_h={args.marker_height}m directional={args.directional}. Ctrl+C to stop.")

    poster.start()
    grace = DetectionGrace(GRACE_S)                    # [OPT-12] dog-loss hold
    marker_hold = DetectionGrace(args.marker_grace)    # robot-pose hold (rigid marker)
    frozen = FrozenFrameGuard(FROZEN_MAX)              # [OPT-13]
    blur_mon = BlurSkipMonitor(window_s=BLUR_WINDOW_S, alert_ratio=BLUR_ALERT_RATIO)  # [C6]
    geom = None                                        # built on first frame (true size)
    frame_i = 0
    last_post = 0.0
    last_payload = None                                # re-sent during a grace hold
    read_fails = 0
    backoff = 0.1
    try:
        while True:
            try:
                frame = src.read_bgr()
            except Exception:
                frame = None
            if frame is None:
                # [OPT-6b] bounded backoff, then reconnect.
                read_fails += 1
                print(f"frame grab failed ({read_fails})")
                time.sleep(backoff)
                backoff = min(RECONNECT_MAX_BACKOFF, backoff * 2)
                if read_fails >= READ_FAIL_MAX:
                    print("too many read failures -> reopening camera")
                    try:
                        src.reopen()
                    except Exception as e:
                        print(f"camera reopen failed: {e}")
                    read_fails = 0
                    frozen.reset()
                continue
            read_fails = 0
            backoff = 0.1

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if geom is None or geom.frame_w != gray.shape[1] or geom.frame_h != gray.shape[0]:
                geom = Geom(gray.shape[1], gray.shape[0], args.hfov, args.tilt,
                            args.cam_height, args.marker_height)

            # [OPT-13] wedged driver replaying one buffer forever.
            if frozen.update(frame_signature(gray)):
                print("frozen stream detected (identical frames) -> reopening camera")
                try:
                    src.reopen()
                except Exception as e:
                    print(f"camera reopen failed: {e}")
                frozen.reset()
                continue

            frame_i += 1
            if frame_i % PROCESS_EVERY:
                continue

            # [C6] blur/low-light gate with blackout telemetry.
            now = time.time()
            sharp = is_sharp(gray)
            alert = blur_mon.record(skipped=not sharp, now=now)
            if alert is not None:
                print(f"ALERT [blur-guard] {alert['ratio'] * 100:.0f}% of frames skipped "
                      f"for low sharpness over {alert['window_s']:.0f}s "
                      f"({alert['skipped']}/{alert['total']}) - low light? vision is "
                      f"contributing nothing (system on PIR + ultrasonic only).")
            if not sharp:
                continue

            # Robot: ArUco marker -> room-frame pose (held briefly across dropouts).
            corners, ids, _ = aruco.detectMarkers(gray)
            pose_now = None
            if ids is not None:
                for marker_id, corner in zip(ids.flatten(), corners):
                    if int(marker_id) == args.marker_id:
                        pts = [(float(u), float(v)) for u, v in corner.reshape(-1, 2)]
                        pose_now = marker_pose_from_corners(pts, geom)
                        break
            pose = marker_hold.update(pose_now is not None, pose_now, now)

            # Dog: YOLO bbox bottom-center, node-side conf gate (LAW).
            found, cx, bottom, conf = detector.detect(frame)
            found = found and conf >= args.conf

            report = frame_report((cx, bottom, conf), pose, geom,
                                  args.zone, args.directional) if found else None
            z = grace.update(found, report[0] if report else None, now)
            if report is not None:
                last_payload = build_payload(z, report[1], report[2], conf, node_id, now)
            elif z is None:
                # [OPT-12] past the grace -> silence (contract: no dog).
                last_payload = None

            if last_payload is not None and now - last_post >= POST_EVERY_S:
                poster.send(last_payload)    # [OPT-6a] non-blocking hand-off
                last_post = now
    except KeyboardInterrupt:
        pass
    finally:
        poster.stop()
        src.release()


if __name__ == "__main__":
    main()
