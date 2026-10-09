#!/usr/bin/env python3
"""Bambu Lab -> Spoolman Automatic Filament Tracking Bridge (Local / Cloud-Safe).

Monitors Bambu Lab 3D printer telemetry via local MQTT (port 8883),
retrieves per-filament slice weight from cache/*.3mf via internal FTPS (port 990),
and automatically deducts consumed filament in Spoolman upon print completion.

Supports dynamic drag-and-drop slot assignment via Spoolman's Kanban 'Location' field (AMS 1..4).
"""

import ftplib
import io
import json
import logging
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

import paho.mqtt.client as mqtt

# ----------------- Configuration -----------------
HOST = os.environ.get("PRINTER_IP")
SN = os.environ.get("PRINTER_SN")
CODE = os.environ.get("PRINTER_CODE")

if not all([HOST, SN, CODE]):
    print("Error: PRINTER_IP, PRINTER_SN, and PRINTER_CODE environment variables must be set.", file=sys.stderr)
    print("Example: PRINTER_IP=192.168.1.100 PRINTER_SN=0309... PRINTER_CODE=12345678 python3 bridge.py", file=sys.stderr)
    sys.exit(1)

SPOOLMAN = os.environ.get("SPOOLMAN_URL", "http://127.0.0.1:7912/api/v1").rstrip("/")
DIR = os.environ.get("BRIDGE_DIR", os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.environ.get("CONFIG_PATH", os.path.join(DIR, "config.json"))
JOBS = os.environ.get("JOBS_PATH", os.path.join(DIR, "jobs.jsonl"))
PENDING = os.environ.get("PENDING_PATH", os.path.join(DIR, "pending.json"))

ACTIVE_STATES = {"PREPARE", "RUNNING", "PAUSE", "SLICING"}
ENDED_STATES = {"FINISH", "FAILED", "IDLE"}

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bambu-bridge")

state = {}
job = None
lock = threading.Lock()


# ----------------- Spoolman API & Slot Resolver -----------------
def spoolman(method: str, path: str, body: dict = None):
    url = f"{SPOOLMAN}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def parse_location_slot(loc: str):
    """Parses slot index (0..3) from Spoolman location string.
    Matches: 'AMS 1', 'AMS-2', 'Slot 3', 'Yuva 4', '1', etc.
    """
    if not loc:
        return None
    s = str(loc).strip()
    m = re.search(r"\b(?:ams(?:\s*lite)?(?:\s*(?:yuva|slot))?|yuva|slot)\s*[:#-]?\s*([1-4])\b", s, re.I)
    if m:
        return int(m.group(1)) - 1
    if re.fullmatch(r"#?[1-4]", s):
        return int(s.lstrip("#")) - 1
    return None


def slot_map():
    """Resolves AMS tray index (0-3) -> Spoolman spool ID.
    Priority: Spoolman spools whose 'location' field matches 'AMS 1'..'AMS 4'.
    Fallback: local config.json file.
    """
    smap = {}
    try:
        spools = spoolman("GET", "/spool?allow_archived=false")
        for s in spools:
            slot_idx = parse_location_slot(s.get("location"))
            if slot_idx is not None:
                if slot_idx in smap:
                    log.warning(
                        "Slot %d has multiple assigned spools: #%s and #%s. Selecting #%s.",
                        slot_idx + 1, smap[slot_idx], s["id"], s["id"]
                    )
                smap[slot_idx] = s["id"]
    except Exception as e:
        log.warning("Could not fetch locations from Spoolman API: %s", e)

    # Fallback from local config.json
    try:
        if os.path.exists(CONFIG):
            cfg_slots = json.load(open(CONFIG)).get("slots", {})
            for k, v in cfg_slots.items():
                smap.setdefault(int(k), v)
    except Exception as e:
        log.warning("Could not read local config fallback (%s): %s", CONFIG, e)

    return smap


# ----------------- Internal FTPS (Port 990 / TLS) -----------------
class ImplicitFTPS(ftplib.FTP_TLS):
    def connect(self, host, port=990, timeout=15):
        self.host, self.port, self.timeout = host, port, timeout
        raw = socket.create_connection((host, port), timeout)
        self.sock = self.context.wrap_socket(raw, server_hostname="bambu")
        self.af = self.sock.family
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname="bambu", session=self.sock.session)
        return conn, size


def fetch_3mf(name: str):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    f = ImplicitFTPS(context=ctx)
    f.connect(HOST, 990)
    f.login("bblp", CODE)
    f.prot_p()

    base = name[:-4] if name.lower().endswith(".3mf") else name
    buf = io.BytesIO()
    try:
        try:
            f.retrbinary(f"RETR cache/{base}.3mf", buf.write)
        except Exception:
            # Fallback: search directory if file has modified name
            nl = f.nlst("cache")
            match = next((x for x in nl if base.lower() in x.lower() and x.endswith(".3mf")), None)
            if not match:
                match = next((x for x in nl if x.endswith(".3mf")), None)
            if match:
                path = match if match.startswith("cache/") else f"cache/{match}"
                f.retrbinary(f"RETR {path}", buf.write)
            else:
                raise FileNotFoundError(f"Could not locate .3mf file for job '{name}' on printer cache.")
    finally:
        f.close()

    return zipfile.ZipFile(buf)


def filament_grams(z: zipfile.ZipFile, plate: int):
    """Extracts per-filament sliced weight in grams from 3MF metadata."""
    root = ET.fromstring(z.read("Metadata/slice_info.config"))
    plates = root.findall("plate")
    chosen = None
    for p in plates:
        idx = next((m.get("value") for m in p.findall("metadata") if m.get("key") == "index"), None)
        if idx and int(idx) == plate:
            chosen = p
    chosen = chosen if chosen is not None else plates[0]
    return {int(fl.get("id")): float(fl.get("used_g", 0)) for fl in chosen.findall("filament")}


# ----------------- Print Job Processing -----------------
def merge_state(report: dict):
    with lock:
        state.update(report)
        return dict(state)


def apply_usage(rec: dict):
    """Deducts calculated filament weight from Spoolman inventory."""
    z = fetch_3mf(rec["name"])
    grams = filament_grams(z, rec["plate"])
    smap = slot_map()
    mapping = rec.get("mapping", [])
    ratio = 1.0 if rec["end"] == "FINISH" else max(0.0, min(rec["percent"], 100)) / 100.0

    used = []
    for fid, g in grams.items():
        tray = None
        if fid - 1 < len(mapping) and mapping[fid - 1] not in (65535, None):
            tray = mapping[fid - 1] & 0xFF
        elif len(grams) == 1 and rec.get("tray_seen") is not None:
            tray = rec["tray_seen"]

        if tray is None or tray not in smap:
            log.warning("No assigned spool found for filament ID %s (AMS tray %s, mapping=%s). Skipping.",
                        fid, tray, mapping[:4] if mapping else [])
            continue

        w = round(g * ratio, 2)
        if w <= 0:
            continue

        spool_id = smap[tray]
        spoolman("PUT", f"/spool/{spool_id}/use", {"use_weight": w})
        used.append({"filament": fid, "tray": tray, "spool": spool_id, "g": w})
        log.info("Deducted %.2f g from Spool #%s (Job: %s, Tray: %d)", w, spool_id, rec["name"], tray + 1)

    with open(JOBS, "a") as f:
        f.write(json.dumps({**rec, "used": used, "done_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
    return True


def load_pending():
    return json.load(open(PENDING)) if os.path.exists(PENDING) else []


def save_pending(p):
    json.dump(p, open(PENDING, "w"), ensure_ascii=False, indent=2)


def flush_pending():
    p = load_pending()
    remaining = []
    for rec in p:
        try:
            apply_usage(rec)
        except Exception as e:
            rec["tries"] = rec.get("tries", 0) + 1
            if rec["tries"] < 20:
                remaining.append(rec)
            log.error("Could not process pending job '%s' (will retry): %s", rec.get("name"), e)
    save_pending(remaining)


# ----------------- MQTT Event Listener -----------------
def on_report(client, userdata, msg):
    global job
    try:
        data = json.loads(msg.payload).get("print")
    except Exception:
        return
    if not data:
        return

    s = merge_state(data)
    gs = s.get("gcode_state")
    if gs is None:
        return

    # Job Started
    if gs in ACTIVE_STATES and job is None:
        job = {
            "start": time.strftime("%Y-%m-%d %H:%M:%S"),
            "percent": 0,
            "mapping": [],
            "tray_seen": None,
            "name": s.get("subtask_name") or "Unnamed_Print"
        }
        log.info("Print job detected: '%s'", job["name"])

    # Job Running
    if job is not None and gs in ACTIVE_STATES:
        job["name"] = s.get("subtask_name") or job.get("name")
        m = re.search(r"plate_(\d+)", s.get("gcode_file") or "")
        job["plate"] = int(m.group(1)) if m else job.get("plate", 1)

        mp = s.get("mapping")
        if mp and any(v != 65535 for v in mp):
            job["mapping"] = mp

        job["percent"] = max(job["percent"], s.get("mc_percent") or 0)

        # Track active feed tray
        ams_data = s.get("ams") or {}
        tn = ams_data.get("tray_now")
        if tn is not None and str(tn).isdigit() and int(tn) < 4:
            job["tray_seen"] = int(tn)

    # Job Completed or Stopped
    if job is not None and gs in ENDED_STATES:
        job["end"] = gs
        if gs == "FINISH":
            job["percent"] = 100
        log.info("Print finished: '%s' (%s, progress: %d%%)", job.get("name"), gs, job["percent"])

        if job.get("name"):
            p = load_pending()
            p.append(job)
            save_pending(p)

        job = None
        threading.Thread(target=flush_pending, daemon=True).start()


def main():
    log.info("Starting Bambu-Spoolman Bridge...")
    log.info("Target Printer: %s (%s)", HOST, SN)
    log.info("Spoolman API: %s", SPOOLMAN)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    c.username_pw_set("bblp", CODE)
    c.tls_set(cert_reqs=ssl.CERT_NONE)
    c.tls_insecure_set(True)
    c.reconnect_delay_set(2, 60)

    def on_connect(cl, u, f, rc, *a):
        log.info("Connected to printer MQTT broker (rc=%s)", rc)
        cl.subscribe(f"device/{SN}/report")
        cl.publish(f"device/{SN}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))

    c.on_connect = on_connect
    c.on_message = on_report

    try:
        c.connect(HOST, 8883, 30)
    except Exception as e:
        log.error("Failed to connect to printer at %s:8883: %s", HOST, e)
        sys.exit(1)

    c.loop_start()

    try:
        while True:
            time.sleep(300)
            # Periodic heartbeat ping & pending job retry
            c.publish(f"device/{SN}/request", json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
            flush_pending()
    except KeyboardInterrupt:
        log.info("Shutting down bridge...")
        c.loop_stop()


if __name__ == "__main__":
    main()
