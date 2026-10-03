#!/usr/bin/env python3
"""Volta-local: home LAN gateway for TONLY MTTL-W01 power strips.

No login, no cloud. Run on any machine on your home LAN:

    python3 server.py            # serves UI on http://0.0.0.0:8080

Stdlib only. Data file: strips.json (same dir).
"""
import concurrent.futures as futures
import ipaddress
import json
import os
import secrets
import socket
import analytics
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import controller as ctrl
import tonly
import uuid

os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("VOLTA_WEB_PORT", os.environ.get("VOLTRA_WEB_PORT", "8080")))
DEVICE_PORT = int(os.environ.get("VOLTA_DEVICE_PORT", os.environ.get("VOLTRA_DEVICE_PORT", "10086")))
TOKEN = os.environ.get("VOLTA_TOKEN", os.environ.get("VOLTRA_TOKEN", ""))
PUBLIC_SERVER_IP = os.environ.get("VOLTA_PUBLIC_IP", "")
STRIPS_FILE = "strips.json"
LOG = []  # in-memory ring
CTL = None  # controller.Controller, started in __main__
PENDING_AUTO_CLAIMS = {}  # {client_ip: (token, expiry_timestamp)}


def register_pending_claim(ip, token, ttl=300):
    if not token or not ip:
        return
    PENDING_AUTO_CLAIMS[ip] = (token, time.time() + ttl)


def check_and_apply_auto_claim(mac, client_ip):
    """If an incoming connection from client_ip has an active pending claim, claim it!"""
    if not mac or not client_ip:
        return None
    mac = norm_mac(mac).upper()
    now = time.time()
    claim_info = PENDING_AUTO_CLAIMS.get(client_ip)
    if not claim_info:
        return None
    token, expiry = claim_info
    if now > expiry:
        PENDING_AUTO_CLAIMS.pop(client_ip, None)
        return None
    
    users = get_users()
    user_entry = users.get(token)
    if not user_entry:
        return None
    
    strips_list = user_entry.setdefault("strips", [])
    if mac not in strips_list:
        strips_list.append(mac)
        save_users(users)
        log(f"[auto-claim] Strip {mac} automatically claimed for user {user_entry.get('name')} ({token[:10]}...) via IP {client_ip}")
    return user_entry.get("name")


def log(msg):
    line = time.strftime("%H:%M:%S") + " " + msg
    LOG.append(line)
    if len(LOG) > 300:
        del LOG[: len(LOG) - 300]
    print(line, flush=True)


def load_strips():
    try:
        with open(STRIPS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"strips": []}


def save_strips(data):
    with open(STRIPS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def norm_mac(m):
    return "".join(c for c in (m or "").lower() if c in "0123456789abcdef")


def get_schedules():
    return load_strips().get("schedules", [])


def get_timers():
    return load_strips().get("timers", [])


def save_timers(timers):
    data = load_strips()
    data["timers"] = timers
    save_strips(data)


def get_users():
    """Return dictionary of user accounts: {token: {name, strips, created_at}}."""
    data = load_strips()
    return data.setdefault("users", {})


def save_users(users_dict):
    data = load_strips()
    data["users"] = users_dict
    save_strips(data)


def resolve_auth_context(token_str):
    """Determine role and permitted MACs for a given token string.
    Returns:
      {
        "authorized": bool,
        "is_admin": bool,
        "token": str,
        "user_name": str,
        "strips": list of uppercase MACs (or None if admin/unrestricted)
      }
    """
    token_str = (token_str or "").strip()
    if not TOKEN:
        # No master token configured: open LAN mode, acts as admin
        return {"authorized": True, "is_admin": True, "token": "", "user_name": "Admin", "strips": None}
    
    if token_str and secrets.compare_digest(token_str, TOKEN):
        return {"authorized": True, "is_admin": True, "token": TOKEN, "user_name": "Admin", "strips": None}
    
    users = get_users()
    for u_tok, info in users.items():
        if token_str and secrets.compare_digest(token_str, u_tok):
            # User token match
            user_strips = [str(m).upper().replace(":", "") for m in info.get("strips", [])]
            return {
                "authorized": True,
                "is_admin": False,
                "token": u_tok,
                "user_name": info.get("name", "User"),
                "strips": user_strips
            }
            
    return {"authorized": False, "is_admin": False, "token": "", "user_name": "", "strips": []}


def save_schedules(scheds):
    data = load_strips()
    data["schedules"] = scheds
    save_strips(data)
    return scheds


def get_settings():
    d = load_strips().get("settings", {})
    return {"lat": d.get("lat", 30.0444), "lon": d.get("lon", 31.2357),
            "ntfy": (d.get("ntfy") or "").strip(),
            "alert_offline_min": int(d.get("alert_offline_min", 10) or 10),
            "notify_offline": bool(d.get("notify_offline", True)),
            "notify_switch": bool(d.get("notify_switch", False)),
            "notify_filter": d.get("notify_filter", "all"),  # 'all' or 'selected'
            "notify_targets": d.get("notify_targets", []),  # list of 'MAC' or 'MAC:outlet'
            "cost_per_kwh": float(d.get("cost_per_kwh", 1.2) or 1.2),
            "currency": (d.get("currency") or "EGP").strip()[:10],
            "temp_alert_c": int(d.get("temp_alert_c", 60) or 60),  # alert if outlet temp exceeds this
            "notify_temp": bool(d.get("notify_temp", True)),
            "voltage_min": float(d.get("voltage_min", 200.0) or 200.0),  # alert if voltage drops below
            "voltage_max": float(d.get("voltage_max", 250.0) or 250.0),  # alert if voltage exceeds
            "notify_voltage": bool(d.get("notify_voltage", True))}


def save_settings(patch):
    data = load_strips()
    d = data.setdefault("settings", {})
    for k in ("lat", "lon", "ntfy", "alert_offline_min", "notify_offline",
              "notify_switch", "notify_filter", "notify_targets", "cost_per_kwh",
              "currency", "temp_alert_c", "notify_temp", "voltage_min", "voltage_max", "notify_voltage"):
        if k in patch:
            d[k] = patch[k]
    save_strips(data)
    return get_settings()


def sun_times(date, lat, lon, tz):
    """Rise/set as tz-aware datetimes (NOAA approx, stdlib only)."""
    import datetime as _dt
    import math as _m
    import zoneinfo as _zi
    zi = _zi.ZoneInfo(tz)
    n = date.timetuple().tm_yday
    lng_h = lon / 15.0
    out = {}
    for ev_name, zenith in (("rise", 90.833), ("set", 90.833)):
        is_rise = (ev_name == "rise")
        t = n + ((6 - lng_h) / 24 if is_rise else (18 - lng_h) / 24)
        m = (0.9856 * t) - 3.289
        l = (m + 1.916 * _m.sin(_m.radians(m)) + 0.020 * _m.sin(_m.radians(2 * m)) + 282.634) % 360
        ra = (_m.degrees(_m.atan(0.91764 * _m.tan(_m.radians(l)))) % 360)
        lq = (l // 90) * 90
        raq = (ra // 90) * 90
        ra = (ra + (lq - raq)) / 15.0
        sin_dec = 0.39782 * _m.sin(_m.radians(l))
        cos_dec = _m.cos(_m.asin(sin_dec))
        cos_h = (_m.cos(_m.radians(zenith)) - sin_dec * _m.sin(_m.radians(lat))) / (cos_dec * _m.cos(_m.radians(lat)))
        cos_h = max(-1.0, min(1.0, cos_h))
        h = ((360 - _m.degrees(_m.acos(cos_h))) if is_rise else _m.degrees(_m.acos(cos_h))) / 15.0
        t_utc = h + ra - (0.06571 * t) - 6.622
        t_utc = (t_utc - lng_h) % 24
        base = _dt.datetime(date.year, date.month, date.day, tzinfo=_dt.timezone.utc)
        out[ev_name] = (base + _dt.timedelta(hours=t_utc)).astimezone(zi)
    return out


def sched_fire_time(s, now):
    """Resolve a schedule to today's HH:MM. Sun kinds use lat/lon + offset."""
    if s.get("kind", "time") == "sun":
        import datetime as _dt
        try:
            st = get_settings()
            ev = sun_times(now.date(), float(st["lat"]), float(st["lon"]), s.get("tz") or "Africa/Cairo")
            fire = ev["rise" if s.get("sun") == "rise" else "set"] + \
                _dt.timedelta(minutes=int(s.get("offset_min", 0) or 0))
            return fire.strftime("%H:%M"), "sun %s%+dmin" % (s.get("sun"), int(s.get("offset_min", 0) or 0))
        except Exception:
            return None, ""
    return s.get("time"), ""


def validate_schedule(b):
    """Returns (sched_dict, error). Kinds: time HH:MM, or sun rise/set+offset."""
    import uuid as _uuid
    mac = norm_mac(b.get("mac"))
    if not mac or len(mac) != 12:
        return None, "mac must be a full strip MAC"
    try:
        outlet = int(b.get("outlet", 1))
    except (TypeError, ValueError):
        return None, "outlet must be 0-4 (0 = all)"
    if outlet not in (0, 1, 2, 3, 4):
        return None, "outlet must be 0-4 (0 = all)"
    kind = (b.get("kind") or "time").strip().lower()
    if kind not in ("time", "sun"):
        return None, "kind must be time or sun"
    if kind == "time":
        import re as _re
        t = (b.get("time") or "").strip()
        if not _re.fullmatch(r"[0-2]\d:[0-5]\d", t):
            return None, "time must be HH:MM (24h)"
        hh, mm = int(t[:2]), int(t[3:])
        if hh > 23:
            return None, "time must be HH:MM (24h)"
        t = f"{hh:02d}:{mm:02d}"
        sun, offset = "", 0
    else:
        sun = (b.get("sun") or "set").strip().lower()
        if sun not in ("rise", "set"):
            return None, "sun must be rise or set"
        try:
            offset = int(b.get("offset_min", 0) or 0)
        except (TypeError, ValueError):
            return None, "offset must be minutes"
        if abs(offset) > 360:
            return None, "offset must be within ±360 min"
        t = ""
    days = b.get("days", [0, 1, 2, 3, 4, 5, 6])
    try:
        days = sorted({int(d) for d in days})
    except (TypeError, ValueError):
        return None, "days must be 0-6 (Mon=0)"
    if not days or any(d < 0 or d > 6 for d in days):
        return None, "days must be 0-6 (Mon=0)"
    tz = (b.get("tz") or "Africa/Cairo").strip() or "Africa/Cairo"
    try:
        import zoneinfo as _zi
        _zi.ZoneInfo(tz)
    except Exception:
        return None, f"unknown timezone {tz!r}"
    return {"id": (b.get("id") or _uuid.uuid4().hex[:12]),
            "mac": mac,
            "outlet": outlet, "on": bool(b.get("on")),
            "kind": kind, "time": t, "sun": sun, "offset_min": offset,
            "days": days, "tz": tz,
            "label": (b.get("label") or "").strip()[:60],
            "enabled": bool(b.get("enabled", True)),
            "last_fired": (b.get("last_fired") or "")}, ""


def should_notify(mac, outlet, event_type, st):
    """Check if notification is enabled for this event and target.
    event_type: 'offline' or 'switch'
    mac: 12-char hex (e.g. '88D039132171')
    outlet: 1-4 (or 0 for strip-level)
    """
    if not st.get("ntfy"):
        return False
    if event_type == "offline" and not st.get("notify_offline", True):
        return False
    if event_type == "switch" and not st.get("notify_switch", False):
        return False
    
    filt = st.get("notify_filter", "all")
    if filt == "all":
        return True
    
    # Check targets list: entries can be 'MAC' (all outlets) or 'MAC:outlet'
    targets = set(t.upper() for t in st.get("notify_targets", []))
    m = mac.upper()
    if m in targets:
        return True
    if outlet and f"{m}:{outlet}" in targets:
        return True
    return False


def ntfy_send(topic_or_url, title, msg, tags=""):
    import urllib.request as _u
    t = (topic_or_url or "").strip()
    if not t:
        return
    # If user passed a full URL (e.g. https://ntfy.sh/Power_Strip or ntfy.sh/Power_Strip), normalize it
    if t.startswith("http://") or t.startswith("https://"):
        target_url = t
    elif "/" in t:
        target_url = f"https://{t}"
    else:
        target_url = f"https://ntfy.sh/{t}"

    req = _u.Request(target_url,
                     data=msg.encode()[:3500],
                     headers={"Title": title[:200], "Tags": tags})
    with _u.urlopen(req, timeout=10) as r:
        r.read(1)


def watcher_loop():
    """Online/offline transitions + temperature alerts -> log + optional ntfy push."""
    last = {}
    while True:
        try:
            if CTL and CTL.wait_ready(0):
                st = get_settings()
                topic = st.get("ntfy")
                try:
                    thresh = max(1, int(st.get("alert_offline_min", 10)))
                except (TypeError, ValueError):
                    thresh = 10
                temp_limit = int(st.get("temp_alert_c", 60))
                notify_temp = bool(st.get("notify_temp", True))
                v_min = float(st.get("voltage_min", 200.0))
                v_max = float(st.get("voltage_max", 250.0))
                notify_voltage = bool(st.get("notify_voltage", True))
                snap = CTL.snapshot()
                names = load_strips().get("names", {})
                for d in snap.get("devices", []):
                    mac = (d.get("mac") or "").upper()
                    nm = names.get(mac.lower().replace(":", ""), {}).get("name") or d.get("name") or mac
                    was = last.get(mac)
                    now_on = bool(d.get("online"))
                    if was is None:
                        last[mac] = now_on
                        continue
                    if was != now_on:
                        last[mac] = now_on
                        evt = "online" if now_on else "offline"
                        ago = d.get("last_seen_ago", 0)
                        log(f"[watch] {nm} {evt} (last seen {ago}s ago)")
                        if topic and should_notify(mac, 0, "offline", st):
                            try:
                                ntfy_send(topic, f"Volta: {nm} {evt}",
                                          f"{nm} ({mac}) is {evt}. Last seen {ago}s ago.",
                                          "green_circle" if now_on else "red_circle")
                            except Exception as e:  # noqa: BLE001
                                log(f"[watch] ntfy failed: {e}")
                    elif not now_on and topic and d.get("last_seen_ago", 0) >= thresh * 60:
                        key = mac + ":alerted"
                        if not last.get(key):
                            last[key] = True
                            if should_notify(mac, 0, "offline", st):
                                try:
                                    ntfy_send(topic, f"Volta: {nm} still offline",
                                              f"{nm} ({mac}) offline {thresh}+ min.",
                                              "warning")
                                except Exception as e:  # noqa: BLE001
                                    log(f"[watch] ntfy failed: {e}")
                    elif now_on:
                        last.pop(mac + ":alerted", None)
                    
                    # Temperature monitoring (check each outlet)
                    if now_on and topic and notify_temp:
                        for outlet in d.get("outlets", []):
                            outlet_num = outlet.get("n")
                            temp = outlet.get("temp_c", 0)
                            if temp >= temp_limit:
                                temp_key = f"{mac}:{outlet_num}:temp"
                                if not last.get(temp_key):
                                    last[temp_key] = True
                                    outlet_names = names.get(mac.lower().replace(":", ""), {}).get("outlets", {})
                                    outlet_nm = outlet_names.get(str(outlet_num)) or f"Outlet {outlet_num}"
                                    if should_notify(mac, outlet_num, "offline", st):  # use same filter for now
                                        try:
                                            ntfy_send(topic, f"Volta: {nm} · {outlet_nm} HOT",
                                                      f"{outlet_nm} on {nm} is {temp}°C (limit {temp_limit}°C). Check immediately!",
                                                      "fire,warning")
                                            log(f"[watch] temp alert {nm} outlet {outlet_num}: {temp}°C")
                                        except Exception as e:  # noqa: BLE001
                                            log(f"[watch] ntfy failed: {e}")
                            elif temp < temp_limit - 5:  # clear alert when temp drops 5°C below threshold
                                last.pop(f"{mac}:{outlet_num}:temp", None)

                    # Voltage monitoring (strip level)
                    v_now = d.get("voltage_v")
                    if now_on and topic and notify_voltage and v_now is not None:
                        if v_now < v_min:
                            v_key = f"{mac}:v_low"
                            if not last.get(v_key):
                                last[v_key] = True
                                last.pop(f"{mac}:v_high", None)
                                if should_notify(mac, 0, "offline", st):
                                    try:
                                        ntfy_send(topic, f"Volta: {nm} LOW VOLTAGE",
                                                  f"{nm} is {v_now}V (min limit: {v_min}V). Risk of brownout/undervoltage!",
                                                  "warning,electric_plug")
                                        log(f"[watch] low voltage alert {nm}: {v_now}V < {v_min}V")
                                    except Exception as e:
                                        log(f"[watch] ntfy failed: {e}")
                        elif v_now > v_max:
                            v_key = f"{mac}:v_high"
                            if not last.get(v_key):
                                last[v_key] = True
                                last.pop(f"{mac}:v_low", None)
                                if should_notify(mac, 0, "offline", st):
                                    try:
                                        ntfy_send(topic, f"Volta: {nm} HIGH VOLTAGE",
                                                  f"{nm} is {v_now}V (max limit: {v_max}V). Risk of overvoltage/surge!",
                                                  "zap,warning")
                                        log(f"[watch] high voltage alert {nm}: {v_now}V > {v_max}V")
                                    except Exception as e:
                                        log(f"[watch] ntfy failed: {e}")
                        elif (v_min + 3) <= v_now <= (v_max - 3):
                            # Reset alert latch once safely back inside normal range
                            last.pop(f"{mac}:v_low", None)
                            last.pop(f"{mac}:v_high", None)
        except Exception as e:  # noqa: BLE001
            log(f"watcher error: {e}")
        time.sleep(30)


def scheduler_loop():
    """Fires due schedules every ~20s. Runs whether any phone is on."""
    import datetime as _dt
    import zoneinfo as _zi
    while True:
        try:
            if CTL and CTL.wait_ready(0):
                scheds = get_schedules()
                dirty = False
                for s in scheds:
                    if not s.get("enabled"):
                        continue
                    try:
                        now = _dt.datetime.now(_zi.ZoneInfo(s.get("tz") or "Africa/Cairo"))
                    except Exception:
                        continue
                    if now.weekday() not in s.get("days", []):
                        continue
                    fire, _note = sched_fire_time(s, now)
                    if not fire or now.strftime("%H:%M") != fire:
                        continue
                    key = now.strftime("%Y-%m-%d %H:%M")
                    if s.get("last_fired") == key:
                        continue
                    channels = [1, 2, 3, 4] if s["outlet"] == 0 else [s["outlet"]]
                    ok, info = CTL.set_outlets(s["mac"], channels, s["on"],
                                               source=f'sched:{s["id"]}')
                    s["last_fired"] = key
                    dirty = True
                    log(f'sched {s["id"]} {s["mac"]} outlet {s["outlet"]} '
                        f'{"on" if s["on"] else "off"}: {"OK" if ok else info}')
                if dirty:
                    save_schedules(scheds)
        except Exception as e:  # noqa: BLE001
            log(f"scheduler error: {e}")
        time.sleep(20)


def timer_loop():
    """Evaluates outlet timers every 2s: countdowns + cyclic on/off loops.
    Runs server-side so timers fire even with no phone/browser open."""
    while True:
        try:
            if CTL and CTL.wait_ready(0):
                now = time.time()
                timers = get_timers()
                dirty = False
                for t in timers:
                    if not t.get("enabled", True):
                        continue
                    try:
                        if t.get("mode", "countdown") == "countdown":
                            # countdown: first wait is always on_sec; after a fired
                            # inversion (repeat modes) alternate on/off waits
                            if t.get("cycles_done"):
                                phase_len = (t["on_sec"] if t.get("phase") == "on" else t["off_sec"]) or 0
                            else:
                                phase_len = t["on_sec"] or 0
                        else:
                            phase_len = (t["on_sec"] if t.get("phase") == "on" else t["off_sec"]) or 0
                    except (KeyError, TypeError):
                        continue
                    if phase_len <= 0:
                        continue
                    started = t.get("phase_started") or now
                    elapsed = now - started
                    if elapsed < phase_len:
                        continue
                    mac = t["mac"]
                    mode = t.get("mode", "countdown")
                    channels = [1, 2, 3, 4] if t.get("outlet", 0) == 0 else [int(t["outlet"])]
                    if mode == "countdown":
                        # fire once per phase: switch, then handle repeat
                        ok, info = CTL.set_outlets(mac, channels, t.get("on", False),
                                                   source=f"timer:{t['id']}")
                        log(f'timer {t["id"]} fired {"on" if t.get("on") else "off"} '
                            f'{mac} outlet {t.get("outlet")}: {"OK" if ok else info}')
                        rep = t.get("repeat", "once")
                        if rep == "forever":
                            # invert action and restart
                            t["on"] = not t.get("on", False)
                            t["phase"] = "on" if t["on"] else "off"
                            t["phase_started"] = now
                            t["cycles_done"] = (t.get("cycles_done") or 0) + (1 if not t["on"] else 0)
                        elif rep == "times":
                            n = int(t.get("repeat_n", 1))
                            done = (t.get("cycles_done") or 0) + 1
                            if done >= n:
                                t["enabled"] = False
                                t["cycles_done"] = done
                            else:
                                t["on"] = not t.get("on", False)
                                t["phase"] = "on" if t["on"] else "off"
                                t["phase_started"] = now
                                t["cycles_done"] = done
                        else:  # once
                            t["enabled"] = False
                        dirty = True
                    elif mode == "cyclic":
                        # flip phase: ON duration -> OFF -> ON...
                        new_on = t.get("phase") != "on"
                        ok, info = CTL.set_outlets(mac, channels, new_on,
                                                   source=f"timer:{t['id']}")
                        log(f'timer {t["id"]} cyclic {mac} -> {"on" if new_on else "off"}: '
                            f'{"OK" if ok else info}')
                        t["phase"] = "on" if new_on else "off"
                        t["phase_started"] = now
                        if not new_on:
                            t["cycles_done"] = (t.get("cycles_done") or 0) + 1
                            rep = t.get("repeat", "forever")
                            if rep == "times" and (t.get("cycles_done") or 0) >= int(t.get("repeat_n", 1)):
                                t["enabled"] = False
                        dirty = True
                if dirty:
                    save_timers(timers)
        except Exception as e:  # noqa: BLE001
            log(f"timer loop error: {e}")
        time.sleep(2)


def router_hint(ip, port=30300, timeout=0.5):
    """If ip answers like a router (and NOT like a strip), explain that.

    Called only on LG-command failure paths, so it never blocks real strips:
    anything answering 30300 is left for the real command to decide.
    """
    try:
        if tonly.banner_grab(ip, port, timeout=timeout).get("connected"):
            return ""
    except Exception:  # noqa: BLE001
        pass
    opens = []
    for p in (53, 80, 443):
        try:
            if tonly.banner_grab(ip, p, timeout=timeout).get("connected"):
                opens.append(p)
        except Exception:  # noqa: BLE001
            pass
    if not opens:
        return ""
    where = ("Join the TONLY_TAP_… AP to reach the strip at 192.168.1.1."
             if ip == "192.168.1.1" else "Check the strip IP — this answers like a router.")
    return (f"No LGAPMODE device on {ip}:{port}, but router-like ports {opens} "
            f"are open — this is probably your router, not a strip. {where}")


def diff_states(snaps):
    """Byte/bit diff across snapshot hex strings. JSON-safe."""
    hexes = [s.get("state_hex", "") for s in snaps if s.get("state_hex")]
    if len(hexes) < 2:
        return {"compared": len(hexes), "note": "need 2+ snapshots with state bytes"}
    n = min(len(h) for h in hexes) // 2
    changed = []
    for i in range(n):
        vals = {h[i * 2:i * 2 + 2] for h in hexes}
        if len(vals) > 1:
            ints = [int(h[i * 2:i * 2 + 2], 16) for h in hexes]
            bits = []
            for b in range(8):
                col = {(v >> b) & 1 for v in ints}
                if len(col) > 1:
                    bits.append(b)
            changed.append({"byte": i, "values": sorted(vals), "bits": bits})
    return {"compared": len(hexes), "bytes_total": n,
            "changed": changed,
            "verdict": "STATE ENCODES ACTIVITY — see changed bytes/bits"
                       if changed else "identical across snapshots"}


def local_nets():
    """Best-guess local /24s without extra deps."""
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent
        ip = s.getsockname()[0]
        s.close()
        nets.add(str(ipaddress.ip_network(ip + "/24", strict=False)))
    except OSError:
        pass
    nets.add("192.168.1.0/24")
    return sorted(nets)


def scan_host(ip, ports, timeout):
    found = []
    for p in ports:
        r = tonly.banner_grab(ip, p, timeout=timeout)
        if r["connected"]:
            found.append({"ip": ip, "port": p, "banner_hex": r["received_hex"],
                          "banner_text": r["received_text"][:200]})
    return found


def wait_route_back(ip, port, timeout=0.8, tries=15, gap=2.0):
    """After a route loss (strip reboot? phone hopped?), poll for return."""
    for _ in range(tries):
        try:
            if tonly.banner_grab(ip, port, timeout=timeout).get("connected"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(gap)
    return False


def run_sweep(ip, port, start, end, timeout=1.5):
    """Shared sweep engine: empty frames across range, state watch, persist."""
    before = tonly.lg_state(ip, port, 3).get("state_hex", "")
    rows, aborted_at, cur = [], None, before
    n, lost, recoveries = 0, 0, []
    for cmd in range(start, end + 1):
        if cmd in (tonly.CMD_WIFI,):
            rows.append({"cmd": cmd, "skipped": "set-wifi, never probed blind"})
            continue
        r = tonly.lg_exchange(ip, cmd, b"", port, timeout)
        rep = r.get("reply") or {}
        err = r.get("error", "")
        rows.append({"cmd": cmd, "cmd_hex": hex(cmd),
                     "marker": rep.get("marker", ""),
                     "length": rep.get("length", ""),
                     "payload_hex": (rep.get("payload_hex", "") or "")[:64],
                     "error": err})
        log(f"sweep {ip} {hex(cmd)}: {rep.get('payload_hex', '')[:32] or err}")
        if "nreachable" in err or "10065" in err or "errno 101" in err.lower():
            # Route gone: strip rebooted (AP down ~10-30s) or phone hopped to
            # another network. Pause and wait for it to come back, then resume.
            lost += 1
            if lost >= 2:
                if len(recoveries) >= 2:
                    aborted_at = aborted_at or "network lost"
                    log(f"sweep {ip}: route gone 3rd time — aborting")
                    break
                log(f"sweep {ip}: route lost at {hex(cmd)} — pausing up to 30s "
                    f"(strip reboot? phone hopped?)")
                if wait_route_back(ip, port):
                    recoveries.append(hex(cmd))
                    log(f"sweep {ip}: route back — resuming after {hex(cmd)}")
                    lost = 0
                    continue
                aborted_at = aborted_at or "network lost"
                log(f"sweep {ip}: route never returned — aborting")
                break
            continue
        lost = 0
        n += 1
        time.sleep(0.3)  # go easy on the strip's tiny stack
        if n % 4 == 0:
            cur = tonly.lg_state(ip, port, 2).get("state_hex", "") or cur
            if before and cur and cur != before:
                aborted_at = hex(cmd)
                log(f"sweep {ip}: STATE CHANGED at {hex(cmd)} — aborting")
                break
    after = tonly.lg_state(ip, port, 3).get("state_hex", "") or cur
    changed = bool(before and after and before != after)
    # Second pass: timeouts on a flapping link are inconclusive, so re-probe
    # exactly those rows once with a longer timeout (skips silence/replies).
    retried = 0
    if not (aborted_at == "network lost"):
        for row in rows:
            if row.get("skipped") or row.get("marker") or row.get("payload_hex"):
                continue
            if "timed out" not in (row.get("error") or "").lower():
                continue
            r = tonly.lg_exchange(ip, row["cmd"], b"", port, 3.0)
            rep = r.get("reply") or {}
            if rep.get("marker") or rep.get("payload_hex"):
                row.update({"marker": rep.get("marker", ""), "length": rep.get("length", ""),
                            "payload_hex": (rep.get("payload_hex", "") or "")[:64],
                            "error": r.get("error", "") + " (retry)"})
                log(f"sweep {ip} {row['cmd_hex']} retry: {rep.get('payload_hex', '')[:32]}")
            else:
                row["error"] = (row.get("error") or "") + " (retry: still silent)"
            retried += 1
            time.sleep(0.3)
        if retried:
            log(f"sweep {ip}: second pass retried {retried} timeout rows")
    resp = {"rows": rows, "state_before": before, "state_after": after,
            "state_changed": changed, "aborted_at": aborted_at, "saved": True,
            "network_lost": aborted_at == "network lost",
            "reboots": recoveries, "retried": retried}
    if all(not (row.get("marker") or row.get("payload_hex")) and not row.get("skipped") for row in rows):
        h = router_hint(ip, port)
        if h:
            resp["hint"] = h
    data = load_strips()
    data.setdefault("sweeps", {})[ip] = {"ts": time.strftime("%H:%M:%S"),
                                         "range": [hex(start), hex(end)], **resp}
    save_strips(data)
    return resp


class Handler(BaseHTTPRequestHandler):
    server_version = "volta-local/1.0"

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _auth_ctx(self):
        """Extract token and return full auth context."""
        t = self.headers.get("X-Token", "")
        if not t:
            auth_h = self.headers.get("Authorization", "")
            if auth_h.lower().startswith("bearer "):
                t = auth_h[7:].strip()
            elif auth_h:
                t = auth_h.strip()
        if not t:
            t = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("t", [""])[0]
        return resolve_auth_context(t)

    def _authorized(self):
        """Token gate: check if caller has valid admin or user token."""
        return self._auth_ctx()["authorized"]

    def _is_admin(self):
        return self._auth_ctx()["is_admin"]

    def _can_access_mac(self, mac):
        """Check if caller is permitted to read or control this MAC."""
        ctx = self._auth_ctx()
        if not ctx["authorized"]:
            return False
        if ctx["is_admin"]:
            return True
        clean_mac = norm_mac(mac).upper()
        return clean_mac in ctx["strips"]

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open("index.html", "rb") as f:
                self.wfile.write(f.read())
        elif u.path == "/api/health":
            self._json({"ok": True, "time": time.time(),
                        "controller": CTL.is_alive() if CTL else False,
                        "server_ip": PUBLIC_SERVER_IP or local_nets()[0] if local_nets() else "",
                        "device_port": DEVICE_PORT,
                        "auth_required": bool(TOKEN or get_users()),
                        "auth": bool(TOKEN or get_users())})
        elif u.path == "/controller.py":
            # Public on purpose: lets a phone/laptop fetch the provisioner.
            # Dynamically inject server public IP and caller host origin so users don't need manual flags.
            text = open("controller.py", "r", encoding="utf-8").read()
            if PUBLIC_SERVER_IP:
                text = text.replace('DEFAULT_SERVER_IP = "YOUR_SERVER_IP"', f'DEFAULT_SERVER_IP = "{PUBLIC_SERVER_IP}"')
            host = self.headers.get("Host", "")
            scheme = "https" if ("443" in self.headers.get("X-Forwarded-Port", "") or "https" in self.headers.get("X-Forwarded-Proto", "") or not host.startswith("127.0.0.1")) else "http"
            origin = f"{scheme}://{host}" if host else ""
            if origin:
                text = text.replace('DEFAULT_WEB_URL = "YOUR_WEB_URL"', f'DEFAULT_WEB_URL = "{origin}"')
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/x-python")
            self.send_header("Content-Disposition", 'attachment; filename="controller.py"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/auth/whoami":
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"authorized": False}, 401)
            return self._json({
                "authorized": True,
                "is_admin": ctx["is_admin"],
                "name": ctx["user_name"],
                "strips": ctx["strips"]
            })
        elif u.path == "/api/users":
            if not self._is_admin():
                return self._json({"error": "admin access required"}, 403)
            return self._json({"ok": True, "users": get_users()})
        elif u.path == "/api/live":
            # Live controller snapshot: strips that dialed in + telemetry.
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"error": "token required"}, 401)
            raw_snap = CTL.snapshot() if CTL else {"devices": []}
            devices = raw_snap.get("devices", [])
            # Filter devices by caller's allowed strips
            # Note: Unclaimed strips always show up for Admin so they can be discovered immediately!
            if not ctx["is_admin"]:
                allowed = set(ctx["strips"])
                devices = [d for d in devices if norm_mac(d.get("mac", "")).upper() in allowed]
            
            snap = {"devices": devices, "server_ip": local_nets()}
            try:
                all_names = load_strips().get("names", {})
                if ctx["is_admin"]:
                    snap["names"] = all_names
                else:
                    allowed = set(ctx["strips"])
                    snap["names"] = {m: v for m, v in all_names.items() if m.upper() in allowed}
                for d in snap["devices"]:
                    nm = snap["names"].get(norm_mac(d.get("mac", "")), {})
                    if nm.get("name"):
                        d["name"] = nm["name"]
                    onames = nm.get("outlets", {})
                    for o in d.get("outlets", []):
                        if str(o.get("n")) in onames:
                            o["nick"] = onames[str(o.get("n"))]
            except Exception:
                pass
            self._json(snap)
        elif u.path == "/api/nets":
            self._json({"nets": local_nets()})
        elif u.path in ("/api/strips", "/api/devices"):
            # Check if caller wants live controller strips formatted for app client
            ctx = self._auth_ctx()
            if ctx["authorized"]:
                raw_snap = CTL.snapshot() if CTL else {"devices": []}
                devs = raw_snap.get("devices", [])
                if not ctx["is_admin"]:
                    allowed = set(ctx["strips"])
                    devs = [d for d in devs if norm_mac(d.get("mac", "")).upper() in allowed]
                names = load_strips().get("names", {})
                formatted = []
                for d in devs:
                    clean_m = norm_mac(d.get("mac", ""))
                    nm = names.get(clean_m, {})
                    strip_name = nm.get("name") or d.get("name") or f"MTTL {clean_m[-6:]}"
                    onames = nm.get("outlets", {})
                    outs = []
                    for o in d.get("outlets", []):
                        onum = str(o.get("n"))
                        outs.append({
                            "n": o.get("n"),
                            "name": onames.get(onum) or o.get("nick") or f"Outlet {onum}",
                            "on": bool(o.get("on")),
                            "power_w": o.get("power_w", 0.0),
                            "energy_kwh": o.get("energy_kwh", 0.0),
                            "temp_c": o.get("temp_c", 25),
                            "locked": False
                        })
                    formatted.append({
                        "mac": d.get("mac"),
                        "name": strip_name,
                        "model": d.get("model", "MTTL-W01"),
                        "fw": d.get("fw", "1.0.0"),
                        "ip": d.get("ip", ""),
                        "online": bool(d.get("online")),
                        "voltage_v": d.get("voltage_v") or 220.0,
                        "rssi": d.get("rssi") or -65,
                        "last_seen": d.get("last_seen", time.time()),
                        "power_w": d.get("power_w", sum(o.get("power_w", 0.0) for o in outs)),
                        "total_power_w": sum(o.get("power_w", 0.0) for o in outs),
                        "outlets": outs
                    })
                return self._json({"ok": True, "strips": formatted, "devices": formatted})
            self._json(load_strips())
        elif u.path == "/api/log":
            self._json({"lines": LOG[-120:]})
        elif u.path == "/api/schedules":
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"error": "token required"}, 401)
            scheds = get_schedules()
            if not ctx["is_admin"]:
                allowed = set(ctx["strips"])
                scheds = [s for s in scheds if norm_mac(s.get("mac", "")).upper() in allowed]
            self._json({"schedules": scheds})
        elif u.path == "/api/timers":
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"error": "token required"}, 401)
            timers = get_timers()
            if not ctx["is_admin"]:
                allowed = set(ctx["strips"])
                timers = [t for t in timers if norm_mac(t.get("mac", "")).upper() in allowed]
            self._json({"timers": timers})
        elif u.path == "/api/settings":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            self._json(get_settings())
        elif u.path in ("/manifest.webmanifest", "/manifest.json"):
            body = open("manifest.webmanifest", "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path in ("/icon.svg", "/icon-192.png", "/icon-512.png"):
            try:
                body = open("icon.svg", "rb").read()
            except OSError:
                return self._json({"error": "no icon"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/sw.js":
            try:
                body = open("sw.js", "rb").read()
            except OSError:
                return self._json({"error": "no sw"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/api/hunt":
            q = urllib.parse.parse_qs(u.query)
            ip = q.get("ip", [""])[0]
            self._json({"snaps": load_strips().get("hunt", {}).get(ip, [])})
        elif u.path == "/api/hunt-diff":
            q = urllib.parse.parse_qs(u.query)
            ip = q.get("ip", [""])[0]
            self._json(diff_states(load_strips().get("hunt", {}).get(ip, [])))
        elif u.path == "/api/analytics/info":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            self._json({"ok": True, "db_size_mb": analytics.db_size_mb()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        if u.path == "/api/discover":
            nets = body.get("nets") or local_nets()[:1]
            ports = [int(p) for p in body.get("ports", [30300])]
            timeout = float(body.get("timeout", 0.6))
            targets = []
            for net in nets:
                try:
                    targets += [str(h) for h in ipaddress.ip_network(net, strict=False).hosts()]
                except ValueError as e:
                    return self._json({"error": f"bad net {net}: {e}"}, 400)
            log(f"scan {len(targets)} hosts ports={ports} timeout={timeout}")
            found = []
            with futures.ThreadPoolExecutor(max_workers=64) as ex:
                futs = {ex.submit(scan_host, ip, ports, timeout): ip for ip in targets}
                for fu in futures.as_completed(futs):
                    found += fu.result()
            log(f"scan done: {len(found)} open")
            for f in sorted(found, key=lambda e: (e["ip"], e["port"])):
                tag = f" port {f['port']}"
                if f.get("banner_text"):
                    tag += f" banner={f['banner_text'][:60]!r}"
                log(f"  hit {f['ip']}{tag}")
            return self._json({"found": found, "scanned": len(targets)})
        if u.path == "/api/strips":
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            data = load_strips()
            if not any(s["ip"] == ip for s in data["strips"]):
                data["strips"].append({"ip": ip, "port": int(body.get("port", 30300)),
                                       "name": body.get("name", ip), "codes": {},
                                       "esp_ip": (body.get("esp_ip") or "").strip()})
                save_strips(data)
                log(f"strip added {ip}")
            return self._json(data)
        if u.path == "/api/users":
            if not self._is_admin():
                return self._json({"error": "admin access required"}, 403)
            name = (body.get("name") or "").strip()[:40] or "User"
            custom_token = (body.get("token") or "").strip()
            new_token = custom_token if custom_token else f"volta_usr_{secrets.token_hex(8)}"
            users = get_users()
            users[new_token] = {
                "name": name,
                "strips": [norm_mac(m).upper() for m in body.get("strips", []) if m],
                "created_at": int(time.time())
            }
            save_users(users)
            log(f"user created: {name} ({new_token[:14]}...)")
            return self._json({"ok": True, "token": new_token, "user": users[new_token]})
        if u.path == "/api/claim":
            # Link a strip MAC to a user token, or register pending claim by IP.
            # Can be called from Termux script or Web UI.
            token_arg = (body.get("token") or self.headers.get("X-Token", "")).strip()
            target_user_token = (body.get("target_token") or "").strip()
            mac = norm_mac(body.get("mac")).upper()

            # If no MAC provided, register an auto-claim expectation for the caller's IP
            if not mac and body.get("expect_auto"):
                ctx = resolve_auth_context(token_arg)
                if not ctx["authorized"]:
                    return self._json({"error": "invalid token"}, 401)
                client_ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or self.client_address[0]
                register_pending_claim(client_ip, ctx["token"])
                log(f"[claim-pending] Registered expectation for {ctx['user_name']} from IP {client_ip}")
                return self._json({"ok": True, "pending_ip": client_ip, "user": ctx["user_name"]})

            if not mac or len(mac) != 12:
                return self._json({"error": "invalid strip mac"}, 400)
            
            ctx = resolve_auth_context(token_arg)
            if not ctx["authorized"]:
                return self._json({"error": "invalid token"}, 401)
            
            users = get_users()
            
            # If admin is performing a transfer / assignment to a specific user
            if ctx["is_admin"] and target_user_token:
                if target_user_token not in users:
                    return self._json({"error": "target user not found"}, 404)
                # Remove MAC from any previous owner first to transfer clean
                for u_t, u_data in users.items():
                    if mac in u_data.get("strips", []):
                        u_data["strips"].remove(mac)
                users[target_user_token].setdefault("strips", []).append(mac)
                save_users(users)
                log(f"[transfer] strip {mac} transferred by Admin to {users[target_user_token].get('name')}")
                return self._json({"ok": True, "transferred": True, "mac": mac, "user": users[target_user_token].get("name")})
            
            if ctx["is_admin"]:
                return self._json({"ok": True, "admin": True, "message": "Admin account has access to all strips."})
            
            user_entry = users.get(ctx["token"])
            if not user_entry:
                return self._json({"error": "user record not found"}, 404)
            
            strips_list = user_entry.setdefault("strips", [])
            if mac not in strips_list:
                strips_list.append(mac)
                save_users(users)
                log(f"[claim] strip {mac} claimed by {ctx['user_name']} ({ctx['token'][:10]}...)")
            return self._json({"ok": True, "mac": mac, "user": ctx["user_name"], "strips": strips_list})
        if u.path == "/api/rename":
            # Friendly names (server-side, keyed by MAC, survive reboots).
            # Body: {mac, name?} or {mac, outlet:1-4, outlet_name?} or {mac, name?, outlets: {1:"..", 2:"..", ..}}
            # Empty string clears back to default.
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = norm_mac(body.get("mac"))
            if not mac:
                return self._json({"error": "mac required"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            data = load_strips()
            names = data.setdefault("names", {})
            entry = names.setdefault(mac, {})
            if "name" in body:
                nm = (body.get("name") or "").strip()[:40]
                if nm:
                    entry["name"] = nm
                else:
                    entry.pop("name", None)
            if "outlets" in body and isinstance(body["outlets"], dict):
                outs = entry.setdefault("outlets", {})
                for k, v in body["outlets"].items():
                    if str(k) in ("1", "2", "3", "4"):
                        val = (v or "").strip()[:40]
                        if val:
                            outs[str(k)] = val
                        else:
                            outs.pop(str(k), None)
            if "outlet" in body:
                try:
                    outlet = int(body.get("outlet"))
                except (TypeError, ValueError):
                    return self._json({"error": "outlet must be 1-4"}, 400)
                if outlet not in (1, 2, 3, 4):
                    return self._json({"error": "outlet must be 1-4"}, 400)
                onm = (body.get("outlet_name") or "").strip()[:40]
                outs = entry.setdefault("outlets", {})
                if onm:
                    outs[str(outlet)] = onm
                else:
                    outs.pop(str(outlet), None)
            if not entry or (not entry.get("name") and not entry.get("outlets")):
                names.pop(mac, None)
            save_strips(data)
            log(f"rename {mac}: {entry}")
            return self._json({"ok": True})
        if u.path in ("/api/onoff", "/api/switch"):
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = norm_mac(body.get("mac"))
            try:
                outlet = int(body.get("outlet", 0))
            except (TypeError, ValueError):
                return self._json({"error": "outlet must be 0-4"}, 400)
            on = bool(body.get("on") or body.get("state") == "on" or body.get("state") is True)
            if not mac or outlet not in (0, 1, 2, 3, 4):
                return self._json({"error": "need mac + outlet 0-4"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            data = load_strips()
            hit = next((s for s in data["strips"]
                        if norm_mac(s.get("mac", "")) == mac or s.get("ip") == body.get("mac")), None)
            if hit and hit.get("esp_ip"):
                import urllib.request as _u
                try:
                    with _u.urlopen(
                            f"http://{hit['esp_ip']}/api/outlet?n={outlet or 1}&s={'on' if on else 'off'}",
                            timeout=6) as resp:
                        ans = json.loads(resp.read().decode())
                    log(f"onoff {mac} outlet {outlet} {on} via ESP: {ans}")
                    return self._json({"ok": True, "via": "esp", "esp": ans})
                except Exception as e:  # noqa: BLE001
                    return self._json({"error": f"ESP unreachable: {e}"}, 502)
            if not CTL:
                return self._json({"error": "controller not running"}, 503)
            channels = [1, 2, 3, 4] if outlet == 0 else [outlet]
            ok, err = CTL.set_outlets(mac, channels, on, source="ui")
            log(f"onoff {mac} outlet {outlet} {on}: {'OK' if ok else err}")
            if not ok:
                return self._json({"error": err or "device offline",
                                   "hint": "Strip must have dialed in (provision it with this server's IP)."}, 502)
            if err.startswith("queued"):
                return self._json({"ok": True, "queued": True, "detail": err})
            return self._json({"ok": True, "confirmed": True})
        if u.path == "/api/provision":
            # One-shot provisioning: up:ip + up:connect plain lines.
            # Body: {host, server_ip, ssid, password}
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            host = (body.get("host") or "192.168.1.1").strip()
            server_ip = (body.get("server_ip") or "").strip()
            ssid, password = body.get("ssid", ""), body.get("password", "")
            if not server_ip or not ssid or not password:
                return self._json({"error": "need server_ip, ssid, password"}, 400)
            try:
                r1, r2 = ctrl.provision(server_ip, ssid, password, host)
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                log(f"provision {host} failed: {e}")
                return self._json({"error": str(e),
                                   "hint": "Join the TONLY_TAP_… AP first; strip at 192.168.1.1."}, 502)
            log(f"provision {host}: {r1} / {r2}")
            return self._json({"ok": True, "ip_reply": r1, "wifi_reply": r2,
                               "next": "Rejoin home LAN; the strip will dial this server shortly."})
        if u.path == "/api/schedules":
            # Create or update a schedule. Kinds: {kind:time,time} or {kind:sun,sun,offset_min}.
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            sched, err = validate_schedule(body)
            if err:
                return self._json({"error": err}, 400)
            if not self._can_access_mac(sched["mac"]):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            scheds = get_schedules()
            for i, s in enumerate(scheds):
                if s.get("id") == sched["id"]:
                    sched["last_fired"] = s.get("last_fired", "")
                    scheds[i] = sched
                    break
            else:
                scheds.append(sched)
            save_schedules(scheds)
            log(f'sched saved {sched["id"]} {sched["mac"]} outlet {sched["outlet"]} '
                f'{"on" if sched["on"] else "off"} {sched["time"]} {sched["tz"]}')
            return self._json({"ok": True, "schedule": sched, "schedules": scheds})
        if u.path == "/api/timers":
            # Create or update an outlet timer.
            # Body: {mac, outlet, on, mode:countdown|cyclic, on_sec, off_sec,
            #        repeat: once|times|forever, repeat_n, label, enabled, id?}
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = norm_mac(body.get("mac", "")).upper()
            outlet = int(body.get("outlet", 0) or 0)
            if not mac or not (0 <= outlet <= 4):
                return self._json({"error": "need mac + outlet 0-4"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            try:
                on_sec = max(5, int(body.get("on_sec", 0) or 0))
                off_sec = max(5, int(body.get("off_sec", 0) or 0))
            except (TypeError, ValueError):
                return self._json({"error": "on_sec/off_sec must be integer seconds"}, 400)
            mode = body.get("mode", "countdown")
            if mode not in ("countdown", "cyclic"):
                return self._json({"error": "mode must be countdown or cyclic"}, 400)
            if mode == "countdown" and on_sec <= 0:
                return self._json({"error": "countdown needs on_sec > 0"}, 400)
            if mode == "cyclic" and (on_sec <= 0 or off_sec <= 0):
                return self._json({"error": "cyclic needs on_sec and off_sec > 0"}, 400)
            repeat = body.get("repeat", "once")
            if repeat not in ("once", "times", "forever"):
                return self._json({"error": "repeat must be once|times|forever"}, 400)
            tid = body.get("id") or f"t_{uuid.uuid4().hex[:10]}"
            now = time.time()
            timers = get_timers()
            timer = {
                "id": tid, "mac": mac, "outlet": outlet,
                "on": bool(body.get("on", False)),
                "mode": mode,
                "on_sec": on_sec, "off_sec": off_sec,
                "phase": "off" if body.get("on", False) is False else "on",
                "phase_started": now,
                "repeat": repeat,
                "repeat_n": max(1, int(body.get("repeat_n", 1) or 1)),
                "cycles_done": 0,
                "label": (body.get("label") or "")[:60],
                "enabled": bool(body.get("enabled", True)),
            }
            for i, t in enumerate(timers):
                if t.get("id") == tid:
                    timer["cycles_done"] = t.get("cycles_done", 0)
                    timer["phase_started"] = now  # reset phase clock on edit
                    timers[i] = timer
                    break
            else:
                timers.append(timer)
            save_timers(timers)
            log(f'timer saved {tid} {mac} outlet {outlet} {mode} '
                f'on={on_sec}s off={off_sec}s repeat={repeat}')
            return self._json({"ok": True, "timer": timer, "timers": timers})
        if u.path == "/api/settings":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            patch = {}
            if "lat" in body or "lon" in body:
                try:
                    lat, lon = float(body.get("lat", 30.0444)), float(body.get("lon", 31.2357))
                except (TypeError, ValueError):
                    return self._json({"error": "lat/lon must be numbers"}, 400)
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    return self._json({"error": "lat -90..90, lon -180..180"}, 400)
                patch["lat"], patch["lon"] = lat, lon
            if "ntfy" in body:
                val = (body.get("ntfy") or "").strip()
                # Clean up leading https://ntfy.sh/ or ntfy.sh/ if provided
                val = __import__("re").sub(r"^https?://", "", val)
                if val.startswith("ntfy.sh/"):
                    val = val[len("ntfy.sh/"):]
                patch["ntfy"] = val[:64]
                if patch["ntfy"] and not __import__("re").fullmatch(r"[A-Za-z0-9_.-]+", patch["ntfy"]):
                    return self._json({"error": "ntfy topic: letters/numbers/_/- only"}, 400)
            if "alert_offline_min" in body:
                try:
                    patch["alert_offline_min"] = max(1, min(120, int(body["alert_offline_min"])))
                except (TypeError, ValueError):
                    return self._json({"error": "alert minutes must be 1-120"}, 400)
            if "notify_offline" in body:
                patch["notify_offline"] = bool(body["notify_offline"])
            if "notify_switch" in body:
                patch["notify_switch"] = bool(body["notify_switch"])
            if "notify_filter" in body:
                filt = str(body["notify_filter"]).strip().lower()
                patch["notify_filter"] = "selected" if filt == "selected" else "all"
            if "notify_targets" in body and isinstance(body["notify_targets"], list):
                patch["notify_targets"] = [str(t).strip().upper() for t in body["notify_targets"] if str(t).strip()]
            if "cost_per_kwh" in body:
                try:
                    patch["cost_per_kwh"] = max(0.01, float(body["cost_per_kwh"]))
                except (TypeError, ValueError):
                    return self._json({"error": "cost_per_kwh must be a number"}, 400)
            if "currency" in body:
                patch["currency"] = str(body.get("currency", "EGP")).strip()[:10] or "EGP"
            if "temp_alert_c" in body:
                try:
                    patch["temp_alert_c"] = max(30, min(100, int(body["temp_alert_c"])))
                except (TypeError, ValueError):
                    return self._json({"error": "temp_alert_c must be 30-100"}, 400)
            if "notify_temp" in body:
                patch["notify_temp"] = bool(body["notify_temp"])
            if "voltage_min" in body:
                try:
                    patch["voltage_min"] = max(100.0, min(260.0, float(body["voltage_min"])))
                except (TypeError, ValueError):
                    return self._json({"error": "voltage_min must be 100-260"}, 400)
            if "voltage_max" in body:
                try:
                    patch["voltage_max"] = max(150.0, min(300.0, float(body["voltage_max"])))
                except (TypeError, ValueError):
                    return self._json({"error": "voltage_max must be 150-300"}, 400)
            if "notify_voltage" in body:
                patch["notify_voltage"] = bool(body["notify_voltage"])
            st = save_settings(patch)
            log(f"settings saved: {st}")
            return self._json({"ok": True, "settings": st})
        if u.path == "/api/notify-test":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            topic = get_settings().get("ntfy")
            if not topic:
                return self._json({"error": "set an ntfy topic first"}, 400)
            try:
                ntfy_send(topic, "Volta test", "Alerts work. You will get online/offline notices here.", "white_check_mark")
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"ntfy failed: {e}"}, 502)
            return self._json({"ok": True})
        if u.path == "/api/analytics/range":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = body.get("mac", "").upper()
            outlet = int(body.get("outlet", 1))
            start = int(body.get("start_ts", 0))
            end = int(body.get("end_ts", 0))
            downsample = body.get("downsample")
            if not mac or outlet not in range(1, 5):
                return self._json({"error": "need mac + outlet 1-4"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            rows = analytics.query_range(mac, outlet, start, end, downsample)
            return self._json({"ok": True, "readings": rows})
        if u.path == "/api/analytics/strip":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = body.get("mac", "").upper()
            start = int(body.get("start_ts", 0))
            end = int(body.get("end_ts", 0))
            if not mac:
                return self._json({"error": "need mac"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            stats = analytics.strip_stats(mac, start, end)
            return self._json({"ok": True, "stats": stats})
        if u.path == "/api/analytics/strip-outlets":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            mac = body.get("mac", "").upper()
            start = int(body.get("start_ts", 0))
            end = int(body.get("end_ts", 0))
            downsample = body.get("downsample")
            if not mac:
                return self._json({"error": "need mac"}, 400)
            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            rows = analytics.query_strip_all_outlets(mac, start, end, downsample)
            return self._json({"ok": True, "readings": rows})
        if u.path == "/api/analytics/all-strips":
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"error": "token required"}, 401)
            start = int(body.get("start_ts", 0))
            end = int(body.get("end_ts", 0))
            downsample = body.get("downsample")
            user_macs = None if ctx["is_admin"] else ctx["strips"]
            rows = analytics.query_all_strips_power(start, end, downsample, macs=user_macs)
            return self._json({"ok": True, "readings": rows})
        if u.path == "/api/analytics/leaderboard":
            ctx = self._auth_ctx()
            if not ctx["authorized"]:
                return self._json({"error": "token required"}, 401)
            start = int(body.get("start_ts", 0))
            end = int(body.get("end_ts", 0))
            limit = int(body.get("limit", 10))
            user_macs = None if ctx["is_admin"] else ctx["strips"]
            board = analytics.outlet_leaderboard(start, end, limit, macs=user_macs)
            return self._json({"ok": True, "leaderboard": board})
        if u.path == "/api/analytics/info":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            return self._json({"ok": True, "db_size_mb": analytics.db_size_mb()})
        if u.path == "/api/learn":
            # store learned hex codes: {ip, outlet, action(on|off|state), hex}
            ip, outlet, action = body.get("ip"), str(body.get("outlet")), body.get("action")
            hexcode = (body.get("hex") or "").replace(" ", "")
            if not (ip and outlet in "1234" and action in ("on", "off", "state") and hexcode):
                return self._json({"error": "need ip, outlet 1-4, action on|off|state, hex"}, 400)
            try:
                bytes.fromhex(hexcode)
            except ValueError:
                return self._json({"error": "hex not valid"}, 400)
            data = load_strips()
            hit = next((s for s in data["strips"] if s["ip"] == ip), None)
            if not hit:
                return self._json({"error": "add this strip first"}, 404)
            hit.setdefault("codes", {})[f"{outlet}:{action}"] = hexcode
            save_strips(data)
            log(f"learned {ip} outlet {outlet} {action} ({len(hexcode)//2} bytes)")
            return self._json({"ok": True})
        if u.path == "/api/probe":
            ip = body.get("ip", "")
            port = int(body.get("port", 30300))
            send_hex = (body.get("send_hex") or "").replace(" ", "")
            send = b""
            if send_hex:
                try:
                    send = bytes.fromhex(send_hex)
                except ValueError:
                    return self._json({"error": "send_hex not valid hex"}, 400)
            r = tonly.tcp_exchange(ip, port, send=send, timeout=float(body.get("timeout", 3)))
            log(f"probe {ip}:{port} sent={r['sent']}B err={r['error'] or 'reply ' + str(len(r['received_hex'])//2) + 'B'}")
            return self._json(r)
        if u.path == "/api/control":
            ip, outlet, action = body.get("ip"), str(body.get("outlet")), body.get("action")
            if outlet not in "1234" or action not in ("on", "off"):
                return self._json({"error": "outlet must be 1-4, action on|off"}, 400)
            data = load_strips()
            hit = next((s for s in data["strips"] if s["ip"] == ip), None)
            if not hit:
                return self._json({"error": "unknown strip, add it first"}, 404)
            if hit.get("esp_ip"):
                # ESP-01 modded strip: route to the ESP's HTTP API (stdlib).
                url = (f"http://{hit['esp_ip']}/api/outlet?"
                       f"n={outlet}&s={action}")
                try:
                    with urllib.request.urlopen(url, timeout=6) as resp:
                        ans = json.loads(resp.read().decode())
                    log(f"control {ip} outlet {outlet} {action} via ESP {hit['esp_ip']}: {ans}")
                    return self._json({"ok": True, "via": "esp", "esp": ans})
                except Exception as e:  # noqa: BLE001
                    log(f"control {ip} via ESP failed: {e}")
                    return self._json({"error": f"ESP unreachable: {e}",
                                       "hint": "ESP powered? Same Wi-Fi? Check its IP in the router list."}, 502)
            code = (hit.get("codes") or {}).get(f"{outlet}:{action}")
            if not code:
                return self._json({"error": "no learned code",
                                    "hint": f"No bytes learned yet for outlet {outlet} {action}. "
                                            "Capture them once (README 'Learn the codes') and save via Learn panel."}, 409)
            r = tonly.tcp_exchange(ip, hit.get("port", 30300), send=bytes.fromhex(code), timeout=4)
            log(f"control {ip} outlet {outlet} {action}: {r['error'] or 'sent ok'}")
            return self._json(r)
        if u.path == "/api/state-poll":
            # best effort: banner grab + report learned-code coverage
            ip = body.get("ip", "")
            data = load_strips()
            hit = next((s for s in data["strips"] if s["ip"] == ip), None)
            if not hit:
                return self._json({"error": "unknown strip"}, 404)
            r = tonly.banner_grab(ip, hit.get("port", 30300), timeout=2.5)
            codes = hit.get("codes", {})
            return self._json({"probe": r, "learned": sorted(codes)})
        if u.path == "/api/diagnose":
            # Read-only detective: port scan + banners + HTTP GETs + ARP MAC.
            # Nothing here switches relays. Paste the result back to the
            # assistant to help identify the protocol.
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            timeout = float(body.get("timeout", 0.7))
            report = {"ip": ip, "mac": tonly.arp_mac(ip), "ports": []}
            with futures.ThreadPoolExecutor(max_workers=24) as ex:
                futs = {ex.submit(tonly.banner_grab, ip, p, timeout): p
                        for p in tonly.COMMON_PORTS}
                for fu in futures.as_completed(futs):
                    p = futs[fu]
                    try:
                        r = fu.result()
                    except Exception as e:  # noqa: BLE001
                        r = {"connected": False, "error": str(e)}
                    if r.get("connected"):
                        report["ports"].append({"port": p, "banner_hex": r.get("received_hex", ""),
                                                "banner_text": (r.get("received_text", "") or "")[:300]})
            report["ports"].sort(key=lambda e: e["port"])
            https = {}
            for e in report["ports"]:
                if e["port"] in (80, 443, 8000, 8001, 8080, 8443, 8888):
                    for path in ("/", "/state", "/status", "/info", "/device"):
                        try:
                            r = tonly.http_get(ip, e["port"], path, timeout=3)
                            https[f"{e['port']}{path}"] = {
                                "reply_hex": r.get("received_hex", "")[:2000],
                                "reply_text": (r.get("received_text", "") or "")[:1000],
                                "error": r.get("error", "")}
                        except Exception as ex:  # noqa: BLE001
                            https[f"{e['port']}{path}"] = {"error": str(ex)}
            report["http"] = https
            log(f"diagnose {ip}: mac={report['mac'] or '?'} open={[e['port'] for e in report['ports']]}")
            return self._json(report)
        if u.path == "/api/lg-info":
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            r = tonly.lg_info(ip, int(body.get("port", 30300)))
            if r.get("info"):
                data = load_strips()
                hit = next((s for s in data["strips"] if s["ip"] == ip), None)
                if hit:
                    hit.update({k: v for k, v in r["info"].items() if v})
                    save_strips(data)
                log(f"info {ip}: {r['info']}")
            else:
                h = router_hint(ip, int(body.get("port", 30300)))
                if h:
                    r["hint"] = h
                log(f"info {ip} failed: {r.get('error') or r.get('reply')}")
            return self._json(r)
        if u.path == "/api/lg-state":
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            r = tonly.lg_state(ip, int(body.get("port", 30300)))
            if not r.get("state_hex"):
                h = router_hint(ip, int(body.get("port", 30300)))
                if h:
                    r["hint"] = h
            log(f"state {ip}: {r.get('state_hex', '?')} err={r.get('error', '')}")
            return self._json(r)
        if u.path == "/api/lg-wifi":
            # Provisioning: sends home WiFi to a strip in AP/setup mode.
            # The strip reboots; no reply is the SUCCESS case.
            ip, ssid, password = (body.get("ip") or "").strip(), body.get("ssid", ""), body.get("password", "")
            if not (ip and ssid and password):
                return self._json({"error": "need ip, ssid, password"}, 400)
            r = tonly.lg_set_wifi(ip, ssid, password, int(body.get("port", 30300)))
            confirm = (r.get("confirm") or {}).get("reply") or {}
            log(f"set-wifi {ip} ssid={ssid}: sent={r['sent']}B ack={str(r.get('reply'))[:80]} "
                f"confirm={str(confirm)[:80]} err={r.get('error', '')}")
            return self._json(r)
        if u.path == "/api/lg-raw":
            # Experimental single-shot sender. The user confirms they are
            # watching the strip; unknown codes are expected to be ignored.
            ip = (body.get("ip") or "").strip()
            try:
                cmd = int(str(body.get("cmd", "")), 0)
            except ValueError:
                return self._json({"error": "cmd must be a number (e.g. 102 or 0x66)"}, 400)
            if not (0 <= cmd <= 0xFFFF):
                return self._json({"error": "cmd out of range"}, 400)
            pay_hex = (body.get("payload_hex") or "").replace(" ", "")
            try:
                payload = bytes.fromhex(pay_hex) if pay_hex else b""
            except ValueError:
                return self._json({"error": "payload_hex not valid hex"}, 400)
            if len(payload) > 256:
                return self._json({"error": "payload too long (max 256B)"}, 400)
            r = tonly.lg_exchange(ip, cmd, payload, int(body.get("port", 30300)))
            if not (r.get("reply") or {}).get("ok"):
                h = router_hint(ip, int(body.get("port", 30300)))
                if h:
                    r["hint"] = h
            log(f"raw {ip} cmd={cmd:#x} plen={len(payload)}: reply={str(r.get('reply'))[:160]} err={r.get('error', '')}")
            return self._json(r)
        if u.path == "/api/arp-hunt":
            # Find vanished strips by MAC: ping-sweep the LAN (populates the
            # kernel ARP table) then match. Works even if 30300 is closed in
            # station mode — answers "where are my strips" either way.
            want = {norm_mac(m) for m in (body.get("macs") or []) if norm_mac(m)}
            if not want:
                return self._json({"error": "macs required (list)"}, 400)
            nets = body.get("nets") or local_nets()[:1]
            timeout = float(body.get("timeout", 0.4))
            targets = []
            for net in nets:
                try:
                    targets += [str(h) for h in ipaddress.ip_network(net, strict=False).hosts()]
                except ValueError as e:
                    return self._json({"error": f"bad net {net}: {e}"}, 400)
            def ping(ip):
                for p in (80, 443):
                    try:
                        s = socket.create_connection((ip, p), timeout=timeout)
                        s.close()
                        return
                    except OSError:
                        pass
            with futures.ThreadPoolExecutor(max_workers=128) as ex:
                list(ex.map(ping, targets))
            found, arp_rows = [], 0
            try:
                with open("/proc/net/arp") as f:
                    for line in f.readlines()[1:]:
                        parts = line.split()
                        if len(parts) < 4:
                            continue
                        arp_rows += 1
                        if norm_mac(parts[3]) in want:
                            found.append({"mac": parts[3], "ip": parts[0]})
            except OSError as e:
                return self._json({"error": f"no ARP table: {e}"}, 500)
            log(f"arp-hunt: swept {len(targets)}, ARP rows={arp_rows}, matches={found}")
            return self._json({"found": found, "scanned": len(targets), "arp_rows": arp_rows,
                               "hint": "" if found else
                               "MACs not on this LAN. Still on strip AP? Wrong network? "
                               "Or check the router's DHCP client list for the MACs."})
        if u.path == "/api/verify-join":
            # After set-wifi: hunt the home LAN for a strip with this MAC.
            want = norm_mac(body.get("mac"))
            if not want:
                return self._json({"error": "mac required"}, 400)
            nets = body.get("nets") or local_nets()[:1]
            rounds = max(1, min(int(body.get("rounds", 4)), 8))
            port = int(body.get("port", 30300))
            targets = []
            for net in nets:
                try:
                    targets += [str(h) for h in ipaddress.ip_network(net, strict=False).hosts()]
                except ValueError as e:
                    return self._json({"error": f"bad net {net}: {e}"}, 400)
            for rnd in range(1, rounds + 1):
                log(f"verify-join round {rnd}/{rounds}: scanning {len(targets)} hosts")
                with futures.ThreadPoolExecutor(max_workers=64) as ex:
                    futs = {ex.submit(scan_host, ip, [port], 0.5): ip for ip in targets}
                    hits = [h for fu in futures.as_completed(futs) for h in fu.result()]
                for h in hits:
                    try:
                        r = tonly.lg_info(h["ip"], port, timeout=2.5)
                    except Exception:  # noqa: BLE001
                        continue
                    got = norm_mac((r.get("info") or {}).get("mac", ""))
                    if got and got == want:
                        data = load_strips()
                        hit = next((s for s in data["strips"] if norm_mac(s.get("mac", "")) == want), None)
                        if not hit:
                            hit = next((s for s in data["strips"] if s["ip"] == h["ip"]), None)
                        if hit:
                            hit["ip"] = h["ip"]
                            hit.update({k: v for k, v in (r.get("info") or {}).items() if v})
                            save_strips(data)
                        log(f"verify-join: FOUND {h['ip']} mac={got}")
                        return self._json({"joined": True, "ip": h["ip"],
                                           "info": r.get("info"), "rounds": rnd})
                time.sleep(2)
            log("verify-join: not found (still in AP mode?)")
            return self._json({"joined": False, "rounds": rounds,
                               "hint": "Strip not on home LAN. Still broadcasting TONLY_TAP_…? "
                                       "Recheck 2.4 GHz + password, retry set-wifi."})
        if u.path == "/api/hunt-snap":
            ip, label = (body.get("ip") or "").strip(), body.get("label", "")
            if not ip:
                return self._json({"error": "ip required"}, 400)
            data = load_strips()
            r = tonly.lg_state(ip, int(body.get("port", 30300)))
            entry = {"ts": time.strftime("%H:%M:%S"), "label": label or f"snap",
                     "state_hex": r.get("state_hex", ""), "error": r.get("error", "")}
            data.setdefault("hunt", {}).setdefault(ip, []).append(entry)
            save_strips(data)
            log(f"hunt {ip} [{entry['label']}]: {entry['state_hex'] or entry['error']}")
            return self._json({"entry": entry, "diff": diff_states(data["hunt"][ip])})
        if u.path == "/api/hunt-clear":
            ip = (body.get("ip") or "").strip()
            data = load_strips()
            data.get("hunt", {}).pop(ip, None)
            save_strips(data)
            return self._json({"ok": True})
        if u.path == "/api/sweep":
            # Guided hunt: read-style (empty) frames across a cmd range.
            # No payloads → nothing is commanded; user watches LEDs anyway.
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            try:
                start = int(str(body.get("start", "0x68")), 0)
                end = int(str(body.get("end", "0x77")), 0)
            except ValueError:
                return self._json({"error": "start/end must be numbers"}, 400)
            start, end = max(0, start), min(0xFF, end)
            if end < start or end - start > 64:
                return self._json({"error": "range max 64 codes"}, 400)
            port, timeout = int(body.get("port", 30300)), 1.5
            return self._json(run_sweep(ip, port, start, end, timeout))
        if u.path == "/api/ap-hunt":
            # One-click AP-mode hunt: info + state + full sweep, one report.
            # Phone must be joined to the TONLY_TAP_… AP (strip at 192.168.1.1).
            ip = (body.get("ip") or "192.168.1.1").strip()
            port = int(body.get("port", 30300))
            try:
                start = int(str(body.get("start", "0x60")), 0)
                end = int(str(body.get("end", "0x7F")), 0)
            except ValueError:
                return self._json({"error": "start/end must be numbers"}, 400)
            start, end = max(0, start), min(0xFF, end)
            if end < start or end - start > 64:
                return self._json({"error": "range max 64 codes"}, 400)
            log(f"ap-hunt {ip}: info+state+sweep {hex(start)}-{hex(end)}")
            info = tonly.lg_info(ip, port, timeout=4)
            state = tonly.lg_state(ip, port, timeout=4)
            if not info.get("info"):
                h = router_hint(ip, port)
                return self._json({"info": {}, "error": info.get("error", "no info reply"),
                                   "hint": h or "Not a strip — join the TONLY_TAP_… AP first."})
            sweep = run_sweep(ip, port, start, end)
            data = load_strips()
            hit = next((s for s in data["strips"] if s["ip"] == ip), None)
            if hit:
                hit.update({k: v for k, v in (info.get("info") or {}).items() if v})
                save_strips(data)
            log(f"ap-hunt {ip} done: model={(info.get('info') or {}).get('model')}, "
                f"state={state.get('state_hex')}, changed={sweep['state_changed']}")
            return self._json({"info": info.get("info"), "state_hex": state.get("state_hex"),
                               "sweep": sweep})
        if u.path == "/api/probe-shots":
            # Guided payload campaign: one frame per shot, slow spacing, state
            # check after every shot. 0x65 (wifi) is always rejected.
            ip = (body.get("ip") or "").strip()
            if not ip:
                return self._json({"error": "ip required"}, 400)
            shots = body.get("shots") or []
            if not isinstance(shots, list) or not shots or len(shots) > 20:
                return self._json({"error": "shots must be a list of 1-20"}, 400)
            port = int(body.get("port", 30300))
            gap = max(2.0, min(float(body.get("gap", 5.0)), 30.0))
            base = tonly.lg_state(ip, port, 3).get("state_hex", "")
            running, out = base, []
            for i, s in enumerate(shots):
                try:
                    cmd = int(str(s.get("cmd", "")), 0)
                except (ValueError, AttributeError):
                    return self._json({"error": f"shot {i}: bad cmd"}, 400)
                if not (0 <= cmd <= 0xFFFF) or cmd == tonly.CMD_WIFI:
                    return self._json({"error": f"shot {i}: cmd rejected"}, 400)
                pay_hex = (s.get("payload_hex") or "").replace(" ", "")
                try:
                    payload = bytes.fromhex(pay_hex) if pay_hex else b""
                except ValueError:
                    return self._json({"error": f"shot {i}: bad payload hex"}, 400)
                if len(payload) > 64:
                    return self._json({"error": f"shot {i}: payload max 64B"}, 400)
                r = tonly.lg_exchange(ip, cmd, payload, port, 4)
                rep = r.get("reply") or {}
                after = tonly.lg_state(ip, port, 3).get("state_hex", "") or running
                changed = bool(running and after and running != after)
                entry = {"n": i, "label": s.get("label", ""), "cmd_hex": hex(cmd),
                         "payload_hex": pay_hex, "marker": rep.get("marker", ""),
                         "reply_hex": (rep.get("payload_hex", "") or "")[:64],
                         "error": r.get("error", ""), "state_after": after,
                         "state_changed": changed}
                out.append(entry)
                log(f"shots {ip} #{i} {s.get('label', '')} {hex(cmd)}+{pay_hex or '-'}: "
                    f"reply={entry['reply_hex'][:32] or entry['error']} state={after} "
                    f"{'CHANGED!' if changed else ''}")
                if changed:
                    running = after
                    break  # stop on first state change — human reviews
                running = after
                time.sleep(gap)
            data = load_strips()
            data.setdefault("payload_probes", {}).setdefault(ip, []).append(
                {"ts": time.strftime("%H:%M:%S"), "shots": out,
                 "changed_any": any(e["state_changed"] for e in out)})
            save_strips(data)
            return self._json({"shots": out, "state_before": base,
                               "changed_any": any(e["state_changed"] for e in out),
                               "stopped_early": len(out) < len(shots), "saved": True})
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/users":
            if not self._is_admin():
                return self._json({"error": "admin access required"}, 403)
            tok = q.get("token", [""])[0]
            if not tok:
                return self._json({"error": "token parameter required"}, 400)
            users = get_users()
            if tok in users:
                u_name = users[tok].get("name", "User")
                del users[tok]
                save_users(users)
                log(f"user revoked: {u_name} ({tok[:10]}...)")
                return self._json({"ok": True, "revoked": tok})
            return self._json({"error": "user token not found"}, 404)
        if u.path == "/api/claim":
            # Unlink a strip MAC from user
            token_arg = (q.get("token", [""])[0] or self.headers.get("X-Token", "")).strip()
            mac = norm_mac(q.get("mac", [""])[0]).upper()
            ctx = resolve_auth_context(token_arg)
            if not ctx["authorized"]:
                return self._json({"error": "invalid token"}, 401)
            users = get_users()
            user_entry = users.get(ctx["token"])
            if user_entry and mac in user_entry.get("strips", []):
                user_entry["strips"].remove(mac)
                save_users(users)
                log(f"[unclaim] strip {mac} unlinked from {ctx['user_name']}")
                return self._json({"ok": True, "unlinked": mac})
            return self._json({"error": "strip not linked to user"}, 404)
        if u.path in ("/api/strips", "/api/strip"):
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            ctx = self._auth_ctx()
            mac = norm_mac(q.get("mac", [""])[0]).upper()
            ip = q.get("ip", [""])[0].strip()

            data = load_strips()
            # If removing by IP from manual list
            if ip and not mac:
                data["strips"] = [s for s in data["strips"] if s.get("ip") != ip]
                save_strips(data)
                log(f"strip removed by IP {ip}")
                return self._json({"ok": True, "removed_ip": ip})

            if not mac:
                return self._json({"error": "mac or ip required"}, 400)

            if not self._can_access_mac(mac):
                return self._json({"error": "forbidden (strip not owned)"}, 403)

            # 1. If non-admin user, unclaim it
            users = get_users()
            if not ctx["is_admin"]:
                user_entry = users.get(ctx["token"])
                if user_entry and mac in user_entry.get("strips", []):
                    user_entry["strips"].remove(mac)
                    save_users(users)
                    log(f"[user-remove] Strip {mac} unlinked from {ctx['user_name']}")
                    return self._json({"ok": True, "unlinked": mac})

            # 2. If Admin, complete wipe of strip metadata & disconnect
            for u_tok, u_entry in users.items():
                if "strips" in u_entry:
                    u_entry["strips"] = [m for m in u_entry["strips"] if norm_mac(m).upper() != mac]
            data["users"] = users

            names = data.setdefault("names", {})
            names.pop(mac, None)
            names.pop(mac.lower(), None)
            data["strips"] = [s for s in data.get("strips", []) if norm_mac(s.get("mac", "")).upper() != mac]
            # Remove associated schedules
            scheds = get_schedules()
            scheds = [s for s in scheds if norm_mac(s.get("mac", "")).upper() != mac]
            data["schedules"] = scheds
            save_strips(data)

            # Remove from live controller
            if CTL:
                CTL.remove_device(mac)

            log(f"[admin-remove] Strip {mac} completely removed by Admin")
            return self._json({"ok": True, "removed": mac})

        if u.path == "/api/schedules":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            sid = (q.get("id", [""])[0])
            if not sid:
                return self._json({"error": "id required"}, 400)
            scheds = get_schedules()
            target_sched = next((s for s in scheds if s.get("id") == sid), None)
            if target_sched and not self._can_access_mac(target_sched.get("mac", "")):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            scheds = [s for s in scheds if s.get("id") != sid]
            save_schedules(scheds)
            log(f"sched deleted {sid}")
            return self._json({"ok": True, "schedules": scheds})
        if u.path == "/api/timers":
            if not self._authorized():
                return self._json({"error": "token required"}, 401)
            tid = (q.get("id", [""])[0])
            if not tid:
                return self._json({"error": "id required"}, 400)
            timers = get_timers()
            target = next((t for t in timers if t.get("id") == tid), None)
            if target and not self._can_access_mac(target.get("mac", "")):
                return self._json({"error": "forbidden (strip not owned)"}, 403)
            timers = [t for t in timers if t.get("id") != tid]
            save_timers(timers)
            log(f"timer deleted {tid}")
            return self._json({"ok": True, "timers": timers})
        return self._json({"error": "not found"}, 404)

    def log_message(self, *a):  # quiet default logging
        pass


def on_outlet_switch(mac, outlet, on, source):
    """Callback fired whenever an outlet state changes (button or remote)."""
    st = get_settings()
    if not should_notify(mac, outlet, "switch", st):
        return
    topic = st.get("ntfy")
    if not topic:
        return
    data = load_strips()
    names = data.get("names", {})
    m = norm_mac(mac)
    strip_nm = names.get(m, {}).get("name") or f"MTTL {mac[-7:]}"
    outlet_nm = names.get(m, {}).get("outlets", {}).get(str(outlet)) or f"Outlet {outlet}"
    action = "ON" if on else "OFF"
    title = f"Volta: {strip_nm} · {outlet_nm} {action}"
    src_tag = f" via {source}" if source else ""
    msg = f"{outlet_nm} on {strip_nm} turned {action}{src_tag}."
    tags = "zap" if on else "power"
    try:
        ntfy_send(topic, title, msg, tags)
    except Exception as e:  # noqa: BLE001
        log(f"[notify] switch ntfy failed: {e}")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "selftest":
        # Offline protocol check: fake strip against a live controller.
        import asyncio as _aio

        async def _t():
            ctl = ctrl.Controller(port=0)
            hub = ctl.hub
            server = await _aio.start_server(hub.handle_client, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            reader, writer = await _aio.open_connection("127.0.0.1", port)
            state = {2}

            async def fake():
                while True:
                    raw = await reader.readline()
                    if not raw:
                        return
                    line = raw.decode().strip()
                    if line == "up:getinfo:all":
                        tags = ":".join(
                            f"{n}:{n * 111};{'on' if n in state else 'off'};"
                            f"{int(n in state)};0;0;"
                            f"{5300 if n == 2 and n in state else 0};"
                            f"0000000A;00000000;00000000;0;00;{30 + n}"
                            for n in (1, 2, 3, 4))
                        writer.write(f"up:getinfo:{tags}\r\n".encode())
                        await writer.drain()
                    elif line.startswith("up:onoff:"):
                        _, _, ch, val = line.split(":")
                        (state.add if val == "on" else state.discard)(int(ch))

            task = _aio.create_task(fake())
            writer.write(b"up:bootinfo:LGU+-TAP-HW002;aabbccddeeff;001122334455;1.0.66;connect\r\n")
            writer.write(b"up:power_report:1:224500\r\nup:query:-52\r\n")
            await writer.drain()
            await _aio.sleep(0.5)
            dev = hub.devices["AABBCCDDEEFF"]
            assert dev.outlets[2]["on"] is True and dev.voltage_v == 224.5
            assert dev.rssi == -52
            assert await hub.session("aabbccddeeff").set_outlets([2], False) is True
            assert dev.outlets[2]["on"] is False
            task.cancel()
            print("selftest: parser + command round-trip OK")

        _aio.run(_t())
        raise SystemExit(0)
    CTL = ctrl.Controller(port=DEVICE_PORT, on_event=log, on_switch=on_outlet_switch, on_connect=check_and_apply_auto_claim)
    CTL.start()
    CTL.wait_ready(10)
    analytics.init_db()
    log("[analytics] database initialized")
    import threading as _th
    _th.Thread(target=scheduler_loop, daemon=True).start()
    _th.Thread(target=timer_loop, daemon=True).start()
    _th.Thread(target=watcher_loop, daemon=True).start()
    _th.Thread(target=analytics.collector_loop, args=(CTL,), daemon=True).start()
    log("[analytics] collector thread started (30s interval)")
    log(f"volta-local on :{PORT} — open http://<this-host>:{PORT}")
    log(f"controller listening :{DEVICE_PORT} (strips dial in; provision them with this server's IP)")
    log(f"auth: {'TOKEN required' if TOKEN else 'OPEN (trusted LAN only — set VOLTA_TOKEN if exposed)'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
