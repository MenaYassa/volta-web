# Volta Android App Developer Handoff — Phase 2: Auth, Safety & Smart Home 📱⚡

**Document Version:** 2.1 (Phase 2 Addendum)  
**Target:** Volta Mobile Client (Android / Kotlin / Jetpack Compose / Flutter)  
**Base URL:** `https://volta.your-domain.com` (or dynamic host)  
**Previous Baseline Completed:** Analytics summary (`/api/analytics/summary`), Leaderboard (`/api/analytics/leaderboard`), Token sanitization (`volta_usr_`), and Account Expiry Lock Screen (`subscription_expired: true`).

---

## What is in this Handoff?
This document specifies the exact **new contracts, data models, state machines, and UI components** required to upgrade the app for:
1. **Google Identity Authentication Flow** (replacing manual token pasting as the primary sign-in).
2. **Dual Hardware Safety Guard Engine** (Voltage sag/surge auto-recovery + Temperature fire cutoff).
3. **Amazon Alexa Smart Home Integration** (in-app linking helper + voice control parity).
4. **City / Geocoding Location Search** (replacing manual lat/lon entry for solar sunrise/sunset automations).

---

## 1. Authentication: Google Sign-In & Profile Parity

### 1.1 Architectural Shift
In Phase 1, the user pasted a raw token string (`volta_usr_...`).  
In Phase 2, Google Sign-In is the **primary onboarding and authentication method**, while token input remains as a secondary fallback.

```
┌───────────────────────────────┐
│       Volta Login Screen      │
│  [ 🔵 Sign in with Google ]   │ ──▶ Uses Google CredentialManager / Play Services
│  [ Or enter user token... ]   │ ──▶ Fallback (Phase 1 SecureTokenManager)
└───────────────┬───────────────┘
                │ Returns Google ID Token (JWT)
                ▼
┌───────────────────────────────┐
│     POST /api/auth/google     │
└───────────────┬───────────────┘
                │ Returns Volta User Token + Quotas + Profile
                ▼
┌───────────────────────────────┐
│   Write to SecureTokenManager │
│    Navigate to Main Screen    │
└───────────────────────────────┘
```

### 1.2 Google Client ID Discovery Endpoint
Do not hardcode the Google Client ID in the app APK if possible. Fetch it dynamically:
```http
GET /api/auth/google/config
```
**Response (200 OK):**
```json
{
  "ok": true,
  "client_id": "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com"
}
```

### 1.3 Google ID Token Exchange Endpoint
Send the Google ID Token obtained from Android's `CredentialManager` or `GoogleSignInAccount.idToken`:
```http
POST /api/auth/google
Content-Type: application/json

{
  "credential": "<GOOGLE_ID_TOKEN_STRING>"
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "token": "volta_usr_48f6c91a72d3e05a8b79b201...",
  "email": "user@gmail.com",
  "name": "Mena Medhat",
  "picture": "https://lh3.googleusercontent.com/a/ACg8oc...",
  "is_admin": false,
  "strips": ["88D03934E61B"],
  "max_strips": 1,
  "expires_at": null,
  "status": "active"
}
```

#### Android Implementation Details:
1. Extract `token` and store it in `SecureTokenManager` (your existing AES-256 GCM encrypted storage).
2. Save `email`, `name`, and `picture` in local user preferences.
3. If `expires_at == null`, display plan badge as **"Lifetime / Free Tier"** (new users get 1 free lifetime strip).
4. If `is_admin == true`, the app can expose admin-only menus.
5. If the user's email matches `eng.menamedhat@gmail.com`, the backend automatically issues the master admin token.

---

## 2. Dual Hardware Safety Guard (Voltage & Temperature)

This is a major hardware protection system that runs 24/7 on the server. The mobile app must reflect live guard states and provide configuration controls.

### 2.1 Live Strip Object Payload Changes
Every device in `GET /api/live` and `GET /api/devices` now includes the `voltage_guard` data model directly inside the strip JSON:

```json
{
  "mac": "88D03934E61B",
  "name": "Living Room TV",
  "online": true,
  "voltage_v": 221.4,
  "power_w": 45.2,
  "outlets": [
    { "n": 1, "name": "TV", "on": true, "power_w": 40.0, "temp_c": 32.0 },
    { "n": 2, "name": "Soundbar", "on": false, "power_w": 0.0, "temp_c": 28.0 },
    { "n": 3, "name": "Apple TV", "on": true, "power_w": 5.2, "temp_c": 30.5 },
    { "n": 4, "name": "Lamp", "on": false, "power_w": 0.0, "temp_c": 26.0 }
  ],
  "voltage_guard": {
    "enabled": true,
    "min_v": 195.0,
    "max_v": 250.0,
    "safe_delay_min": 3,
    "tripped": false,
    "fault_type": null,
    "trip_voltage": null,
    "saved_outlets": [],
    "safe_since": null,
    "temp_enabled": true,
    "max_temp_c": 65.0,
    "temp_tripped": false,
    "temp_fault_type": null,
    "trip_temp_c": null
  }
}
```

### 2.2 Kotlin Data Models for Safety Guard

```kotlin
data class VoltageGuardState(
    @SerializedName("enabled") val enabled: Boolean = true,
    @SerializedName("min_v") val minV: Float = 195.0f,
    @SerializedName("max_v") val maxV: Float = 250.0f,
    @SerializedName("safe_delay_min") val safeDelayMin: Int = 3,
    @SerializedName("tripped") val tripped: Boolean = false,
    @SerializedName("fault_type") val faultType: String? = null,
    @SerializedName("trip_voltage") val tripVoltage: Float? = null,
    @SerializedName("saved_outlets") val savedOutlets: List<Int> = emptyList(),
    @SerializedName("safe_since") val safeSince: Double? = null,
    @SerializedName("temp_enabled") val tempEnabled: Boolean = true,
    @SerializedName("max_temp_c") val maxTempC: Float = 65.0f,
    @SerializedName("temp_tripped") val tempTripped: Boolean = false,
    @SerializedName("temp_fault_type") val tempFaultType: String? = null,
    @SerializedName("trip_temp_c") val tripTempC: Float? = null
)
```

---

### 2.3 Strip Card Presentation & State Machine

In the Strip List and Strip Details screens, render status indicators based on guard states:

| Priority | State Condition | Visual Presentation | Meaning to User |
|---|---|---|---|
| **1 (Highest)** | `guard.temp_tripped == true` | **Red Pulsing Banner / Badge**<br>`🔥 OVERHEAT TRIPPED (${guard.trip_temp_c}°C)` | Fire hazard shutdown! All outlets turned OFF. **No auto turn-on**. User must tap to inspect and manually clear trip. |
| **2** | `guard.tripped == true && guard.safe_since == null` | **Red Banner / Badge**<br>`🚨 VOLTAGE TRIPPED (${guard.trip_voltage}V)` | Brownout or surge detected. Outlets safely cut OFF. Grid voltage is currently out of safe bounds. |
| **3** | `guard.tripped == true && guard.safe_since != null` | **Amber Banner / Progress Indicator**<br>`⏳ Stabilizing (${elapsed}s / ${delay*60}s)` | Voltage has returned to normal range. The server is timing stability before automatically restoring the previously active outlets. |
| **4** | `guard.enabled || guard.temp_enabled` | **Green Pill**<br>`🛡️ Guard Armed` | Active monitoring: voltage window `${guard.minV}-${guard.maxV}V` and temp `≤${guard.maxTempC}°C`. |
| **5** | `!guard.enabled && !guard.temp_enabled` | **Muted Grey Pill**<br>`🛡️ Guard Off` | Safety monitoring disabled for this strip. |

---

### 2.4 Safety Guard Endpoints

#### A. Fetch Strip Guard Config:
```http
GET /api/voltage-guard?mac=88D03934E61B
X-Token: <TOKEN>
```
**Response:**
```json
{
  "ok": true,
  "mac": "88D03934E61B",
  "guard": { ... }
}
```

#### B. Update Guard Settings:
```http
POST /api/voltage-guard
X-Token: <TOKEN>
Content-Type: application/json

{
  "mac": "88D03934E61B",
  "enabled": true,
  "min_v": 195.0,
  "max_v": 250.0,
  "safe_delay_min": 3,
  "temp_enabled": true,
  "max_temp_c": 65.0
}
```

#### C. Clear / Reset Trip Latch (Manual Override):
```http
POST /api/voltage-guard/reset
X-Token: <TOKEN>
Content-Type: application/json

{
  "mac": "88D03934E61B"
}
```

---

### 2.5 Guard BottomSheet UI Specification
Provide a dedicated bottom sheet (`SafetyGuardBottomSheet`) accessible by tapping the `🛡️ Guard` badge on any strip card:

1. **Header**: Strip Name + Live Voltage (`221.4V`) + Max Current Temp (`32°C`).
2. **Thermal Trip Warning Box** (Visible only if `temp_tripped == true`):
   - Text: *"Emergency shut down due to overheating. For fire safety, power does NOT turn back on automatically. Check connected appliances."*
   - Red Button: **"Clear Thermal Trip"** $\rightarrow$ calls `POST /api/voltage-guard/reset`.
3. **Card 1: 🌡️ Temperature Guard**:
   - Switch: `Enable Overheat Protection` (default: ON).
   - Slider / Input: `Max Temperature (°C)` (range: 40–80°C, step: 1, default: 65°C).
4. **Card 2: ⚡ Voltage Guard**:
   - Switch: `Enable Voltage Guard` (default: ON).
   - Input: `Brownout Cutoff (V)` (default: 195V).
   - Input: `Surge Cutoff (V)` (default: 250V).
   - Input: `Stabilization Delay (Minutes)` (range: 1–30 min, default: 3 min).
   - Caption: *"When voltage normalizes, Volta restores only the outlets that were originally on."*
5. **Footer**:
   - Primary Button: **"Save Guard Settings"** $\rightarrow$ calls `POST /api/voltage-guard`.

---

## 3. Amazon Alexa Voice Control & In-App Linking

### 3.1 Capabilities Live on the Backend
The Volta controller now provides full Amazon Alexa Smart Home directives. Every outlet is discovered by Alexa with:
- **`Alexa.PowerController`**: Voice turn ON / turn OFF.
- **`Alexa.TemperatureSensor`**: Voice query: *"Alexa, what is the temperature of Bedroom TV?"*
- **`Alexa.RangeController` (Instance: `Voltage`)**: Reports real-time Volts (`Alexa.Unit.Volt`).
- **`Alexa.RangeController` (Instance: `Power`)**: Reports real-time Watts (`Alexa.Unit.Watt`).
- **`Alexa.EndpointHealth`**: Device online/offline connectivity.

### 3.2 In-App Account Linking Button
Add a **"Connect to Amazon Alexa"** card in the app's **Settings / Integrations** section:
- **Action on Tap**: Launch a Chrome Custom Tab or external browser to:
  ```text
  https://volta.your-domain.com/oauth/authorize?client_id=volta-alexa-client&response_type=code&redirect_uri=https://pitangui.amazon.com/api/skill/link/MVO1KJEHWER4U&state=volta_android
  ```
- **Behavior**:
  - The web page presents the multi-user authorization screen with one-tap Google Sign-In or token paste.
  - Upon sign-in, it redirects back to Amazon's Alexa app and confirms: *"Volta Smart Home successfully linked!"*
  - Alexa then discovers all strips assigned to that specific user.

---

## 4. City / Country Geocoding (Sun Automation)

Replace manual latitude and longitude text inputs in the Settings screen with autocomplete city search.

### 4.1 Autocomplete City Search Endpoint
```http
GET /api/geo/search?q={query}
X-Token: <TOKEN>
```
**Example:** `GET /api/geo/search?q=Cairo`  
**Response (200 OK):**
```json
{
  "ok": true,
  "results": [
    {
      "name": "Cairo",
      "country": "Egypt",
      "country_code": "EG",
      "admin1": "Cairo Governorate",
      "latitude": 30.06263,
      "longitude": 31.24967,
      "timezone": "Africa/Cairo"
    }
  ]
}
```

### 4.2 Query Calculated Sun Times
```http
GET /api/geo/sun?lat=30.06263&lon=31.24967&tz=Africa/Cairo
X-Token: <TOKEN>
```
**Response (200 OK):**
```json
{
  "ok": true,
  "sunrise": "05:48",
  "sunset": "17:34",
  "timezone": "Africa/Cairo"
}
```

### 4.3 Updating Location Settings
Save the resolved city and coordinates to the server settings:
```http
POST /api/settings
X-Token: <TOKEN>
Content-Type: application/json

{
  "city": "Cairo",
  "country": "Egypt",
  "lat": 30.06263,
  "lon": 31.24967,
  "timezone": "Africa/Cairo"
}
```

---

## 5. Summary Implementation Checklist for Android Dev

| Area | Feature | Endpoint(s) | UI Component |
|---|---|---|---|
| **Auth** | Google Sign-In (CredentialManager) | `POST /api/auth/google` | Sign-in screen: "Sign in with Google" button |
| **Auth** | Client ID Auto-Discovery | `GET /api/auth/google/config` | Background config loader |
| **Safety** | Strip Voltage Sag/Surge Guard | `POST /api/voltage-guard` | Guard BottomSheet + Strip Card badge |
| **Safety** | Strip Overheat Guard | `POST /api/voltage-guard` | Fire badge + Manual reset button |
| **Safety** | Clear Trip Latch | `POST /api/voltage-guard/reset` | Trip warning banner action button |
| **Alexa** | In-App Skill Account Linking | Custom Tab to `/oauth/authorize` | Settings $\rightarrow$ Integrations: "Link Amazon Alexa" |
| **Geo** | City Autocomplete Search | `GET /api/geo/search?q=...` | Settings: Location search text field with dropdown |
| **Geo** | Sunrise / Sunset Display | `GET /api/geo/sun` | Solar preview card showing live rise & set times |
