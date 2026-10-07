# Volta ⚡

**Local-first controller, real-time web dashboard, and telemetry engine for Korean smart power strips (TONLY / LG U+ MTTL-W01).**

No cloud subscriptions. No Korean phone numbers. No vendor lock-in.

---

## Highlights

- **Direct Dial-In Architecture**: Strips dial outbound to your server over TCP port `10086`. No open incoming ports or router port forwards needed on your home network.
- **Fast Local Web Dashboard**: Control individual outlets or master switches in real time with instantaneous socket confirmation.
- **Full Electrical Telemetry**: Live power (W), cumulative energy (kWh), grid voltage (V), internal temperature (°C), and Wi-Fi signal (dBm).
- **Time-Series Analytics**: Interactive charts for aggregate house load, per-strip power, voltage fluctuations, temperature, and top energy-consuming outlets.
- **Dual Hardware Safety Guards**: Configurable Voltage Guard (sag/surge cutoff with automatic state restoration) and Temperature Guard (overheat cutoff with manual safety reset).
- **Accidental Switch Locks**: Server-persisted outlet locks (`🔒`) that protect critical equipment from accidental switching and exclude them from batch `ALL ON` and `ALL OFF` operations.
- **Custom Drag-and-Drop Organization**: Reorder power strips on desktop and mobile with automatic per-user cloud persistence.
- **Google Sign-In & Multi-Tenant User Isolation**: Native Google authentication, 1-strip free lifetime tier, scoped user accounts with restricted device visibility, subscription quotas, and multi-user device assignment.
- **Native Amazon Alexa Smart Home**: Control outlets and monitor power, voltage, and temperature via voice through Echo and Alexa apps.
- **Automated Provisioning & Mobile Pairing**: Seamless Android app setup wizard and one-shot Termux script that pairs strips and claims them automatically.
- **Location, Timezone & Solar Engine**: Select your country and city to automatically sync your local timezone and precise sunrise/sunset times for solar schedules and timers.
- **Instant Alerts**: Push notifications for power outages, strip dropouts, voltage sags/surges, and extreme temperature thresholds via [ntfy.sh](https://ntfy.sh).

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
│   server.py     (HTTP API, Multi-tenancy, Solar/Geo)   │
│   analytics.py  (Timeseries collector, SQLite store)   │
│   index.html    (Modern responsive web interface)      │
│                                                        │
│   Persistent Stores:                                   │
│    ├── strips.json   (Names, schedules, user accounts) │
│    └── analytics.db  (Historical sensor data)          │
└────────────────────────────────────────────────────────┘
            ▲
            │ HTTPS (Port 8080 or Reverse Proxy)
   [ Web Browser / Mobile App ]
```

---

## Quick Start (Docker)

### 1. Clone & Configure
```bash
git clone https://github.com/MenaYassa/volta.git
cd volta
```

Create a `.env` file with your master admin token:
```bash
echo "VOLTA_TOKEN=$(openssl rand -hex 16)" > .env
```

### 2. Start with Docker Compose
```bash
docker compose up -d --build
```
- Web UI & REST API: `http://localhost:8080` (or via Cloudflare Tunnel / reverse proxy)
- Strip TCP Dial-in: `0.0.0.0:10086`

### 3. Verify Health
```bash
curl -s http://localhost:8080/api/health
```
Expected output:
```json
{"ok": true, "controller": true, "auth_required": true, "auth": true}
```

---

## Documentation Index

Detailed engineering guides and specifications are organized in the [`docs/`](./docs) folder:

- **[Architecture, Protocol & Solar Engine (`docs/architecture.md`)](./docs/architecture.md)**: Deep dive into the `up:` text protocol, framing, geocoding, and solar calculations.
- **[Setup & Provisioning (`docs/setup-and-provisioning.md`)](./docs/setup-and-provisioning.md)**: Mobile pairing wizard, Termux script, Wi-Fi AP handshake, and claiming.
- **[Multi-Tenancy, Subscriptions & Security (`docs/multi-tenancy.md`)](./docs/multi-tenancy.md)**: User accounts, plan limits, duration stacking, expiration warnings, and multi-user device assignment.
- **[Analytics & Metrics Engine (`docs/analytics.md`)](./docs/analytics.md)**: Database schema, canonical KPIs summary endpoint, and leaderboard ranking.
- **[Hardware Reference & Modding (`docs/hardware-reference.md`)](./docs/hardware-reference.md)**: Teardown, RTL8711AF SoC, I2C relay codes, and USB rail details.
- **[Hardware Mod Guide (ESP-01) (`docs/hardware-mod/README.md`)](./docs/hardware-mod/README.md)**: Circuit wiring, firmware sketch, and pinout instructions for replacing a dead MCU with an ESP-01.
- **[Alexa Smart Home Skill Guide (`docs/alexa-integration-guide.md`)](./docs/alexa-integration-guide.md)**: Full AWS Lambda skill handler, device discovery, and voice control setup.
- **[Mobile App Developer Handoff — Phase 2 (`docs/app-developer-handoff.md`)](./docs/app-developer-handoff.md)**: Guide for Google Sign-In, Dual Safety Guards (Voltage & Temp), Quotas, Geocoding, and Alexa linking.
- **[Mobile App Developer Handoff — Phase 3 (`docs/app-developer-handoff-phase3.md`)](./docs/app-developer-handoff-phase3.md)**: Guide for per-user drag-and-drop strip reordering, server-side outlet locks (`🔒`), selective batch switching, and responsive telemetry HUD.
- **[Future Improvements Roadmap (`docs/future-plans-auth-licensing.md`)](./docs/future-plans-auth-licensing.md)**: Google Sign-In, 1-strip free lifetime tier, Admin Users & Licensing console, and Alexa Smart Home Skill integration.

---

## Hardware Compatibility

- **Device**: LG U+ / TONLY MTTL-W01 Smart Power Strip (Korean 4-Outlet + 2 USB).
- **SoC**: Realtek RTL8711AF (ARM Cortex-M3).
- **Firmware Tested**: `0.1.32-1.0.38`, `0.1.50-1.0.60`, `0.1.52-1.0.62`, `0.1.54-1.0.105`.
