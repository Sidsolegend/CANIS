# CANIS vision — Raspberry Pi 5 camera setup

One-time setup to bring the Pi Camera Module online for `detect.py` (Phase 6).
Run these **on the Pi**. They escalate from "is the camera alive" to "is the full
detection pipeline working," so you always know which layer failed.

## 0. Connect the camera

- Plug the **CSI ribbon** into the camera and into one of the Pi 5's two **CAM**
  ports (not the DISP port). Contacts face the right way: on the Pi 5 the blue
  tab faces the USB/Ethernet side; on the camera the contacts face the lens away.
- Power off before plugging/unplugging the ribbon.

> The Pi Camera Module needs this CSI adapter ribbon because the Pi 5 uses the
> smaller connector — that's the cable that just arrived; it replaces the USB
> webcam stand-in.

## 1. Hardware sanity check (no Python — pure libcamera)

```bash
rpicam-hello --list-cameras       # should list your camera; empty = cable/seating issue
rpicam-still -o /tmp/cam.jpg -t 1000
```

Open `/tmp/cam.jpg`. If you get an image, the camera + cable + drivers are good.
If `--list-cameras` is empty, reseat the ribbon and check
`/boot/firmware/config.txt` has `camera_auto_detect=1`, then reboot.

## 2. Python dependencies

`picamera2` ships as a system (apt) package; OpenCV-with-ArUco comes from pip.
The clean way to get both in one place is a venv that can see the system packages:

```bash
sudo apt update
sudo apt install -y python3-picamera2          # libcamera Python bindings

cd ~/CANIS/vision                              # wherever the repo lives
python3 -m venv --system-site-packages .venv   # --system-site-packages = sees picamera2
source .venv/bin/activate
pip install opencv-contrib-python              # ArUco lives in contrib, NOT stock opencv
```

> Why `opencv-contrib-python` (not `opencv-python`): the ArUco marker detector is
> a contrib module. Stock OpenCV will `AttributeError` on `cv2.aruco`.

## 3. Pipeline sanity check (camera → OpenCV → ArUco, no dashboard)

```bash
python3 detect.py --snap
```

Captures one frame, saves `snapshot.jpg`, and prints the resolution + any marker
IDs it sees. Hold Shifu's marker (ID 0) in view and it should report
`markers seen: [0]`. This proves the whole vision pipeline before you wire in
the dashboard/ESP32.

## 4. Channel check (Pi → ESP32, no camera)

With the dashboard running (`python3 ../dashboard/app.py`):

```bash
python3 detect.py --test               # fake a centre detection
python3 detect.py --test --side left   # fake a left-side detection
```

Watch the ESP32 Serial Monitor — it should print `[VISION] dog in zone: ...`
and arousal should climb. A `-left`/`-right` suffix is what biases the orient.

## 5. Live run

```bash
python3 detect.py                # Pi Camera (default)
python3 detect.py --camera usb   # fall back to a UVC webcam if ever needed
```

## Marker for Shifu's collar

`DOG_MARKER_IDS = {0}`, dictionary `DICT_4X4_50`. Generate & print ID 0:

```bash
python3 - <<'PY'
import cv2
d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
cv2.imwrite("marker0.png", cv2.aruco.generateImageMarker(d, 0, 600))
print("wrote marker0.png — print it, keep the white border, mount flat on the collar")
PY
```

## Things to set per install

- **`ZONE`** in `detect.py` — the room this camera watches (e.g. `"kitchen"`).
- **`DIRECTIONAL`** — on by default; appends `-left/-center/-right` so the ESP32
  can bias which way it orients. Camera-frame left/right is only a rough hint —
  the robot's 3 ultrasonics are the authoritative bearing.
- Autostart on boot is a later step (a systemd unit like the dashboard's); run it
  by hand until the live test passes.
