#!/usr/bin/env python3
"""CLI utility to inspect or set Bambu AMS slot mapping in Spoolman.

Usage:
  python3 slot.py            # Show current slot mapping
  python3 slot.py <slot> <spool_id>  # Assign spool_id to slot (1..4)
"""

import json
import os
import re
import sys
import urllib.request

D = os.path.dirname(os.path.abspath(__file__))
C = os.environ.get("CONFIG_PATH", os.path.join(D, "config.json"))
S = os.environ.get("SPOOLMAN_URL", "http://127.0.0.1:7912/api/v1").rstrip("/")


def parse_location_slot(loc):
    if not loc:
        return None
    s = str(loc).strip()
    m = re.search(r"\b(?:ams(?:\s*lite)?(?:\s*(?:yuva|slot))?|yuva|slot)\s*[:#-]?\s*([1-4])\b", s, re.I)
    if m:
        return int(m.group(1)) - 1
    if re.fullmatch(r"#?[1-4]", s):
        return int(s.lstrip("#")) - 1
    return None


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{S}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def main():
    if len(sys.argv) == 3:
        try:
            slot = int(sys.argv[1])
            spool_id = int(sys.argv[2])
        except ValueError:
            print("Error: Slot and Spool ID must be integers.")
            sys.exit(1)

        if not (1 <= slot <= 4):
            print("Error: Slot must be between 1 and 4.")
            sys.exit(1)

        # 1. Clear location for any previous spool occupying this slot
        try:
            all_spools = call("GET", "/spool?allow_archived=false")
            for s in all_spools:
                if s["id"] != spool_id and parse_location_slot(s.get("location")) == (slot - 1):
                    call("PATCH", f"/spool/{s['id']}", {"location": ""})
            # 2. Set new spool location
            call("PATCH", f"/spool/{spool_id}", {"location": f"AMS {slot}"})
            print(f"Assigned Spool #{spool_id} to AMS Slot {slot}.")
        except Exception as e:
            print(f"Warning: Could not update Spoolman location ({e}). Updating local config fallback.")

        # 3. Update local config fallback
        try:
            cfg = json.load(open(C)) if os.path.exists(C) else {"slots": {}}
            cfg.setdefault("slots", {})[str(slot - 1)] = spool_id
            json.dump(cfg, open(C, "w"), ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Warning: Could not save local config ({e})")

    # List current mappings
    smap = {}
    try:
        for s in call("GET", "/spool?allow_archived=false"):
            idx = parse_location_slot(s.get("location"))
            if idx is not None:
                smap[idx] = s
    except Exception as e:
        print(f"Warning: Could not list Spoolman spools: {e}")

    # Fallback to config.json
    if os.path.exists(C):
        try:
            cfg = json.load(open(C)).get("slots", {})
            for k, sid in cfg.items():
                idx = int(k)
                if idx not in smap:
                    try:
                        smap[idx] = call("GET", f"/spool/{sid}")
                    except Exception:
                        smap[idx] = {"id": sid, "filament": {"name": "Unknown", "material": "?"}, "remaining_weight": 0}
        except Exception:
            pass

    print("\nCurrent AMS Slot Mapping:")
    print("-" * 40)
    for slot_idx in range(4):
        s = smap.get(slot_idx)
        if s:
            f = s.get("filament") or {}
            vname = (f.get("vendor") or {}).get("name", "")
            print(f"Slot {slot_idx + 1}: #{s['id']} {vname} {f.get('name')} ({f.get('material')}) - Remaining: {s.get('remaining_weight', 0):.0f}g [Location: {s.get('location') or '-'}]")
        else:
            print(f"Slot {slot_idx + 1}: <EMPTY>")
    print("-" * 40)


if __name__ == "__main__":
    main()
