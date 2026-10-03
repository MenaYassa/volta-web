# Setup & Provisioning Guide

This guide describes how to configure an MTTL-W01 smart power strip to connect to your home Wi-Fi and dial in to your Volta server.

---

## Prerequisites

1. **Strip in AP Mode**:
   - Hold the physical power button on the strip for **5–7 seconds** until the LED blinks rapidly.
   - The strip will broadcast a Wi-Fi hotspot named:
     ```text
     TONLY_TAP_<SUFFIX>   (or U+TAP_<SUFFIX>)
     ```
     where `<SUFFIX>` is the last 7 hex characters of the strip's MAC address (printed on the bottom sticker).
   - **AP Password**: `LGU_<SUFFIX>` (e.g., if SSID is `TONLY_TAP_9132171`, the password is `LGU_9132171`).
2. **Volta Host IP / Hostname**:
   - The public IPv4 address, WAN domain, or dynamic DNS hostname of your Volta server where port `10086` is reachable.
3. **User Account Quota**:
   - Ensure your account has not exceeded its `max_strips` limit before claiming.

---

## Provisioning Method A: Android App Pairing Wizard (Fastest)

The native Volta Android app features a step-by-step pairing wizard with zero manual MAC entry:

1. **Open the Volta App** and tap **+ Add Strip**.
2. **Wi-Fi Details**: Enter your 2.4 GHz home Wi-Fi SSID and Password.
3. **Auto-Discovery**: The app detects the strip hotspot, queries the internal MAC via `192.168.1.1:30300`, writes the server destination, provisions Wi-Fi, and automatically claims the strip to your user account via `POST /api/claim`.
4. The strip reboots, dials in to the server, and appears immediately on your dashboard.

---

## Provisioning Method B: Termux / CLI Setup Script

Because the strip in AP mode provides **no internet routing**, provisioning requires downloading the script first, then connecting to the strip hotspot.

### Step 1: Download `controller.py` while on regular Wi-Fi or Mobile Data
Open Termux or your laptop terminal and run:
```bash
curl -s https://volta.your-domain.com/controller.py -o controller.py
```

### Step 2: Connect to Strip Wi-Fi
- Go to Wi-Fi settings.
- Select `TONLY_TAP_<SUFFIX>` and enter `LGU_<SUFFIX>`.
- *(If your operating system warns "Wi-Fi has no internet access", select "Stay connected" or "Keep connection".)*

### Step 3: Run the Provisioning Script
Execute the script, providing your home Wi-Fi and server information:
```bash
python3 controller.py \
  --server-ip YOUR_SERVER_IP \
  --token "YOUR_USER_TOKEN" \
  --web-url "https://volta.your-domain.com" \
  --ssid "YOUR_HOME_WIFI" \
  --password "YOUR_WIFI_PASSWORD"
```

#### What this does automatically:
1. Queries the strip at `192.168.1.1:30300` to fetch its exact MAC address.
2. Writes the server IPv4 callback destination (`up:ip:<server-ip>`).
3. Sends your home Wi-Fi credentials (`up:connect:<ssid>:<password>`).
4. Reaches out to the Volta Web API (`POST /api/claim`) to automatically claim the device MAC under your token account!
5. The strip reboots, joins your home Wi-Fi, and connects to Volta on port `10086`.

---

## Provisioning Method C: Web UI Manual Claim

If you already configured the strip to dial in to your server:
1. Open the Volta Web UI.
2. Navigate to **Setup → Claim a strip to your account**.
3. Enter the 12-character MAC address (e.g. `88D039132171`) and click **Claim Strip**.
4. If your plan has available capacity, the strip is instantly linked and live controls become active.
