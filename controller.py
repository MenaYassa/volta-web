#!/usr/bin/env python3
"""Volta controller: the strip dials OUT to us; we serve clients.

Implements the strip's native `up:` text protocol over TCP 10086, rederived
from powerk's published protocol reference (facts, own code):
  strip -> us : up:bootinfo:<model>;<mac>;<mac>;<fw>;connect
  strip -> us : up:getinfo:<ch>:<runtime>;<relay>;<state>;<ovl>;<ovh>;
               <power mW>;<energy hex Wh>;<prev>;<config>;<status>;
               <event hex>;<temp C>  (x4, ch 5 = aggregate, skipped)
  strip -> us : up:power_report:<ch>:<mV>=50000 (else per-outlet mA, unused)
  strip -> us : up:query:<rssi>
  strip -> us : up:event:onoff:<0-4>:on|off   (physical button; 0 = master)
  us -> strip : up:getinfo:all | up:onoff:<1-4>:on|off |
               up:power_report:1:vol | up:query:wifirssi
Setup (strip AP 192.168.1.1:30300, one line per connection, CRLF):
  up:ip:<server-ip>            -> up:ip:ip_ok
  up:connect:<ssid>:<password> -> up:connect:connect_ok

Stdlib only. Run under server.py (background thread) or standalone.
"""
from __future__ import annotations

import asyncio
import re
import socket
import threading
import time

DEVICE_PORT = 10086
POLL_SECONDS = 8.0
DIAG_EVERY = 3

BOOTINFO_RE = re.compile(
    r"^up:bootinfo:([^;\r\n]+);([0-9A-Fa-f]{12});([0-9A-Fa-f]{12});([^;\r\n]+);connect$")
GETINFO_RE = re.compile(
    r"(?P<ch>[1-5]):(?P<runtime>-?\d+);(?P<relay>on|off);(?P<state>-?\d+);"
    r"(?P<overload>[^;:]+);(?P<overheat>[^;:]+);(?P<power>-?\d+);"
    r"(?P<energy>[0-9A-Fa-f]{8});(?P<previous>[0-9A-Fa-f]{8});"
    r"(?P<config>[0-9A-Fa-f]{8});(?P<status>[^;:]+);"
    r"(?P<event>[0-9A-Fa-f]{2});(?P<temperature>-?\d+)", re.I)
ONOFF_ACK_RE = re.compile(r"^up:onoff:([1-4]):(on|off)$", re.I)
EVENT_RE = re.compile(r"^up:event:onoff:([0-4]):(on|off)$", re.I)
POWER_REPORT_RE = re.compile(r"^up:power_report:([1-5]):(-?\d+)$", re.I)
QUERY_RE = re.compile(r"^up:query:(-?\d+)$")


def parse_getinfo(line):
    text = line.strip("\r\n\x00 ")
    if text.startswith("up:getinfo:"):
        text = text[len("up:getinfo:"):]
    out = []
    for m in GETINFO_RE.finditer(text):
        g = m.groupdict()
        ch = int(g["ch"])
        if ch > 4:
            continue
        out.append({"n": ch, "on": g["relay"].lower() == "on",
                    "power_w": round(int(g["power"]) / 1000.0, 2),
                    "energy_kwh": round(int(g["energy"], 16) / 1000.0, 3),
                    "temp_c": int(g["temperature"])})
    return out


class Device:
    def __init__(self, mac, model, fw, ip):
        self.mac = mac.upper()
        self.model = model
        self.fw = fw
        self.ip = ip
        self.online = False
        self.voltage_v = None
        self.rssi = None
        self.last_seen = time.time()
        self.outlets = {n: {"n": n, "on": False, "power_w": 0.0,
                            "energy_kwh": 0.0, "temp_c": 0} for n in range(1, 5)}

    @property
    def name(self):
        return f"MTTL {self.mac[-7:]}"

    def touch(self):
        self.last_seen = time.time()

    def snapshot(self, pending=0):
        outs = list(self.outlets.values())
        total_w = round(sum(o["power_w"] for o in outs), 2)
        return {"mac": self.mac, "name": self.model and self.name,
                "model": self.model, "fw": self.fw, "ip": self.ip,
                "online": self.online, "on": any(o["on"] for o in outs),
                "power_w": total_w,
                "energy_kwh": round(sum(o["energy_kwh"] for o in outs), 3),
                "voltage_v": self.voltage_v,
                "current_a": round(total_w / self.voltage_v, 2) if self.voltage_v else 0.0,
                "rssi": self.rssi, "outlets": outs,
                "last_seen": self.last_seen,
                "last_seen_ago": round(time.time() - self.last_seen),
                "pending": pending}


class Session:
    def __init__(self, ip, reader, writer):
        self.ip = ip
        self.reader = reader
        self.writer = writer
        self.lock = asyncio.Lock()
        self.device = None
        self.got_getinfo = asyncio.Event()

    async def send(self, cmd):
        self.writer.write((cmd + "\r\n").encode("ascii"))
        await self.writer.drain()

    async def _refresh(self, timeout=7.0):
        self.got_getinfo.clear()
        await self.send("up:getinfo:all")
        try:
            await asyncio.wait_for(self.got_getinfo.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def refresh(self, timeout=7.0):
        async with self.lock:
            return await self._refresh(timeout)

    async def set_outlets(self, channels, on):
        async with self.lock:
            for ch in channels:
                await self.send(f"up:onoff:{ch}:{'on' if on else 'off'}")
            await asyncio.sleep(0.3)
            return await self._refresh()

    async def diagnostics(self):
        async with self.lock:
            await self.send("up:power_report:1:vol")
            await self.send("up:query:wifirssi")


class Hub:
    def __init__(self, on_event=None, on_switch=None, on_connect=None):
        self.devices = {}
        self.sessions = {}
        self.pending = {}  # mac -> [{channels:[..], on:bool, ts:float, source:str}]
        self.on_event = on_event  # fn(msg) for server log
        self.on_switch = on_switch  # fn(mac, outlet, on, source)
        self.on_connect = on_connect  # fn(mac, ip)

    def _log(self, msg):
        if self.on_event:
            try:
                self.on_event(msg)
            except Exception:
                pass
        else:
            print(msg, flush=True)

    def session(self, mac):
        s = self.sessions.get(mac.upper())
        if s and s.device and s.device.online and not s.writer.is_closing():
            return s
        return None

    def snapshot(self):
        return {"devices": [d.snapshot(len(self.pending.get(d.mac.upper(), [])))
                            for d in self.devices.values()]}

    def enqueue(self, mac, channels, on, source="ui"):
        """Queue a command for an offline strip. Coalesces per outlet."""
        mac = mac.upper()
        q = self.pending.setdefault(mac, [])
        for ch in channels:
            q = [e for e in q if ch not in e["channels"]]
            self.pending[mac] = q
            q.append({"channels": [ch], "on": on,
                      "ts": time.time(), "source": source})
        q = self.pending[mac]
        if len(q) > 20:
            del q[:len(q) - 20]
        return len(q)

    def pending_count(self, mac):
        return len(self.pending.get(mac.upper(), []))

    def clear_pending(self, mac):
        self.pending.pop(mac.upper(), None)

    async def drain_pending(self, session):
        q = self.pending.get(session.device.mac.upper(), []) if session.device else []
        if not q:
            return 0
        n = 0
        for entry in list(q):
            try:
                async with session.lock:
                    for ch in entry["channels"]:
                        await session.send(f"up:onoff:{ch}:{'on' if entry['on'] else 'off'}")
                    await asyncio.sleep(0.2)
                    await session._refresh(timeout=5.0)
                n += 1
            except Exception as err:
                self._log(f"[device] {session.device.name} queued cmd failed: {err!r}")
                break
            try:
                self.pending.get(session.device.mac.upper(), []).remove(entry)
            except ValueError:
                pass
        if n:
            self._log(f"[device] {session.device.name} applied {n} queued command(s) ({session.device.mac})")
        return n

    async def handle_client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"

        # Enable aggressive TCP keepalive to detect dead sockets from router restarts / IP rotation
        try:
            sock = writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Linux keepalive tuning: 15s idle + 5s interval * 3 probes = 30s dead socket purge
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except Exception:
            pass

        s = Session(ip, reader, writer)
        self._log(f"[device] connection from {ip}")
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                if len(raw) > 8192:
                    raise ValueError("line too long")
                line = raw.replace(b"\x00", b"").decode("utf-8", "replace").strip("\r\n ")
                m = BOOTINFO_RE.match(line)
                if m:
                    mac = m.group(2).upper()
                    d = self.devices.get(mac) or Device(mac, m.group(1), m.group(4), ip)
                    d.model, d.fw, d.ip, d.online = m.group(1), m.group(4), ip, True
                    d.touch()
                    s.device = d
                    self.devices[mac] = d
                    old = self.sessions.get(mac)
                    if old and old is not s:
                        old.writer.close()
                    self.sessions[mac] = s
                    self._log(f"[device] {d.name} model={d.model} fw={d.fw}")
                    if self.on_connect:
                        try:
                            self.on_connect(mac, ip)
                        except Exception:
                            pass
                    async def _join(sess):
                        await sess.refresh()
                        await self.drain_pending(sess)
                    asyncio.create_task(_join(s))
                    continue
                if not s.device:
                    continue
                s.device.touch()
                if line.startswith("up:getinfo:"):
                    recs = parse_getinfo(line)
                    if len(recs) == 4:
                        s.device.outlets = {r["n"]: r for r in recs}
                        s.got_getinfo.set()
                    continue
                if line.lower().startswith("up:power_report:"):
                    m2 = POWER_REPORT_RE.fullmatch(line)
                    if m2 and int(m2.group(2)) >= 50000:
                        s.device.voltage_v = round(int(m2.group(2)) / 1000.0, 1)
                    continue
                m2 = QUERY_RE.fullmatch(line)
                if m2:
                    s.device.rssi = int(m2.group(1))
                    continue
                m2 = EVENT_RE.fullmatch(line)
                if m2:
                    ch, on = int(m2.group(1)), m2.group(2).lower() == "on"
                    if ch:
                        s.device.outlets[ch]["on"] = on
                    self._log(f"[device] {s.device.name} outlet {ch} -> {m2.group(2)} (button)")
                    if self.on_switch:
                        try:
                            self.on_switch(s.device.mac, ch, on, "button")
                        except Exception as e:
                            self._log(f"[device] on_switch hook error: {e}")
                    continue
                if ONOFF_ACK_RE.fullmatch(line):
                    continue
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as err:
            self._log(f"[device] session error {ip}: {err!r}")
        finally:
            if s.device and self.sessions.get(s.device.mac) is s:
                self.sessions.pop(s.device.mac, None)
                s.device.online = False
                self._log(f"[device] {s.device.name} offline")
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def poll_loop(self):
        n = 0
        while True:
            n += 1
            for s in list(self.sessions.values()):
                if not s.device:
                    continue
                try:
                    if not await s.refresh():
                        self._log(f"[device] {s.device.name} poll timeout")
                    elif n % DIAG_EVERY == 0:
                        await s.diagnostics()
                except Exception as err:
                    self._log(f"[device] poll error: {err!r}")
            await asyncio.sleep(POLL_SECONDS)


def setup_send(cmd, expect, host="192.168.1.1", port=30300, timeout=6.0):
    """One plain-text line to the setup service (own connection, CRLF)."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        s.sendall((cmd + "\r\n").encode())
        buf, deadline = b"", time.monotonic() + timeout
        while b"\n" not in buf and time.monotonic() < deadline:
            try:
                chunk = s.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
    text = buf.decode("utf-8", "replace").strip()
    if expect and expect not in text:
        raise RuntimeError(f"strip answered {text!r}, expected {expect!r}")
    return text


HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$|^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def valid_server(s):
    """IP or DNS hostname. Strips dial out to this, so it must be reachable."""
    s = (s or "").strip()
    if not s or any(c in s for c in " :\r\n/"):
        return ""
    try:
        import ipaddress as _ip
        return str(_ip.IPv4Address(s))
    except ValueError:
        pass
    if HOST_RE.match(s):
        return s.lower()
    return ""


def get_ap_bootinfo(host="192.168.1.1", port=30300, timeout=4.0):
    """Connect to strip AP and query bootinfo to extract its MAC address."""
    import re
    try:
        # First try plain ASCII query 'up:bootinfo'
        resp = setup_send("up:bootinfo", "", host=host, port=port, timeout=timeout)
        m = re.search(r"([0-9A-Fa-f]{12})", resp)
        if m:
            return m.group(1).upper()
    except Exception:
        pass
    try:
        # Fallback to binary tonly exchange cmd 0x67
        import tonly
        res = tonly.lg_exchange(host, 0x67, port=port, timeout=timeout)
        mac = (res.get("reply") or {}).get("mac", "")
        if mac:
            return mac.upper().replace(":", "")
    except Exception:
        pass
    return ""


def claim_strip_api(web_url, token, mac):
    """Call the Volta claim API to link this strip MAC to the user's token."""
    import urllib.request as _ur
    import urllib.error as _ue
    import json
    
    url = (web_url or "").rstrip("/")
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    claim_endpoint = f"{url}/api/claim"
    
    req = _ur.Request(
        claim_endpoint,
        data=json.dumps({"mac": mac, "token": token}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": token},
        method="POST"
    )
    with _ur.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def provision(server_ip, ssid, password, host="192.168.1.1", port=30300):
    """Write server address (IP or hostname) + Wi-Fi into the strip."""
    server_ip = valid_server(server_ip)
    if not server_ip:
        raise ValueError("server must be an IPv4 address or hostname (no spaces, no protocol)")
    for v in (ssid, password):
        if any(c in v for c in ":\r\n"):
            raise ValueError("SSID/password cannot contain ':' or newlines (strip protocol limit)")
    r1 = setup_send(f"up:ip:{server_ip}", "up:ip:ip_ok", host, port)
    r2 = setup_send(f"up:connect:{ssid}:{password}", "up:connect:connect_ok", host, port)
    return r1, r2


class Controller(threading.Thread):
    """Background 10086 listener. start()/snapshot()/set_outlets()."""

    def __init__(self, port=DEVICE_PORT, on_event=None, on_switch=None, on_connect=None):
        super().__init__(daemon=True)
        self.hub = Hub(on_event=on_event, on_switch=on_switch, on_connect=on_connect)
        self.port = port
        self.loop = None
        self._ready = threading.Event()

    def run(self):
        async def main():
            server = await asyncio.start_server(self.hub.handle_client, "0.0.0.0", self.port)
            self.loop = asyncio.get_running_loop()
            asyncio.create_task(self.hub.poll_loop())
            self._ready.set()
            async with server:
                await server.serve_forever()
        asyncio.run(main())

    def wait_ready(self, timeout=10):
        return self._ready.wait(timeout)

    def remove_device(self, mac):
        """Thread-safe: disconnect and remove a device from memory."""
        mac = mac.upper()
        if self.loop:
            async def _remove():
                s = self.hub.sessions.pop(mac, None)
                if s:
                    s.writer.close()
                    try:
                        await s.writer.wait_closed()
                    except Exception:
                        pass
                self.hub.devices.pop(mac, None)
                self.hub.pending.pop(mac, None)
            asyncio.run_coroutine_threadsafe(_remove(), self.loop)
        else:
            self.hub.sessions.pop(mac, None)
            self.hub.devices.pop(mac, None)
            self.hub.pending.pop(mac, None)

    def snapshot(self):
        return self.hub.snapshot()

    def set_outlets(self, mac, channels, on, timeout=12, queue=True, source="ui"):
        """Thread-safe: run the coroutine on the controller loop.
        Offline + queue=True -> stores the command, returns (True, 'queued')."""
        import concurrent.futures as _f
        s = self.hub.session(mac)
        if not s:
            if queue:
                n = self.hub.enqueue(mac, list(channels), on, source)
                return True, f"queued ({n} pending, applies on reconnect)"
            return False, "device offline"
        fut = asyncio.run_coroutine_threadsafe(s.set_outlets(channels, on), self.loop)
        try:
            res = bool(fut.result(timeout=timeout))
            if res and self.hub.on_switch:
                for ch in channels:
                    try:
                        self.hub.on_switch(mac, ch, on, source)
                    except Exception as e:
                        self.hub._log(f"[device] on_switch hook error: {e}")
            return res, ""
        except _f.TimeoutError:
            return False, "command timeout"
        except Exception as e:
            return False, str(e)


DEFAULT_SERVER_IP = "YOUR_SERVER_IP"
DEFAULT_WEB_URL = "YOUR_WEB_URL"


if __name__ == "__main__":
    import argparse as _ap
    ap = _ap.ArgumentParser(description="Provision a TONLY / MTTL-W01 strip (join its AP first).")
    ap.add_argument("--server-ip", default=DEFAULT_SERVER_IP, help="Server IP the strip dials into")
    ap.add_argument("--ssid", default="", help="Home 2.4GHz Wi-Fi SSID")
    ap.add_argument("--password", default="", help="Home Wi-Fi password")
    ap.add_argument("--token", default="", help="Optional Volta user token")
    ap.add_argument("--web-url", default=DEFAULT_WEB_URL, help="Volta Web UI URL")
    ap.add_argument("--host", default="192.168.1.1", help="Strip AP default gateway (default: 192.168.1.1)")
    a = ap.parse_args()

    ssid = a.ssid
    password = a.password
    server_ip = a.server_ip

    if not ssid:
        try:
            ssid = input("Enter Home 2.4GHz Wi-Fi SSID: ").strip()
        except Exception:
            pass
    if not password:
        try:
            password = input("Enter Wi-Fi Password: ").strip()
        except Exception:
            pass

    if not ssid:
        raise SystemExit("Error: Wi-Fi SSID is required.")

    if not valid_server(server_ip):
        raise SystemExit(f"--server-ip must be IPv4 or hostname (got {server_ip!r})")

    print(f"Connecting to strip at {a.host}:30300 ...")
    r1, r2 = provision(server_ip, ssid, password, a.host)
    print(f"  {r1}\n  {r2}")

    # Register auto-claim expectation with server if token is present
    if a.token and a.web_url and a.web_url != "YOUR_WEB_URL":
        try:
            import urllib.request as _ur, json as _js
            req = _ur.Request(
                f"{a.web_url.rstrip('/')}/api/claim",
                data=_js.dumps({"expect_auto": True, "token": a.token}).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-Token": a.token},
                method="POST"
            )
            with _ur.urlopen(req, timeout=5) as resp:
                pass
        except Exception:
            pass

    print(f"\n✓ Strip configured! It will now connect to '{ssid}' and dial into {server_ip}:10086.")
    print("It will automatically appear on your Volta dashboard within 10-30 seconds.")



