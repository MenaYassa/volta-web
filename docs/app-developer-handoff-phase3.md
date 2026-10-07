# Volta Mobile App Developer Handoff — Phase 3: UX, Outlet Locks, Reordering & Live Controls 📱⚡

**Document Version:** 3.0 (Phase 3 Addendum)  
**Target:** Volta Mobile Client (Android / iOS / Kotlin Multiplatform / Flutter / React Native)  
**Base URL:** `https://volta.your-domain.com` (or dynamic host)  
**Date:** October 7, 2026  
**Previous Baseline (Phase 2):** Google OAuth CredentialManager, Dual Hardware Safety Guards (Voltage & Temp), Alexa Smart Home integration, and City Geocoding.

---

## Executive Summary & What's New Since Phase 2

Since Phase 2, major improvements were made across backend API contracts, security permissions, telemetry HUD, safety locks, and UX interactions. The mobile app must incorporate these new capabilities to maintain 1:1 feature parity with the web platform:

1. **Per-User Drag & Drop Strip Card Reordering**:
   - Custom visual order of power strips saved per user on the backend.
   - Survives across sessions and multi-device logins.
2. **Server-Side Accidental Outlet Switch Locks (`🔒` / `🔓`)**:
   - Individual outlets can be locked against accidental physical/remote toggling.
   - Persisted server-side per strip MAC and synchronized across all users sharing that strip.
   - Enforced by backend: `POST /api/onoff` returns `409 Conflict` if attempting to switch a locked outlet.
3. **Selective Batch Switching (`ALL ON` / `ALL OFF`)**:
   - `POST /api/onoff` now accepts explicit channel arrays (`channels: [1, 2, 4]`).
   - `ALL ON` and `ALL OFF` operations **skip locked outlets** automatically.
4. **Hero Telemetry HUD & Status Indicators**:
   - Dynamic strip status LED dot (Red: offline, Muted: 0 outlets ON, Light green: 1–3 ON, Glowing shiny green: all 4 ON).
   - Dynamic `ALL ON` (green accent when all ON) and `ALL OFF` (red accent when all OFF) states.
5. **Role-Based Navigation Gating**:
   - Regular users must **not** see the Strips Management tab (hardware provisioning is admin-only).
   - Direct strip release/removal action directly on the strip card for users who claimed it.
6. **Responsive Layouts & Modal Dismissal Rules**:
   - All modals dismiss cleanly when tapping the outside backdrop overlay without saving.
   - Single-column vertical stacking for secondary metrics and subcharts to prevent boundary clipping.

---

## 1. Drag & Drop Strip Reordering API Contract

Users can rearrange their strip cards on the Home / Control screen. This order is saved **per-user** in the backend.

### 1.1 Save User Strip Order
```http
POST /api/strip/order
Authorization: Bearer <USER_TOKEN>
Content-Type: application/json

{
  "order": ["AABBCCDDEEFF", "112233445566"]
}
```

#### Response (200 OK)
```json
{
  "ok": true,
  "order": ["AABBCCDDEEFF", "112233445566"]
}
```

### 1.2 Fetching the Custom Order
The backend automatically orders the `devices` list returned by `GET /api/live` according to the authenticated user's saved preference. Additionally, the saved order array is returned explicitly:
```http
GET /api/live
Authorization: Bearer <USER_TOKEN>
```
```json
{
  "devices": [ ... ],
  "strip_order": ["AABBCCDDEEFF", "112233445566"]
}
```

### 1.3 Mobile Implementation Guidelines
- **UI Element**: Add a vertical grip icon (`⠿` / `drag_indicator`) in the strip card header.
- **Gesture**: Use `ReorderableLazyColumn` (Jetpack Compose) or `ReorderableListView` (Flutter).
- **Polling Conflict Prevention**: While the user is actively dragging a strip, pause background auto-refresh timers/polling (`live` calls) to avoid jumping or visual flicker.
- **Commit Trigger**: Call `POST /api/strip/order` immediately on drag release (`onDragEnd` / `onReorder`).

---

## 2. Server-Side Outlet Switch Locks (`🔒` / `🔓`)

To prevent critical appliances (e.g. 3D printers, servers, refrigerators, medical equipment) from accidental toggles or batch `ALL OFF` shutoffs, individual outlets can be locked.

### 2.1 Backend Contract & Endpoints

#### Toggle or Explicitly Set Outlet Lock
```http
POST /api/outlet/lock
Authorization: Bearer <USER_TOKEN>
Content-Type: application/json

{
  "mac": "AABBCCDDEEFF",
  "outlet": 2,
  "locked": true
}
```
*Note: Omitting `"locked"` automatically inverts/toggles the current lock state.*

#### Response (200 OK)
```json
{
  "ok": true,
  "mac": "AABBCCDDEEFF",
  "outlet": 2,
  "locked": true,
  "locked_outlets": [2, 4]
}
```

### 2.2 Live Snapshot Data Model
Each outlet in `GET /api/live` and `GET /api/devices` includes its lock status, and each device object includes `locked_outlets`:
```json
{
  "mac": "AABBCCDDEEFF",
  "locked_outlets": [2, 4],
  "outlets": [
    {
      "n": 1,
      "name": "Desk Lamp",
      "on": true,
      "locked": false,
      "power_w": 12.5,
      "energy_kwh": 0.42,
      "temp_c": 28.5
    },
    {
      "n": 2,
      "name": "NAS Server",
      "on": true,
      "locked": true,
      "power_w": 45.0,
      "energy_kwh": 14.8,
      "temp_c": 32.1
    }
  ]
}
```

### 2.3 Switch Lock Enforcement Rules
1. **Disabled Switch Button**: When `locked == true`, disable the toggle switch button in the UI (`enabled = false`) and show a persistent padlock badge (`🔒`).
2. **Backend Rejection**: If a user attempts to call `POST /api/onoff` on a locked outlet, the server rejects the request:
   ```json
   {
     "error": "outlet 2 is locked against switching"
   }
   ```
   Status: `409 Conflict`.
3. **Unlock Confirmation**: Tapping the padlock icon toggles the lock state. Optionally prompt a confirmation dialog before unlocking critical outlets.

---

## 3. Selective Batch Switching (`ALL ON` / `ALL OFF`)

The `ALL ON` and `ALL OFF` buttons must **never** toggle locked outlets.

### 3.1 Updated `POST /api/onoff` Contract
`POST /api/onoff` now supports an explicit `channels` list:

```http
POST /api/onoff
Authorization: Bearer <USER_TOKEN>
Content-Type: application/json

{
  "mac": "AABBCCDDEEFF",
  "channels": [1, 3],
  "on": false
}
```

### 3.2 Mobile Logic for ALL ON / ALL OFF
When the user taps `ALL ON` or `ALL OFF`:
1. Collect all available outlet numbers for the strip (`[1, 2, 3, 4]`).
2. Filter out outlets where `locked == true`.
3. If **all outlets are locked**, abort and show a Snackbar/Toast:
   `"All outlets on <Strip Name> are locked 🔒. Unlock at least one outlet first."`
4. If some outlets are locked, prompt a clear confirmation dialog:
   `"Turn OFF 2 unlocked outlet(s) on Living Room Strip? (2 locked outlet(s) will remain untouched 🔒)"`
5. Send `POST /api/onoff` with `channels: unlockedChannels`.

---

## 4. Visual Status Language & Telemetry HUD

To ensure seamless visual continuity between the Web UI and the native mobile app, adhere to these styling specifications:

### 4.1 Strip Header Status Dot
Located directly to the left of the Strip Name:
- **Offline**: Solid Red (`#EF4444`).
- **Online (0 outlets ON)**: Muted Gray (`#94A3B8`).
- **Online (1–3 outlets ON)**: Vibrant Light Green (`#4ADE80`).
- **Online (All 4 outlets ON)**: Shiny Glowing Green (`#22C55E` with box/drop shadow `0 0 6px #22C55E`).

### 4.2 ALL ON / ALL OFF Button Accents
- **ALL ON Button**:
  - Highlights in Solid Green (`#22C55E`, white bold text) when all outlets on the strip are currently `ON`.
  - Otherwise rendered as a subtle outline/ghost button.
- **ALL OFF Button**:
  - Highlights in Solid Red (`#EF4444`, white bold text) when all outlets on the strip are currently `OFF`.
  - Otherwise rendered as a subtle outline/ghost button.

### 4.3 Outlet Card Symmetry & Lock Alignment
- In 2-column or list cards, place the padlock toggle icon (`🔒`/`🔓`) at the **far right** of the outlet title row.
- Dim the card opacity slightly when an outlet is in a switching/busy state (`isBusy = true`).

---

## 5. Navigation & Role Permissions

### 5.1 Strips Management Tab
- **Admin Users (`whoami.is_admin == true`)**: Full access to the "Strips" provisioning and claim management tab.
- **Regular Users (`whoami.is_admin == false`)**:
  - Hide the Strips tab completely from the navigation bar.
  - Users only see **Control**, **Analytics**, and **Settings**.

### 5.2 Direct Strip Removal / Unclaim
Normal users can release their claimed strips directly from the strip's card in the Control view via the trash icon button (`🗑`):
```http
POST /api/unclaim
Authorization: Bearer <USER_TOKEN>
Content-Type: application/json

{
  "mac": "AABBCCDDEEFF"
}
```
*Prompt user: `"Remove <Strip Name> from your account? You can reclaim it later using its MAC address."`*

---

## 6. Mobile Layout & Modal Dismissal Rules

1. **Backdrop Dismissal**:
   All bottom sheets and modal dialogs (Safety Guard setup, Outlet Renaming, Schedule Creator) must support tapping outside on the modal scrim/overlay to dismiss **without saving** pending changes.
2. **Subcharts Stacking**:
   In the Analytics view, secondary telemetry charts (Grid Voltage and Enclosure Temperature) must stack vertically in a single column on phone viewports to prevent chart canvas clipping.
3. **Monospace Code Container for Server Diagnostics**:
   In Settings → Server, render health diagnostics within a horizontally scrollable monospace card with `word-break: break-all` to prevent view hierarchy overflow.

---

## 7. Complete API Reference Cheat Sheet (Phase 3)

| Endpoint | Method | Auth | Body / Params | Description |
|---|---|---|---|---|
| `/api/live` | `GET` | Bearer Token | - | Returns live telemetry, sorted by user's custom strip order, including `locked_outlets` |
| `/api/strip/order` | `POST` | Bearer Token | `{"order": ["MAC1", ...]}` | Persists custom drag-and-drop strip layout for caller's user account |
| `/api/outlet/lock` | `POST` | Bearer Token | `{"mac": "...", "outlet": 1-4, "locked": bool}` | Persists accidental toggle lock state server-side |
| `/api/onoff` | `POST` | Bearer Token | `{"mac": "...", "channels": [1,2], "on": bool}` | Toggles specified outlets (returns 409 if any specified outlet is locked) |
| `/api/unclaim` | `POST` | Bearer Token | `{"mac": "..."}` | Releases strip from current user account |
| `/api/rename` | `POST` | Bearer Token | `{"mac": "...", "name": "...", "outlet": 1-4, "outlet_name": "..."}` | Renames strip or specific outlet |
| `/api/voltage-guard` | `POST` | Bearer Token | `{"mac": "...", "enabled": bool, "min_v": float, "max_v": float, ...}` | Configures Voltage & Temperature Guard |
