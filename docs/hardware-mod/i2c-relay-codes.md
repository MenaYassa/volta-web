# I2C relay codes (hardware bus — NOT network)

Source: Hackaday project 202043, `I2C_Relay{1..4}_{ON,OFF}.csv`.
Bus: LED-I2C from the Control PCB to the Power PCB (GPIO expander at 7-bit
address 0x54 / 8-bit write 0xA8), driving the HFE39 latching-relay coils.

> These frames travel on the **internal flat cable inside the strip**.
> They cannot be sent over Wi-Fi. Using them requires the author's hardware
> mod: isolate the LED-I2C bus and drive it with an ESP-01 (see the
> project's "4. Hacking" log). Packet 2 in every file is bus filler.

Per relay: first pulse the coil (pkt 0), then clear (pkt 1).

| Relay | ON pulse              | ON clear          | OFF pulse               | OFF clear         |
|-------|-----------------------|-------------------|-------------------------|-------------------|
| 1     | `13 1A 1A 00 00`      | `13 1A 06 00 00`  | `13 2A 26 00 00`        | `13 2A 0A 00 00`  |
| 2     | `13 26 0A 01 00`      | `13 26 06 00 00`  | `13 2A 06 02 00`        | `13 2A 0A 00 00`  |
| 3     | `13 29 0A 04 00`      | `13 29 06 00 00`  | `13 2A 06 08 00`        | `13 2A 0A 00 00`  |
| 4     | `13 2A 09 10 00`      | `13 2A 05 00 00`  | `13 2A 06 20 00`        | `13 2A 0A 00 00`  |

(All bytes are I2C data writes to 0xA8; coil bit per relay: R1=none visible/
bank select, R2=0x01/0x02, R3=0x04/0x08, R4=0x10/0x20.)
