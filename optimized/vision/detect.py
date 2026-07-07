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

  [OPT-12] Marker-loss GRACE PERIOD (vision_logic.DetectionGrace). ArUco loses
           the collar marker for a few frames whenever the dog turns his head;
           without a grace the posted detection flapped, and the ESP32's
           arousal saw-toothed — which could reward "leaving" at a dog that
           merely looked away. The last zone is now held for CANIS_VISION_GRACE_S
           (default 1.5 s) after the last real sighting. Deliberate trade-off:
           the genuine "dog left" clear (and thus the leaving-reward) is
           delayed by up to the same amount — keep it small.
  [OPT-13] Stale/FROZEN-frame guard (vision_logic.FrozenFrameGuard). A wedged
           camera driver can return the SAME buffer forever: reads "succeed",
           so the [OPT-6b] failure-reconnect never fires while the system
           watches a frozen photo of an empty room. Frames are fingerprinted
           (cheap subsample); too many identical frames in a row forces a
           camera reopen.
  [C6]     Blur-guard skip-ratio telemetry (vision_logic.BlurSkipMonitor). The
           blur gate (is_sharp) rejects low-variance frames, which at dusk means
           LOW LIGHT rejecting ~100% of frames — vision then silently contributes
           nothing (ENGINEERING_REVIEW §4.5). The skip ratio is now tracked over
           a rolling window (CANIS_BLUR_WINDOW_S) and an ALERT is logged when it
           exceeds CANIS_BLUR_ALERT_RATIO (default > 90% / 60 s), so a blackout
           is visible instead of silent.

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

Bring-up camera check [C7] (replaces the manual step in hardware/TESTING.md):
    python3 detect.py --selftest                 # PASS/FAIL on ArUco detection
    python3 detect.py --selftest --camera usb    # with concrete remediation tips
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

from vision_logic import (DetectionGrace, FrozenFrameGuard,  # [OPT-12/13] pure logic,
                          BlurSkipMonitor)                   # [C6] all unit-tested

# ---------------- Static defaults (rarely changed; still overridable) ----------------
CAM_INDEX      = int(os.environ.get("CANIS_CAM_INDEX", "0"))
FRAME_SIZE     = (640, 480)
ARUCO_DICT     = cv2.aruco.DICT_4X4_50
PROCESS_EVERY  = int(os.environ.get("CANIS_PROCESS_EVERY", "3"))
POST_EVERY_S   = float(os.environ.get("CANIS_POST_EVERY_S", "1.0"))
BLUR_THRESHOLD = float(os.environ.get("CANIS_BLUR_THRESHOLD", "40.0"))
READ_FAIL_MAX  = 10      # consecutive bad reads before we tear down + reconnect
RECONNECT_MAX_BACKOFF = 5.0
GRACE_S        = float(os.environ.get("CANIS_VISION_GRACE_S", "1.5"))   # [OPT-12]
FROZEN_MAX     = int(os.environ.get("CANIS_FROZEN_MAX", "30"))          # [OPT-13]
BLUR_WINDOW_S    = float(os.environ.get("CANIS_BLUR_WINDOW_S", "60.0"))   # [C6]
BLUR_ALERT_RATIO = float(os.environ.get("CANIS_BLUR_ALERT_RATIO", "0.9")) # [C6]


def frame_signature(gray):
    """Cheap fingerprint for the frozen-frame guard: a coarse subsample. Any
    live sensor has noise, so identical signatures ~never repeat on real video."""
    return gray[::32, ::32].tobytes()


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
    ap.add_argument("--selftest", action="store_true",
                    help="[C7] open the camera, grab a few frames, PASS/FAIL on ArUco "
                         "detection with remediation tips, and exit (bring-up check)")
    return ap.parse_args()


# ---------------- Commissioning self-test [C7] ----------------
SELFTEST_TIMEOUT_S = 5.0     # stop once we've looked this long
SELFTEST_MAX_FRAMES = 60     # ...or after this many frames, whichever comes first


def run_selftest(src, detector, marker_ids):
    """[C7] One-shot camera bring-up check. Replaces the manual camera step in
    hardware/TESTING.md (Test 5): grab a handful of frames, look for a dog marker,
    and print a clear PASS/FAIL with concrete remediation. Returns True on PASS."""
    print(f"[C7] vision self-test: looking for ArUco IDs {sorted(marker_ids)} "
          f"for up to {SELFTEST_TIMEOUT_S:.0f}s...")
    start = time.time()
    frames = 0
    sharp_frames = 0
    seen_any = set()          # every marker id seen (even non-dog)
    found = False
    while time.time() - start < SELFTEST_TIMEOUT_S and frames < SELFTEST_MAX_FRAMES:
        gray = src.read_gray()
        if gray is None:
            time.sleep(0.05)
            continue
        frames += 1
        if is_sharp(gray):
            sharp_frames += 1
        _, ids, _ = detector.detectMarkers(gray)
        if ids is not None:
            for i in ids.flatten():
                seen_any.add(int(i))
            if seen_any & marker_ids:
                found = True
                break

    print(f"[C7] captured {frames} frame(s); {sharp_frames} sharp; "
          f"markers seen: {sorted(seen_any) or 'none'}")

    if found:
        print("[C7] PASS — dog marker detected. Vision pipeline is good to go.")
        return True

    # FAIL: give the operator the most likely cause first.
    print("[C7] FAIL — no dog marker detected. Try, in order:")
    if frames == 0:
        print("      * No frames captured — check the camera cable/connection and "
              "that the right --camera backend (pi/usb) is selected.")
    elif sharp_frames == 0:
        print("      * Every frame was blurry/low-variance — check FOCUS and add "
              "LIGHTING (a dusk-dark room reads as blur and detects nothing).")
    elif seen_any and not (seen_any & marker_ids):
        print(f"      * Markers {sorted(seen_any)} were seen but none match the dog "
              f"IDs {sorted(marker_ids)} — pass the right --marker-ids.")
    else:
        print("      * Move the marker CLOSER and square-on to the camera.")
        print("      * Improve LIGHTING and check FOCUS.")
        print("      * Confirm the printed tag is the DICT_4X4_50 dictionary.")
    return False


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

    if args.selftest:
        try:
            ok = run_selftest(src, detector, marker_ids)   # [C7] bring-up camera check
        finally:
            src.release()
        raise SystemExit(0 if ok else 1)

    print(f"CANIS vision up [{args.camera}]. zone={args.zone!r}, marker IDs "
          f"{sorted(marker_ids)}, directional={args.directional}. Ctrl+C to stop.")

    poster.start()
    grace = DetectionGrace(GRACE_S)          # [OPT-12]
    frozen = FrozenFrameGuard(FROZEN_MAX)    # [OPT-13]
    blur_mon = BlurSkipMonitor(window_s=BLUR_WINDOW_S, alert_ratio=BLUR_ALERT_RATIO)  # [C6]
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
                    frozen.reset()
                continue
            read_fails = 0
            backoff = 0.1

            # [OPT-13] a "successful" read can still be a wedged driver replaying
            # one buffer forever; fingerprint it and reopen on a frozen streak.
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

            # [C6] Blur/low-light guard with skip-ratio telemetry. is_sharp()
            # rejects low-variance frames (motion blur OR low light); record the
            # skip so a dusk blackout is logged and alerted, not silent (§4.5).
            now = time.time()
            sharp = is_sharp(gray)
            alert = blur_mon.record(skipped=not sharp, now=now)
            if alert is not None:
                print(f"ALERT [blur-guard] {alert['ratio'] * 100:.0f}% of frames skipped "
                      f"for low sharpness over {alert['window_s']:.0f}s "
                      f"({alert['skipped']}/{alert['total']}) — low light? vision is "
                      f"contributing nothing (system on PIR + ultrasonic only).")
            if not sharp:
                continue

            corners, ids, _ = detector.detectMarkers(gray)

            side = "center"
            found = False
            if ids is not None:
                flat = [int(i) for i in ids.flatten()]
                for marker_id, corner in zip(flat, corners):
                    if marker_id in marker_ids:
                        found = True
                        side = marker_side(corner, gray.shape[1])
                        break

            # [OPT-12] real sighting -> its zone; brief loss -> held zone for
            # GRACE_S (bridges head-turns); past the grace -> None (real clear).
            z = grace.update(found,
                             zone_label(args.zone, side, args.directional) if found else None,
                             now)
            if z is not None and now - last_post >= POST_EVERY_S:
                poster.send(z)     # [OPT-6a] non-blocking hand-off
                last_post = now
    except KeyboardInterrupt:
        pass
    finally:
        poster.stop()
        src.release()


if __name__ == "__main__":
    main()
