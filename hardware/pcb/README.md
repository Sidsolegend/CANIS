# CANIS main board (v1, untested)

One 100 x 90 mm, 2-layer, all through-hole board that replaces the Arduino Uno, the breadboard and the loose wiring. The L298N module stays off-board and is wired to it. The ESP32 DevKit plugs into two 1x15 sockets. Everything else is on the board.

Status: DRC clean (0 errors, 0 unconnected, only silkscreen-overlap warnings) in KiCad, and `check_netlist.py` passes. **Never fabricated or powered.** Treat the checklist below as required before ordering.

## What is on it

| Block | Parts | Notes |
|---|---|---|
| Logic | ATmega328P-PU in a DIP-28 socket, 16 MHz crystal, 2 x 22p, 10k reset, reset button | Same pins as the Uno, so `Treat_Dispenser_CANIS.ino` runs unchanged (D2 button, D3/D4/D7/D8/D12/D5 motors, D6 buzzer, D9 servo, D10/D11 link, A0 beam) |
| Motors | 1x6 header (ENA IN1 IN2 IN3 IN4 ENB, same order as the module's own header) and a 2-pin screw terminal (12V, GND) for your existing red L298N module | Use female-female jumpers for the 6 control wires. Motor wires go straight to the module's own OUT terminals. Leave the module's 5V jumper on; its 5V terminal is not connected to this board |
| Power | LM2596T-5.0 buck + 33uH + 1N5822 + 470u in/out, 1N5822 reverse-polarity diode on the battery | Battery in goes to the L298 (VMOT) and the buck. 1N5819 from 5V to ESP32 VIN so USB cannot back-feed the buck |
| Sensors | 3 x 4-pin (5V, TRIG, ECHO, GND) with 1k series on each ECHO, 2 x 4-pin encoder (3V3, GND, A, B) with 10k pull-ups, I2C header | Pin map is `canis_config.h` and the `.ino` files, not the old WIRING.md |
| Outputs | servo (3-pin), buzzer (2-pin with 220R), button (2-pin), beam (3-pin), ESP32-CAM 5V/GND | |
| Programming | ICSP 2x3, UART 4-pin (GND, DTR, TX, RX) with auto-reset cap | |
| Link | ESP32 GPIO13 to ATmega D10 direct; ATmega D11 to ESP32 GPIO4 through 1k + 2.2k divider | This is the dispense-ACK return line, so `CANIS_USE_DISPENSE_ACK` can be 1 on this board |

ESP32 pins: US trig/echo 27/25 (centre), 32/34 (left), 14/35 (right); encoders L 33/26, R 36/39; link 13 and 4; I2C 21/22.

## Before you order (check these)

1. **DevKit pinout and row spacing.** The board assumes a 30-pin DevKit V1 with rows 25.4 mm apart and USB at the top edge of the board. Measure yours with calipers (`ESP_AX`/`ESP_BX` in `gen_pcb.py`) and check the pin order against its silkscreen.
2. **L298N module header order.** The 6-pin control header is wired ENA, IN1, IN2, IN3, IN4, ENB, top to bottom as on the usual red module. Check yours against its silkscreen. Print `canis_main_testprint_1to1.pdf` at 100% and drop the real parts on it (LM2596, terminal blocks, crystal, DevKit) before paying for boards.
3. **LM2596 pinout** is the standard TO-220-5 (1 Vin, 2 Out, 3 GND, 4 FB, 5 ON/OFF to GND). Set nothing, it is the fixed 5.0 V part. Measure 5 V on the board with nothing plugged in before adding the ESP32 or the ATmega.
4. **Motor drop.** The L298N module still drops about 2 V, plus 0.4 V in the reverse diode, so the N20 motors see roughly 7 V from a 9.9 V pack. Same as the module today, minus the diode.
5. **Antenna.** There is a copper keep-out under the DevKit's antenna end. It does not fix the weak WiFi (-72 to -84 dBm in the notes).

## Bring-up order

1. Solder power only (J3, D1, C1, C3, U2, D2, L1, C2). Battery in, meter the 5V rail. Mind the polarity: the lower terminal of J3 is +.
2. Add the ATmega socket parts, crystal, caps, reset. The chip needs the Uno bootloader once: burn it over ICSP (a USBasp or another Uno as ISP), or buy it pre-flashed. After that the UART header uploads the sketch with auto-reset. Do not power the board from the programmer's 5V at the same time as the battery.
3. Add the headers and terminals, then wire the L298N module. Plug the DevKit in last.

GND pads are connected solid to the pour (thermal spokes kept landing on islands), so use a hot iron and flux.

## Files

- `gen_pcb.py` builds the board; `route.py` autoroutes with Freerouting (jar not in the repo, Java 21+) and pours GND; `build.sh` regenerates and renders.
- `check_netlist.py` re-checks the board's pins against the firmware sources (run it after any change).
- `canis_main.kicad_pcb`, `canis_main.kicad_pro`: open in KiCad. There is no schematic file; the netlist lives in `gen_pcb.py` (net names are the signal names above).
- `gerbers/` and `canis_main_gerbers.zip`: fab files (Gerber + Excellon).
- `canis_main_bom.csv`, `canis_main_preview.png`, `canis_main_testprint_1to1.pdf`.
