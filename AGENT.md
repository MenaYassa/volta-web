# AGENT BRIEF — Volta Local Controller (`volta-web/`)

Operational and architectural briefing for AI agents working in this repository.

---

## 1. System Mission & Current State

Volta is a production local-first controller, real-time telemetry engine, and multi-tenant web dashboard for Korean **TONLY MTTL-W01 (LG U+)** smart power strips.

- **Status**: **100% Fully Functional & Live in Production**.
- **Architecture**: **Dial-In Role Reversal**. The strips dial *outbound* to Volta over TCP port `10086`. No cloud accounts or subscriptions are used.
- **Active Fleet**: 6 strips connected continuously to the VPS daemon on port `10086`.

---

## 2. Core Architecture & Daemons

The system runs inside the Docker container named `volta` (`volta-local:latest`) defined in `docker-compose.yml`:

| Component | File | Port | Role |
|---|---|---|---|
| **Controller Daemon** | `controller.py` | `10086` | Asyncio TCP server. Handles native `up:` text wire protocol, session handshakes (`up:bootinfo`), polling loop (`up:getinfo:all`), outlet switching (`up:onoff`), and Wi-Fi diagnostics. |
| **Web API & Auth** | `server.py` | `8080` | Multi-tenant HTTP API and web server. Handles user token resolution, strip ownership scoping, relay dispatch, custom naming, schedules, and ntfy alerts. |
| **Analytics Engine** | `analytics.py` | — | Background collector thread (30s interval) logging power, voltage, temperature, and cumulative energy to SQLite (`analytics.db`). Powers interactive charts and the energy leaderboard. |
| **Frontend UI** | `index.html` | — | Single-page PWA dashboard. Tabs: Live Control, Analytics & Charts, Strips & Names, Schedules, Setup & Pairing, and Settings/Users. |

---

## 3. Data Storage & Persistence

Mounted into the container to survive rebuilds and restarts:
- **`strips.json`**: Stores custom strip/outlet names, schedules, alert settings, and multi-tenant user accounts (`users: { token: { name, strips, created_at } }`).
- **`analytics.db`**: SQLite database storing 30-second interval timeseries readings (`readings` table).
- **`.env`**: Master administrator token (`VOLTA_TOKEN` / `VOLTRA_TOKEN`).

---

## 4. Multi-Tenancy & Authorization Rules

All `/api/*` endpoints (except `/api/health`, `/api/nets`, and public static files) require authentication via `X-Token` header.
- **Admin**: Matches `VOLTA_TOKEN`. Can see, switch, name, and transfer all strips, as well as create/revoke tenant accounts.
- **Scoped User (`volta_usr_...`)**: Resolved via `strips.json` -> `users`. Endpoints automatically scope returned devices (`GET /api/live`), verify ownership before switching (`POST /api/onoff`), and restrict leaderboard/chart queries (`POST /api/analytics/*`).

---

## 5. Protocol Quick Reference (`up:` ASCII Protocol)

- **Connect**: `up:bootinfo:<model>;<mac>;<mac>;<fw>;connect`
- **Info/Telemetry**: `up:getinfo:all` -> responds with 4 channels of power (mW), energy (Wh hex), relay state, and temperature (°C).
- **Relay Toggle**: `up:onoff:<channel>:<on|off>` (channel `1`–`4`, or `0` for all).
- **Voltage**: `up:power_report:1:vol` -> `up:power_report:1:<mV>`.
- **Wi-Fi Signal**: `up:query:wifirssi` -> `up:query:<dBm>`.

---

## 6. Development & Safety Rules

1. **Syntax Integrity**:
   - Always run `python3 -m py_compile <file>` after editing Python files.
   - Always extract `<script>` and run `node -c <script>` after editing `index.html`.
2. **Container Lifecycle**:
   - Apply changes via `docker compose up -d --build` from `/home/ubuntu/volta-web`.
   - Never use `docker compose down` unless instructed (to avoid volume detach issues).
3. **Telemetry & DB Safety**:
   - Never drop or truncate `analytics.db` or delete `strips.json`.
4. **Secrets**:
   - Never hardcode or echo master tokens in scripts or git commits. Read from `.env`.
