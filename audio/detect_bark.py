#!/usr/bin/env python3
"""
CANIS - bark detector (runs on the Pi, next to the Bluetooth microphone)
=========================================================================
Listens to the DJI Mic 3 (Bluetooth headset audio, 16 kHz mono) through PipeWire, decides when Shifu
has BARKED and when he has then been QUIET for QUIET_S seconds, and tells the dashboard:

    POST /bark {"event": "bark", "duration_s": .., "peak_db": ..}
    POST /bark {"event": "quiet", "barks": n}          -> the dashboard signals the ESP32 -> one treat
    POST /bark {"event": "mic_lost" | "mic_ok"}

The decision logic lives in bark_logic.py (pure, unit-tested). This file only does the I/O.

SAFETY - a dead microphone must never look like a quiet dog:
  * no audio for 3 s, or digital silence for DEAD_S, is reported as mic_lost and cancels any pending "quiet";
  * mic_lost is posted BEFORE anything else, and the dashboard refuses "quiet" while the mic is lost;
  * the script keeps trying to reconnect the Bluetooth link and re-find the audio source.

Uses only pw-record, wpctl, bluetoothctl and numpy (all already on the Pi; no apt, no sudo).

Run (on the Pi, from your own terminal):
    cd ~/canis && python3 audio/detect_bark.py --bt-mac <MIC_BLUETOOTH_MAC>
Try it without touching the dashboard:   python3 audio/detect_bark.py --dry-run
Logic self-test, no microphone needed:   python3 audio/detect_bark.py --selftest

    --dashboard / CANIS_BARK_URL   (default http://localhost:5000/bark)
    --token / CANIS_TOKEN
    --source   substring of the PipeWire source name (default "DJI Mic")
    --bt-mac   the microphone's Bluetooth address, for automatic reconnects (optional)
    --quiet-s  seconds of quiet after a bark before the treat signal (default 5)
    --rise-db  how far above the room noise a bark must be (default 35)
"""

import argparse
import json
import os
import re
import select
import subprocess
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bark_logic as B  # noqa: E402

RATE = 16000
FRAME_SAMPLES = int(RATE * B.FRAME_S)
FRAME_BYTES = FRAME_SAMPLES * 2
STALL_S = 3.0              # no audio for this long -> mic lost
DEAD_S = 2.0               # digital silence this long -> mic lost (a muted/off transmitter sends zeros); MUST stay < QUIET_S
DEAD_DB = B.DEAD_DB


# --------------------------------------------------------------------------- #
# audio stream parsing (pure, unit-testable)
# --------------------------------------------------------------------------- #
def parse_header(buf):
    """pw-record to stdout writes an AU/.snd container. Returns (data_offset, big_endian) once the header is
    complete, None if more bytes are needed, or (0, False) if there is no header (raw PCM)."""
    if len(buf) < 8:
        return None
    if buf[:4] == b"dns." or buf[:4] == b".snd":
        big = buf[:4] == b".snd"
        off = int.from_bytes(buf[4:8], "big" if big else "little")
        if len(buf) < off:
            return None
        return off, big
    return 0, False


def frame_db(frame_bytes, big_endian=False):
    x = np.frombuffer(frame_bytes, dtype=">i2" if big_endian else "<i2").astype(np.float32) / 32768.0
    return 20.0 * np.log10(float(np.sqrt(np.mean(x * x))) + 1e-9)


# --------------------------------------------------------------------------- #
# dashboard + system helpers
# --------------------------------------------------------------------------- #
class Poster:
    def __init__(self, url, token, dry):
        self.url, self.token, self.dry = url, token, dry

    def post(self, event, **fields):
        """Fire-and-forget with a short timeout. A stale 'quiet' is never queued: if the dashboard is down the
        treat signal is simply dropped."""
        body = dict(fields, event=event)
        if self.dry:
            print("   [dry-run] would POST", json.dumps(body))
            return True
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-CANIS-Token"] = self.token
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        try:
            urllib.request.urlopen(req, timeout=2).read()
            return True
        except Exception as e:
            print("   [post failed]", event, e)
            return False


def find_source(substr):
    """PipeWire node id of the microphone, or None."""
    try:
        out = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        m = re.search(r"(\d+)\.\s+(.*?)\s+\[vol", line)
        if m and substr.lower() in m.group(2).lower():
            return m.group(1)
    return None


def bt_connect(mac):
    try:
        subprocess.run(["bluetoothctl", "--timeout", "15", "connect", mac],
                       capture_output=True, text=True, timeout=25)
    except Exception:
        pass


class Capture:
    """pw-record -> 50 ms frames of 16 kHz mono s16."""

    def __init__(self, target):
        self.proc = subprocess.Popen(
            ["pw-record", "--target", str(target), "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf = b""
        self.big = False
        self.have_header = False
        self.last_data = time.time()

    def frames(self, timeout=1.0):
        """Yield zero or more complete frames; returns when no data arrives within `timeout`."""
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            return
        chunk = os.read(self.proc.stdout.fileno(), 8192)
        if not chunk:
            raise EOFError("pw-record ended")
        self.last_data = time.time()
        self.buf += chunk
        if not self.have_header:
            h = parse_header(self.buf)
            if h is None:
                return
            off, self.big = h
            self.buf = self.buf[off:]
            self.have_header = True
        while len(self.buf) >= FRAME_BYTES:
            yield self.buf[:FRAME_BYTES]
            self.buf = self.buf[FRAME_BYTES:]

    def stalled(self):
        return time.time() - self.last_data > STALL_S

    def close(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# main loop
# --------------------------------------------------------------------------- #
def run(args):
    poster = Poster(args.dashboard, args.token, args.dry_run)
    det = B.BarkDetector(rise_db=args.rise_db, quiet_s=args.quiet_s)
    assert DEAD_S < args.quiet_s, "DEAD_S must be shorter than the quiet time"
    mic_state = None            # "ok" / "lost" / None (never reported)
    t0 = time.time()
    last_bt_try = 0.0

    def set_mic(state):
        nonlocal mic_state
        if state != mic_state:
            mic_state = state
            print(f"[{time.time() - t0:7.1f}s] microphone {state.upper()}")
            poster.post("mic_ok" if state == "ok" else "mic_lost")
            if state == "lost":
                det.reset_episode()          # silence from a dead mic is not a quiet dog

    print(f"bark detector up: source~'{args.source}', quiet {args.quiet_s:g}s, rise {args.rise_db:g} dB, "
          f"dashboard {'(dry-run)' if args.dry_run else args.dashboard}")
    while True:
        if args.max_seconds and time.time() - t0 > args.max_seconds:
            return
        src = find_source(args.source)
        if src is None:
            set_mic("lost")
            if args.bt_mac and time.time() - last_bt_try > 10:
                last_bt_try = time.time()
                print("   microphone not found, trying Bluetooth reconnect")
                bt_connect(args.bt_mac)
            time.sleep(2)
            continue

        cap = Capture(src)
        dead_since = None
        try:
            while True:
                if args.max_seconds and time.time() - t0 > args.max_seconds:
                    return
                for fr in cap.frames(1.0):
                    now = time.time() - t0
                    db = frame_db(fr, cap.big)
                    if db <= DEAD_DB:
                        dead_since = dead_since or now
                    else:
                        dead_since = None
                    if dead_since is not None and now - dead_since >= DEAD_S:
                        set_mic("lost")                      # a muted or switched-off transmitter
                    elif dead_since is None:
                        set_mic("ok")
                    if mic_state != "ok":
                        continue
                    for ev in det.feed(float(db), now):
                        if ev[0] == "bark":
                            print(f"[{now:7.1f}s] BARK   lasts {ev[2]:.2f}s  peak {ev[3]:.1f} dBFS")
                            poster.post("bark", duration_s=round(ev[2], 2), peak_db=round(ev[3], 1))
                        else:
                            print(f"[{now:7.1f}s] QUIET after {ev[2]} bark(s)  -> treat signal")
                            poster.post("quiet", barks=ev[2])
                if cap.stalled():
                    raise EOFError("no audio for %.0f s" % STALL_S)
        except EOFError as e:
            print("   audio stream problem:", e)
        finally:
            cap.close()
        set_mic("lost")
        time.sleep(1)


# --------------------------------------------------------------------------- #
# self-test (no microphone, no dashboard)
# --------------------------------------------------------------------------- #
def selftest():
    # 1. container parsing: little-endian header, then PCM
    pcm = (np.full(FRAME_SAMPLES, 3277, dtype="<i2")).tobytes()          # ~ -20 dBFS
    hdr = b"dns." + (24).to_bytes(4, "little") + b"\xff" * 4 + (3).to_bytes(4, "little") \
        + (RATE).to_bytes(4, "little") + (1).to_bytes(4, "little")
    off, big = parse_header(hdr + pcm)
    assert (off, big) == (24, False), (off, big)
    assert parse_header(hdr[:6]) is None
    assert parse_header(b"\x00\x01" * 8) == (0, False)                   # raw PCM, no header
    d = frame_db((hdr + pcm)[off:off + FRAME_BYTES])
    assert -21 < d < -19, d
    be = np.full(FRAME_SAMPLES, 3277, dtype=">i2").tobytes()
    assert abs(frame_db(be, big_endian=True) - d) < 0.01

    # 2. decision logic on a synthetic scene: room, a bark, quiet
    det = B.BarkDetector()
    seq = [(-58.0, 3), (-5.0, 0.5), (-58.0, 8)]
    kinds, t = [], 0.0
    for level, secs in seq:
        for _ in range(int(round(secs / B.FRAME_S))):
            kinds += [e[0] for e in det.feed(level, t)]
            t += B.FRAME_S
    assert kinds == ["bark", "quiet"], kinds
    print("detect_bark selftest PASS")


def main():
    ap = argparse.ArgumentParser(description="CANIS bark detector")
    ap.add_argument("--dashboard", default=os.environ.get("CANIS_BARK_URL", "http://localhost:5000/bark"))
    ap.add_argument("--token", default=os.environ.get("CANIS_TOKEN", ""))
    ap.add_argument("--source", default="DJI Mic")
    ap.add_argument("--bt-mac", default=os.environ.get("CANIS_BARK_MIC_MAC", ""))
    ap.add_argument("--quiet-s", type=float, default=B.QUIET_S)
    ap.add_argument("--rise-db", type=float, default=B.RISE_DB)
    ap.add_argument("--dry-run", action="store_true", help="print events instead of POSTing them")
    ap.add_argument("--max-seconds", type=float, default=0, help="stop after this long (0 = run forever)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    try:
        run(args)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
