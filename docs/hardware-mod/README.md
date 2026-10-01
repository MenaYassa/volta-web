# ESP-01 mod — local relay control for TONLY MTTL-W01

Method from Hackaday project 202043 ("4. Hacking" log), adapted to our
decoded frames (`docs-i2c-relay-codes.md`) and the `esp01_voltra.ino` sketch
in this folder, which exposes the outlets over HTTP for the gateway.

> ⚠️ **DANGER — MAINS VOLTAGE.** The power board carries 220V. Unplug the
> strip before opening or touching anything. Never touch components while
> plugged in. Work with dry hands, good light, and someone nearby. If you
> are not comfortable, stop — this is optional (cloud-token path exists).
> Used strips, no warranty — but your safety is worth more than a strip.

## Parts

- ESP-01 or ESP-01S (you have spares), 3.3V USB-serial adapter (NOT 5V).
- Fine wire, soldering iron, small cutter, multimeter (continuity mode).
- Optional: 470µF capacitor (3.3V rail stability), 2× 4k7 resistors.

## Which strip first

Use the spare/factory-reset one — NOT the Voltra-connected strip. Keep one
working strip untouched as fallback until the mod is proven.

## Steps

### 1. Flash the sketch FIRST (safe, on your desk)

1. Arduino IDE → install the **ESP8266 core** (Boards Manager).
2. Settings: board **Generic ESP8266 Module**, flash **1M (512K SPIFFS)**.
3. Check `WIFI_SSID` / `WIFI_PASS` at the top of `esp01_voltra.ino`.
4. Wire for flashing: `GPIO0→GND`, `CH_PD→3V3`, `VCC→3V3`, `GND→GND`,
   `TX→RX`, `RX→TX` on the 3.3V adapter. Plug in, upload.
5. Unplug, remove the GPIO0→GND jumper (run mode), power via USB-serial,
   join check: the module should appear on your Wi-Fi (`tonly-strip`).

### 2. Open the strip (UNPLUGGED)

Remove screws, open the housing. You will see three boards: Control (big
chip RTL8711AF, Wi-Fi), Power (4 relays HFE39, buttons, LEDs), USB (small).
A 22-pin flat cable links Control ↔ Power.

### 3. Isolate the LED-I2C bus (the one irreversible-ish cut)

On the flat cable, find the two lines `LED-SCL1` and `LED-SDA1` (author's
labels; verify with continuity mode against the test pads marked on the
Power board — see project photos). **Cut only those two conductors** (or
lift them at the connector), on the assumption you may bridge them back
later. Solder two wires to the **Power-board side** pads.

Why: the I2C bus allows ONE master. Cutting the SoC off this bus lets the
ESP-01 drive the relays without fighting it. Everything else (buttons,
LEDs, mains) keeps working; the stock SoC simply loses relay chatter.

### 4. Power + I2C for the ESP-01

- `ESP VCC → Control-board 3.3V rail` (near the SoC regulator output —
  verify 3.3V with the multimeter, strip UNPLUGGED can't be measured;
  measure with extreme care while powered ONLY if you must, hands clear).
  **3.3V only.** Add the 470µF cap across VCC/GND near the ESP if it
  brownouts/resets when relays click.
- `ESP GND → common ground`.
- `ESP GPIO0 → Power-board LED-SDA pad`, `ESP GPIO2 → LED-SCL pad`.

ESP-01 fits inside the housing (author-confirmed). Route wires clear of
mains traces, no exposed copper, close the housing before mains power.

### 5. Power up and test (hands off, housing CLOSED)

1. Plug in. The ESP joins home Wi-Fi (~5 s). Find `tonly-strip` in the
   router DHCP list (or scan port 80).
2. Open `http://<esp-ip>/` — press outlet buttons one by one, listening
   for relay clicks. If a relay doesn't move, recheck SDA/SCL (swapped?)
   and the cut (SoC still driving?).
3. In the volta-web gateway, add the strip with its **ESP IP** (section 2
   "Add manually" extended field) — ON/OFF buttons now drive it, no login.

## Troubleshooting

- ESP resets on click → power starvation: 470µF cap, shorter 3.3V wire.
- ESP boots into flash mode / spews garbage instead of running → GPIO0 is
  being held LOW at boot. It must float HIGH (the bus pull-ups do this once
  wired); check wiring, then power-cycle.
- No I2C response → SDA/SCL swapped, wrong pads, or SoC not isolated.
- Relay moves opposite → labels differ per unit; swap ON/OFF mapping.
- To revert: bridge the two cut conductors back (solder blob/wire).

## What NOT to do

- Never power the ESP-01 from 5V or the 18V rail.
- Never flash with GPIO0 floating issues — if upload fails, recheck
  GPIO0→GND and swap TX/RX.
- Never work inside with mains connected. Ever.
