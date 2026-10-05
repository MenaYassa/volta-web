# Future Improvements & Architectural Roadmap

This document outlines the planned strategic improvements for Volta, spanning authentication, automated consumer licensing, administrative tools, and third-party voice assistant ecosystems.

---

## 1. Google Authentication ("Sign in with Google")

### Objective
Replace manual token distribution and copying with standard OAuth2 / Google Identity Services (GIS), providing a seamless, one-click sign-in experience for Web and Android apps while retaining headless API keys for IoT scripts.

### Architecture & Data Flow
1. **Frontend (Web & Android)**:
   - **Web**: Uses Google Identity Services (`https://accounts.google.com/gsi/client`) to render a native "Sign in with Google" button or One-Tap prompt.
   - **Android**: Uses Android Credential Manager / Google Sign-In SDK.
   - Both clients receive a cryptographically signed Google ID Token (`JWT`).
2. **Backend Verification (`POST /api/auth/google`)**:
   - Accepts `{ "id_token": "..." }`.
   - Backend validates the token signature against Google's public JWKS (`https://www.googleapis.com/oauth2/v3/certs`) matching `GOOGLE_CLIENT_ID`.
   - Decodes identity fields: `email`, `sub` (Google User ID), `name`, `picture`.
   - Issues a secure session cookie (`HttpOnly; Secure; SameSite=Strict`) for browsers and a session token for the Android app.

---

## 2. Default Free Tier (1 Lifetime Strip License)

### Objective
Enable new users to onboard instantly without requiring manual administrative approval, providing a free single-device tier forever.

### Auto-Provisioning Logic
When a user authenticates via Google for the first time:
- Check if `email` or `google_sub` exists in `strips.json`.
- If new, automatically create the account record with free tier defaults:
  ```json
  {
    "email": "user@gmail.com",
    "google_id": "109823456789...",
    "name": "Jane Doe",
    "picture": "https://lh3.googleusercontent.com/...",
    "plan": "free_tier",
    "max_strips": 1,
    "expires_at": null,
    "status": "active",
    "strips": [],
    "created_at": 1791090000
  }
  ```
- **Guarantees**:
  - `max_strips: 1`: User can claim and control 1 power strip for free indefinitely.
  - `expires_at: null`: Lifetime free license (no recurring subscription expiry).
  - Attempting to claim a 2nd strip triggers HTTP 403: *"Subscription strip limit reached (1/1). Please upgrade your plan."*

---

## 3. Dedicated Admin Users & Licensing Console

### Objective
Move user administration out of the Settings tab into a top-level, full-page **Users & Licenses** console visible exclusively to Administrators.

### Features
- **User Directory**:
  - Profile avatar, name, and Gmail address.
  - Plan type badge (`Free Tier (1 strip)`, `Pro (5 strips)`, `Enterprise`).
  - Active status pill (`Active`, `Suspended / Canceled`, `Expired`).
  - Quota indicator (`X / Y strips claimed`).
  - Creation date and expiration date countdown pill.
- **Search & Filtering**:
  - Search by email or name.
  - Quick filter chips: `All`, `Free Tier`, `Multi-Strip Paid`, `Expiring Soon (< 30d)`, `Suspended`.
- **Administrative Actions**:
  - **Edit License**: Adjust strip quotas and extend time directly.
  - **Assign Devices**: Open multi-tenant strip assignment modal.
  - **Suspend / Reactivate**: Instantly toggle account access.

---

## 4. License & Quota Modification Workflow

### Capabilities
From the Admin Users page, clicking **Edit License** on any account allows:
1. **Adjust Strip Quota**: Modify `max_strips` (e.g. from 1 to 3, 5, 10, or custom).
2. **Add Duration (Smart Stacking)**:
   - Preserves existing days: adds duration (`+ 1 Month`, `+ 3 Months`, `+ 1 Year`, `Lifetime`) on top of remaining days.
   - For lapsed/expired accounts, the new duration begins on the day of renewal.
3. **Plan Tier Labeling**: Assign friendly labels (e.g. `Family Plan`, `Pro User`).

---

## 5. Controlling Volta via Amazon Alexa (Standard Smart Home)

### Objective
Allow users to control Volta power strips and individual outlets natively using Amazon Echo devices and the Alexa mobile app (e.g., *"Alexa, turn on the Living Room AC"*, *"Alexa, turn off all outlets"*).

### Architecture
```
┌─────────────────────┐
│  Echo / Alexa App   │
└──────────┬──────────┘
           │ Voice Directive / App Touch
           ▼
┌───────────────────────────────────────────────┐
│  Alexa Smart Home Skill (AWS Lambda)          │
│   - Handles Alexa.Discovery & Alexa.PowerController
│   - Validates OAuth Bearer Token (Google / Volta)
└──────────┬────────────────────────────────────┘
           │ HTTPS REST Call
           ▼
┌───────────────────────────────────────────────┐
│  Volta Server API (https://volta.domain.com)  │
│   - GET  /api/devices   (returns switch endpoints)
│   - POST /api/onoff     (relays to strip socket)
└───────────────────────────────────────────────┘
```

### Key Components:
1. **Account Linking (OAuth 2.0)**:
   - In the Alexa app, users enable the Volta skill and link their account via standard OAuth (leveraging the Google Sign-In or Volta auth bridge).
2. **Device Discovery (`Alexa.Discovery`)**:
   - Alexa queries the Lambda function, which calls Volta (`GET /api/devices`).
   - Volta returns each outlet as an `Alexa.PowerController` switch endpoint:
     - Friendly names (e.g. *"AC"*, *"Heater"*, *"Strip 1 - Outlet 2"*).
     - Display category: `SMARTPLUG` or `SWITCH`.
     - Capabilities: `Alexa.PowerController`, `Alexa.EndpointHealth`.
3. **Control Directives (`Alexa.PowerController`)**:
   - When the user issues a voice command (*"Alexa, turn off Bedroom Strip"*), Amazon sends a `TurnOff` directive to the Lambda.
   - Lambda dispatches `POST /api/onoff` to Volta with `{ "mac": "...", "outlet": 1, "on": false }`.
   - Volta immediately switches the relay over the persistent TCP socket on port `10086` and returns state confirmation back to Alexa.
4. **State Reporting & Proactive Updates**:
   - Alexa app displays real-time ON/OFF state for every outlet.
   - Routines, schedules, and Alexa room groups work out of the box.

---

## 6. Security & Migration Path

- **Backwards Compatibility**: Existing `volta_usr_...` tokens continue to function alongside Google authentication so existing mobile installations or headless scripts are not broken.
- **Account Linking**: When an existing token-based user signs in with Google using matching contact info, their claimed strips and custom names are automatically migrated.
