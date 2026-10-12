# Known issues and limits

Last updated 11 October 2026. This is what I know is wrong or unfinished, grouped by where it bites. Where a number comes from a config value, the file and line are given.

## Welfare

- **Motion can frighten a dog who is afraid of moving objects.** Shifu is exactly that dog. The mitigations are in code: the robot never drives towards him (`CANIS_NEVER_APPROACH`, `canis_config.h:170`), mobile drive is off (`CANIS_MOBILE_ENABLED 0`, line 233), and I supervise every run. They reduce the risk. They do not remove it.
- **`PLAY` may over-excite him.** A robot that backs away from a dog can read as a chase. Watching for excitement, as well as fear, is on me.
- **The marker tone without a treat would weaken the conditioning.** The firmware goes silent when a treat jams or the hopper is empty, but the jam sensor is not installed, so a jam is not actually detected yet.
- **The `GUARD` buzzer cue could itself be unpleasant to him.** It is a short, mild tone. If I see a fear response to it, the cue gets redesigned.

## Drive and power

- **The two motors run at different speeds.** The left one is stiffer, and with no feedback the robot drifts and keeps turning. The encoders are wired but I have not measured their counts per turn.
- **The encoder and wheel settings still describe the old robot.** `CANIS_ENC_COUNTS_PER_REV` is 1320 (line 255), which assumes a different motor. `CANIS_WHEEL_DIAMETER_MM` is 65 and `CANIS_WHEEL_BASE_MM` is 140 (lines 257 and 258). The robot has 43 mm wheels and a 165 mm track. These must be corrected before mobile mode is ever enabled.
- **The closed-loop controller and safety filter have never run on the robot.** The code and its tests are written, and the whole path is switched off.
- **Motor load is unmeasured.** The N20 motors drive a robot of about 2 kg through the L298N, which drops roughly 2 V, from a 6 x AA pack. It rolls on a smooth floor. I have not measured current or checked it on carpet.
- **No rear sensor.** `PLAY` backs away from the dog without being able to see behind it.

## Sensors

- **The front ultrasonic is only lightly tested.** It is a JSN-SR04M-2, which needs a large, flat target and cannot see closer than roughly 20 to 25 cm. It gave one valid reading in six tries in my last check, and I have not yet compared it against a measured distance.
- **An HC-SR04 can hang with its echo line stuck high** after the ESP32 resets. The firmware detects this (`CANIS_US_HUNG_RUNS`, `canis_config.h:80`), treats that sensor as blocked and retries. Power-cycling clears it.
- **The ultrasonics are aimed for dog-sized targets.** Thin, soft or glass obstacles can be missed.

## Network and camera

- **WiFi is weak where the robot works** (about -72 to -84 dBm). It joins slowly, so the firmware uses a fixed IP address instead of DHCP. A closed door cut the camera stream from about 26 fps to about 2.5.
- **The ESP32-CAM is powered from the breadboard's 5 V rail.** I estimate the total draw at about half an amp, but I have not measured it.

## Software

- **The bark detector is tuned on one recording.** The threshold (`RISE_DB` in `audio/bark_logic.py`) came from a single session close to the microphone, so real use will need retuning. It runs when I start it by hand. There is no boot service yet.
- **A microphone that dies while a treat is pending is covered by unit tests, but I have not tried it on hardware.**
- **The firmware's config and the dashboard's config are two files kept in step by a test.** `canis_config.h` and `dashboard/canis_config.py` can drift. `tests/test_config_parity.py` catches it, but nothing prevents it.
- **Vision depends on the camera view.** The dog must be in frame, reasonably close, and not blurred.

## Hardware build

- **No enclosure.** The wiring is on a breadboard and the boards are held with zip ties and tape.
- **The servo is attached all the time**, so it can twitch when power changes. It twitched once when I switched the motor pack off.
- **The v4 dispenser CAD sealed the servo inside the housing.** A two-piece version is in `hardware/cad/dispenser_split.scad`, and on the finished robot the servo sits outside the housing.

## Study

- **The study has not started.** There are no results. The protocol is a draft, and it should be committed with a date before the first baseline session so it counts as pre-registration.
- **The baseline week has to run with the bark reward off** (`CANIS_BARK_REWARD_ENABLED 0`, `canis_config.h:197`). It is currently on.
- **It is one dog in one home with one owner.** Nothing from it generalises.
