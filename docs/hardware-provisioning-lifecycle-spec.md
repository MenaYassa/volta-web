# Hardware Provisioning & Strip Lifecycle Consolidation Spec (Phase 4)

This specification defines the consolidated architecture, hardware protocols, network lifecycles, and API contracts for provisioning, claiming, unclaiming, and permanently deleting MTTL-W01 smart power strips across the Volta Web ecosystem and native mobile applications (Android / iOS).

---

## 1. Hardware Architecture & Realtek Module Constraints

The MTTL-W01 smart power strip is driven by a Realtek RTL8711AF Wi-Fi microcontroller running custom Tonly/LGU+ firmware. Understanding its hardware constraints is essential for reliable mobile provisioning.

### 1.1 Microcontroller Modes
1. **Normal Run Mode (Connected)**:
   - Associated with home 2.4 GHz Wi-Fi router.
   - Maintains a single persistent outbound TCP socket to the Volta controller on **Port 10086**.
   - Sends telemetry (`up:power_report`, `up:query:wifirssi`) and receives commands (`up:onoff:<ch>:<state>`).
2. **Access Point (AP) Configuration Mode**:
   - Triggered by holding the main power switch for **10 seconds** until the blue LED flashes rapidly.
   - Broadcasts a 2.4 GHz SoftAP hotspot:
     - **SSID Format**: `TONLY_TAP_<SUFFIX>` or `U+TAP_<SUFFIX>`
     - `<SUFFIX>`: Last 7 hex digits of the hardware MAC address printed on the back label (e.g. `9132171`).
     - **WPA2 Password**: `LGU_<SUFFIX>` (uppercase, e.g. `LGU_9132171`).
     - **AP Gateway IP**: `192.168.1.1`
     - **Configuration Port**: TCP `30300` (ASCII command exchange).

### 1.2 Crucial Hardware Pitfall: The 10-Second Reset
When a user holds the physical button for 10 seconds:
- **Both the Wi-Fi credentials AND the server destination address are completely wiped from non-volatile flash.**
- If a mobile app or pairing tool only sends home Wi-Fi credentials (`up:connect`), the strip will connect to home Wi-Fi with an **empty server address** and attempt to dial default vendor cloud servers in China (which are offline/unreachable).
- **Rule**: Every provisioning flow must send **both** `up:ip:<server>` and `up:connect:<ssid>:<password>` over TCP `192.168.1.1:30300`.

---

## 2. Low-Level Provisioning Protocol (`192.168.1.1:30300`)

While the client mobile device is connected to the strip's `TONLY_TAP_<SUFFIX>` Wi-Fi AP, the client must open a raw TCP socket to `192.168.1.1:30300`.

### Step 2.1: Reading Hardware MAC Address
Send ASCII query:
```text
up:bootinfo\r\n
```
Strip replies with:
```text
up:bootinfo:LGU+-TAP-HW002;88D039132171;001122334455;1.0.60;connect\r\n
```
The second semicolon-separated token is the normalized 12-character uppercase **MAC Address** (`88D039132171`).

### Step 2.2: Programming Server Destination
Send ASCII command:
```text
up:ip:<SERVER_HOST_OR_IP>\r\n
```
- `<SERVER_HOST_OR_IP>`: Domain name or IPv4 address of the Volta backend (e.g. `strip.your-domain.com` or `132.145.181.76`). Do not include protocol prefixes (`http://`) or port numbers (`:10086`).
- Strip replies with:
  ```text
  up:ip:ip_ok\r\n
  ```

### Step 2.3: Programming Home 2.4 GHz Wi-Fi Credentials
Send ASCII command:
```text
up:connect:<SSID>:<PASSWORD>\r\n
```
- Restrictions: SSID and password cannot contain colons (`:`) or newline characters (`\r`, `\n`).
- Strip replies with:
  ```text
  up:connect:connect_ok\r\n
  ```
- **Post-Command Behavior**: The strip automatically shuts down its SoftAP, applies Wi-Fi settings, reboots its radio, associates with the home Wi-Fi network, and dials into the server at `<SERVER_HOST_OR_IP>:10086`.

---

## 3. End-to-End Mobile App Pairing Flow (Recommended UX)

Because the mobile device loses WAN / Internet connectivity while connected to the strip's SoftAP (`192.168.1.1`), the mobile pairing wizard must follow this exact 4-phase state machine:

```text
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ Phase 1: WAN    │ ----> │ Phase 2: AP     │ ----> │ Phase 3: WAN    │ ----> │ Phase 4: Live   │
│ Get Server Host │       │ TCP 192.168.1.1 │       │ Claim MAC on    │       │ Poll Live State │
│ & Wi-Fi Details │       │ 30300 Handshake │       │ Backend API     │       │ Strip is Ready! │
└─────────────────┘       └─────────────────┘       └─────────────────┘       └─────────────────┘
```

### Phase 1: User Input (While on normal Internet / Mobile Data)
1. User enters or selects their **Home 2.4 GHz Wi-Fi SSID** and **Password**.
2. App fetches the configured server destination hostname from the backend:
   ```http
   GET /api/health
   ```
   Extract `server_ip` (e.g. `strip.your-domain.com`).
3. Guide the user to hold the strip's main power button for 10s until the blue LED flashes rapidly.

### Phase 2: SoftAP Association & Direct TCP Handshake
1. App prompts user or automatically binds to strip hotspot `TONLY_TAP_<SUFFIX>` with password `LGU_<SUFFIX>`.
   *(On Android, use `WifiNetworkSpecifier` or `WifiManager` to bind network socket traffic to the Wi-Fi interface).*
2. Connect raw socket to `192.168.1.1:30300`.
3. Query `up:bootinfo\r\n` and extract the 12-char MAC address (`mac`).
4. Send `up:ip:<server_host>\r\n` and verify `up:ip:ip_ok`.
5. Send `up:connect:<ssid>:<password>\r\n` and verify `up:connect:connect_ok`.
6. Close TCP socket and disconnect from the strip SoftAP.

### Phase 3: Reclamation & Backend Association (Back on Internet)
1. Restore connection to home Wi-Fi or cellular network.
2. Call the Volta claim API using the user's bearer token:
   ```http
   POST /api/claim
   Authorization: Bearer <USER_TOKEN>
   Content-Type: application/json

   {
     "mac": "88D039132171"
   }
   ```
   **Response (200 OK)**:
   ```json
   {
     "ok": true,
     "mac": "88D039132171",
     "user": "Mina Yassa",
     "strips": ["6C5AB554FED3", "88D039132171"],
     "max_strips": 10
   }
   ```
   *(If the user's plan capacity is exceeded, the server returns `403 Forbidden` with an upgrade prompt).*

### Phase 4: Verification & Live Polling
1. Poll `GET /api/live` every 2 seconds.
2. The strip will connect to port `10086`, send its initial boot frame, and transition to:
   ```json
   {
     "mac": "88D039132171",
     "online": true
   }
   ```
3. Dismiss the setup wizard and display success celebration.

---

## 4. Strip Lifecycle State Machine (Claim, Unclaim, Delete, Restore)

Volta enforces a clear separation between **Tenant Ownership** (`unclaim`) and **System Deletion** (`delete`).

```text
                   ┌──────────────────────────────────────────────┐
                   │                                              │
                   ▼                                              │
         ┌──────────────────┐                                     │
         │   UNASSIGNED     │ ──(POST /api/claim)──> ┌──────────────────┐
         │ (Admin Sees In   │                        │     CLAIMED      │
         │  Inventory List) │ <──(POST /api/unclaim)─│  (Visible to     │
         └──────────────────┘                        │   Tenant User)   │
                   │                                 └──────────────────┘
                   │ (Admin DELETE /api/strip)                │
                   ▼                                          │ (Admin DELETE)
         ┌──────────────────┐                                 │
         │    DELETED /     │ <───────────────────────────────┘
         │   TOMBSTONED     │
         └──────────────────┘
                   │
                   └───(POST /api/claim with MAC)───> [Restored to Active]
```

### 4.1 Claiming a Strip (`POST /api/claim`)
Links a strip to the authenticated user's account.
- **Access**: Any authenticated user (subject to `max_strips` quota).
- **Behavior**:
  - Automatically clears any deletion tombstone if the strip was previously deleted by Admin.
  - Adds the MAC to `user.strips`.
  - Strip appears immediately in the user's dashboard and mobile view.

### 4.2 Releasing / Unclaiming a Strip (`POST /api/unclaim`)
Releases the strip from a specific user's account without deleting hardware configurations or disconnecting the physical strip.
- **Access**: Any user who currently owns the strip.
- **Request**:
  ```http
  POST /api/unclaim
  Authorization: Bearer <USER_TOKEN>
  Content-Type: application/json

  {
    "mac": "88D039132171"
  }
  ```
- **Behavior**:
  - Removes MAC from `user.strips`.
  - The strip disappears completely from that user's mobile app and dashboard.
  - Strip remains connected to the controller and remains visible to Admin under the Strips Inventory as "⚠️ Unassigned".

### 4.3 Deleting a Strip (`DELETE /api/strip?mac=<MAC>`)
Completely purges the strip from the entire system.
- **Access**: **Admin Only** (`is_admin == true`).
- **Behavior**:
  - Unlinks the MAC from all user accounts.
  - Adds the MAC to the persistent `deleted_strips` tombstone in `strips.json`.
  - Wipes custom friendly names, outlet nicknames, schedules, and custom ordering.
  - Closes the active TCP connection and instructs the controller to reject future TCP connections from that MAC.
  - Strips listed in `deleted_strips` are completely omitted from `/api/live` and `/api/devices`.
- **Reversibility**: Admin or any user can reverse the deletion at any time by simply calling `POST /api/claim` with the strip's MAC address.

---

## 5. Summary Table for Mobile App Engineers

| Scenario | API Endpoint | HTTP Method | Who Can Call | Result |
|---|---|---|---|---|
| **Pair New Strip** | TCP `192.168.1.1:30300` | Socket | Mobile Client | Writes server host and home Wi-Fi into strip flash |
| **Claim Strip to Account** | `/api/claim` | `POST` | Any User | Links strip to caller's account; clears deletion tombstone |
| **Unclaim Strip** | `/api/unclaim` | `POST` | Regular User / Admin | Releases strip from caller's account; strip remains in system |
| **Delete Strip Completely** | `/api/strip?mac=...` | `DELETE` | Admin Only | Purges strip, disconnects socket, tombstones MAC |
| **Reclaim Deleted Strip** | `/api/claim` | `POST` | Any User | Removes from tombstone and re-links to account |
| **Get Provisioning Host** | `/api/health` | `GET` | Public | Returns `server_ip` (public destination host for `up:ip`) |
