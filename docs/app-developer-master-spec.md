# Volta Mobile App Developer Master Specification — Consolidated Handoff (Phases 1–4) 📱⚡

**Document Version:** 4.0 (Unified Master Architecture)  
**Target Clients:** Volta Native Mobile App (Android / iOS / Kotlin Multiplatform / Flutter / React Native)  
**Host Target:** `https://volta.your-domain.com` (or user-configured server URL)  
**Controller TCP Port:** `10086`  
**Strip AP Pairing Port:** `192.168.1.1:30300`  
**Date:** October 2026  

---

## Executive Summary & Scope

This unified master document consolidates **all 4 implementation phases** of the Volta ecosystem into a single, cohesive specification for mobile app developers.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       VOLTA MOBILE MASTER ARCHITECTURE                      │
├──────────────────────┬──────────────────────┬───────────────────────────────┤
│ 1. Identity & Auth   │ 2. Telemetry & Safety│ 3. Control, Locks & Ordering  │
│  • Google Sign-In    │  • 2.5s Safety Loop  │  • Per-User Drag-and-Drop     │
│  • Token Fallback    │  • Voltage Guard     │  • Accidental Outlet Locks    │
│  • Multi-Tenancy     │  • Temp Cutoff (Fire)│  • Selective Batch ALL ON/OFF │
│  • Quota Gating      │  • Timeseries Charts │  • UI Status Accent Language  │
├──────────────────────┴──────────────────────┴───────────────────────────────┤
│ 4. Hardware Provisioning & Strip Lifecycle                                  │
│  • 4-Phase SoftAP Wi-Fi Pairing (192.168.1.1:30300)                          │
│  • Automatic MAC Query & Destination Server Injection                       │
│  • Claiming, Unclaiming (Tenant Release) & Admin Deletion Tombstoning        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# SECTION 1: Identity, Authentication & Multi-Tenancy

### 1.1 Sign-In Architecture
The app supports two sign-in methods:
1. **Google Identity (Primary)**: Uses Google Credential Manager (Android) / Google Sign-In (iOS).
2. **Volta Token (Fallback / Developer)**: Manual entry of master token (`VOLTA_TOKEN`) or scoped user token (`volta_usr_...`).

#### Authentication Flow
1. Fetch Google ID token (`credential`) from the device.
2. Exchange with backend:
   ```http
   POST /api/auth/google
   Content-Type: application/json

   {
     "credential": "<GOOGLE_ID_TOKEN_JWT>"
   }
   ```
3. Backend returns session token, profile, and subscription quotas:
   ```json
   {
     "ok": true,
     "token": "volta_usr_269b58bd35d783c9",
     "user": {
       "name": "Mina Yassa",
       "email": "eng.menamedhat@gmail.com",
       "picture": "https://lh3.googleusercontent.com/...",
       "strips": ["6C5AB554FED3", "88D039132171"],
       "max_strips": 10,
       "status": "active",
       "expires_at": null
     }
   }
   ```
4. Save `token` securely in `EncryptedSharedPreferences` / Keychain (`SecureTokenManager`).
5. Include token on all subsequent requests:
   - Header: `Authorization: Bearer <TOKEN>` or `X-Token: <TOKEN>`.

### 1.2 User Profile & Session Heartbeat (`GET /api/auth/whoami`)
Call on app launch and network resume:
```http
GET /api/auth/whoami
Authorization: Bearer <TOKEN>
```
Response:
```json
{
  "authorized": true,
  "is_admin": false,
  "name": "Mina Yassa",
  "email": "eng.menamedhat@gmail.com",
  "picture": "https://lh3.googleusercontent.com/...",
  "strips": ["6C5AB554FED3", "88D039132171"],
  "max_strips": 10,
  "status": "active",
  "expires_at": null
}
```

#### Subscription Quotas & Account Expiry Rules:
- If `status == "expired"` or `status == "canceled"`:
  - Backend returns HTTP `401 Unauthorized` with `{"subscription_expired": true}`.
  - App must present a persistent **Account Locked / Expired** screen blocking strip control with a button to contact support/admin.
- If user attempts to claim a strip exceeding `max_strips`:
  - Backend returns HTTP `403 Forbidden` (`"Subscription strip limit reached (X/Y). Please upgrade your plan."`).
  - App displays an upgrade modal.

---

# SECTION 2: Live Control, Outlet Locks & Reordering

### 2.1 Live Snapshot (`GET /api/live` & `GET /api/devices`)
The backend returns currently connected strips and known claimed offline strips with their last known telemetry, automatically sorted according to the user's saved layout.

```http
GET /api/live
Authorization: Bearer <TOKEN>
```
```json
{
  "devices": [
    {
      "mac": "88D039132171",
      "name": "Living Room Strip",
      "model": "lgutap",
      "fw": "0.1.50-1.0.60",
      "online": true,
      "voltage_v": 236.5,
      "power_w": 172.6,
      "current_a": 0.693,
      "energy_kwh": 6.34,
      "rssi": -65,
      "locked_outlets": [2],
      "voltage_guard": {
        "enabled": true,
        "min_v": 195.0,
        "max_v": 250.0,
        "tripped": false,
        "temp_enabled": true,
        "max_temp_c": 65.0,
        "temp_tripped": false
      },
      "outlets": [
        {
          "n": 1,
          "name": "TV OLED",
          "on": true,
          "power_w": 64.4,
          "current_a": 0.253,
          "energy_kwh": 1.84,
          "temp_c": 28,
          "event_code": "00",
          "event_desc": "Normal / OK",
          "overload_ok": true,
          "overheat_ok": true,
          "locked": false
        },
        {
          "n": 2,
          "name": "NAS Storage",
          "on": true,
          "power_w": 35.1,
          "current_a": 0.129,
          "energy_kwh": 0.83,
          "temp_c": 30,
          "event_code": "00",
          "event_desc": "Normal / OK",
          "overload_ok": true,
          "overheat_ok": true,
          "locked": true
        }
      ]
    }
  ],
  "strip_order": ["88D039132171", "6C5AB554FED3"]
}
```

### 2.2 Drag-and-Drop Strip Layout Reordering
- **UI Interaction**: Vertical drag handle (`⠿` icon) on the strip card header.
- **Polling Safety Rule**: While user is actively dragging (`isDragging == true`), **pause background live polling** to prevent jumping or visual glitching.
- **Persistence Trigger**: On drag release, immediately persist the new list:
  ```http
  POST /api/strip/order
  Authorization: Bearer <TOKEN>
  Content-Type: application/json

  {
    "order": ["88D039132171", "6C5AB554FED3", "88D03943BAA4"]
  }
  ```

### 2.3 Accidental Switch Locks (`🔒` / `🔓`)
To protect sensitive appliances (refrigerators, servers, 3D printers, routers) from accidental switching or batch shutoffs:
1. **Toggle Lock Endpoint**:
   ```http
   POST /api/outlet/lock
   Authorization: Bearer <TOKEN>
   Content-Type: application/json

   {
     "mac": "88D039132171",
     "outlet": 2,
     "locked": true
   }
   ```
   *(Omitting `"locked"` toggles current state).*
2. **Client Switch Behavior**:
   - When `outlet.locked == true`, disable the toggle switch button in the UI (`enabled = false`) and display a persistent `🔒` badge.
   - If user taps the disabled switch, prompt: `"Outlet is locked to prevent accidental switching. Tap the 🔒 icon to unlock first."`
3. **Backend Enforcement**:
   - Calling `POST /api/onoff` on a locked outlet triggers HTTP `409 Conflict`:
     `{"error": "outlet 2 is locked against switching"}`.

### 2.4 Physical Strip Enclosure & Master Power Button (`⏻`)
The dashboard visual enclosure accurately mirrors the physical MTTL smart strip:
1. **Master Power Button (`⏻`)**:
   - Located on the far-left of the enclosure, beside the 4 socket buttons and separated by a subtle divider.
   - **Visual Feedback**:
     - **Green (`all-on`)**: When all controllable (unlocked) outlets are ON.
     - **Amber (`partial-on`)**: When some controllable outlets are ON and some are OFF.
     - **Red/Muted (`all-off`)**: When all controllable outlets are OFF.
   - **One-Tap Master Toggle**:
     - Evaluates **unlocked (switchable)** outlets so locked-ON outlets do not deadlock the master switch.
     - If any switchable outlet is ON $\rightarrow$ clicking master turns all switchable outlets OFF.
     - If all switchable outlets are OFF $\rightarrow$ clicking master turns all switchable outlets ON.
     - Never flips locked outlets (`🔒`).
2. **Physical Socket Buttons & Lock Badges**:
   - Outlets 1–4 are displayed as circular tactile buttons (`⚡`).
   - Tapping an outlet socket button directly toggles that channel.
   - When locked, a compact `🔒` badge anchors to the socket button, and its border turns into a dashed warning style with safety prompt guard.

### 2.5 Selective Batch Switching
- Master toggle and batch commands must **never switch locked outlets**.
- When user invokes batch toggle:
  1. Filter out locked outlets: `unlocked = outlets.filter(o => !o.locked)`.
  2. If all outlets are locked, abort and notify: `"All outlets on this strip are locked 🔒."`
  3. Send `POST /api/onoff` targeting only unlocked channels:
     ```http
     POST /api/onoff
     Authorization: Bearer <TOKEN>
     Content-Type: application/json

     {
       "mac": "88D039132171",
       "channels": [1, 3, 4],
       "on": false
     }
     ```

### 2.6 Visual Language & Status HUD
Maintain 1:1 visual continuity with the web dashboard:
- **Strip Header Status LED Dot**:
  - `Offline`: Solid Red (`#EF4444`).
  - `Online (0 outlets ON)`: Muted Gray (`#94A3B8`).
  - `Online (1–3 outlets ON)`: Vibrant Light Green (`#4ADE80`).
  - `Online (All 4 outlets ON)`: Shiny Glowing Green (`#22C55E` with subtle drop-shadow).
- **Physical Master Power Button**:
  - Highlights according to switchable outlet state (Green / Amber / Red-Muted).
- **Strip Long-Press Shortcut Drawer / Context Menu**:
  - Triggered on mobile by a `450ms` press-and-hold on the strip enclosure card (with optional haptic vibration feedback `40ms`).
  - Presents quick actions without cluttering the main screen:
    - `🏠 Home & Properties`: View and reassign the strip to a specific Home / House (`POST /api/homes/assign`).
    - `ℹ️ Hardware Info & Diagnostics`: Model, Firmware version, IP address, Wi-Fi RSSI (dBm), and Grid Voltage.
    - `🛡️ Safety Guard Settings`: Directly opens the Voltage & Temperature Guard configuration.
    - `🔄 Reboot Physical Strip`: Quick hardware restart trigger (`POST /api/reboot`).
- **Header Utilities**:
  - `🔄 Reboot`: Header utility button sending `POST /api/reboot` with prompt confirmation.
  - `🗑 Delete / Unclaim`: Header trash button to release or remove strip.
- **Outlet Specs Row**:
  - Always render formatted specs consistently:
    `[STATE] • [POWER W] • [CURRENT A] • [ENERGY kWh] • [TEMP °C]`
    (e.g., `ON • 103.71 W • 0.43 A • 1.856 kWh • 28°C`).
  - If `overload_ok == false` or `overheat_ok == false`, display a warning tag (`⚠️ OVERLOAD` / `⚠️ OVERHEAT`).

---

# SECTION 3: Dual Hardware Safety Guard Engine

The server runs a real-time 2.5-second protection loop for Voltage and Thermal limits.

```
┌────────────────────────────────────────────────────────┐
│             DUAL SAFETY GUARD ARCHITECTURE             │
├───────────────────────────┬────────────────────────────┤
│ Voltage Guard (Sag/Surge) │ Temperature Guard (Fire)   │
│  • Auto-cutoff if V < min │  • Emergency cut if T > max│
│    or V > max             │  • LATCHING (MANUAL RESET) │
│  • AUTO-RECOVERY with     │  • Never restores power    │
│    safe delay timer (min) │    automatically!          │
└───────────────────────────┴────────────────────────────┘
```

### 3.1 Safety Guard Configuration API
```http
POST /api/voltage-guard
Authorization: Bearer <TOKEN>
Content-Type: application/json

{
  "mac": "88D039132171",
  "enabled": true,
  "min_v": 195.0,
  "max_v": 250.0,
  "safe_delay_min": 3,
  "temp_enabled": true,
  "max_temp_c": 65.0
}
```

### 3.2 Safety Guard Trip States & Manual Thermal Reset
- **Voltage Sag/Surge**:
  - Trips automatically. When grid voltage returns to safe range (`min_v <= V <= max_v`) and stays stable for `safe_delay_min` minutes, the server automatically restores previously ON outlets.
- **Temperature Overheat (Fire Safety)**:
  - If any outlet sensor exceeds `max_temp_c` (default: 65°C), all 4 outlets cut off instantly.
  - **Latching rule**: Auto-recovery is strictly blocked for thermal events.
  - The UI must display a prominent Red Alert banner: `"🚨 Thermal Trip: Overheat (72°C on Outlet 1). Manual Reset Required."`
  - To clear a thermal trip after cooling down, user must tap **"Reset Safety Trip"**:
    ```http
    POST /api/voltage-guard/reset
    Authorization: Bearer <TOKEN>
    Content-Type: application/json

    {
      "mac": "88D039132171"
    }
    ```

---

# SECTION 4: Hardware Provisioning & Strip Lifecycle (Phase 4)

### 4.1 Realtek RTL8711AF Hardware Quirks
1. **The 10-Second Physical Button Reset**:
   - Holding the button for 10 seconds wipes **both** home Wi-Fi and server IP from flash memory.
   - The strip resets to SoftAP mode: SSID `TONLY_TAP_<SUFFIX>` (password `LGU_<SUFFIX>`).
   - The strip's internal network stack listens on **`192.168.1.1:30300`**.
2. **Mandatory Dual-Command Payload**:
   - The provisioning tool **must** send:
     1. `up:ip:<SERVER_HOST>` &rarr; Points strip to Volta's port `10086`.
     2. `up:connect:<SSID>:<PASSWORD>` &rarr; Joins home Wi-Fi.

### 4.2 The 4-Phase In-App Pairing Wizard

```text
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ Phase 1: WAN    │ ----> │ Phase 2: AP     │ ----> │ Phase 3: WAN    │ ----> │ Phase 4: Live   │
│ Get Server Host │       │ TCP 192.168.1.1 │       │ Claim MAC on    │       │ Poll Live State │
│ & Wi-Fi Details │       │ 30300 Handshake │       │ Backend API     │       │ Strip is Ready! │
└─────────────────┘       └─────────────────┘       └─────────────────┘       └─────────────────┘
```

#### Phase 1: Input (Internet Connected)
- User selects home 2.4 GHz Wi-Fi SSID and enters Password.
- App queries backend for server destination:
  ```http
  GET /api/health
  ```
  Extract `server_ip` (e.g. `strip.your-domain.com` or `132.145.181.76`).

#### Phase 2: SoftAP Socket Handshake (`192.168.1.1:30300`)
- Connect phone Wi-Fi to `TONLY_TAP_<SUFFIX>` with password `LGU_<SUFFIX>`.
  *(On Android, use `WifiNetworkSpecifier` or `bindProcessToNetwork`)*.
- Open raw TCP socket to `192.168.1.1:30300`.
- **Query MAC Address**:
  ```text
  Send:    up:bootinfo\r\n
  Receive: up:bootinfo:LGU+-TAP-HW002;<MAC>;001122334455;1.0.60;connect\r\n
  ```
  Extract 12-char uppercase `<MAC>`.
- **Program Server Destination**:
  ```text
  Send:    up:ip:<server_ip>\r\n
  Receive: up:ip:ip_ok\r\n
  ```
- **Program Wi-Fi Credentials**:
  ```text
  Send:    up:connect:<SSID>:<PASSWORD>\r\n
  Receive: up:connect:connect_ok\r\n
  ```
- Close TCP socket and disconnect from SoftAP.

#### Phase 3: Claiming Device on Backend (Internet Connected)
- Phone reconnects to home Wi-Fi / cellular network.
- Call Volta API using the logged-in user's token:
  ```http
  POST /api/claim
  Authorization: Bearer <TOKEN>
  Content-Type: application/json

  {
    "mac": "<MAC>"
  }
  ```

#### Phase 4: Live Verification
- Poll `GET /api/live` every 2s until strip appears with `online: true`.
- Transition to Control screen.

---

### 4.3 Complete Strip Lifecycle Matrix

| Scenario | API Endpoint | HTTP Method | Who Can Call | Result |
|---|---|---|---|---|
| **Claim / Reclaim Strip** | `/api/claim` | `POST` | Any User | Links strip to caller's account; clears deletion tombstone |
| **Unclaim Strip** | `/api/unclaim` | `POST` | Regular User | Unlinks strip from caller's account; strip remains active in system |
| **Delete Strip Completely** | `/api/strip?mac=<MAC>` | `DELETE` | Admin Only | Purges strip, closes TCP socket, tombstones MAC |
| **Fetch Host Info** | `/api/health` | `GET` | Public | Returns `server_ip` (destination host for `up:ip`) |

#### Role-Based UI Permissions:
- **Admin Users (`whoami.is_admin == true`)**:
  - Full access to the "Strips Management" tab.
  - Delete strip button shows: `"Delete strip completely from controller?"`
- **Regular Users (`whoami.is_admin == false`)**:
  - **Hide the Strips Management tab completely** from the bottom navigation bar.
  - Users manage their strips directly via the card header trash button (`🗑`), calling `POST /api/unclaim`.
  - Confirmation prompt: `"Remove <Strip Name> from your account? You can reclaim it later using its MAC address."`

---

# SECTION 5: Amazon Alexa Smart Home Integration

The server provides a native Alexa Smart Home skill bridge.

### 5.1 In-App Account Linking Helper
Add a card under **Settings → Smart Home & Voice Assistants**:
- **Status Indicator**: "Alexa Integration: Ready"
- **Quick-Link Button**: Opens Alexa app account linking deep link:
  ```text
  https://alexa.amazon.com/spa/index.html#skills/dp/<ALEXA_SKILL_ID>/link
  ```
- **Direct Auth URL**:
  ```text
  https://volta.your-domain.com/oauth/authorize?client_id=alexa-volta&response_type=code&redirect_uri=<ALEXA_REDIRECT_URI>
  ```
- Strips claimed by the user are automatically discovered by Alexa. Outlet switch commands (`Turn on TV`, `Turn off Kitchen`) map seamlessly to Volta's backend.

---

# SECTION 6: Analytics, Solar Geocoding & Responsive Guidelines

### 6.1 Timeseries Analytics & Downsampling
- **Endpoint**: `POST /api/analytics/all-strips` and `POST /api/analytics/strip`
- Supports time ranges: `1h`, `6h`, `24h`, `7d`, `30d`, `6m`, `1y`.
- For mobile screen widths, request `downsample: "auto"` or `"minute"` to prevent JSON payloads from exceeding 100KB.
- In portrait orientation, stack secondary charts (Voltage, Temperature) vertically in a single column to prevent horizontal clipping.

### 6.2 Solar Sunrise/Sunset Automation Geocoding
- Use City / Country search (`GET /api/geo/search?q=<CITY>`) instead of forcing manual latitude/longitude input.
- Fetch calculated NOAA sunrise/sunset times via `GET /api/geo/sun`.

---

# SECTION 7: Complete Mobile API Reference Cheat Sheet

| Endpoint | Method | Auth | Body / Params | Description |
|---|---|---|---|---|
| `/api/auth/google` | `POST` | Public | `{"credential": "<JWT>"}` | Exchanges Google token for Volta user token & profile |
| `/api/auth/whoami` | `GET` | Bearer | - | Returns user identity, quotas, and admin status |
| `/api/live` | `GET` | Bearer | - | Unified live telemetry snapshot sorted by custom order |
| `/api/strip/order` | `POST` | Bearer | `{"order": ["MAC1", ...]}` | Persists drag-and-drop strip layout |
| `/api/outlet/lock` | `POST` | Bearer | `{"mac": "...", "outlet": 1-4, "locked": bool}` | Toggles or sets accidental switch lock |
| `/api/onoff` | `POST` | Bearer | `{"mac": "...", "channels": [1,2], "on": bool}` | Switches outlets (409 Conflict if locked) |
| `/api/claim` | `POST` | Bearer | `{"mac": "..."}` | Claims strip to account; un-tombstones deleted strip |
| `/api/unclaim` | `POST` | Bearer | `{"mac": "..."}` | Releases strip from current user account |
| `/api/strip` | `DELETE` | Admin | `?mac=...` | Permanently deletes strip and tombstones MAC |
| `/api/rename` | `POST` | Bearer | `{"mac": "...", "name": "...", "outlets": {...}}` | Renames strip or specific outlets |
| `/api/voltage-guard` | `POST` | Bearer | `{"mac": "...", "min_v": 195, ...}` | Configures Voltage & Thermal Safety Guards |
| `/api/voltage-guard/reset`| `POST` | Bearer | `{"mac": "..."}` | Manually resets latching thermal overheat trip |
| `/api/reboot` | `POST` | Bearer | `{"mac": "..."}` | Sends hardware restart command (`up:reboot:0`) to strip |
| `/api/homes` | `GET` | Bearer | - | Lists homes/houses owned by user with assigned device counts |
| `/api/homes` | `POST` | Bearer | `{"name": "...", "icon": "home"}` | Creates or updates a home/house entity |
| `/api/homes/update` | `POST` | Bearer | `{"id": "...", "name": "...", "icon": "..."}` | Updates home name, icon, or sorting order |
| `/api/homes/delete` | `POST` | Bearer | `{"id": "..."}` | Deletes home and unbinds associated strips |
| `/api/homes/assign` | `POST` | Bearer | `{"mac": "...", "home_id": "..."}` | Assigns or reassigns a strip to a specific home |
| `/api/health` | `GET` | Public | - | Returns `server_ip` destination host for SoftAP pairing |
| `/api/geo/search` | `GET` | Bearer | `?q=<CITY>` | Geocodes city for solar schedule calculations |
| `/api/geo/sun` | `GET` | Bearer | `?lat=...&lon=...` | Returns calculated NOAA sunrise/sunset times |
