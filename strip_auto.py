#!/usr/bin/env python3
"""Auto-join TONLY_TAP_* setup APs and run LGAPMODE blocks.

One-block-per-connection design: for every chunk it (re)joins the strip AP,
verifies the strip answers, runs the block, and moves on. Reboot-aware: if
the AP vanishes (strip rebooted), it waits for it to come back instead of
dying — the exact failure that killed manual sweeps.

Needs: PC with Wi-Fi (Windows: netsh, Linux: nmcli). No extra packages.
`import tonly` gives the protocol frames (same folder).

Examples:
  python3 strip_auto.py info                        # join AP, read info
  python3 strip_auto.py state                       # join AP, read state
  python3 strip_auto.py sweep --start 0x60 --end 0x7F --chunk 4
  python3 strip_auto.py wifi --home-ssid Patko --home-pass '...'
  python3 strip_auto.py hunt                        # info + state + sweep 0x60-0x7F

Safety: empty frames only in sweep; 0x65 (wifi) runs ONLY via `wifi`
subcommand with explicit credentials. Never probes blind writes.
"""
import argparse
import json
import platform
import re
import subprocess
import sys
import time

import tonly

AP_PREFIXES = ("TONLY_TAP_", "U+TAP_")
STRIP_IP = "192.168.1.1"
STRIP_PORT = 30300
CONNECT_TIMEOUT = 25
AP_WAIT = 90  # max wait for a rebooting strip's AP to return


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def derive_pw(ssid):
    """LGU_<suffix> where suffix follows the last underscore. None if N/A."""
    if not ssid or not ssid.startswith(AP_PREFIXES) or "_" not in ssid:
        return None
    suf = ssid.split("_")[-1]
    return ("LGU_" + suf.upper()) if suf else None


# ---------------- Wi-Fi backends ----------------

def _run(cmd, timeout=30):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 99, str(e)


class LinuxWifi:
    def scan(self):
        _, txt = _run(["nmcli", "-t", "-f", "SSID,SIGNAL", "dev", "wifi", "list",
                       "--rescan", "yes"], timeout=40)
        nets = []
        for line in txt.splitlines():
            if ":" not in line:
                continue
            ssid, _, sig = line.partition(":")
            if ssid:
                nets.append((ssid.strip(), sig))
        return nets

    def connect(self, ssid, password):
        args = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        return _run(args, timeout=CONNECT_TIMEOUT + 10)

    def current(self):
        _, txt = _run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show",
                       "wlan0"], timeout=10)
        m = re.search(r"GENERAL\.CONNECTION:(.+)", txt)
        return (m.group(1).strip() if m else "") or None

    def disconnect(self):
        _run(["nmcli", "dev", "disconnect", "wlan0"], timeout=15)

    def set_autoconnect(self, ssid, auto):
        if ssid:
            _run(["nmcli", "connection", "modify", ssid,
                  "connection.autoconnect", "yes" if auto else "no"], timeout=15)


class WindowsWifi:
    LOCATION_MSG = ("Windows blocks Wi-Fi scans until Location is ON: Settings > "
                    "Privacy & security > Location > Location services ON "
                    "(+ 'Let desktop apps access your location'). Then rerun.")

    def scan(self):
        _, txt = _run(["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=40)
        if "location permission" in txt.lower():
            log(self.LOCATION_MSG)
            return []
        nets = [(m.group(1).strip(), "") for m in re.finditer(r"^SSID \d+ : (.+)$", txt, re.M)]
        if not nets:  # locale or mode quirk — retry plain listing
            _, txt = _run(["netsh", "wlan", "show", "networks"], timeout=40)
            nets = [(m.group(1).strip(), "") for m in re.finditer(r"^SSID \d+ : (.+)$", txt, re.M)]
            if not nets and txt.strip():
                log("netsh RAW (unparsed, locale?): " + txt.strip()[:400].replace("\r", " | "))
                nets = [("__raw__", txt.strip()[:200])]
        return nets

    def _profile(self, ssid, password):
        import xml.sax.saxutils as sax
        prof = (f'<?xml version="1.0"?><WLANProfile xmlns="http://www.microsoft.com'
                f'/networking/WLAN/profile/v1"><name>{sax.escape(ssid)}</name>'
                f'<SSIDConfig><SSID><name>{sax.escape(ssid)}</name></SSID></SSIDConfig>'
                f'<connectionType>ESS</connectionType><connectionMode>manual</connectionMode>'
                f'<MSM><security><authEncryption><authentication>WPA2PSK</authentication>'
                f'<encryption>AES</encryption><useOneX>false</useOneX></authEncryption>'
                f'<sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
                f'<keyMaterial>{sax.escape(password or "")}</keyMaterial></sharedKey>'
                f'</security></MSM></WLANProfile>')
        path = "tonly-tap-profile.xml"
        with open(path, "w") as f:
            f.write(prof)
        return path

    def connect(self, ssid, password):
        if password:
            rc, txt = _run(["netsh", "wlan", "add", "profile",
                            f"filename={self._profile(ssid, password)}"], timeout=20)
            if rc != 0:
                return rc, txt
        return _run(["netsh", "wlan", "connect", f"name={ssid}"],
                    timeout=CONNECT_TIMEOUT + 10)

    def current(self):
        _, txt = _run(["netsh", "wlan", "show", "interfaces"], timeout=10)
        st = re.search(r"^\s*State\s*:\s*(.+)$", txt, re.M)
        ss = re.search(r"^\s*SSID\s*:\s*(.+)$", txt, re.M)
        if not st or st.group(1).strip().lower() != "connected":
            return None
        return ss.group(1).strip() if ss else None

    def disconnect(self):
        # Drop the home network first: Windows prefers known networks WITH
        # internet and will otherwise hop back off the strip AP mid-run.
        _run(["netsh", "wlan", "disconnect"], timeout=15)

    def set_autoconnect(self, ssid, auto):
        if ssid:
            _run(["netsh", "wlan", "set", "profileparameter", f"name={ssid}",
                  f"connectionmode={'auto' if auto else 'manual'}"], timeout=15)


def backend():
    return WindowsWifi() if platform.system() == "Windows" else LinuxWifi()


# ---------------- connection management ----------------

def find_ap(wifi, prefixes=AP_PREFIXES, timeout=60):
    """Scan until a strip AP appears. Returns ssid or None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        nets = wifi.scan()
        log("visible: " + (", ".join(s for s, _ in nets[:12]) or "(none)"))
        for ssid, _ in nets:
            if ssid.upper().startswith(prefixes):
                return ssid
        time.sleep(5)
    return None


def join_ap(wifi, ssid, password, timeout=45):
    """Connect and confirm: interface SSID matches AND strip answers info."""
    log(f"joining {ssid} ...")
    rc, txt = wifi.connect(ssid, password)
    t0 = time.time()
    last_cur, n = None, 0
    while time.time() - t0 < timeout:
        try:
            cur = wifi.current()
            if cur != last_cur and cur:
                log(f"interface now on: {cur}")
                last_cur = cur
            if cur == ssid:
                r = tonly.lg_info(STRIP_IP, STRIP_PORT, timeout=4)
                if r.get("info"):
                    log(f"joined + verified: {r['info']}")
                    return True
                log("associated, waiting for strip service ...")
            else:
                # Even if the interface report lags, the strip may be there.
                r = tonly.lg_info(STRIP_IP, STRIP_PORT, timeout=2)
                if r.get("info"):
                    log(f"strip answers (interface reports {cur!r}): {r['info']}")
                    return True
        except Exception as e:  # noqa: BLE001
            log(f"join poll: {e}")
        n += 1
        time.sleep(3)
    log(f"join FAILED after {timeout}s (netsh/nmcli rc={rc}). "
        f"Interface says: {wifi.current()!r}. Uncheck 'connect automatically' "
        f"on the home network so Windows stops hopping back.")
    return False


def ensure_ap(wifi, ssid, password, wait=AP_WAIT):
    """Wait for the AP (reboot-aware), then join. Returns bool."""
    if wifi.current() == ssid:
        r = tonly.lg_info(STRIP_IP, STRIP_PORT, timeout=3)
        if r.get("info"):
            return True
    log(f"waiting for {ssid} (up to {wait}s, reboot-aware) ...")
    t0 = time.time()
    tries = 0
    while time.time() - t0 < wait:
        nets = [s for s, _ in wifi.scan()]
        if ssid in nets or any(s.startswith(AP_PREFIXES) for s in nets):
            found = ssid if ssid in nets else next(
                s for s in nets if s.startswith(AP_PREFIXES))
            tries += 1
            if join_ap(wifi, found, derive_pw(found) or password):
                return True
            if tries >= 3:
                log("3 join attempts failed while AP visible — stopping")
                return False
            log("retrying join ...")
        time.sleep(5)
    return False


# ---------------- blocks ----------------

def block_info():
    r = tonly.lg_info(STRIP_IP, STRIP_PORT)
    print(json.dumps(r.get("info") or {"error": r.get("error")}, indent=1))
    return bool(r.get("info"))


def block_state():
    r = tonly.lg_state(STRIP_IP, STRIP_PORT)
    print(json.dumps({"state_hex": r.get("state_hex", ""),
                      "error": r.get("error", "")}, indent=1))
    return bool(r.get("state_hex"))


def block_sweep(start, end, timeout=1.5):
    import server
    resp = server.run_sweep(STRIP_IP, STRIP_PORT, start, end, timeout)
    interesting = [row for row in resp["rows"]
                   if row.get("marker") or row.get("payload_hex")]
    print(json.dumps({"range": [hex(start), hex(end)],
                      "state_before": resp["state_before"],
                      "state_after": resp["state_after"],
                      "state_changed": resp["state_changed"],
                      "aborted_at": resp["aborted_at"],
                      "reboots": resp.get("reboots"),
                      "interesting": interesting,
                      "silent": sum(1 for row in resp["rows"]
                                    if not row.get("skipped") and not row.get("marker")
                                    and not row.get("payload_hex"))}, indent=1))
    return resp


CANNED = [
    ("0x66 baseline", "0x66", ""),
    ("0x66 +00", "0x66", "00"),
    ("0x66 +01", "0x66", "01"),
    ("0x66 +02", "0x66", "02"),
    ("0x66 +03", "0x66", "03"),
    ("0x66 +04", "0x66", "04"),
    ("0x66 +00000000", "0x66", "00000000"),
    ("0x66 +01000000", "0x66", "01000000"),
    ("0x66 +00000001", "0x66", "00000001"),
    ("0x68 baseline", "0x68", ""),
    ("0x68 +00", "0x68", "00"),
    ("0x68 +01", "0x68", "01"),
    ("0x68 outlet1 on?", "0x68", "0101"),
    ("0x68 outlet1 off?", "0x68", "0100"),
    ("0x68 outlet2 on?", "0x68", "0201"),
    ("0x68 outlet2 off?", "0x68", "0200"),
    ("0x68 outlet3 on?", "0x68", "0301"),
    ("0x68 outlet4 on?", "0x68", "0401"),
    ("0x68 +I2C R1-ON bytes (long shot)", "0x68", "131a1a"),
    ("0x68 +I2C R1-OFF bytes (long shot)", "0x68", "132a26"),
    ("0x69 baseline", "0x69", ""),
]


def block_campaign(wifi, ssid, gap=5.0, fresh=False):
    """Canned payload shots with state check after each. Stops on change."""
    base = tonly.lg_state(STRIP_IP, STRIP_PORT, 3).get("state_hex", "")
    running, out, lost = base, [], 0
    for i, (label, cmd_s, pay_hex) in enumerate(CANNED):
        cmd = int(cmd_s, 0)
        payload = bytes.fromhex(pay_hex) if pay_hex else b""
        if fresh and i > 0:
            # One-block-per-connection: re-verify (and rejoin if needed) so
            # each shot is attributed cleanly even if the last one rebooted.
            r = tonly.lg_info(STRIP_IP, STRIP_PORT, timeout=3)
            if not r.get("info"):
                log(f"shot #{i}: link dirty — rejoining before firing")
                if not ensure_ap(wifi, ssid, derive_pw(ssid) or "", wait=60):
                    out.append({"n": i, "label": label,
                                "error": "rejoin failed, stopping"})
                    break
        r = tonly.lg_exchange(STRIP_IP, cmd, payload, STRIP_PORT, 4)
        rep = r.get("reply") or {}
        err = r.get("error", "")
        if "nreachable" in err or "10065" in err:
            lost += 1
            out.append({"n": i, "label": label, "error": err + " (route lost)"})
            log(f"shot #{i} {label}: ROUTE LOST ({lost}x)")
            if lost >= 2:
                # Discriminate: AP still visible = this machine hopped;
                # AP gone = the strip rebooted.
                try:
                    visible = [s for s, _ in wifi.scan()]
                    cur = wifi.current()
                except Exception:  # noqa: BLE001
                    visible, cur = [], "?"
                if ssid in visible:
                    verdict = ("AP STILL VISIBLE — this machine hopped networks "
                               f"(interface now on {cur!r}), the strip is fine")
                else:
                    verdict = ("AP GONE from scan — the strip REBOOTED on "
                               f"shot #{i} ({label}). Reproducible crash = live parser.")
                log("VERDICT: " + verdict)
                out.append({"n": i, "verdict": verdict})
                log("route gone twice — rejoin the AP and re-run to resume")
                break
            continue
        lost = 0
        after = tonly.lg_state(STRIP_IP, STRIP_PORT, 3).get("state_hex", "") or running
        changed = bool(running and after and running != after)
        out.append({"n": i, "label": label, "cmd": hex(cmd),
                    "marker": rep.get("marker", ""),
                    "reply": (rep.get("payload_hex", "") or "")[:64],
                    "error": err, "state_after": after, "changed": changed})
        log(f"shot #{i} {label}: reply={(rep.get('payload_hex', '') or err)[:32]} "
            f"state={after} {'CHANGED!' if changed else ''}")
        if changed:
            break
        running = after
        time.sleep(gap)
    changed_any = any(e.get("changed") for e in out)
    print(json.dumps({"state_before": base, "changed_any": changed_any,
                      "shots": out}, indent=1))
    return changed_any


def main():
    ap = argparse.ArgumentParser(description="Auto-join strip AP, run blocks.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("state")
    sw = sub.add_parser("sweep")
    sw.add_argument("--start", default="0x68")
    sw.add_argument("--end", default="0x77")
    sw.add_argument("--chunk", type=int, default=4,
                    help="reconnect after every N commands (reboot-proofing)")
    cp = sub.add_parser("campaign",
                        help="16 canned payload shots (0x66/0x68/0x69), "
                             "state check after each, stops on change")
    cp.add_argument("--gap", type=float, default=5.0,
                    help="seconds between shots (watch LEDs)")
    cp.add_argument("--fresh", action="store_true",
                    help="re-verify/rejoin before every shot (slow, clean attribution)")
    sub.add_parser("hunt")
    wf = sub.add_parser("wifi")
    wf.add_argument("--home-ssid", required=True)
    wf.add_argument("--home-pass", required=True)
    ap.add_argument("--ssid", default=None, help="strip AP (default: first found)")
    ap.add_argument("--ap-timeout", type=int, default=60)
    ap.add_argument("--no-disconnect", action="store_true",
                    help="don't drop the current Wi-Fi first (Windows hops back "
                         "to internet networks mid-run otherwise)")
    a = ap.parse_args()

    wifi = backend()
    ssid = a.ssid or find_ap(wifi, timeout=a.ap_timeout)
    if not ssid:
        log("no TONLY_TAP_* AP visible. Power-cycle / factory-reset the strip.")
        return 2
    pw = derive_pw(ssid)
    if not pw:
        log(f"cannot derive password for {ssid!r}")
        return 2
    log(f"target {ssid} password {pw[:4]}… (derived)")
    home, pinned = None, False
    if not a.no_disconnect:
        try:
            home = wifi.current()
        except Exception:  # noqa: BLE001
            home = None
        if home and not home.startswith(AP_PREFIXES):
            wifi.set_autoconnect(home, False)  # pin it to manual; restored below
            pinned = True
            log(f"pinned {home} to manual-connect (restored on exit)")
        log("dropping current Wi-Fi so the OS stops hopping back")
        wifi.disconnect()
        time.sleep(2)

    try:
        return _dispatch(a, wifi, ssid, pw)
    finally:
        if pinned:
            wifi.set_autoconnect(home, True)
            log(f"restored {home} to auto-connect — reconnect it yourself")


def _dispatch(a, wifi, ssid, pw):
    if a.cmd == "wifi":
        ok = ensure_ap(wifi, ssid, pw) and tonly.lg_set_wifi(
            STRIP_IP, a.home_ssid, a.home_pass)["sent"] == 148
        log("wifi block " + ("SENT (watch for c9 ack in gateway log)" if ok else "FAILED"))
        return 0 if ok else 1

    if a.cmd in ("info", "state"):
        if not ensure_ap(wifi, ssid, pw):
            return 1
        return 0 if (block_info() if a.cmd == "info" else block_state()) else 1

    if a.cmd == "campaign":
        if not ensure_ap(wifi, ssid, pw):
            return 1
        log("WATCH THE LEDs — finger over the physical buttons")
        return 0 if not block_campaign(wifi, ssid, gap=a.gap, fresh=a.fresh) else 3

    # sweep / hunt: reconnect per chunk (one-block-per-connection)
    if a.cmd == "sweep":
        start, end, chunk = int(a.start, 0), int(a.end, 0), a.chunk
    else:
        start, end, chunk = 0x60, 0x7F, 4
    if a.cmd == "hunt":
        if not ensure_ap(wifi, ssid, pw):
            return 1
        block_info()
        block_state()
    cur, all_ok = start, True
    while cur <= end:
        if not ensure_ap(wifi, ssid, pw):
            log("AP gone for good — stopping. Re-run to resume from "
                f"{hex(cur)}.")
            return 1
        nxt = min(cur + chunk - 1, end)
        log(f"--- chunk {hex(cur)}-{hex(nxt)} ---")
        import server
        resp = server.run_sweep(STRIP_IP, STRIP_PORT, cur, nxt)
        if resp.get("state_changed") or resp.get("aborted_at"):
            log(f"chunk flagged: changed={resp['state_changed']} "
                f"aborted={resp['aborted_at']} — stopping for human review")
            all_ok = False
            break
        cur = nxt + 1
    log("SWEEP COMPLETE, no flags" if all_ok else "SWEEP STOPPED on flag")
    return 0 if all_ok else 3


if __name__ == "__main__":
    sys.exit(main())
