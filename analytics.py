"""Time-series analytics engine for power/energy tracking."""
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "analytics.db"
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create tables if not exist."""
    with _lock, _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                mac TEXT NOT NULL,
                outlet INTEGER NOT NULL,
                is_on INTEGER NOT NULL,
                power_w REAL,
                energy_kwh REAL,
                temp_c INTEGER,
                voltage_v REAL,
                rssi INTEGER
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_readings_mac ON readings(mac, outlet, ts)")
        db.commit()


def record_snapshot(devices):
    """Store current telemetry for all devices. Called every 30s by collector."""
    now = int(time.time())
    with _lock, _conn() as db:
        for d in devices:
            mac = d.get("mac", "").upper()
            if not mac:
                continue
            voltage = d.get("voltage_v")
            rssi = d.get("rssi")
            for o in d.get("outlets", []):
                db.execute("""
                    INSERT INTO readings (ts, mac, outlet, is_on, power_w, energy_kwh, temp_c, voltage_v, rssi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (now, mac, o["n"], int(o["on"]), o.get("power_w"), o.get("energy_kwh"),
                      o.get("temp_c"), voltage, rssi))
        db.commit()


def query_range(mac, outlet, start_ts, end_ts, downsample=None):
    """Return readings for a MAC+outlet in [start_ts, end_ts].
    downsample: None (raw) | 'minute' | 'hour' — aggregates to reduce points."""
    with _lock, _conn() as db:
        if downsample == "minute":
            sql = """
                SELECT (ts / 60) * 60 AS ts,
                       AVG(is_on) AS is_on,
                       AVG(power_w) AS power_w,
                       MAX(energy_kwh) - MIN(energy_kwh) AS energy_delta,
                       AVG(temp_c) AS temp_c,
                       AVG(voltage_v) AS voltage_v,
                       AVG(rssi) AS rssi
                FROM readings
                WHERE mac = ? AND outlet = ? AND ts >= ? AND ts <= ?
                GROUP BY ts / 60
                ORDER BY ts
            """
        elif downsample == "hour":
            sql = """
                SELECT (ts / 3600) * 3600 AS ts,
                       AVG(is_on) AS is_on,
                       AVG(power_w) AS power_w,
                       MAX(energy_kwh) - MIN(energy_kwh) AS energy_delta,
                       AVG(temp_c) AS temp_c,
                       AVG(voltage_v) AS voltage_v,
                       AVG(rssi) AS rssi
                FROM readings
                WHERE mac = ? AND outlet = ? AND ts >= ? AND ts <= ?
                GROUP BY ts / 3600
                ORDER BY ts
            """
        else:
            sql = """
                SELECT ts, is_on, power_w, energy_kwh, temp_c, voltage_v, rssi
                FROM readings
                WHERE mac = ? AND outlet = ? AND ts >= ? AND ts <= ?
                ORDER BY ts
            """
        rows = db.execute(sql, (mac.upper(), outlet, start_ts, end_ts)).fetchall()
        return [dict(r) for r in rows]


def query_strip_all_outlets(mac, start_ts, end_ts, downsample=None):
    """Return readings for all outlets (1-4) of a strip, grouped by ts and outlet."""
    with _lock, _conn() as db:
        if downsample == "hour":
            group = "(ts / 3600) * 3600"
        elif downsample == "minute":
            group = "(ts / 60) * 60"
        else:
            group = "ts"
        sql = f"""
            SELECT {group} AS ts,
                   outlet,
                   AVG(is_on) AS is_on,
                   AVG(power_w) AS power_w,
                   AVG(temp_c) AS temp_c,
                   AVG(voltage_v) AS voltage_v
            FROM readings
            WHERE mac = ? AND ts >= ? AND ts <= ?
            GROUP BY {group}, outlet
            ORDER BY ts, outlet
        """
        rows = db.execute(sql, (mac.upper(), start_ts, end_ts)).fetchall()
        return [dict(r) for r in rows]


def query_all_strips_power(start_ts, end_ts, downsample=None, macs=None):
    """Return total power aggregated across strips and outlets, grouped by ts and mac."""
    with _lock, _conn() as db:
        if downsample == "hour":
            group = "(ts / 3600) * 3600"
        elif downsample == "minute":
            group = "(ts / 60) * 60"
        else:
            group = "ts"
        params = [start_ts, end_ts]
        mac_clause = ""
        if macs is not None:
            clean_macs = [str(m).upper().replace(":", "") for m in macs]
            if not clean_macs:
                return []
            placeholders = ",".join("?" for _ in clean_macs)
            mac_clause = f"AND mac IN ({placeholders})"
            params.extend(clean_macs)
        sql = f"""
            SELECT {group} AS ts,
                   mac,
                   SUM(power_w) AS power_w,
                   AVG(voltage_v) AS voltage_v,
                   AVG(CASE WHEN temp_c > 0 THEN temp_c ELSE NULL END) AS temp_c
            FROM readings
            WHERE ts >= ? AND ts <= ? {mac_clause}
            GROUP BY {group}, mac
            ORDER BY ts, mac
        """
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def strip_stats(mac, start_ts, end_ts):
    """Aggregate stats for entire strip (all 4 outlets combined)."""
    with _lock, _conn() as db:
        row = db.execute("""
            SELECT COUNT(*) AS samples,
                   SUM(power_w) AS total_power_w,
                   AVG(power_w) AS avg_power_w,
                   MAX(power_w) AS peak_power_w,
                   AVG(voltage_v) AS avg_voltage_v,
                   MIN(CASE WHEN voltage_v > 0 THEN voltage_v ELSE NULL END) AS min_voltage_v,
                   MAX(voltage_v) AS max_voltage_v,
                   AVG(CASE WHEN temp_c > 0 THEN temp_c ELSE NULL END) AS avg_temp_c,
                   MIN(CASE WHEN temp_c > 0 THEN temp_c ELSE NULL END) AS min_temp_c,
                   MAX(CASE WHEN temp_c > 0 THEN temp_c ELSE NULL END) AS max_temp_c
            FROM readings
            WHERE mac = ? AND ts >= ? AND ts <= ?
        """, (mac.upper(), start_ts, end_ts)).fetchone()
        stats = dict(row) if row else {}
        # Period energy: per-outlet meter delta (end - first sample in window).
        # Outlets that are OFF report a zeroed meter, so MAX-MIN across the whole
        # strip window overcounts; first/last per outlet is the correct delta.
        rows = db.execute("""
            SELECT outlet,
                   MAX(energy_kwh) AS e_end,
                   (SELECT energy_kwh FROM readings r2
                     WHERE r2.mac = r.mac AND r2.outlet = r.outlet
                       AND r2.ts >= ? AND r2.ts <= ? AND r2.energy_kwh > 0
                     ORDER BY ts ASC LIMIT 1) AS e_start,
                   AVG(CASE WHEN power_w > 0 THEN power_w END) AS avg_pos_w,
                   MIN(ts) AS t_min, MAX(ts) AS t_max
            FROM readings r
            WHERE mac = ? AND ts >= ? AND ts <= ?
            GROUP BY outlet
        """, (start_ts, end_ts, mac.upper(), start_ts, end_ts)).fetchall()
        kwh = 0.0
        for r in rows:
            if r["e_end"] is not None and r["e_start"] is not None:
                kwh += max(0.0, r["e_end"] - r["e_start"])
            elif r["avg_pos_w"] and r["t_max"] and r["t_min"] and r["t_max"] > r["t_min"]:
                # meter unavailable: integrate positive power over the window
                kwh += (r["avg_pos_w"] * (r["t_max"] - r["t_min"])) / 3600000.0
        stats["energy_delta_kwh"] = kwh
        return stats


def outlet_leaderboard(start_ts, end_ts, limit=10, macs=None):
    """Top consumers in period: ranked strictly by cumulative consumption (kWh) first.
    If macs is provided (list or tuple), filters strictly to those MAC addresses.
    Energy = per-outlet meter delta (last - first positive sample in window),
    with power-integration fallback. Excludes outlets with zero consumption."""
    with _lock, _conn() as db:
        mac_clause = ""
        if macs is not None:
            clean_macs = [str(m).upper().replace(":", "") for m in macs]
            if not clean_macs:
                return []
            placeholders = ",".join("?" for _ in clean_macs)
            mac_clause = f"AND mac IN ({placeholders})"

        # Base per-outlet aggregates
        base_rows = db.execute(f"""
            SELECT mac, outlet,
                   AVG(power_w) AS avg_power_w,
                   AVG(CASE WHEN power_w > 0 THEN power_w END) AS avg_pos_w,
                   MAX(power_w) AS peak_power_w,
                   MIN(ts) AS t_min, MAX(ts) AS t_max
            FROM readings
            WHERE ts >= ? AND ts <= ? {mac_clause}
            GROUP BY mac, outlet
            HAVING (MAX(power_w) > 0.1) OR (AVG(power_w) > 0.05)
        """, [start_ts, end_ts]).fetchall()

        out = []
        for r in base_rows:
            # meter delta for this outlet within the window
            er = db.execute("""
                SELECT MAX(energy_kwh) AS e_end,
                       (SELECT energy_kwh FROM readings r2
                         WHERE r2.mac = ? AND r2.outlet = ?
                           AND r2.ts >= ? AND r2.ts <= ? AND r2.energy_kwh > 0
                         ORDER BY ts ASC LIMIT 1) AS e_start
            """, (r["mac"], r["outlet"], start_ts, end_ts)).fetchone()
            if er and er["e_end"] is not None and er["e_start"] is not None:
                kwh = max(0.0, er["e_end"] - er["e_start"])
            elif r["avg_pos_w"] and r["t_max"] > r["t_min"]:
                kwh = (r["avg_pos_w"] * (r["t_max"] - r["t_min"])) / 3600000.0
            else:
                kwh = 0.0
            if kwh <= 0.00005 and (r["peak_power_w"] or 0) <= 0.1 and (r["avg_power_w"] or 0) <= 0.05:
                continue
            out.append({
                "mac": r["mac"], "outlet": r["outlet"],
                "energy_kwh": kwh,
                "avg_power_w": r["avg_power_w"] or 0.0,
                "peak_power_w": r["peak_power_w"] or 0.0,
                "watt_hours": kwh * 1000.0,
                "samples": 0,
            })
        out.sort(key=lambda x: (x["energy_kwh"], x["avg_power_w"], x["peak_power_w"]), reverse=True)
        return out[:limit]


def prune_old(days=30):
    """Delete readings older than N days to keep DB size reasonable."""
    cutoff = int(time.time()) - (days * 86400)
    with _lock, _conn() as db:
        db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        db.execute("VACUUM")
        db.commit()


def db_size_mb():
    """Return DB file size in MB."""
    if not DB_PATH.exists():
        return 0.0
    return round(DB_PATH.stat().st_size / 1048576, 2)


def collector_loop(controller, interval=30):
    """Background thread: snapshot telemetry every N seconds."""
    while True:
        try:
            if controller and controller.wait_ready(0):
                snap = controller.snapshot()
                if snap and snap.get("devices"):
                    record_snapshot(snap["devices"])
                    print(f"[analytics] collected {len(snap['devices'])} devices snapshot", flush=True)
        except Exception as e:
            print(f"[analytics] collector error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    init_db()
    print("analytics.db initialized")
