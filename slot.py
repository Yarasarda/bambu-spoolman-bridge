#!/usr/bin/env python3
"""CLI utility to inspect or set Bambu AMS slot mapping in Spoolman.

Usage:
  python3 slot.py                    # Show current slot mapping
  python3 slot.py <slot> <spool_id>  # Assign a spool to a slot

<slot> is anything the bridge understands as a Spoolman location:
  1..4            first AMS / AMS Lite, slot N
  "AMS 2-3"       second AMS unit, slot 3 (multi-AMS)
  "AMS HT"        first AMS HT   ("AMS HT 2" for the next one)
"""

import json
import os
import sys
import urllib.request

from slotparse import parse_location_slot, slot_label

D = os.path.dirname(os.path.abspath(__file__))
C = os.environ.get("CONFIG_PATH", os.path.join(D, "config.json"))
S = os.environ.get("SPOOLMAN_URL", "http://127.0.0.1:7912/api/v1").rstrip("/")


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{S}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def main():
    if len(sys.argv) == 3:
        idx = parse_location_slot(sys.argv[1])
        if idx is None:
            print(f"Error: could not understand slot '{sys.argv[1]}'. Try 1..4, 'AMS 2-3' or 'AMS HT'.")
            sys.exit(1)
        try:
            spool_id = int(sys.argv[2])
        except ValueError:
            print("Error: Spool ID must be an integer.")
            sys.exit(1)

        label = slot_label(idx)
        # 1. Clear the location of any previous spool occupying this slot
        try:
            for s in call("GET", "/spool?allow_archived=false"):
                if s["id"] != spool_id and parse_location_slot(s.get("location")) == idx:
                    call("PATCH", f"/spool/{s['id']}", {"location": ""})
            # 2. Set the new spool's location
            call("PATCH", f"/spool/{spool_id}", {"location": label})
            print(f"Assigned Spool #{spool_id} to {label}.")
        except Exception as e:
            print(f"Warning: Could not update Spoolman location ({e}). Updating local config fallback.")

        # 3. Update local config fallback
        try:
            cfg = json.load(open(C)) if os.path.exists(C) else {"slots": {}}
            cfg.setdefault("slots", {})[str(idx)] = spool_id
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
            for k, sid in json.load(open(C)).get("slots", {}).items():
                idx = int(k)
                if idx not in smap:
                    try:
                        smap[idx] = call("GET", f"/spool/{sid}")
                    except Exception:
                        smap[idx] = {"id": sid, "filament": {"name": "Unknown", "material": "?"}, "remaining_weight": 0}
        except Exception:
            pass

    print("\nCurrent slot mapping:")
    print("-" * 40)
    # Always show the 4 slots of the first AMS, plus anything else that is assigned
    for idx in sorted(set(range(4)) | set(smap)):
        s = smap.get(idx)
        if s:
            f = s.get("filament") or {}
            vname = (f.get("vendor") or {}).get("name", "")
            print(f"{slot_label(idx)}: #{s['id']} {vname} {f.get('name')} ({f.get('material')}) - "
                  f"Remaining: {s.get('remaining_weight', 0):.0f}g [Location: {s.get('location') or '-'}]")
        else:
            print(f"{slot_label(idx)}: <EMPTY>")
    print("-" * 40)


if __name__ == "__main__":
    main()
