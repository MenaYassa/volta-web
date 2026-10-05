# Alexa Smart Home Skill Integration Guide for Volta ⚡

This guide describes how to link Volta with **Amazon Alexa** to control strips and individual outlets using voice commands (*"Alexa, turn on Living Room AC"*, *"Alexa, turn off Desk Charger"*) and the Alexa mobile app.

---

## 1. Architecture Overview

Amazon Alexa Smart Home Skills use a directive-based architecture running on AWS Lambda:

```
┌──────────────────────────┐
│   Amazon Echo / App      │
└────────────┬─────────────┘
             │ Voice / App command
             ▼
┌──────────────────────────┐
│  Alexa Smart Home Skill  │ (AWS Developer Console)
└────────────┬─────────────┘
             │ Invokes
             ▼
┌──────────────────────────┐
│    AWS Lambda Function   │ (Python 3.12 / Node.js)
│    (alexa_lambda.py)     │
└────────────┬─────────────┘
             │ HTTPS Directive (Bearer Token)
             ▼
┌──────────────────────────┐
│       Volta API          │ (https://volta.your-domain.com)
│   /api/alexa/devices     │  - Discovery
│   /api/alexa/state       │  - Report State
│   /api/onoff             │  - TurnOn / TurnOff
└────────────┬─────────────┘
             │ Raw TCP Port 10086
             ▼
┌──────────────────────────┐
│     MTTL-W01 Strips      │
└──────────────────────────┘
```

---

## 2. Volta Backend API Endpoints for Alexa

The Volta server already provides the native endpoints required by the Alexa Smart Home Skill:

| Endpoint | Method | Role |
|---|---|---|
| `/api/alexa/devices` | `GET` | Returns list of endpoints formatted for `Alexa.Discovery` response. |
| `/api/alexa/state?endpointId=...` | `GET` | Returns `powerState` (`ON`/`OFF`) and connectivity for `Alexa.ReportState`. |
| `/api/onoff` | `POST` | Toggles outlet relay (`Alexa.PowerController` `TurnOn` / `TurnOff`). |

---

## 3. Step-by-Step Setup Guide

### Step 3.1: Create the AWS Lambda Function

1. Log into your **AWS Management Console** and navigate to **AWS Lambda**.
2. Click **Create function**:
   - **Function name**: `volta-smart-home-skill`
   - **Runtime**: `Python 3.12`
   - **Architecture**: `x86_64`
3. Click **Create function**.
4. In the **Code source** editor, copy and paste the contents of [`lambda_function.py`](./alexa-lambda/lambda_function.py) (provided in this repository).
5. Configure Environment Variables under **Configuration → Environment variables**:
   - `VOLTA_BASE_URL`: `https://volta.your-domain.com` (or your domain)
   - `VOLTA_DEFAULT_TOKEN`: *(Optional master token or leave blank to rely on Account Linking)*
6. Click **Deploy**.

---

### Step 3.2: Create the Alexa Skill in Amazon Developer Console

1. Go to the [Amazon Developer Console](https://developer.amazon.com/alexa/console/ask) and sign in.
2. Click **Create Skill**:
   - **Skill name**: `Volta Smart Home`
   - **Primary locale**: English (US) / English (UK)
   - **Model**: Select **Smart Home**.
   - **Hosting**: Select **Provision your own** (since we use AWS Lambda).
3. Click **Create Skill**.
4. In the **Smart Home Service Endpoint** section:
   - Copy your **Skill ID** (e.g. `amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`).
5. Go back to AWS Lambda:
   - Click **+ Add trigger** $\rightarrow$ select **Alexa Smart Home**.
   - Paste the **Skill ID** $\rightarrow$ click **Add**.
   - Copy the Lambda **Function ARN** (top right, e.g. `arn:aws:lambda:us-east-1:123456789012:function:volta-smart-home-skill`).
6. Return to Amazon Developer Console:
   - Under **Default endpoint**, paste your Lambda Function ARN.
   - Click **Save**.

---

### Step 3.3: Account Linking (Connecting User Token to Alexa)

To authorize Alexa to access your Volta strips:

1. In the Alexa Developer Console, click **Account Linking** in the left sidebar.
2. If using standard OAuth or custom token:
   - **Authorization URI**: `https://accounts.google.com/o/oauth2/v2/auth` (or your Volta OAuth bridge)
   - **Access Token URI**: `https://oauth2.googleapis.com/token`
   - **Client ID**: `YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com`
   - **Client Secret**: `YOUR_GOOGLE_CLIENT_SECRET`
   - **Scopes**: `openid`, `email`, `profile`
3. Click **Save**.

---

## 4. Voice Commands Supported

Once linked in the Alexa app, Alexa will automatically discover all active outlets. You can speak naturally:

* *"Alexa, turn on Outlet 1"*
* *"Alexa, turn off Living Room AC"*
* *"Alexa, is Desk Charger on?"*
* *"Alexa, turn off all smart plugs in Office"*
