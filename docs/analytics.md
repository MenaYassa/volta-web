# Analytics & Telemetry Engine

Volta records time-series electrical and environmental telemetry into a local SQLite database (`analytics.db`), mounted persistently outside Docker.

---

## 1. Data Collection

Every **30 seconds**, the background collector thread in `analytics.py` records a snapshot of all active outlets across all connected strips into the `readings` table:

```sql
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,          -- Unix epoch timestamp
    mac TEXT NOT NULL,            -- Strip MAC address (12 hex digits)
    outlet INTEGER NOT NULL,      -- Outlet index (1 to 4)
    is_on INTEGER NOT NULL,       -- Relay state (1 = ON, 0 = OFF)
    power_w REAL NOT NULL,        -- Instantaneous active power in Watts
    energy_kwh REAL NOT NULL,     -- Cumulative meter reading in kWh
    temp_c INTEGER,               -- Thermistor reading in Celsius
    voltage_v REAL,               -- Grid voltage in Volts
    rssi INTEGER                  -- Wi-Fi signal in dBm
);
```

### Database Indexes
- `idx_readings_mac_ts (mac, ts)`
- `idx_readings_ts (ts)`

---

## 2. Canonical Summary & KPIs (`POST /api/analytics/summary`)

To eliminate wasteful client-side loops and inconsistent multi-strip calculations across web and mobile clients, Volta provides a single canonical endpoint for period KPIs:

- **Endpoint**: `POST /api/analytics/summary` (or alias `POST /api/analytics/kpis`)
- **Payload**:
  ```json
  {
    "range": "24h",
    "mac": "__all__"
  }
  ```
- **Response**:
  ```json
  {
    "ok": true,
    "range": "24h",
    "kpis": {
      "avg_power_w": 284.5,
      "energy_kwh": 6.828,
      "cost": 14.88,
      "cost_rate": 2.18,
      "currency": "EGP",
      "avg_voltage_v": 221.4,
      "max_temp_c": 38.0
    }
  }
  ```

### Multi-Strip Power Summation:
When querying all strips (`mac: "__all__"`), `avg_power_w` represents the **sum of all online strips' average loads** over the chosen period, ensuring the dashboard shows the true aggregate house power rather than an unweighted average of individual devices.

---

## 3. Leaderboard Calculation

The **Top Energy Consumers** leaderboard (`POST /api/analytics/leaderboard`) ranks outlets using cumulative kilowatt-hours:
1. Calculates `MAX(energy_kwh) - MIN(energy_kwh)`.
2. Fallback for short sample windows: calculates integral of average wattage over elapsed time:
   $$\text{kWh} = \frac{\text{avg\_power\_w} \times \Delta t}{3{,}600{,}000}$$
3. **Filtering**: Automatically excludes inactive/zero-consumption outlets (`HAVING energy_kwh > 0.00005 OR avg_power_w > 0.05`).
4. **Scoping**: Respects multi-tenancy by filtering strictly to the caller's assigned strips.
