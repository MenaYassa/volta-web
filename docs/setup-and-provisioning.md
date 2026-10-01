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
2. **Volta Host IP**:
   - The public IPv4 address or local network IP of your Volta server where port `10086` is reachable.

---

## Provisioning Method A: Android / Termux (Recommended)

Because the strip in AP mode provides **no internet routing**, provisioning requires downloading the script first, then connecting to the strip hotspot.

### Step 1: Download `controller.py` while on regular Wi-Fi or Mobile Data
Open Termux and run:
```bash
pkg install -y python
curl -s https://volta.your-domain.com/controller.py -o controller.py
```

### Step 2: Connect Phone to Strip Wi-Fi
- Go to Android Wi-Fi settings.
- Select `TONLY_TAP_<SUFFIX>` and enter `LGU_<SUFFIX>`.
- *(If Android warns "Wi-Fi has no internet access", select "Stay connected" or "Keep connection".)*

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

## Provisioning Method B: Manual / Web UI Claim

If you prefer to configure the strip manually or using an existing setup tool:
1. Connect a laptop or phone to the strip's AP (`192.168.1.1:30300`).
2. Send the commands via raw TCP socket (CRLF terminated):
   ```text
   up:ip:YOUR_SERVER_IP
   up:connect:YOUR_SSID:YOUR_PASSWORD
   ```
3. Once the strip joins your Wi-Fi, open the Volta Web UI.
4. Navigate to **Setup → Claim a strip to your account**.
5. Enter the 12-character MAC address (e.g. `88D039132171`) and click **Claim Strip**.
