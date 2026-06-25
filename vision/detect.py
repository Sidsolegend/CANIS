#!/usr/bin/env python3
"""
CANIS — vision detection (Pi, Phase 6)
--------------------------------------
Runs on the Pi with a USB webcam. Detects the ArUco marker on Shifu's collar;
when it's seen, POSTs this camera's ZONE to the dashboard's /detect endpoint.
The robot's ESP32 polls /command and treats that as a remote detection.

Model: one camera watches one room, so the camera's location IS the zone. The
marker identifies the dog; seeing it means "the dog is in this room."

STATUS: the detection + POST logic is complete. Live capture is untested until
the USB webcam arrives. You can prove the whole Pi->ESP32 channel TODAY without
a camera:

    python3 detect.py --test          # sends one fake detection, then exits

Watch the ESP32 Serial Monitor — it should print "[VISION] dog in zone: kitchen"
and arousal should climb. That confirms vision -> Pi -> ESP32 end to end.

Once the webcam is in:  python3 detect.py
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
CAM_INDEX      = 0                # /dev/video0
ARUCO_DICT     = cv2.aruco.DICT_4X4_50
PROCESS_EVERY  = 3                # process every Nth frame (cut Pi CPU load)
POST_EVERY_S   = 1.0             # at most one POST/sec while detected (keeps /command fresh)
BLUR_THRESHOLD = 40.0            # skip frames blurrier than this (Laplacian variance)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true",
                    help="send one fake detection and exit (no camera needed)")
    args = ap.parse_args()

    if args.test:
        ok = post_zone(ZONE)
        print(f"sent test detection zone={ZONE!r} -> {'OK' if ok else 'FAILED'}")
        return

    # New-style ArUco API (OpenCV 4.7+, you have 4.13)
    dictionary = cv2.aruco.getPredefinedDictionary(ARUCO_DICT)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open camera {CAM_INDEX}. Is the USB webcam plugged in?")

    print(f"CANIS vision up. zone={ZONE!r}, watching for marker IDs {DOG_MARKER_IDS}. Ctrl+C to stop.")
    frame_i = 0
    last_post = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("frame grab failed"); time.sleep(0.1); continue

            frame_i += 1
            if frame_i % PROCESS_EVERY:           # frame skip
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if not is_sharp(gray):                # blurry (likely motion) -> skip
                continue

            _, ids, _ = detector.detectMarkers(gray)
            seen = {int(i) for i in ids.flatten()} if ids is not None else set()

            if seen & DOG_MARKER_IDS:             # dog's marker is in frame
                now = time.time()
                if now - last_post >= POST_EVERY_S:
                    if post_zone(ZONE):
                        print(f"detected dog -> posted zone={ZONE!r}")
                    last_post = now
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()


if __name__ == "__main__":
    main()
