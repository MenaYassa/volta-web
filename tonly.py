"""TONLY / MTTL-W01 local TCP driver (port 30300).

Protocol reverse-engineered from a PCAPdroid capture of the vendor app's
"Add strip" flow (phone joined to the strip AP, talking to 192.168.1.1:30300).

Frame layout (all multi-byte ints little-endian):
    magic   12B  ASCII "LGAPMODE0010"
    cmd     u16  0x67=get info, 0x66=get state(?), 0x65=set wifi
    zero    u16  0x0000
    length  u32  payload length in bytes
    payload …

Replies echo the magic; bytes 12-15 are a reply marker, bytes 16-19 the
payload length. No checksums, no auth on the LAN side.

Known commands:
    0x67  query  → 70B info: MAC(12 ascii) + id(12) + zeros(8) +
                    firmware(16) + model(16) + hwrev(6)
    0x66  query  → 4B state blob (all-zero observed in AP mode)
    0x65  128B wifi block → 0xC9 ack (seen 1/4 times) or RST/silence,
            then the strip reboots onto the new network.
            Layout: SSID(32B, NUL-padded) + PASS(32B) + SSID(32B) + PASS(32B).
            Must be split-sent: 20B header, 2.0 s gap, 128B on one connection.

Relay on/off was NOT observed locally (the app switches outlets via its
cloud API). Probing unknown command codes is left to an explicit,
user-confirmed "experimental" sender in the UI.
"""
import socket
import struct
import time

DEFAULT_PORT = 30300
READ_SIZE = 4096
MAGIC = b"LGAPMODE0010"

CMD_WIFI = 0x65
CMD_STATE = 0x66
CMD_INFO = 0x67


def tcp_exchange(ip, port=DEFAULT_PORT, send=b"", timeout=3.0, read_after_send=True):
    """Connect, optionally send bytes, read reply. Returns dict."""
    t0 = time.time()
    out = {"connected": False, "sent": 0, "received_hex": "",
           "received_text": "", "latency_ms": 0, "error": ""}
    try:
        s = socket.create_connection((ip, int(port)), timeout=timeout)
        out["connected"] = True
        s.settimeout(timeout)
        if send:
            s.sendall(send)
            out["sent"] = len(send)
        if read_after_send or not send:
            try:
                data = s.recv(READ_SIZE)
                out["received_hex"] = data.hex()
                out["received_text"] = data.decode("utf-8", "replace")
            except socket.timeout:
                out["error"] = "connected, no reply within timeout (device silent)"
        s.close()
    except Exception as e:  # noqa: BLE001 - report to UI
        out["error"] = f"{type(e).__name__}: {e}"
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def banner_grab(ip, port=DEFAULT_PORT, timeout=3.0):
    """Read-only: connect and listen without sending anything."""
    return tcp_exchange(ip, port, send=b"", timeout=timeout)


def lg_frame(cmd, payload=b""):
    """Build one LGAPMODE0010 request frame."""
    return MAGIC + struct.pack("<H", cmd) + b"\x00\x00" \
        + struct.pack("<I", len(payload)) + payload


def lg_parse_reply(data):
    """Split a reply into marker/length/payload. Returns dict."""
    if len(data) < 20 or not data.startswith(MAGIC):
        return {"ok": False, "error": "not an LGAPMODE reply",
                "raw_hex": data.hex()}
    marker = data[12:16].hex()
    (length,) = struct.unpack("<I", data[16:20])
    payload = data[20:20 + length]
    return {"ok": True, "marker": marker, "length": length,
            "payload_hex": payload.hex(), "raw_hex": data.hex()}


def _recv_all(sock, timeout=4.0):
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            b = sock.recv(READ_SIZE)
            if not b:
                break
            chunks.append(b)
            if len(b) < READ_SIZE:
                break
    except socket.timeout:
        pass
    return b"".join(chunks)


def lg_exchange(ip, cmd, payload=b"", port=DEFAULT_PORT, timeout=4.0):
    """Send one LG frame, read the full reply. Returns dict."""
    t0 = time.time()
    out = {"connected": False, "sent": 0, "reply": {}, "latency_ms": 0,
           "error": ""}
    try:
        s = socket.create_connection((ip, int(port)), timeout=timeout)
        out["connected"] = True
        frame = lg_frame(cmd, payload)
        s.sendall(frame)
        out["sent"] = len(frame)
        raw = _recv_all(s, timeout)
        s.close()
        if raw:
            out["reply"] = lg_parse_reply(raw)
        else:
            out["error"] = "connected, no reply within timeout"
    except Exception as e:  # noqa: BLE001 - report to UI
        out["error"] = f"{type(e).__name__}: {e}"
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def _asc(raw):
    return raw.split(b"\x00")[0].decode("ascii", "replace")


def lg_info(ip, port=DEFAULT_PORT, timeout=4.0):
    """Query device info. Safe, read-only."""
    r = lg_exchange(ip, CMD_INFO, b"", port, timeout)
    try:
        p = bytes.fromhex(r.get("reply", {}).get("payload_hex", ""))
    except ValueError:
        p = b""
    if r.get("reply", {}).get("ok") and len(p) >= 70:
        mac = _asc(p[0:12])
        r["info"] = {"mac": ":".join(mac[i:i + 2] for i in range(0, 12, 2)),
                     "chip_id": _asc(p[12:24]),
                     "firmware": _asc(p[32:48]),
                     "model": _asc(p[48:64]),
                     "hw_rev": _asc(p[64:70])}
    return r


def lg_state(ip, port=DEFAULT_PORT, timeout=4.0):
    """Query state blob. Safe, read-only (meaning of bytes TBD)."""
    r = lg_exchange(ip, CMD_STATE, b"", port, timeout)
    try:
        p = bytes.fromhex(r.get("reply", {}).get("payload_hex", ""))
    except ValueError:
        p = b""
    if p:
        r["state_hex"] = p.hex()
        r["state_ints"] = list(p)
    return r


def _pad(s, n):
    b = s.encode("utf-8")[:n]
    return b + b"\x00" * (n - len(b))


def lg_set_wifi(ip, ssid, password, port=DEFAULT_PORT, timeout=8.0):
    """Send home-WiFi credentials exactly like the vendor app.

    Choreography (observed 4/4 times in the reference capture):
      1. new connection, send the 20B header (cmd 0x65, length 128),
      2. wait 2.0 s on the SAME connection,
      3. send the 128B block, read the 0xC9 ack (or RST),
      4. ~1 s later, fresh connection, 0x66 query (24B 0xCA reply).
    Then the strip reboots onto the new network (2.4 GHz only).
    Sending all 148B at once does NOT work — the strip ignores it.
    """
    block = _pad(ssid, 32) + _pad(password, 32) + _pad(ssid, 32) + _pad(password, 32)
    t0 = time.time()
    out = {"connected": False, "sent": 0, "reply": {}, "confirm": {},
           "latency_ms": 0, "error": ""}
    try:
        s = socket.create_connection((ip, int(port)), timeout=timeout)
        out["connected"] = True
        s.sendall(lg_frame(CMD_WIFI, block)[:20])  # header declares len 128
        out["sent"] = 20
        time.sleep(2.0)
        s.sendall(block)
        out["sent"] = 20 + len(block)
        raw = _recv_all(s, timeout)
        s.close()
        if raw:
            out["reply"] = lg_parse_reply(raw)
        else:
            out["error"] = "no ack (device may already be rebooting)"
    except Exception as e:  # noqa: BLE001 - report to UI
        out["error"] = f"{type(e).__name__}: {e}"
    time.sleep(1.0)
    try:
        out["confirm"] = lg_exchange(ip, CMD_STATE, b"", port, 4)
    except Exception as e:  # noqa: BLE001
        out["confirm"] = {"error": str(e)}
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def http_probe(ip, port=DEFAULT_PORT, timeout=3.0):
    """Some firmwares answer plain HTTP; try GET / and GET /state."""
    results = {}
    for path in ("/", "/state", "/status"):
        req = f"GET {path} HTTP/1.0\r\nHost: {ip}\r\n\r\n".encode()
        results[path] = tcp_exchange(ip, port, send=req, timeout=timeout)
    return results


# Ports worth checking on a smart strip: web setup pages, Tuya, MQTT…
COMMON_PORTS = [22, 23, 53, 80, 443, 3000, 5000, 6666, 6667, 6668, 8000, 8001,
                8080, 8443, 8888, 9999, 1883, 8883, 1900, 5353, 30300, 30301]


def http_get(ip, port, path="/", timeout=3.0):
    req = (f"GET {path} HTTP/1.0\r\nHost: {ip}\r\n"
           f"User-Agent: volta-local\r\nConnection: close\r\n\r\n").encode()
    return tcp_exchange(ip, port, send=req, timeout=timeout)


def arp_mac(ip):
    """MAC from kernel ARP table (Linux/Termux). Identifies chipset vendor."""
    try:
        with open("/proc/net/arp") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if parts and parts[0] == ip:
                    return parts[3] if len(parts) > 3 else ""
    except OSError:
        pass
    return ""
