# Hardware & Protocols Reference

This document summarizes the hardware reverse engineering and bus architecture of the **LG U+ / TONLY MTTL-W01** smart power strip.

---

## 1. Physical Architecture

The power strip contains three distinct circuit boards:

```
+-----------------------------------------------------------+
|                        MTTL-W01                           |
|                                                           |
|  +--------------------+        22-pin        +----------+ |
|  |    Control PCB     |=====================>| Power PCB| |
|  |   (RTL8711AF SoC)  |   Flat Ribbon Cable  | (Relays) | |
|  +--------------------+                      +----------+ |
|                                                    |      |
|                                         6-pin DC   |      |
|                                                    v      |
|                                              +----------+ |
|                                              | USB PCB  | |
|                                              | (5V DC)  | |
|                                              +----------+ |
+-----------------------------------------------------------+
```

### A. Control PCB
- **SoC**: Realtek **RTL8711AF** (ARM Cortex-M3 32-bit CPU, 802.11b/g/n Wi-Fi).
- **Firmware storage**: SPI flash with `RTKWin` bootloader (Image1/Image2).
- **Communication Buses**:
  - `I2C1` and `I2C2`: Connects to current sensing ICs on the power board.
  - `LED-I2C`: Connects to GPIO expander driving relay coils and LEDs.
  - `UART_LOG`: Serial console output at 38400 baud.

### B. Power PCB
- **Relays**: 4 × **HFE39** 277VAC / 20A dual-coil latching relays (one coil for ON pulse, one coil for OFF pulse).
- **Sensors**: 4 × **1809 BJY3** current-measuring ICs directly sensing AC lines for Outlets 1–4.
- **Relay Driver**: **T4118 3218** 24-pin GPIO expander at I2C address `0x54`.

### C. USB PCB
- Contains two USB-A charging ports and a DC step-down regulator.
- Receives raw DC power from the main power board via a 6-pin connector.
- **Note**: The USB rail does not have an inline current shunt or I2C data bus connection. It functions as an unmetered, always-on 5V auxiliary power supply.

---

## 2. Low-Level I2C Relay Control (Internal Bus)

For hardware modding (e.g. replacing the RTL8711AF with an ESP-01), the latching relays are triggered over the internal `LED-I2C` bus (`0x54` 7-bit / `0xA8` 8-bit). Each toggle requires an **activation pulse** followed by a **clear pulse**:

| Outlet | ON Pulse | ON Clear | OFF Pulse | OFF Clear |
|---|---|---|---|---|
| **Outlet 1** | `13 1A 1A 00 00` | `13 1A 06 00 00` | `13 2A 26 00 00` | `13 2A 0A 00 00` |
| **Outlet 2** | `13 26 0A 01 00` | `13 26 06 00 00` | `13 2A 06 02 00` | `13 2A 0A 00 00` |
| **Outlet 3** | `13 29 0A 04 00` | `13 29 06 00 00` | `13 2A 06 08 00` | `13 2A 0A 00 00` |
| **Outlet 4** | `13 2A 0A 10 00` | `13 2A 06 00 00` | `13 2A 06 20 00` | `13 2A 0A 00 00` |

## 3. Sensor Math & Hardware Offset Calibration

Reverse-engineering and empirical testing of the 1809 BJY3 current sensing ICs on the power board reveal key hardware characteristics:

- **Current Sensor Zero-Offset Baseline**:
  - The onboard current sensors output a baseline idle noise floor of approximately **0.019 A** (~4.5W ghost load) per channel even when no load is attached.
  - To eliminate ghost readings, production telemetry logic subtracts `0.019 A` from each active channel's calculated current and clamps negative values to `0.0 A`:
    $$\text{current\_a} = \max\left(\frac{P_W}{V} - 0.019, 0.0\right)$$
  - Channels that are powered OFF are clamped to `0.0 A` immediately.

- **Dual-Cycle Safety Debounce**:
  - Motor inrush currents, inductive compressor starts, and line noise frequently cause momentary microsecond/sub-second voltage or current sags and spikes.
  - While thermal overheating triggers instantaneous hardware cutoff on the first violation, voltage and overload guards enforce a **2-consecutive-cycle debounce** before tripping the safety latch, completely eliminating nuisance tripping.

- **Relay State NVRAM Flash Delay**:
  - The internal Realtek MCU commits relay states to non-volatile flash memory exactly **10 seconds** after the last switching command.
  - If power is cut within 10 seconds of a toggle, the strip reboots to its previous state upon recovery.

---

## 4. Alternative Operating Modes (Standalone Firmware)

For environments requiring 100% offline, local-only Home Assistant MQTT control without any central VPS dial-in, the open-source **`af950833/mttl_w01_standalone`** firmware (`comMTTL-W01_2.0.8.fwr`) can be flashed directly to the Realtek RTL8711AF:
- **Flashing Method**: Over Wi-Fi AP (`TONLY_TAP_...:30300`) using the OTA tool or via the device's web UI.
- **Embedded Web Server**: Strips run an internal HTTP web interface, SSE telemetry, and MQTT Home Assistant auto-discovery.
- **Trade-offs**: Standalone mode does not provide multi-user Google auth, long-term SQLite timeseries analytics, Alexa cloud integration, or global WAN dashboard routing without local port forwarding or VPNs. Volta combines the best of both worlds with server-side offset calibration and safety debouncing.

