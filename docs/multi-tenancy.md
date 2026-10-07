# Multi-Tenancy, Subscriptions & Security

Volta includes an enterprise-grade multi-tenant isolation and subscription management layer that allows a single server installation to be securely shared among family members, tenants, or customers with strict quota and lifecycle enforcement.

---

## 1. Authentication Roles & Token Formats

Volta uses a token-based authentication model passing the token via the `X-Token` header:

| Role | Token Format | Privileges |
|---|---|---|
| **Administrator** | Master Token (`VOLTA_TOKEN` in `.env`) | Full superuser access. View all strips, configure global server settings, manage user accounts, assign strips, modify subscription plans, and inspect gateway logs. |
| **Standard User** | Scoped Token (`volta_usr_...`) *(Legacy `voltra_usr_` supported)* | Scoped access. Can only view, control, name, schedule, and view analytics for strips explicitly assigned to them. Cannot access gateway logs, unassigned devices, or internal server IPs. |

---

## 2. Subscription Lifecycle & Limits

Each user account in Volta has structured subscription and quota parameters stored in `strips.json`:

```json
{
  "name": "Mina",
  "strips": ["88D039132171", "6C5AB554FED3"],
  "max_strips": 5,
  "created_at": 1711200000,
  "expires_at": 1742736000,
  "status": "active"
}
```

### Plan Parameters:
- **`status`**: `"active"`, `"canceled"`, or `"expired"`.
- **`max_strips`**: Maximum number of strips allowed to be claimed/assigned concurrently (default: `10`).
- **`expires_at`**: Unix timestamp when the subscription ends. `null` indicates a **Lifetime / Unlimited** account.

### Subscription Enforcement & Gating:
1. **Device Capacity Gating (`POST /api/claim`)**:
   - If claiming a new strip exceeds `max_strips`, the server rejects the request with `403 Forbidden` (`Subscription strip limit reached (X/Y). Please upgrade your plan.`).
2. **Account Lockout (`GET /api/auth/whoami`)**:
   - If an account has `status: "canceled"` or the current time exceeds `expires_at`, `whoami` returns `401 Unauthorized` with:
     ```json
     {
       "authorized": false,
       "subscription_expired": true,
       "status": "expired",
       "expires_at": 1742736000,
       "error": "Subscription expired or canceled. Please contact admin."
     }
     ```
3. **Smart Renewal Stacking (No Lost Time)**:
   - When an Administrator extends an active subscription (e.g. `+ 1 Month`, `+ 1 Year`), the duration is **stacked onto the remaining time** rather than resetting from today:
     $$\text{New Expiration} = \text{Current Expiration} + \text{Duration}$$
   - Only lapsed/expired subscriptions start from the current day.
4. **Urgent Expiration Warnings (< 30 Days)**:
   - When a user has $\le$ 30 days remaining on their plan, an alert banner appears on their dashboard with remaining day count and renewal guidance.
   - The Admin User Management table displays an **Expires** column beside **Created** with color-coded warning pills (Amber for $\le 30$ days, Red for $\le 7$ days).

---

## 3. Authorization Context & Scoping

Every request to `/api/*` is authenticated via `resolve_auth_context(token)`:

```json
{
  "authorized": true,
  "is_admin": false,
  "user_token": "volta_usr_269b58bd35d783c9",
  "user_name": "Mina",
  "strips": ["88D039132171", "6C5AB554FED3"],
  "max_strips": 5,
  "expires_at": 1742736000,
  "status": "active"
}
```

### Strict Scoping Rules:
- **`GET /api/live` & `GET /api/strips`**: Non-admin users only see devices present in their `strips` array.
- **`POST /api/onoff`**: Verifies `can_access_mac(mac)`. Switching unowned strips returns `403 Forbidden`. Attempting to switch an outlet locked with accidental toggle protection returns `409 Conflict`.
- **`POST /api/outlet/lock`**: Verifies `can_access_mac(mac)`. Unauthorized users cannot modify lock states on unowned strips.
- **`POST /api/strip/order`**: Scoped per-user. Stores user-specific card ordering without impacting other tenants.
- **`GET /api/log`**: Restricted strictly to Admins; non-admins receive `403 Forbidden`.
- **`POST /api/analytics/*`**: Restricts timeseries metrics, aggregate stats, and leaderboard rankings strictly to assigned strips.
- **`⚠️ Unassigned` Filter Chip**: Exclusively visible to administrators.
- **`Strips` Provisioning Tab**: Hidden from regular user UI navigation (admin-only). Regular users unclaim their strips directly via the card header.

---

## 4. Multi-User Device Assignment

Strips can be shared across multiple user accounts (e.g. household members or co-tenants):

- **Assigning Multiple Users (`POST /api/claim`)**:
  ```json
  {
    "mac": "88D039132171",
    "target_tokens": [
      "volta_usr_269b58bd35d783c9",
      "volta_usr_0b73c6acf940c429"
    ]
  }
  ```
- **Web UI Assignment Modal**:
  - In **Strips & Names**, clicking the user pill opens the multi-select assignment modal allowing instantaneous multi-user assignment with real-time quota validation (`X/Y strips used`).

---

## 5. User Management API (Admin Only)

Administrators have access to user management under the **Settings** tab:

### Create User
- **`POST /api/users`**
```json
{
  "name": "Guest Bedroom",
  "max_strips": 3,
  "sub_value": 6,
  "sub_unit": "months"
}
```
*Response:*
```json
{
  "ok": true,
  "token": "volta_usr_7f8e9d0c...",
  "user": {
    "name": "Guest Bedroom",
    "strips": [],
    "max_strips": 3,
    "expires_at": 1758451200,
    "status": "active"
  }
}
```

### Update User, Plan & Limits
- **`POST /api/users`** (with existing user token)
```json
{
  "token": "volta_usr_7f8e9d0c...",
  "name": "Mina Office",
  "max_strips": 8,
  "status": "active",
  "sub_value": 1,
  "sub_unit": "years"
}
```

### Revoke / Delete User
- **`DELETE /api/users?token=<target_token>`**
Revokes the token. All claimed strips associated with the account are disassociated.
