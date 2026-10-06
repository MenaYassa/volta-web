# Volta Android App Developer Handoff 📱⚡

**Document Version:** 2.0  
**Target:** Volta Mobile Client (Android / iOS / Flutter)  
**Target Backend:** Volta Cloud Controller (API Version 1.0)  
**Base URL:** `https://volta.your-domain.com` (or dynamic host configured by user)

---

## 1. Executive Summary of What Changed
The Volta backend and Web UI have undergone a major upgrade:
1. **Google OAuth & Multi-Tenancy**: Zero manual token sharing. Users sign in with Google or personal user tokens. Automatic provisioning with a 1-strip lifetime free license for new accounts.
2. **Hardware Safety Guard (Voltage & Overheat Guard)**:
   - **Voltage Guard**: Brownout (< `min_v`) and Surge (> `max_v`) auto-cutoff with selective auto-recovery after grid stabilization.
   - **Temperature Overheat Guard**: Immediate all-outlet cutoff if any outlet exceeds `max_temp_c` with **strict manual reset** requirement.
   - **Default-On Protection**: Automatically armed on newly claimed strips.
3. **Alexa Smart Home Integration**: Central OAuth bridge for Account Linking and real-time state reporting (Power, Temperature, Voltage, Wattage).
4. **City / Country Solar Automation**: Replacement of manual latitude/longitude with Open-Meteo geocoding search and auto-timezone sync.

---

## 2. Authentication & User Profile Management

### 2.1 Google Sign-In Flow
Instead of manually typing a `VOLTA_TOKEN`, the app should present a **"Sign in with Google"** button using Google Identity Services / Google Play Services (`GoogleSignInClient` / `CredentialManager`).

* **Google Client ID (Web/Backend Audience):**  
  `YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com`
* **API Dynamic Config Endpoint:**
  ```http
  GET /api/auth/google/config
  ```
  **Response:**
  ```json
  { "ok": true, "client_id": "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com" }
  ```

* **Token Exchange Endpoint:**
  ```http
  POST /api/auth/google
  Content-Type: application/json

  {
    "credential": "<GOOGLE_ID_TOKEN>"
  }
  ```
  **Response:**
  ```json
  {
    "ok": true,
    "token": "volta_usr_a1b2c3d4e5f6...",
    "email": "user@gmail.com",
    "name": "John Doe",
    "picture": "https://lh3.googleusercontent.com/...",
    "is_admin": false,
    "strips": ["88D03934E61B"],
    "max_strips": 1,
    "expires_at": null,
    "status": "active"
  }
  ```
* **Client Behavior:**
  - Store the returned `token` securely in `EncryptedSharedPreferences` / Keychain.
  - Pass this token in the `X-Token` HTTP header on **every subsequent API request**.

---

### 2.2 Profile, License & Quota Check
```http
GET /api/auth/whoami
X-Token: <STORED_TOKEN>
```
**Response:**
```json
{
  "authorized": true,
  "is_admin": false,
  "name": "John Doe",
  "email": "user@gmail.com",
  "picture": "https://lh3.googleusercontent.com/...",
  "strips": ["88D03934E61B"],
  "max_strips": 1,
  "expires_at": 1791234567,
  "status": "active"
}
```

#### Client Rules for Licenses:
1. **Strip Quota Enforcement**: If `strips.length >= max_strips`, disable or hide the "Add New Strip" wizard and show an in-app banner: *"You've reached your license limit (X/X strips). Contact admin to expand."*
2. **Subscription Expiry Banner**:
   - If `expires_at != null` and days remaining $\le 30$: show an amber warning pill/card: *"Plan renews in X days"*.
   - If `status == "expired"` or `status == "canceled"`: backend returns HTTP 401 with `{"subscription_expired": true}`. Show an account locked dialog.

---

## 3. Strip Safety Guard (Voltage & Temperature)

Every strip now features real-time hardware safety guards that must be visible and configurable inside the mobile app.

### 3.1 Fetch Strip Guard Configuration
```http
GET /api/voltage-guard?mac=88D03934E61B
X-Token: <TOKEN>
```
**Response:**
```json
{
  "ok": true,
  "mac": "88D03934E61B",
  "guard": {
    "enabled": true,
    "min_v": 195.0,
    "max_v": 250.0,
    "safe_delay_min": 3,
    "tripped": false,
    "fault_type": null,
    "tripped_at": null,
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

*(Note: The live strip object returned by `GET /api/live` and `GET /api/devices` also includes this exact `voltage_guard` sub-object directly on each strip!)*

---

### 3.2 Update Guard Settings
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

---

### 3.3 Clear / Reset Tripped State
```http
POST /api/voltage-guard/reset
X-Token: <TOKEN>
Content-Type: application/json

{
  "mac": "88D03934E61B"
}
```

---

### 3.4 Mobile UI Specifications for Safety Guard

#### Strip Card Status Badges:
1. **Normal Armed (Safe):** Show a green badge: `🛡️ Guard Armed` (tap opens Guard Sheet).
2. **Voltage Tripped:** Show an urgent amber/red blinking card:
   - Title: `🚨 VOLTAGE TRIPPED (${guard.trip_voltage}V)`
   - Subtitle: *"Power shut off to protect appliances. Stabilizing..."*
3. **Overheat Tripped:** Show a prominent red fire banner:
   - Title: `🔥 OVERHEAT TRIPPED (${guard.trip_temp_c}°C)`
   - Subtitle: *"Outlets shut down for fire protection. Inspect hardware."*
   - Action: Show a **"Clear Thermal Trip"** button calling `POST /api/voltage-guard/reset`.

#### Guard Configuration BottomSheet / Dialog:
Provide a bottom sheet containing two distinct cards:
1. **🌡️ Temperature Guard**:
   - Switch: `Enable Temperature Guard` (default: ON).
   - Number Picker / Slider: `Max Temp Cutoff` (Range: 40°C – 80°C, default: 65°C).
   - Explanatory note: *"Emergency cutoff. For fire safety, power does NOT turn back on automatically."*
2. **⚡ Voltage Guard**:
   - Switch: `Enable Voltage Guard` (default: ON).
   - Input: `Min Voltage (V)` (default: 195V).
   - Input: `Max Voltage (V)` (default: 250V).
   - Input: `Safe Delay (Minutes)` (default: 3 min).
   - Explanatory note: *"Automatically restores previously active outlets once voltage stays within safe limits continuously for the selected delay."*

---

## 4. City & Country Geocoding (Sun Automation)

Manual latitude and longitude entry has been eliminated in favor of city search with automatic coordinate and timezone resolution.

### 4.1 Search Cities (Autocomplete)
```http
GET /api/geo/search?q=Cairo
X-Token: <TOKEN>
```
**Response:**
```json
{
  "ok": true,
  "results": [
    {
      "name": "Cairo",
      "country": "Egypt",
      "country_code": "EG",
      "admin1": "Cairo",
      "latitude": 30.06263,
      "longitude": 31.24967,
      "timezone": "Africa/Cairo"
    }
  ]
}
```

### 4.2 Query Sun Times (Sunrise / Sunset)
```http
GET /api/geo/sun?lat=30.06263&lon=31.24967&tz=Africa/Cairo
X-Token: <TOKEN>
```
**Response:**
```json
{
  "ok": true,
  "sunrise": "05:48",
  "sunset": "17:34",
  "timezone": "Africa/Cairo"
}
```

---

## 5. Amazon Alexa In-App Linking Helper

The backend provides a complete turnkey OAuth 2.0 flow for Alexa. The mobile app can provide a convenient shortcut:

1. **"Connect to Amazon Alexa" Button in Settings**:
   - When tapped, launch the Amazon Alexa skill link or open the custom tab to:
     ```text
     https://volta.your-domain.com/oauth/authorize?client_id=volta-alexa-client&response_type=code&redirect_uri=https://pitangui.amazon.com/api/skill/link/MVO1KJEHWER4U&state=volta_app
     ```
   - If the user is already authenticated in the app, the mobile webview will auto-link and hand off to the Alexa app seamlessly!

2. **Alexa Skill Capabilities Supported**:
   - Relay Switch (`Alexa.PowerController`)
   - Live Temperature (`Alexa.TemperatureSensor`)
   - Live Voltage (`Alexa.RangeController` Instance: `Voltage`)
   - Live Wattage (`Alexa.RangeController` Instance: `Power`)

---

## 6. Summary Checklist for App Developer

| Feature Area | Required App Implementation | Status in Web App |
|---|---|---|
| **Google Sign-In** | Google Play Services Sign-In $\rightarrow$ `POST /api/auth/google` | ✅ Live |
| **User Profile / Quota** | Check `strips.length < max_strips` before opening add wizard | ✅ Live |
| **Strip Live Telemetry** | Display `voltage_v`, `power_w`, and per-outlet `temp_c` | ✅ Live |
| **Voltage Guard UI** | Config modal with `min_v`, `max_v`, `safe_delay_min` + trip state | ✅ Live |
| **Temp Guard UI** | Config modal with `max_temp_c` + manual reset button | ✅ Live |
| **Geocoding City Search** | Autocomplete search via `GET /api/geo/search?q=...` | ✅ Live |
| **Alexa Linking Button** | One-tap deep-link in app Settings | ✅ Live |
