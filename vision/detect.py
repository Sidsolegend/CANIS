#!/usr/bin/env python3
"""
CANIS — vision detection (Pi, Phase 6)
--------------------------------------
Runs on the Pi. Detects the ArUco marker on Shifu's collar; when it's seen,
POSTs this camera's ZONE to the dashboard's /detect endpoint. The robot's ESP32
polls /command and treats that as a remote detection.

Model: one camera watches one room, so the camera's location IS the zone. The
marker identifies the dog; seeing it means "the dog is in this room."

Two camera backends:
    --camera pi    Raspberry Pi Camera Module via picamera2 (DEFAULT, needs the
                   CSI ribbon — the Pi-5-compatible adapter cable)
    --camera usb   any UVC USB webcam via OpenCV (/dev/videoN)

Directional hint: when DIRECTIONAL is on, the posted zone gets a "-left",
"-center" or "-right" suffix from where the marker sits in the frame (e.g.
"kitchen-left"). The ESP32 uses that to bias which way it orients during a
correction. NOTE: camera-frame left/right is only a rough hint — it's not the
robot's bearing (the camera and robot face different ways). The robot's own
3 ultrasonics are the authoritative bearing; this suffix is just the fallback.

Prove the whole Pi->ESP32 channel WITHOUT a camera:
    python3 detect.py --test                 # one fake detection, then exit
    python3 detect.py --test --side left      # fake a left-side detection

Watch the ESP32 Serial Monitor — it should print "[VISION] dog in zone: ..."
and arousal should climb. Live run once wired:  python3 detect.py
"""

import argparse
import json
import time
import urllib.request
import urllib.error

import cv2

# ---------------- Config ----------------
PI_URL         = "http://127.0.0.1:5000/detect"  # dashboard runs on this same Pi
ZONE           = "kitchen"        # the room THIS camera watches
DOG_MARKER_IDS = {0}              # ArUco IDs that count as Shifu's collar marker
CAM_INDEX      = 0                # /dev/video0 (usb backend only)
FRAME_SIZE     = (640, 480)       # capture resolution
ARUCO_DICT     = cv2.aruco.DICT_4X4_50
PROCESS_EVERY  = 3                # process every Nth frame (cut Pi CPU load)
POST_EVERY_S   = 1.0              # at most one POST/sec while detected (keeps /command fresh)
BLUR_THRESHOLD = 40.0             # skip frames blurrier than this (Laplacian variance)
DIRECTIONAL    = True             # append -left/-center/-right to the zone


def post_zone(zone):
    """POST {"zone": zone} to the dashboard. Returns True on success."""
    data = json.dumps({"zone": zone}).encode()
    req = urllib.request.Request(
        PI_URL, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1).read()
        return True
    except urllib.error.URLError as e:
        print(f"POST failed ({e}). Is the dashboard running?")
        return False


def is_sharp(gray):
    """Motion-blur guard: reject frames whose Laplacian variance is too low."""
    return cv2.Laplacian(gray, cv2.CV_64F).var() >= BLUR_THRESHOLD


def marker_side(corner, width):
    """Which third of the frame the marker's centre sits in: left/center/right."""
    cx = float(corner.reshape(-1, 2)[:, 0].mean())
    if cx < width / 3:
        return "left"
    if cx > 2 * width / 3:
        return "right"
    return "center"


def zone_label(side):
    """Compose the zone string posted to the dashboard."""
    return f"{ZONE}-{side}" if DIRECTIONAL else ZONE


# ---------------- Camera backends ----------------
class PiCamSource:
    """Raspberry Pi Camera Module via picamera2 (libcamera)."""
    def __init__(self, size):
        try:
            from picamera2 import Picamera2
        except ImportError as e:
            raise SystemExit(
                "picamera2 not found. Install it (sudo apt install -y python3-picamera2) "
                "or use --camera usb.") from e
        self.cam = Picamera2()
        cfg = self.cam.create_video_configuration(main={"size": size, "format": "RGB888"})
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(0.5)   # let exposure/AWB settle

    def read_gray(self):
        frame = self.cam.capture_array()                 # RGB888
        return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

    def read_bgr(self):                                  # for --snap (saving)
        return cv2.cvtColor(self.cam.capture_array(), cv2.COLOR_RGB2BGR)

    def release(self):
        self.cam.stop()


class UsbCamSource:
    """Any UVC USB webcam via OpenCV."""
    def __init__(self, index, size):
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open camera {index}. Is the USB webcam plugged in?")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])

    def read_gray(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def read_bgr(self):                                  # for --snap (saving)
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        self.cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", choices=["pi", "usb"], default="pi",
                    help="camera backend (default: pi / Pi Camera Module)")
    ap.add_argument("--test", action="store_true",
                    help="send one fake detection and exit (no camera needed)")
    ap.add_argument("--side", choices=["left", "center", "right"], default="center",
                    help="side label to use in --test mode")
    ap.add_argument("--snap", action="store_true",
                    help="capture ONE frame, save it, report markers seen, and exit "
                         "(camera sanity check; no dashboard needed)")
    args = ap.parse_args()

    if args.test:
        z = zone_label(args.side)
        ok = post_zone(z)
        print(f"sent test detection zone={z!r} -> {'OK' if ok else 'FAILED'}")
        return

    # New-style ArUco API (OpenCV 4.7+)
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

    src = PiCamSource(FRAME_SIZE) if args.camera == "pi" else UsbCamSource(CAM_INDEX, FRAME_SIZE)

    if args.snap:
        try:
            frame = src.read_bgr()
            if frame is None:
                raise SystemExit("camera returned no frame — check the cable/connection.")
            out = "snapshot.jpg"
            cv2.imwrite(out, frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, ids, _ = detector.detectMarkers(gray)
            seen = sorted(int(i) for i in ids.flatten()) if ids is not None else []
            h, w = gray.shape
            print(f"captured {w}x{h} -> saved {out}. markers seen: {seen or 'none'} "
                  f"(dog IDs {sorted(DOG_MARKER_IDS)})")
        finally:
            src.release()
        return
    print(f"CANIS vision up [{args.camera}]. zone={ZONE!r}, watching marker IDs "
          f"{DOG_MARKER_IDS}. Ctrl+C to stop.")

    frame_i = 0
    last_post = 0.0
    try:
        while True:
            gray = src.read_gray()
            if gray is None:
                print("frame grab failed"); time.sleep(0.1); continue

            frame_i += 1
            if frame_i % PROCESS_EVERY:           # frame skip
                continue
            if not is_sharp(gray):                # blurry (likely motion) -> skip
                continue

            corners, ids, _ = detector.detectMarkers(gray)
            if ids is None:
                continue
            flat = [int(i) for i in ids.flatten()]

            # find the dog's marker, if present, and where it is in the frame
            side = "center"
            found = False
            for marker_id, corner in zip(flat, corners):
                if marker_id in DOG_MARKER_IDS:
                    found = True
                    side = marker_side(corner, gray.shape[1])
                    break

            if found:
                now = time.time()
                if now - last_post >= POST_EVERY_S:
                    z = zone_label(side)
                    if post_zone(z):
                        print(f"detected dog -> posted zone={z!r}")
                    last_post = now
    except KeyboardInterrupt:
        pass
    finally:
        src.release()


if __name__ == "__main__":
    main()
