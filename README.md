# Volta ⚡

**Local-first controller, real-time web dashboard, and telemetry engine for Korean smart power strips (TONLY / LG U+ MTTL-W01).**

No cloud subscriptions. No Korean phone numbers. No vendor lock-in.

---

## Highlights

- **Direct Dial-In Architecture**: Strips dial outbound to your server over TCP port `10086`. No open incoming ports needed on your home network.
- **Fast Local Web Dashboard**: Control individual outlets or master switch in real time with immediate socket confirmation.
- **Full Electrical Telemetry**: Live power (W), cumulative energy (kWh), grid voltage (V), current (A), internal temperature (°C), and Wi-Fi signal (dBm).
- **Time-Series Analytics**: Interactive charts for house load, per-strip power, voltage fluctuations, temperature, and top energy-consuming outlets.
- **Multi-Tenant User Isolation**: Create scoped user accounts with restricted visibility; users can only see and control the power strips assigned to them.
- **Automated Provisioning**: One-shot setup script for Android/Termux or CLI that pairs strips and claims them automatically.
- **Schedules & Offline Queuing**: Configure daily outlet schedules with timezone support. Commands issued while a strip is offline are queued and executed automatically upon reconnect.
- **Instant Alerts**: Push notifications for power outages, strip dropouts, and extreme temperature thresholds via [ntfy.sh](https://ntfy.sh).

---

## System Architecture

```
   [ MTTL-W01 Strips ]
            │
            │ Persistent TCP dial-out (Port 10086)
            ▼
┌────────────────────────────────────────────────────────┐
│  Volta Server (VPS or Local Machine)                   │
│                                                        │
│   controller.py (TCP daemon, ASCII protocol parser)    │
│   server.py     (HTTP API, Multi-tenancy, Auth)        │
│   analytics.py  (Timeseries collector, SQLite store)   │
│   index.html    (Modern responsive web interface)      │
│                                                        │
│   Persistent Stores:                                   │
│    ├── strips.json   (Names, schedules, user registry) │
│    └── analytics.db  (Historical sensor data)          │
└────────────────────────────────────────────────────────┘
            ▲
            │ HTTPS (Port 8080 or Reverse Proxy)
   [ Web Browser / Mobile ]
```

---

## Quick Start (Docker)

### 1. Clone & Configure
```bash
git clone https://github.com/your-repo/volta-web.git
cd volta-web
```

Create a `.env` file with your master admin token:
```bash
echo "VOLTA_TOKEN=$(openssl rand -hex 16)" > .env
```

### 2. Start with Docker Compose
```bash
docker compose up -d --build
```
- Web UI & REST API: `http://localhost:8080` (or your domain/reverse proxy)
- Strip TCP Dial-in: `0.0.0.0:10086`

### 3. Verify Health
```bash
curl -s http://localhost:8080/api/health
```
Expected output:
```json
{"ok": true, "controller": true, "auth": true}
```

---

## Documentation Index

Detailed engineering guides and specifications are organized in the [`docs/`](./docs) folder:

- **[Architecture & Protocol (`docs/architecture.md`)](./docs/architecture.md)**: Deep dive into the `up:` text protocol, framing, and command flows.
- **[Setup & Provisioning (`docs/setup-and-provisioning.md`)](./docs/setup-and-provisioning.md)**: Step-by-step pairing guide using Termux, Wi-Fi AP handshake, and claiming.
- **[Multi-Tenancy & Permissions (`docs/multi-tenancy.md`)](./docs/multi-tenancy.md)**: Managing user accounts, scoped tokens, and transferring strips.
- **[Analytics & Metrics Engine (`docs/analytics.md`)](./docs/analytics.md)**: Database schema, rolling downsampling, and leaderboard ranking.
- **[Hardware Reference & Modding (`docs/hardware-reference.md`)](./docs/hardware-reference.md)**: Teardown, RTL8711AF SoC, I2C relay codes, and USB rail details.

---

## Hardware Compatibility

- **Device**: LG U+ / TONLY MTTL-W01 Smart Power Strip (Korean 4-Outlet + 2 USB).
- **SoC**: Realtek RTL8711AF (ARM Cortex-M3).
- **Firmware Tested**: `0.1.32-1.0.38`, `0.1.50-1.0.60`, `0.1.52-1.0.62`, `0.1.54-1.0.105`.

---

## License & Credits

- Licensed under the MIT License.
- Reverse engineering and protocol rederivation informed by open community research (Hackaday project 202043 & powerk protocol notes).
