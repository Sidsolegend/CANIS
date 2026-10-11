# CANIS — Problems We Ran Into, and How We Got Past Them

A build log, not a risk register: the actual problems hit while making CANIS, in
roughly the order they showed up, and what it took to get past each one. Some are
fully solved; a few are still open and are marked as such.

---

## Phase 1 — The firmware had a silent, dangerous bug hiding in it

The very first serious problem wasn't hardware — it was a bug in the state machine
that would only bite in exactly the situation the robot exists to handle safely.

**The bug:** the ESP32 decides "is the dog here?" partly from the Pi's vision feed,
latched in a variable (`visionActive`). That latch was only ever *cleared* by a
**successful** poll of the Pi that said "no dog." If WiFi dropped, or the Pi
crashed, or the router rebooted — the poll never came back cleanly, and the latch
just stayed **stuck on forever**. The buzzer would keep firing every 2 seconds, and
at higher exposure levels the robot would keep re-orienting toward a dog that might
have walked away an hour ago, triggered by nothing more than an ordinary router
hiccup. This was the worst thing the system could do, and it was reachable by the
single most common home-network failure there is.

**The fix:** a local timeout on the latch (`CANIS_VISION_TTL_MS`, 4 seconds) —
if no fresh sighting refreshes it in that window, it clears itself locally, no
network round-trip required. Tested directly (`VisionLatch` mirror in the test
suite) so it can't regress silently.

A second, related bug sat next to it: a single stalled loop tick (one slow HTTP
call) could make the elapsed-time calculation `dt` balloon to multiple seconds,
and the arousal math would add `rise-rate × dt` in one jump — enough to teleport
straight from calm to a full correction in one tick. Fixed by clamping `dt` to
0.2 s per tick, so a stall can only ever nudge arousal, never launch it.

## Phase 2 — The state machine chattered, and the robot could drive at the dog

Early on, a dog sitting right at a threshold (say, arousal hovering at exactly 70)
made the state flip back and forth every 20 ms — CORRECTION on, CORRECTION off, on,
off — re-firing the buzzer and incrementing intervention counts dozens of times for
one continuous event. **Fixed with hysteresis**: separate "enter" and "exit"
thresholds, so you have to climb clearly past one line to turn a state on and fall
clearly past a *lower* line to turn it off. No more chatter.

Worse: at the lowest "safe" exposure level (orient only, never approach), the
bearing logic could legitimately compute "FORWARD" as the direction to face the
dog — and the Arduino has no concept of "face" vs "drive at"; FORWARD is FORWARD.
That meant the one guarantee the whole project rested on ("never close distance on
a fearful dog below a deliberately-armed level") could be violated by the normal
bearing calculation, not a bug in some rare branch. **Fixed** by rewriting any
FORWARD bearing below the approach-enabled level into an in-place pivot instead.

## Phase 3 — The dispenser could count treats that never fell

The original dispenser logic counted a treat as "given" the moment the ESP32
*asked* for one — not when one actually dropped. A jammed gate or an empty hopper
would still ring the marker tone and still increment the reward count. For a
system whose entire mechanism is "sound predicts treat," a marker with nothing
behind it doesn't just waste a test run — it teaches the dog the marker is a lie.
**Fixed** two ways: the Arduino now confirms delivery back to the ESP32 (counted on
confirmed ACK, not on request), and a correlation id was added to every dispense
so a coincidental local button-press can't get miscredited to a pending automatic
reward. After two jams in a row the dispenser goes silent rather than keep beeping
over an empty hopper.

## Phase 4 — The CAD looked fine and didn't fit

Three earlier print revisions each had a real mechanical interference that only
showed up once printed: the treat chute ran straight through where the castor
wheel needed to sit; the funnel's sloped wall reached further than the shelf's
cutout accounted for and got clipped; the castor's spacer overlapped the deck's
skirt wall by 7 mm; and the hopper lid was modeled as a straight plug pushed into a
funnel that keeps narrowing — so it physically couldn't seat the last 2.5 mm. Every
one of these looked correct in a normal 3D preview and only failed on the actual
printed plastic — an expensive way to find a design bug (a full print cycle per
attempt).

**The fix that mattered more than any single patch:** instead of eyeballing a
preview, every part's real assembly position was checked by asking OpenSCAD to
compute the literal 3D intersection of two placed solids. Two parts that don't
touch intersect to nothing; two parts that overlap intersect to a solid you can
see and measure. That turned "does this fit" from a rendering judgment call into a
yes/no computation, and it's how every dimension claimed anywhere in this project
now gets checked before it's trusted — including everything below.

## Phase 5 — The metal chassis meant re-deriving the whole plan

Partway through, the plan changed: the printed deck wasn't going to happen (a
Meccano-style perforated metal chassis was available and simpler), and the shell
was shelved. That meant the mounting layout, wiring plan, and assembly order that
assumed a printed deck all had to be re-derived for a bare metal plate with holes
every 10 mm instead of the CAD's exact bolt patterns — the castor's 24 mm bolt
circle and the dispenser's 3 feet at 25 mm radius/120° don't land on a square grid
cleanly. Worked around with "bolt whichever foot lands on a real hole, zip-tie/tape
the rest" rather than pretending the grid and the CAD agree.

## Phase 6 — The dispenser you already printed had no way to put the servo in

This was the sharpest problem of the session. The dispenser housing is supposed to
have the SG90 servo sitting inside it — the build notes even said "drop the servo
in, shaft up" — but when the actual printed geometry was checked (rendering the
real bottom face, not just reading the source), there was **no opening large
enough for the servo body**. Only a 12 mm shaft-clearance hole existed; the servo's
23×12.5 mm body pocket was fully sealed on all sides. Someone had updated the 3D
model without ever adding a way to assemble it — invisible in a screen preview,
real the moment you had a printed part in hand and a servo that wouldn't go in.

Two fixes were built, in order:
1. **A 2-piece split** (base cup + snap-on funnel top) that lets the servo drop in
   before the two halves are pressed together — verified by actually measuring the
   exported STL files (bounding boxes, seated-intersection check) rather than
   trusting the source code by eye a second time. The first version of this split
   had its own bug — a wide bored joint that would have printed as an unsupported
   bridge — caught by rendering the piece in its actual print orientation before
   shipping it, not after.
2. Ultimately not used: the owner had already printed the sealed dispenser and
   didn't want to reprint, so the accepted fix is **cutting the opening by hand**
   with a hobby knife on the part already in hand. The split model still exists in
   the repo as a fallback.

A related problem surfaced during the fix and is still **open**: the SG90's output
shaft sits off-center from its own body by a few millimeters, but the housing was
modeled assuming a centered shaft. There's only ~1.5 mm of slack in the pocket, so
the flap may not fully cover the treat gate when "closed." The only real check is
physical — dry-fit the flap before gluing anything, and re-seat it a spline-tooth
over if it leaks.

## Phase 7 — The plan asked the robot to do something it structurally cannot do

The most important problem of the session wasn't technical — it was that the
stated goal ("robot notices Shifu doing something bad, and goes and fixes it")
doesn't match what any of this hardware can sense. A fixed forward camera and three
ultrasonics can tell you "something is close" or "a marker is in view." They cannot
tell you "the dog is chewing a shoe in the other room." Scavenging, toy
destruction, and barking (no microphone at all) are exactly the failure modes that
are invisible to this sensor set, no matter how the firmware is written.

Rather than build a feature that would look like it worked and quietly do nothing,
the actual root cause got named instead: those behaviours are boredom-driven, and a
robot that plays with the dog (treat-and-flee chase) or rewards him for settling
somewhere good (place-training) is something this exact hardware genuinely can do,
and it addresses the same underlying problem from the other side. The corrective
"guard a spot" mode was kept too, for the cases where there really is a fixed
off-limits place. Three modes shipped instead of one that overpromised.

## Phase 8 — "Roam and correct him" nearly reintroduced the thing the project was built to prevent

Once roaming was on the table, the original welfare promise — the robot never
closes distance on a fearful dog below a deliberately armed level — needed to be
re-guaranteed for an entirely new class of motion that didn't exist when that rule
was written (wandering, not just reacting in place). **Fixed** by making
"never move toward the dog" a single hard invariant enforced everywhere motion is
decided: the old approach behavior is compiled out entirely, guard-mode correction
is buzzer-only, and every dog-triggered movement in every mode is defined as a
retreat — turn away from whichever side the camera saw him on, or back straight off
if he's dead-center. It was easier to make this one rule apply everywhere than to
audit every motion path for exceptions after the fact.

## Phase 9 — Realizing the robot doesn't actually know where the dog is

Late in the session, a direct question ("how would it know where he is?") exposed
that "the robot detects the dog's direction" had quietly been leaning on the
ultrasonics for bearing — sensors that cannot tell a dog from a wall. Traced all
the way through: the *only* real signal for "which way is the dog" is where his
collar marker sits in the camera frame, split into left/center/right thirds, and
that only exists at all if the vision script is run with a `--directional` flag.
**Fixed** by rewriting the flee-direction logic to trust the camera's frame
position exclusively and stop reading the ultrasonics as if they knew anything
about the dog's location.

That, in turn, surfaced the still-**open** problem underneath: a small ArUco tag
on a dog is genuinely hard to detect at a useful range once he's moving (motion
blur is actively rejected by the vision pipeline as noise), and it's now the *only*
thing that tells the robot "the dog is here." The honest options — a much bigger
marker (impractical on a moving dog, decided against), or a wireless collar tag for
real distance/direction (UWB, a real hardware addition) — were laid out plainly
instead of pretending the small marker would just work. The decision for now: keep
the small marker and accept the miss rate, revisit if it's too unreliable in
practice.

---

## Phase 10: Rebuilding on encoder motors

In September I took the robot apart and rebuilt it, because the safety layer needs to know how far the wheels really turn, and the old motors had no encoders. The new ones are small N20 geared motors with encoders, on 43 mm wheels.

Almost nothing fitted the first time. The bracket screws were too thin for the nuts, so the motors are held with zip ties. The printed ball castor had a rusty steel ball and dragged, so I bought plastic castors and packed one up with a 1.5 cm block of wood to level the frame. The dispenser servo moved the wrong way at boot until I set the closed and open angles to 120 and 40 degrees. The buzzer measured 14.4 ohms, which is too low for an Arduino pin to drive directly, so it got a 220 ohm resistor.

The ESP32 sits on a breadboard that covers most of its pins, so the encoder wires could only go to a few of them. The left encoder uses D33 and D26. The right one uses VP and VN, which are input-only pins. The encoders take their 3.3 V from the Uno, because the ESP32's own 3V3 pin was out of reach.

## Phase 11: Sensors that lied

The left and right HC-SR04 sensors sometimes locked up with their echo line stuck high after the ESP32 reset. For a while it looked like my own firmware change had broken the right one. Comparing the two compiled binaries showed the code was identical, and the real cause was the sensor hanging. The firmware now detects a stuck echo line and counts that sensor as blocked, never as clear.

The front sensor, a JSN-SR04M-2, took longer. I checked its power (4.96 V), checked that its echo line idled low, tried the trigger on a different pin, and watched its LED. The fault turned out to be the echo wire, which was sitting in a bad row of the breadboard. Moving it to another row fixed it. I now use the trigger on D27, which moved the left encoder's second channel to D26.

## Phase 12: A camera on the robot, and weak WiFi

The ESP32-CAM streams video that the Pi reads. It worked straight away on its programmer board, but the robot's WiFi is weak (about -72 to -84 dBm), and a closed door dropped the stream from about 26 frames per second to about 2.5. The robot's ESP32 also took 20 to 30 seconds to join and often failed to get an address, so the firmware now uses a fixed IP.

## Phase 13: Treat when he stops barking

Shifu barks when he wants to play. I wanted the robot to give him a treat after he stops, which means it has to hear him. The robot's own ESP32 has no microphone, so the DJI Mic 3 connects to the Pi over Bluetooth instead. The detector measures loudness against the room's noise floor, calls a loud burst of the right length a bark, and fires after five seconds of quiet.

The important design decision was what a dead microphone looks like. A muted or disconnected microphone sends perfect silence, which would look exactly like a quiet dog and earn a treat. So digital silence never counts as quiet, a silent stream is reported as a lost microphone, and the dashboard refuses a "quiet" signal while the microphone is lost.

## Phase 14: The first drive without a laptop

With the 6 x AA pack running the motors and a small power bank running the electronics, I flashed a test build that boots straight into `PLAY` and put the robot on the floor. It sat still for the first minute, which is the sensor warm-up, and then roamed around without crashing. The motors run at different speeds, so it drifts. The dispenser servo twitched when I switched the motor pack off.

While preparing the encoder bring-up I also found that the firmware with mobile mode enabled did not compile at all: a variable was used before it was declared. That was a one-line fix, and both builds compile now. The mobile code itself has still never run on the robot.

## What is still open

The full list is in [KNOWN_ISSUES.md](KNOWN_ISSUES.md). The main items are measuring the encoders' counts per turn, correcting the wheel settings in the config, tuning the bark detector on more recordings, and running the study.
