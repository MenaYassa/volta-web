# Multi-Tenancy & Security

Volta includes a built-in multi-tenant isolation layer that allows a single server installation to be shared securely among multiple family members, roommates, or tenants.

---

## 1. Authentication Roles

Volta uses a token-based authentication model:

| Role | Token Type | Privileges |
|---|---|---|
| **Administrator** | Master Token (`VOLTA_TOKEN` or `VOLTRA_TOKEN` in `.env`) | Full superuser access. Can see all strips, modify server configuration, manage users, and transfer device ownership. |
| **Standard User** | Scoped Token (`volta_usr_...`) | Scoped access. Can only view, control, name, schedule, and view analytics for strips they own. |

---

## 2. Authorization Context & Scoping

Every request to `/api/*` is authenticated via the `X-Token` header (or request body/query parameter). The server resolves the token via `resolve_auth_context(token)`:

```json
{
  "authorized": true,
  "is_admin": false,
  "user_token": "volta_usr_a1b2c3...",
  "name": "Mina",
  "strips": ["88D039132171", "6C5AB554FED3"]
}
```

### Scoped Endpoints:
- **`GET /api/live`**: Only returns devices belonging to `strips`. Unclaimed strips or strips owned by other users are excluded.
- **`POST /api/onoff`**: Verifies `can_access_mac(mac)`. If a user attempts to switch another tenant's outlet, the server returns `403 Forbidden`.
- **`POST /api/rename`**: Allows naming only owned devices.
- **`POST /api/analytics/*`**: Restricts timeseries metrics, aggregate stats, and leaderboard rankings to the user's claimed strips.

---

## 3. User Management (Admin Only)

Administrators have access to user management under the **Settings** tab or via REST API:

### Create User
- **`POST /api/users`**
```json
{
  "name": "Guest Room",
  "token": "optional_custom_token"
}
```
*Response:*
```json
{
  "ok": true,
  "name": "Guest Room",
  "token": "volta_usr_7f8e9d0c..."
}
```

### Revoke User
- **`DELETE /api/users?token=<target_token>`**
Revokes the token. All claimed strips owned by the deleted account are released back to the global pool.

### Transfer Strip Ownership
- **`POST /api/claim`**
```json
{
  "mac": "88D039132171",
  "target_token": "volta_usr_destination_token"
}
```
Transfers ownership of the specified strip from one user to another.
