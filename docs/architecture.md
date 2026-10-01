# Architecture & Protocol

Volta eliminates reliance on vendor cloud infrastructure (LG U+ / OMMEQ) through **role reversal**: instead of the server polling strips on local LAN (where strips have zero open ports in station mode), the strips dial **outbound** to Volta over a persistent TCP socket.

```
+-------------------------------------------------------------+
|                     Volta Server (VPS / LAN)                |
|                                                             |
|  +--------------------+             +--------------------+  |
|  | Web UI & REST API  |<----------->|    controller.py   |  |
|  |     (Port 8080)    |             |    (Port 10086)    |  |
|  +--------------------+             +--------------------+  |
|            |                                   ^            |
|            v                                   |            |
|     analytics.db                               | (TCP)      |
|    (SQLite Engine)                             |            |
+------------------------------------------------|------------+
                                                 |
                   Persistent Dial-Out Connection|
                                                 |
                                    +--------------------+
                                    | MTTL-W01 Strips    |
                                    | (RTL8711AF SoC)    |
                                    +--------------------+
```

---

## 1. Network Topology & Ports

| Port | Protocol | Direction | Purpose |
|---|---|---|---|
| **10086** | ASCII (`up:`) | Strip -> Volta | Persistent TCP connection. Carries telemetry, status queries, and outlet switching. |
| **8080** | HTTP / JSON | Browser / Client -> Volta | Web interface and REST API for telemetry, switches, analytics, schedules, and admin. |
| **30300** | ASCII (`up:`) | Client -> Strip | **AP Mode Only** (`192.168.1.1`). Used strictly during the initial setup/pairing process. |

---

## 2. Strip Protocol (`up:` Wire Protocol)

The MTTL-W01 communicates using a plain ASCII text protocol separated by colons and semicolons, terminated with `\r\n`.

### A. Handshake (`up:bootinfo`)
When the strip boots or reconnects, it initiates the session by sending:
```text
up:bootinfo:<model>;<mac>;<mac>;<firmware>;connect
```
*Example:*
```text
up:bootinfo:lgutap;88D039132171;88D039132171;0.1.50-1.0.60;connect
```
The server registers the socket session, touches the device timestamp, and drains any offline queued commands.

### B. Status & Telemetry (`up:getinfo`)
The server polls the strip with:
```text
up:getinfo:all
```
The strip responds with a multi-channel payload (channels 1 to 4):
```text
up:getinfo:1:<runtime>;<relay>;<state>;<ovl>;<ovh>;<power_mW>;<energy_hex_Wh>;<prev>;<cfg>;<status>;<event>;<temp_C>:2:...:3:...:4:...
```
*Fields:*
- `relay`: `on` or `off` (relay contact position).
- `power_mW`: Active load in milliwatts (`3500` = 3.5 W).
- `energy_hex_Wh`: Hex-encoded cumulative energy in Watt-hours (`0000001F` = 31 Wh = 0.031 kWh).
- `temp_C`: Temperature in Celsius from the internal board thermistor.

### C. Outlet Switching (`up:onoff`)
To toggle an outlet, the server sends:
```text
up:onoff:<outlet_num>:<on|off>
```
- `<outlet_num>`: `1` to `4` (or `0` for master all-outlets toggle).
- The strip acknowledges: `up:onoff:<outlet_num>:<on|off>`.

### D. Diagnostics (`up:power_report` & `up:query`)
- **Voltage measurement**: Server sends `up:power_report:1:vol`. The strip returns `up:power_report:1:<millivolts>` (e.g. `238400` = 238.4 V).
- **Wi-Fi Signal**: Server sends `up:query:wifirssi`. The strip returns `up:query:<rssi_dBm>` (e.g. `-65`).
