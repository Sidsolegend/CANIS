#!/usr/bin/env python3
"""
CANIS - YOLO dog detection (Pi)  *** NEW DEFAULT VISION PIPELINE ***
====================================================================
Replaces the ArUco collar-marker pipeline (vision/detect.py, kept in place as
the legacy fallback) with an on-device YOLO-nano DOG detector (COCO class
"dog") - no marker on the dog needed. The Pi 5 runs headless OS-Lite with a
fan, so a nano model on CPU is comfortably real-time at our processing rate
(we only run inference on every PROCESS_EVERY'th frame anyway).

WHAT IS PRESERVED FROM detect.py (imported/reused, not re-implemented):
  [OPT-6a] background POST worker (capture never blocks on the network) - JsonPoster below is the JSON-payload variant of detect.Poster.
  [OPT-6b] camera read-failure backoff + reconnect (PiCamSource/UsbCamSource
           reused from detect.py).
  [OPT-12] DetectionGrace 1.5 s hold across brief detector loss (head-turns).
  [OPT-13] FrozenFrameGuard wedged-driver reopen.
  [C6]     BlurSkipMonitor low-light blackout alerting.

WIRE CONTRACT (docs/INTERFACE_CONTRACT.md §3 - LAW, backward-compatible):
  POST /detect JSON. "zone" is unchanged and required ("{zone}-{side}" when
  directional). NEW optional fields sent by this node:
    dog_present  bool - always true when we POST (silence = no dog; we NEVER
                         post dog_present:false)
    bearing_deg  float - camera frame, 0.0 = optical axis, negative = LEFT
    distance_m   float|null - monocular estimate from bbox height + known dog
                         height; null when the estimate is not trustworthy
    confidence   float [0,1]
    node_id      str - this reporter's name (default "cam-<zone>")
    ts           float - epoch seconds at frame capture
  Side mapping (LAW): left if bearing_deg < -10.0, right if > +10.0, else
  center. Node-side confidence gate YOLO_CONF_MIN = 0.60: detections below it
  are NOT posted (the dashboard does not re-gate).
  The old dashboard ignores the unknown keys, so this node also works against
  a pre-upgrade dashboard unchanged.

RUNTIME CHOICE: ultralytics YOLO11n, exported to NCNN (fastest CPU backend on
Raspberry Pi per ultralytics' own Pi benchmarks). The plain .pt also works
(slower). The heavy import is guarded: --selftest and --test run WITHOUT
ultralytics or any model file present (stub detector + synthetic frame).

HEADLESS PI 5 SETUP (exact commands, Raspberry Pi OS Lite 64-bit, Bookworm):
    sudo apt update
    sudo apt install -y python3-pip python3-opencv python3-picamera2
    pip3 install --break-system-packages ultralytics ncnn
    # one-time model download + NCNN export (pulls yolo11n.pt ~5.4 MB from
    # the ultralytics release assets, then exports; run in ~/canis/vision):
    yolo export model=yolo11n.pt format=ncnn imgsz=320
    # -> creates yolo11n_ncnn_model/  (the default model path below)
    # sanity check without camera/model:  python3 detect_yolo.py --selftest
    # run:  python3 detect_yolo.py --zone kitchen --token "$CANIS_TOKEN"
  NOTE: the NCNN export is baked to imgsz=320 - keep CANIS_YOLO_IMGSZ=320 (or
  re-export with a matching size). ~320 px nano inference on the Pi 5 CPU is
  ~40-60 ms/frame: far faster than our processed-frame cadence needs.

MONOCULAR DISTANCE MODEL (documented so the estimate is auditable):
    f_px       = (frame_w / 2) / tan(HFOV/2)          (square pixels assumed)
    distance_m = DOG_HEIGHT_M * f_px / bbox_height_px
  DOG_HEIGHT_M is the dog's real bounding-box height as the camera sees him,
  default 0.45 m (Shifu's shoulder-to-ground; tune per dog with
  CANIS_DOG_HEIGHT_M). It is a HEURISTIC: sitting/lying poses read farther
  than truth. Estimates outside [DIST_MIN_M, DIST_MAX_M] are reported as
  null (contract: never fake a number). Bearing uses the same pinhole model:
    bearing_deg = atan2(cx - frame_w/2, f_px)

CAMERA MODE (canis_config.h "Camera architecture A/B/C" block - one switch,
three architectures; docs/MOBILE_UPGRADE_STATUS.md §2/§5b):
    A  onboard          camera attached to the Pi (picamera2 / USB) - the
                        original robot's-eye pipeline, unchanged default.
    B  esp32cam_stream  robot's-eye frames pulled from an ESP32-CAM MJPEG/HTTP
                        stream (Pi off-board). Same YOLO pipeline, same guards
                        (frozen-frame, blur, grace, reconnect) - only the frame
                        SOURCE changes. Reconnect logic treats a stalled or
                        dropped stream exactly like a failed camera read.
    C  fixed_room       served by the dedicated vision/detect_fixed.py (fixed
                        room camera: YOLO dog + ArUco robot marker, room-frame
                        geometry). Selecting it here exits with a pointer.
  All modes POST the SAME contract body (§3), so controller + dashboard stay
  camera-mode-agnostic. Select with --camera-mode / CANIS_CAMERA_MODE.

Config precedence: CLI arg > env var > default.
    --camera-mode / CANIS_CAMERA_MODE      (onboard | esp32cam_stream |
                                            fixed_room; default "onboard")
    --stream-url / CANIS_ESP32CAM_URL      (mode B MJPEG URL; default
                                            http://esp32cam.local:81/stream - mirrors CANIS_ESP32CAM_STREAM_URL)
    --zone / CANIS_ZONE                    (default "kitchen")
    --model / CANIS_YOLO_MODEL             (default yolo11n_ncnn_model if the
                                            dir exists, else yolo11n.pt)
    --conf / CANIS_YOLO_CONF_MIN           (default 0.60 - contract gate)
    --hfov / CANIS_CAM_HFOV_DEG            (default 66.0 - Pi Cam Module 3;
                                            v2 = 62.2, wide M3 = 102)
    --dog-height / CANIS_DOG_HEIGHT_M      (default 0.45)
    --imgsz / CANIS_YOLO_IMGSZ             (default 320)
    --node-id / CANIS_NODE_ID              (default "cam-<zone>")
    --url / CANIS_DETECT_URL               (default http://127.0.0.1:5000/detect)
    --token / CANIS_TOKEN
    --directional / --no-directional / CANIS_DIRECTIONAL=0|1   (default on)

Test the Pi->dashboard channel without a camera or model:
    python3 detect_yolo.py --test --side left --zone hallway
Bring-up logic check without a camera or model (stub frame, stub detector):
    python3 detect_yolo.py --selftest
"""

import argparse
import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.request

from vision_logic import (DetectionGrace, FrozenFrameGuard,   # [OPT-12/13]
                          BlurSkipMonitor)                    # [C6]

# ---------------- Tunables (env-overridable; CLI wins over env) ----------------
FRAME_SIZE     = (640, 480)
CAM_INDEX      = int(os.environ.get("CANIS_CAM_INDEX", "0"))
PROCESS_EVERY  = int(os.environ.get("CANIS_PROCESS_EVERY", "3"))
POST_EVERY_S   = float(os.environ.get("CANIS_POST_EVERY_S", "1.0"))
READ_FAIL_MAX  = 10
RECONNECT_MAX_BACKOFF = 5.0
GRACE_S        = float(os.environ.get("CANIS_VISION_GRACE_S", "1.5"))    # [OPT-12]
FROZEN_MAX     = int(os.environ.get("CANIS_FROZEN_MAX", "30"))           # [OPT-13]
BLUR_WINDOW_S    = float(os.environ.get("CANIS_BLUR_WINDOW_S", "60.0"))  # [C6]
BLUR_ALERT_RATIO = float(os.environ.get("CANIS_BLUR_ALERT_RATIO", "0.9"))

# Contract §3 (LAW): node-side confidence gate; detections below are silence.
YOLO_CONF_MIN  = float(os.environ.get("CANIS_YOLO_CONF_MIN", "0.60"))
# Contract §3 (LAW): side mapping hysteresis-free boundary at +/- 10 degrees.
SIDE_DEADBAND_DEG = 10.0

# Camera architecture switch (canis_config.h CANIS_CAMERA_MODE mirror).
CAMERA_MODES        = ("onboard", "esp32cam_stream", "fixed_room")
CAMERA_MODE_DEFAULT = os.environ.get("CANIS_CAMERA_MODE", "onboard")
# Mode B stream source (canis_config.h CANIS_ESP32CAM_STREAM_URL mirror).
# Accept BOTH the header-consistent name and the legacy short name so a value
# set from canis_config.h's CANIS_ESP32CAM_STREAM_URL is never silently ignored.
ESP32CAM_URL_DEFAULT   = (os.environ.get("CANIS_ESP32CAM_STREAM_URL")
                          or os.environ.get("CANIS_ESP32CAM_URL")
                          or "http://esp32cam.local:81/stream")
STREAM_CONNECT_TIMEOUT_S = float(os.environ.get("CANIS_STREAM_CONNECT_TIMEOUT_S", "3.0"))
STREAM_STALL_S           = float(os.environ.get("CANIS_STREAM_STALL_S", "5.0"))   # CANIS_ESP32CAM_STALL_S
STREAM_CHUNK             = 4096
STREAM_BUF_MAX           = 1 << 20   # runaway (EOI never arrives) -> reset buffer
# NOTE: the stock ESP32-CAM OV2640 lens is ~65 deg HFOV - in mode B pass
# --hfov 65 (or CANIS_CAM_HFOV_DEG=65) to keep bearing/distance honest.

HFOV_DEG_DEFAULT   = float(os.environ.get("CANIS_CAM_HFOV_DEG", "66.0"))
DOG_HEIGHT_M       = float(os.environ.get("CANIS_DOG_HEIGHT_M", "0.45"))
YOLO_IMGSZ         = int(os.environ.get("CANIS_YOLO_IMGSZ", "320"))
DIST_MIN_M, DIST_MAX_M = 0.15, 12.0    # outside this -> distance_m = null
MIN_BBOX_H_PX      = 8                 # tinier than this -> distance_m = null


def _default_model_path():
    p = os.environ.get("CANIS_YOLO_MODEL")
    if p:
        return p
    return "yolo11n_ncnn_model" if os.path.isdir("yolo11n_ncnn_model") else "yolo11n.pt"


# ---------------- Pure geometry (unit-testable, exercised by --selftest) ----------------
def focal_px(frame_w, hfov_deg):
    """Pinhole focal length in pixels from the horizontal FOV."""
    return (frame_w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def bearing_deg_from_cx(cx, frame_w, hfov_deg):
    """Bearing of a pixel column: 0.0 = optical axis, negative = LEFT (contract)."""
    return math.degrees(math.atan2(cx - frame_w / 2.0, focal_px(frame_w, hfov_deg)))


def side_from_bearing(bearing_deg):
    """Contract §3 side mapping (LAW): must agree with ArUco's thirds rule."""
    if bearing_deg < -SIDE_DEADBAND_DEG:
        return "left"
    if bearing_deg > SIDE_DEADBAND_DEG:
        return "right"
    return "center"


def distance_m_from_bbox(bbox_h_px, frame_w, hfov_deg, dog_height_m=DOG_HEIGHT_M):
    """Monocular range from bbox height. Returns None (-> JSON null) when the
    estimate is not trustworthy - the contract forbids faking a number."""
    if bbox_h_px < MIN_BBOX_H_PX:
        return None
    d = dog_height_m * focal_px(frame_w, hfov_deg) / float(bbox_h_px)
    if not (DIST_MIN_M <= d <= DIST_MAX_M):
        return None
    return round(d, 2)


def zone_label(zone, side, directional):
    return f"{zone}-{side}" if directional else zone


def build_payload(zone, bearing_deg, distance_m, confidence, node_id, ts):
    """Contract §3 body. dog_present is ALWAYS true here: we only ever POST
    while reporting a dog; silence means no dog."""
    return {
        "zone": zone,
        "dog_present": True,
        "bearing_deg": round(float(bearing_deg), 1),
        "distance_m": distance_m if distance_m is None else round(float(distance_m), 2),
        "confidence": round(float(confidence), 3),
        "node_id": node_id,
        "ts": round(float(ts), 3),
    }


# ---------------- MJPEG stream parsing + source (mode B) ----------------
JPEG_SOI = b"\xff\xd8"   # start-of-image marker
JPEG_EOI = b"\xff\xd9"   # end-of-image marker


def extract_jpeg(buf):
    """Pull one complete JPEG out of an MJPEG byte buffer (pure function,
    exercised by --selftest). Returns (jpeg_bytes | None, remaining_buffer).
    Multipart boundary lines/headers are just skipped as inter-frame garbage - scanning SOI..EOI is robust to every ESP32-CAM boundary variant."""
    start = buf.find(JPEG_SOI)
    if start < 0:
        # No frame start in sight: keep only the last byte (it may be the
        # first half of a marker split across chunks), drop the garbage.
        return None, buf[-1:]
    end = buf.find(JPEG_EOI, start + 2)
    if end < 0:
        return None, buf[start:]          # partial frame: wait for more bytes
    return buf[start:end + 2], buf[end + 2:]


class Esp32CamStreamSource:
    """Mode B frame source: reads an ESP32-CAM MJPEG/HTTP stream and yields
    BGR frames through the SAME read_bgr()/reopen()/release() surface as
    detect.PiCamSource / detect.UsbCamSource, so the main loop's [OPT-6b]
    backoff-reconnect and [OPT-13] frozen-frame machinery apply unchanged - a stalled or dropped stream looks exactly like a failed camera read.

    Deliberately tolerant at startup: if the stream is down when we launch
    (robot booting, WiFi flaking - the KNOWN cost of option B), we come up
    anyway and keep retrying instead of dying."""

    def __init__(self, url, connect_timeout_s=STREAM_CONNECT_TIMEOUT_S,
                 stall_s=STREAM_STALL_S):
        self.url = url
        self.connect_timeout_s = float(connect_timeout_s)
        self.stall_s = float(stall_s)
        self._resp = None
        self._buf = b""
        self._try_open()

    def _try_open(self):
        try:
            self._resp = urllib.request.urlopen(self.url,
                                                timeout=self.connect_timeout_s)
            self._buf = b""
            print(f"ESP32-CAM stream connected: {self.url}")
        except Exception as e:
            self._resp = None
            print(f"ESP32-CAM stream connect failed ({e}) - will retry")

    def read_bgr(self):
        """One BGR frame, or None on stall/EOF/corruption (caller backs off,
        then calls reopen() - same contract as the local camera sources)."""
        import cv2          # lazy: --selftest/--test never import cv2
        import numpy as np
        if self._resp is None:
            self._try_open()
            if self._resp is None:
                return None
        deadline = time.time() + self.stall_s
        while time.time() < deadline:
            jpeg, self._buf = extract_jpeg(self._buf)
            if jpeg is not None:
                # imdecode returns None on a truncated/corrupt JPEG -> caller
                # treats it as a failed read; the stream itself stays up.
                return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8),
                                    cv2.IMREAD_COLOR)
            try:
                chunk = self._resp.read(STREAM_CHUNK)
            except Exception:
                chunk = b""
            if not chunk:                 # EOF / socket timeout: connection is dead
                self._close()
                return None
            self._buf += chunk
            if len(self._buf) > STREAM_BUF_MAX:   # EOI never arrived - reset
                self._buf = self._buf[-2:]
        return None                       # stalled past stall_s with no full frame

    def _close(self):
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None
        self._buf = b""

    def reopen(self):
        self._close()
        self._try_open()

    def release(self):
        self._close()


# ---------------- Async JSON POST worker [OPT-6a] ----------------
class JsonPoster(threading.Thread):
    """detect.Poster's JSON-payload sibling: background sender fed by a size-1
    coalescing queue, so a slow dashboard can never stall frame capture."""

    def __init__(self, url, token):
        super().__init__(daemon=True)
        self.url = url
        self.token = token
        self.q = queue.Queue(maxsize=1)
        self._stop = threading.Event()

    def send(self, payload):
        """Non-blocking hand-off; only the newest detection matters."""
        try:
            self.q.put_nowait(payload)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(payload)
            except queue.Full:
                pass

    def run(self):
        while not self._stop.is_set():
            try:
                payload = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._post(payload)

    def _post(self, payload):
        data = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-CANIS-Token"] = self.token
        req = urllib.request.Request(self.url, data=data, headers=headers)
        try:
            urllib.request.urlopen(req, timeout=1).read()
            print(f"posted zone={payload['zone']!r} bearing={payload.get('bearing_deg')} "
                  f"dist={payload.get('distance_m')} conf={payload.get('confidence')}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"POST rejected ({e.code}): bad/missing token (set --token).")
            else:
                print(f"POST HTTP error ({e.code}).")
        except urllib.error.URLError as e:
            print(f"POST failed ({e}). Is the dashboard running?")

    def stop(self):
        self._stop.set()


# ---------------- Dog detectors ----------------
class YoloDogDetector:
    """Real detector. The ultralytics import lives HERE (guarded) so --selftest
    and --test never need the package or the model file."""

    def __init__(self, model_path, conf_min=YOLO_CONF_MIN, imgsz=YOLO_IMGSZ):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise SystemExit(
                "ultralytics not installed. On the Pi 5 (OS Lite):\n"
                "  sudo apt install -y python3-pip python3-opencv python3-picamera2\n"
                "  pip3 install --break-system-packages ultralytics ncnn\n"
                "  yolo export model=yolo11n.pt format=ncnn imgsz=320\n"
                "then re-run. (--selftest works without any of this.)") from e
        self.model = YOLO(model_path)      # .pt auto-downloads; ncnn dir loads local
        self.conf_min = float(conf_min)
        self.imgsz = int(imgsz)
        names = self.model.names or {}
        # Target class defaults to "dog"; override with CANIS_YOLO_TARGET_CLASS
        # (e.g. "person") for bench-testing the full loop without a live dog.
        target = os.environ.get("CANIS_YOLO_TARGET_CLASS", "dog").strip().lower()
        dog_ids = [int(k) for k, v in names.items() if str(v).lower() == target]
        if not dog_ids:
            raise SystemExit(f"model {model_path!r} has no {target!r} class - use a "
                             "COCO-trained model (yolo11n / yolov8n).")
        self._dog_cls = set(dog_ids)       # COCO: dog=16, person=0

    def detect(self, frame_bgr):
        """Return (found, cx_px, bbox_h_px, confidence) for the single most
        confident dog at/above conf_min, else (False, 0.0, 0.0, 0.0)."""
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
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                best = (True, (x1 + x2) / 2.0, max(0.0, y2 - y1), conf)
        return best if best else (False, 0.0, 0.0, 0.0)


class StubDogDetector:
    """--selftest detector: scripted detections, zero dependencies. Yields a
    'dog' walking left->center->right, then absence (to exercise the grace)."""

    def __init__(self, frame_w, frame_h):
        third, h = frame_w / 6.0, frame_h * 0.4
        self._script = [
            (True,  third * 1, h, 0.91),   # far left  -> "left"
            (True,  third * 3, h, 0.88),   # center    -> "center"
            (True,  third * 5, h, 0.86),   # far right -> "right"
            (True,  third * 3, h, 0.40),   # BELOW YOLO_CONF_MIN -> must be silence
            (False, 0.0, 0.0, 0.0),        # gone -> grace hold, then clear
        ]
        self._i = 0

    def detect(self, _frame):
        r = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return r


# ---------------- CLI ----------------
def _env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip() not in ("0", "false", "False", "no", "")


def parse_args():
    ap = argparse.ArgumentParser(description="CANIS YOLO dog detector (new default)")
    ap.add_argument("--camera-mode", choices=list(CAMERA_MODES),
                    default=CAMERA_MODE_DEFAULT,
                    help="camera architecture (canis_config.h CANIS_CAMERA_MODE): "
                         "onboard = Pi-local camera (A), esp32cam_stream = "
                         "ESP32-CAM MJPEG stream (B), fixed_room = handled by "
                         "detect_fixed.py (C). Env CANIS_CAMERA_MODE")
    ap.add_argument("--stream-url", default=ESP32CAM_URL_DEFAULT,
                    help="mode B MJPEG stream URL (env CANIS_ESP32CAM_URL; "
                         "config CANIS_ESP32CAM_STREAM_URL)")
    ap.add_argument("--camera", choices=["pi", "usb"], default="pi",
                    help="onboard-mode backend (ignored in esp32cam_stream mode)")
    ap.add_argument("--zone", default=os.environ.get("CANIS_ZONE", "kitchen"))
    ap.add_argument("--model", default=_default_model_path(),
                    help="ultralytics model: NCNN export dir (recommended) or .pt "
                         "(env CANIS_YOLO_MODEL)")
    ap.add_argument("--conf", type=float, default=YOLO_CONF_MIN,
                    help="node-side confidence gate, contract YOLO_CONF_MIN "
                         "(env CANIS_YOLO_CONF_MIN, default 0.60)")
    ap.add_argument("--hfov", type=float, default=HFOV_DEG_DEFAULT,
                    help="camera horizontal FOV in degrees (env CANIS_CAM_HFOV_DEG)")
    ap.add_argument("--dog-height", type=float, default=DOG_HEIGHT_M,
                    help="dog's real height in meters for the monocular range "
                         "estimate (env CANIS_DOG_HEIGHT_M)")
    ap.add_argument("--imgsz", type=int, default=YOLO_IMGSZ,
                    help="inference size; MUST match the NCNN export imgsz "
                         "(env CANIS_YOLO_IMGSZ)")
    ap.add_argument("--node-id", default=os.environ.get("CANIS_NODE_ID", ""),
                    help="reporter id for /detect (default cam-<zone>)")
    ap.add_argument("--url", default=os.environ.get("CANIS_DETECT_URL",
                                                    "http://127.0.0.1:5000/detect"))
    ap.add_argument("--token", default=os.environ.get("CANIS_TOKEN", ""))
    dgroup = ap.add_mutually_exclusive_group()
    dgroup.add_argument("--directional", dest="directional", action="store_true")
    dgroup.add_argument("--no-directional", dest="directional", action="store_false")
    ap.set_defaults(directional=_env_bool("CANIS_DIRECTIONAL", True))
    ap.add_argument("--test", action="store_true",
                    help="send ONE fake full-contract detection and exit "
                         "(no camera, no model)")
    ap.add_argument("--side", choices=["left", "center", "right"], default="center",
                    help="side to fake in --test mode")
    ap.add_argument("--selftest", action="store_true",
                    help="run the geometry + pipeline logic on a stub frame with a "
                         "stub detector - PASSes with NO model installed")
    return ap.parse_args()


# ---------------- Self-test (no camera, no model, no network) ----------------
def run_selftest():
    """Verify the contract-critical math and the detection->grace->payload
    pipeline using the stub detector. Returns True on PASS."""
    w, h = FRAME_SIZE
    hfov = HFOV_DEG_DEFAULT
    fails = []

    def check(name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))
        if not cond:
            fails.append(name)

    print("[selftest] 1/4 geometry")
    check("bearing at optical axis == 0", abs(bearing_deg_from_cx(w / 2, w, hfov)) < 1e-9)
    bl = bearing_deg_from_cx(0, w, hfov)
    br = bearing_deg_from_cx(w, w, hfov)
    check("left edge bearing negative ~ -HFOV/2", abs(bl + hfov / 2) < 0.5, f"{bl:.1f}")
    check("right edge bearing positive ~ +HFOV/2", abs(br - hfov / 2) < 0.5, f"{br:.1f}")
    check("side mapping LAW boundaries",
          side_from_bearing(-10.1) == "left" and side_from_bearing(-10.0) == "center"
          and side_from_bearing(10.0) == "center" and side_from_bearing(10.1) == "right")
    d_near = distance_m_from_bbox(h * 0.8, w, hfov)
    d_far = distance_m_from_bbox(h * 0.1, w, hfov)
    check("distance monotonic (smaller bbox = farther)",
          d_near is not None and d_far is not None and d_far > d_near,
          f"near={d_near} far={d_far}")
    check("tiny bbox -> distance null", distance_m_from_bbox(4, w, hfov) is None)

    print("[selftest] 2/4 MJPEG stream parser (mode B, no network)")
    jpeg1 = JPEG_SOI + b"frame-one-payload" + JPEG_EOI
    jpeg2 = JPEG_SOI + b"frame-two-payload" + JPEG_EOI
    buf = b"--boundary\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg1 + b"\r\n--boundary\r\n" + jpeg2
    f1, buf = extract_jpeg(buf)
    check("first JPEG extracted through multipart garbage", f1 == jpeg1)
    f2, buf = extract_jpeg(buf)
    check("second JPEG extracted, buffer drained", f2 == jpeg2 and buf == b"")
    part, rest = extract_jpeg(jpeg1[:-1])          # EOI missing -> wait for more
    check("partial frame held, not emitted", part is None and rest == jpeg1[:-1])
    none_f, rest = extract_jpeg(b"no-marker-garbage-here")
    check("garbage trimmed to 1-byte tail (split-marker safe)",
          none_f is None and len(rest) == 1)
    split, rest = extract_jpeg(b"junk\xff")        # first half of an SOI marker
    check("half SOI marker survives the trim", split is None and rest == b"\xff")

    print("[selftest] 3/4 stub-frame pipeline (detector -> gate -> grace -> payload)")
    try:
        import numpy as np
        stub_frame = np.zeros((h, w, 3), dtype=np.uint8)   # synthetic frame
    except ImportError:
        stub_frame = None                                  # stub detector ignores it
    det = StubDogDetector(w, h)
    grace = DetectionGrace(GRACE_S)
    posted, now = [], 1000.0
    for step in range(6):
        found, cx, bh, conf = det.detect(stub_frame)
        gated = found and conf >= YOLO_CONF_MIN            # node-side gate (LAW)
        bearing = bearing_deg_from_cx(cx, w, hfov) if gated else 0.0
        z = grace.update(gated,
                         zone_label("kitchen", side_from_bearing(bearing), True)
                         if gated else None, now)
        if z is not None and gated:
            posted.append(build_payload(z, bearing,
                                        distance_m_from_bbox(bh, w, hfov),
                                        conf, "cam-selftest", now))
        elif z is not None:
            posted.append({"held": z})
        now += 0.5
    sides = [p["zone"] for p in posted if "zone" in p]
    check("scripted walk labeled left/center/right",
          sides == ["kitchen-left", "kitchen-center", "kitchen-right"], f"{sides}")
    check("sub-threshold detection (conf .40) NOT posted",
          not any(p.get("confidence") == 0.4 for p in posted))
    check("grace held the last zone after loss",
          any(p.get("held") == "kitchen-right" for p in posted))
    check("grace cleared past window (silence = no dog)",
          grace.update(False, None, now + GRACE_S + 1) is None)
    p0 = next(p for p in posted if "zone" in p)
    check("payload has all contract fields",
          all(k in p0 for k in ("zone", "dog_present", "bearing_deg", "distance_m",
                                "confidence", "node_id", "ts")) and p0["dog_present"] is True)

    print("[selftest] 4/4 environment (informational, does not gate PASS)")
    try:
        import ultralytics  # noqa: F401
        print(f"  [ok] ultralytics importable (v{ultralytics.__version__})")
    except ImportError:
        print("  [--] ultralytics NOT installed - fine for selftest; needed to run "
              "live (see header for the exact pip/model commands)")
    mp = _default_model_path()
    print(f"  [--] default model path: {mp!r} "
          f"({'present' if os.path.exists(mp) else 'not downloaded yet'})")
    print(f"  [--] camera mode: {CAMERA_MODE_DEFAULT!r} (env CANIS_CAMERA_MODE); "
          f"mode-B stream URL: {ESP32CAM_URL_DEFAULT!r}")

    print(f"[selftest] {'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}")
    return not fails


# ---------------- Main loop ----------------
def main():
    args = parse_args()
    if args.camera_mode not in CAMERA_MODES:      # env var can inject junk
        raise SystemExit(f"unknown CANIS_CAMERA_MODE {args.camera_mode!r} "
                         f"(expected one of {CAMERA_MODES})")
    node_id = args.node_id or f"cam-{args.zone}"
    poster = JsonPoster(args.url, args.token.strip())

    if args.selftest:
        raise SystemExit(0 if run_selftest() else 1)

    if args.camera_mode == "fixed_room" and not args.test:
        raise SystemExit(
            "camera mode 'fixed_room' (option C) is served by its dedicated "
            "entry point:\n    python3 detect_fixed.py --zone <zone> ...\n"
            "(fixed room camera: YOLO dog + ArUco robot marker, room-frame "
            "geometry - see canis_config.h 'Camera architecture A/B/C').")

    if args.test:
        # One fake full-contract detection (no camera/model): pick a bearing
        # that lands in the requested side per the LAW mapping.
        bearing = {"left": -20.0, "center": 0.0, "right": 20.0}[args.side]
        z = zone_label(args.zone, args.side, args.directional)
        poster._post(build_payload(z, bearing, 1.5, 0.99, node_id, time.time()))
        return

    # Camera + guards come from the proven legacy module [OPT-6b/13/C6]. cv2 is
    # imported transitively here (not at module top) so --selftest/--test stay
    # dependency-free.
    import cv2
    from detect import PiCamSource, UsbCamSource, frame_signature, is_sharp

    detector = YoloDogDetector(args.model, conf_min=args.conf, imgsz=args.imgsz)

    # Frame SOURCE is the only thing the camera mode changes (A vs B); the
    # whole pipeline below - frozen guard, blur monitor, grace, poster - is
    # source-agnostic and identical in both modes.
    if args.camera_mode == "esp32cam_stream":
        src = Esp32CamStreamSource(args.stream_url)
        src_desc = f"esp32cam_stream {args.stream_url}"
    else:  # "onboard"
        src = PiCamSource(FRAME_SIZE) if args.camera == "pi" else UsbCamSource(CAM_INDEX, FRAME_SIZE)
        src_desc = f"onboard/{args.camera}"

    print(f"CANIS YOLO vision up [{src_desc}]. zone={args.zone!r} node={node_id!r} "
          f"model={args.model!r} conf>={args.conf} hfov={args.hfov} "
          f"dog_h={args.dog_height}m directional={args.directional}. Ctrl+C to stop.")

    poster.start()
    grace = DetectionGrace(GRACE_S)          # [OPT-12]
    frozen = FrozenFrameGuard(FROZEN_MAX)    # [OPT-13]
    blur_mon = BlurSkipMonitor(window_s=BLUR_WINDOW_S, alert_ratio=BLUR_ALERT_RATIO)  # [C6]
    frame_i = 0
    last_post = 0.0
    last_payload = None                      # re-sent during a grace hold
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

            found, cx, bbox_h, conf = detector.detect(frame)
            # detector already gates at conf_min, but keep the LAW explicit:
            found = found and conf >= args.conf

            if found:
                bearing = bearing_deg_from_cx(cx, gray.shape[1], args.hfov)
                side = side_from_bearing(bearing)
                dist = distance_m_from_bbox(bbox_h, gray.shape[1], args.hfov,
                                            args.dog_height)
                z = grace.update(True, zone_label(args.zone, side, args.directional),
                                 now)
                last_payload = build_payload(z, bearing, dist, conf, node_id, now)
            else:
                # [OPT-12] brief loss -> hold and keep re-posting the LAST real
                # detection (its ts stays at capture time - honest); past the
                # grace -> silence, which the contract defines as "no dog".
                z = grace.update(False, None, now)
                if z is None:
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
