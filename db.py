import os
import time
import logging
from contextlib import contextmanager

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False

logger = logging.getLogger("volta-db")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

class Database:
    def __init__(self, dsn=None):
        self.dsn = dsn or DATABASE_URL
        self.pool = None
        if self.dsn and PSYCOPG_AVAILABLE:
            try:
                self.pool = ConnectionPool(
                    conninfo=self.dsn,
                    min_size=1,
                    max_size=10,
                    open=False
                )
                self.pool.open()
                logger.info("[db] Connected to PostgreSQL / Supabase cluster")
            except Exception as e:
                logger.error(f"[db] Failed to initialize connection pool: {e}")
                self.pool = None
        elif not PSYCOPG_AVAILABLE:
            logger.warning("[db] psycopg not installed; falling back to offline/stub mode")

    @contextmanager
    def get_conn(self):
        if not self.pool:
            raise RuntimeError("Database pool not initialized")
        with self.pool.connection() as conn:
            yield conn

    def fetch_all(self, query, params=None):
        if not self.pool:
            return []
        with self.get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params or ())
                return cur.fetchall()

    def fetch_one(self, query, params=None):
        if not self.pool:
            return None
        with self.get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params or ())
                return cur.fetchone()

    def execute(self, query, params=None):
        if not self.pool:
            return 0
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                conn.commit()
                return cur.rowcount

    # -------------------------------------------------------------
    # Homes Management
    # -------------------------------------------------------------
    def list_homes(self, user_id=None):
        if user_id:
            return self.fetch_all(
                "SELECT * FROM homes WHERE user_id = %s OR user_id IS NULL ORDER BY sort_order, created_at",
                (user_id,)
            )
        return self.fetch_all("SELECT * FROM homes ORDER BY sort_order, created_at")

    def get_home(self, home_id):
        return self.fetch_one("SELECT * FROM homes WHERE id = %s", (home_id,))

    def create_home(self, home_id, name, user_id=None, icon="home", is_default=False):
        self.execute(
            """INSERT INTO homes (id, user_id, name, icon, is_default, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
               ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, icon = EXCLUDED.icon, updated_at = NOW()""",
            (home_id, user_id, name, icon, is_default)
        )
        return self.get_home(home_id)

    def update_home(self, home_id, name=None, icon=None, sort_order=None):
        fields = []
        params = []
        if name is not None:
            fields.append("name = %s")
            params.append(name)
        if icon is not None:
            fields.append("icon = %s")
            params.append(icon)
        if sort_order is not None:
            fields.append("sort_order = %s")
            params.append(sort_order)
        if not fields:
            return self.get_home(home_id)
        fields.append("updated_at = NOW()")
        params.append(home_id)
        sql = f"UPDATE homes SET {', '.join(fields)} WHERE id = %s"
        self.execute(sql, tuple(params))
        return self.get_home(home_id)

    def delete_home(self, home_id):
        # Set home_id = NULL for associated devices first, then delete home
        self.execute("UPDATE devices SET home_id = NULL WHERE home_id = %s", (home_id,))
        return self.execute("DELETE FROM homes WHERE id = %s", (home_id,))

    # -------------------------------------------------------------
    # Devices & Outlets
    # -------------------------------------------------------------
    def list_devices(self, home_id=None):
        if home_id:
            return self.fetch_all(
                "SELECT * FROM devices WHERE home_id = %s ORDER BY sort_order, created_at",
                (home_id,)
            )
        return self.fetch_all("SELECT * FROM devices ORDER BY sort_order, created_at")

    def assign_device_home(self, mac, home_id, room_id=None):
        mac = mac.upper()
        return self.execute(
            "UPDATE devices SET home_id = %s, room_id = %s WHERE mac = %s",
            (home_id, room_id, mac)
        )

    def upsert_device(self, mac, name, model="MTTL-W01", fw=None, ip=None, home_id=None):
        mac = mac.upper()
        return self.execute(
            """INSERT INTO devices (mac, name, model, fw_version, ip_address, home_id, last_seen)
               VALUES (%s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (mac) DO UPDATE SET
                   name = COALESCE(EXCLUDED.name, devices.name),
                   model = COALESCE(EXCLUDED.model, devices.model),
                   fw_version = COALESCE(EXCLUDED.fw_version, devices.fw_version),
                   ip_address = COALESCE(EXCLUDED.ip_address, devices.ip_address),
                   last_seen = NOW()""",
            (mac, name, model, fw, ip, home_id)
        )

    # -------------------------------------------------------------
    # Readings / Telemetry
    # -------------------------------------------------------------
    def record_reading(self, mac, outlet, is_on, power_w=0.0, energy_kwh=0.0, temp_c=0, voltage_v=0.0, rssi=0):
        return self.execute(
            """INSERT INTO readings (mac, outlet, is_on, power_w, energy_kwh, temp_c, voltage_v, rssi, ts)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
            (mac.upper(), outlet, bool(is_on), power_w, energy_kwh, temp_c, voltage_v, rssi)
        )

# Global database instance
db = Database()
