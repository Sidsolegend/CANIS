#!/usr/bin/env python3
"""
CANIS — vision detection (Pi)  [OPTIMIZED DUPLICATE]
====================================================
Same job as the original vision/detect.py: detect Shifu's ArUco collar marker
and POST this camera's zone to the dashboard's /detect endpoint. Three
review-identified fixes:

  [OPT-6a] The POST is moved OFF the capture loop. The original called
           post_zone() inline (orig detect.py:210-213); with urlopen(timeout=1)
           a slow/unreachable dashboard stalled frame capture for up to a
           second per detection. Now a background worker thread drains a small
           queue, so capture never blocks on the network. Only the newest
           detection matters, so the queue is size 1 and coalesces.
  [OPT-6b] Camera-read failures back off and RECONNECT instead of spinning a
           tight sleep loop forever (orig: `print(...); time.sleep(0.1);
           continue`, detect.py:185-186). After N consecutive failures the
           source is torn down and re-opened (handles a USB re-enumeration or a
           picamera2 hiccup).
  [OPT-6c] ZONE / DOG_MARKER_IDS / DIRECTIONAL (and more) are now CLI args or
           env vars instead of source constants you had to hand-edit per camera
           (orig detect.py:42-50). One code copy now serves every room.

  Token [OPT-2]: if the dashboard requires a shared secret, pass --token or set
  CANIS_TOKEN; it is sent as the X-CANIS-Token header on every POST.

Config precedence: CLI arg > env var > default.
    --zone / CANIS_ZONE                 (default "kitchen")
    --marker-ids / CANIS_MARKER_IDS     comma list, default "0"
    --directional / --no-directional / CANIS_DIRECTIONAL=0|1  (default on)
    --url / CANIS_DETECT_URL            (default http://127.0.0.1:5000/detect)
    --token / CANIS_TOKEN

Test the Pi->ESP32 channel without a camera:
    python3 detect.py --test
    python3 detect.py --test --side left --zone hallway
"""

import argparse
import json
import os
import queue
import threading
import time
import urllib.request
import urllib.error

import cv2

# ---------------- Static defaults (rarely changed; still overridable) ----------------
CAM_INDEX      = int(os.environ.get("CANIS_CAM_INDEX", "0"))
FRAME_SIZE     = (640, 480)
ARUCO_DICT     = cv2.aruco.DICT_4X4_50
PROCESS_EVERY  = int(os.environ.get("CANIS_PROCESS_EVERY", "3"))
POST_EVERY_S   = float(os.environ.get("CANIS_POST_EVERY_S", "1.0"))
BLUR_THRESHOLD = float(os.environ.get("CANIS_BLUR_THRESHOLD", "40.0"))
READ_FAIL_MAX  = 10      # consecutive bad reads before we tear down + reconnect
RECONNECT_MAX_BACKOFF = 5.0


# ---------------- Async POST worker [OPT-6a] ----------------
class Poster(threading.Thread):
    """Background sender. Capture thread hands it the latest zone via a size-1
    queue; a slow dashboard can never stall capture."""

    def __init__(self, url, token):
        super().__init__(daemon=True)
        self.url = url
        self.token = token
        self.q = queue.Queue(maxsize=1)
        self._stop = threading.Event()

    def send(self, zone):
        """Non-blocking hand-off; coalesce by dropping the stale pending item."""
        try:
            self.q.put_nowait(zone)
        except queue.Full:
            try:
                self.q.get_nowait()      # drop the stale one
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(zone)
            except queue.Full:
                pass

    def run(self):
        while not self._stop.is_set():
            try:
                zone = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._post(zone)

    def _post(self, zone):
        data = json.dumps({"zone": zone}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-CANIS-Token"] = self.token
        req = urllib.request.Request(self.url, data=data, headers=headers)
        try:
            urllib.request.urlopen(req, timeout=1).read()
            print(f"posted zone={zone!r}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"POST rejected ({e.code}): bad/missing token (set --token).")
            else:
                print(f"POST HTTP error ({e.code}).")
        except urllib.error.URLError as e:
            print(f"POST failed ({e}). Is the dashboard running?")

    def stop(self):
        self._stop.set()


def is_sharp(gray):
    return cv2.Laplacian(gray, cv2.CV_64F).var() >= BLUR_THRESHOLD


def marker_side(corner, width):
    cx = float(corner.reshape(-1, 2)[:, 0].mean())
    if cx < width / 3:
        return "left"
    if cx > 2 * width / 3:
        return "right"
    return "center"


def zone_label(zone, side, directional):
    return f"{zone}-{side}" if directional else zone


# ---------------- Camera backends (with reopen support) [OPT-6b] ----------------
class PiCamSource:
    def __init__(self, size):
        self.size = size
        self._open()

    def _open(self):
        try:
            from picamera2 import Picamera2
        except ImportError as e:
            raise SystemExit(
                "picamera2 not found. Install it (sudo apt install -y python3-picamera2) "
                "or use --camera usb.") from e
        self.cam = Picamera2()
        cfg = self.cam.create_video_configuration(main={"size": self.size, "format": "RGB888"})
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(0.5)

    def read_gray(self):
        try:
            frame = self.cam.capture_array()
        except Exception:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

    def read_bgr(self):
        return cv2.cvtColor(self.cam.capture_array(), cv2.COLOR_RGB2BGR)

    def reopen(self):
        try:
            self.cam.stop()
        except Exception:
            pass
        self._open()

    def release(self):
        self.cam.stop()


class UsbCamSource:
    def __init__(self, index, size):
        self.index = index
        self.size = size
        self._open()

    def _open(self):
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open camera {self.index}. Is the USB webcam plugged in?")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])

    def read_gray(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    def read_bgr(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def reopen(self):
        try:
            self.cap.release()
        except Exception:
            pass
        self._open()

    def release(self):
        self.cap.release()


def _env_bool(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip() not in ("0", "false", "False", "no", "")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", choices=["pi", "usb"], default="pi",
                    help="camera backend (default: pi / Pi Camera Module)")
    ap.add_argument("--zone", default=os.environ.get("CANIS_ZONE", "kitchen"),
                    help="room this camera watches (env CANIS_ZONE)")
    ap.add_argument("--marker-ids", default=os.environ.get("CANIS_MARKER_IDS", "0"),
                    help="comma-separated ArUco IDs for the dog's marker (env CANIS_MARKER_IDS)")
    ap.add_argument("--url", default=os.environ.get("CANIS_DETECT_URL",
                                                    "http://127.0.0.1:5000/detect"),
                    help="dashboard /detect URL (env CANIS_DETECT_URL)")
    ap.add_argument("--token", default=os.environ.get("CANIS_TOKEN", ""),
                    help="shared secret for the dashboard (env CANIS_TOKEN)")
    dgroup = ap.add_mutually_exclusive_group()
    dgroup.add_argument("--directional", dest="directional", action="store_true")
    dgroup.add_argument("--no-directional", dest="directional", action="store_false")
    ap.set_defaults(directional=_env_bool("CANIS_DIRECTIONAL", True))
    ap.add_argument("--test", action="store_true",
                    help="send one fake detection and exit (no camera needed)")
    ap.add_argument("--side", choices=["left", "center", "right"], default="center",
                    help="side label to use in --test mode")
    ap.add_argument("--snap", action="store_true",
                    help="capture ONE frame, save it, report markers seen, and exit")
    return ap.parse_args()


def main():
    args = parse_args()
    marker_ids = {int(x) for x in args.marker_ids.split(",") if x.strip() != ""}
    poster = Poster(args.url, args.token.strip())

    if args.test:
        z = zone_label(args.zone, args.side, args.directional)
        poster._post(z)   # synchronous for the one-shot test
        return

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
                  f"(dog IDs {sorted(marker_ids)})")
        finally:
            src.release()
        return

    print(f"CANIS vision up [{args.camera}]. zone={args.zone!r}, marker IDs "
          f"{sorted(marker_ids)}, directional={args.directional}. Ctrl+C to stop.")

    poster.start()
    frame_i = 0
    last_post = 0.0
    read_fails = 0
    backoff = 0.1
    try:
        while True:
            gray = src.read_gray()
            if gray is None:
                # [OPT-6b] bounded backoff, then reconnect instead of tight-looping.
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
                continue
            read_fails = 0
            backoff = 0.1

            frame_i += 1
            if frame_i % PROCESS_EVERY:
                continue
            if not is_sharp(gray):
                continue

            corners, ids, _ = detector.detectMarkers(gray)
            if ids is None:
                continue
            flat = [int(i) for i in ids.flatten()]

            side = "center"
            found = False
            for marker_id, corner in zip(flat, corners):
                if marker_id in marker_ids:
                    found = True
                    side = marker_side(corner, gray.shape[1])
                    break

            if found:
                now = time.time()
                if now - last_post >= POST_EVERY_S:
                    z = zone_label(args.zone, side, args.directional)
                    poster.send(z)     # [OPT-6a] non-blocking hand-off
                    last_post = now
    except KeyboardInterrupt:
        pass
    finally:
        poster.stop()
        src.release()


if __name__ == "__main__":
    main()
