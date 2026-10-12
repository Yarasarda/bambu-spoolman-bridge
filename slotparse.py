"""Slot helpers shared by bridge.py and slot.py.

Slot index = the printer's *global tray index*:
  * AMS / AMS Lite unit 1..4, slot 1..4  -> 0..15   (index = (unit-1)*4 + (slot-1))
  * AMS HT unit 1..8 (single slot each)  -> 128..135 (index = 128 + (unit-1))

Spoolman 'location' strings understood (case-insensitive):
  "AMS 1".."AMS 4"        first AMS unit, slot N      (v1 syntax, unchanged)
  "AMS 2-3", "AMS2-3"     AMS unit 2, slot 3          (multi-AMS)
  "AMS HT", "AMS HT 1", "HT1"   first AMS HT          (AMS HT; HT2.. for more units)
  "Slot 3", "Yuva 3", "3" same as "AMS 3"
"""
import re

HT_BASE = 128

_RE_MULTI = re.compile(r"\bams\s*(?:lite\s*)?([1-4])\s*[-/:.]\s*([1-4])\b", re.I)
_RE_HT = re.compile(r"\b(?:ams[\s-]*)?ht[\s-]*([1-8])?\b", re.I)
_RE_SINGLE = re.compile(r"\b(?:ams(?:\s*lite)?(?:\s*(?:yuva|slot))?|yuva|slot)\s*[:#-]?\s*([1-4])\b", re.I)


def parse_location_slot(loc):
    """Spoolman location string -> global tray index, or None."""
    if not loc:
        return None
    s = str(loc).strip()
    m = _RE_MULTI.search(s)
    if m:
        return (int(m.group(1)) - 1) * 4 + (int(m.group(2)) - 1)
    m = _RE_HT.fullmatch(s) or _RE_HT.search(s)
    if m and re.search(r"ht", s, re.I):
        return HT_BASE + (int(m.group(1)) - 1 if m.group(1) else 0)
    m = _RE_SINGLE.search(s)
    if m:
        return int(m.group(1)) - 1
    if re.fullmatch(r"#?[1-4]", s):
        return int(s.lstrip("#")) - 1
    return None


def slot_label(idx):
    """Global tray index -> the location string to put in Spoolman."""
    if idx >= HT_BASE:
        return "AMS HT" if idx == HT_BASE else f"AMS HT {idx - HT_BASE + 1}"
    unit, slot = divmod(idx, 4)
    return f"AMS {slot + 1}" if unit == 0 else f"AMS {unit + 1}-{slot + 1}"


def decode_tray(value):
    """Printer-reported tray/mapping value -> global tray index, or None.

    Handles the plain form (0..15 for AMS, 128+ for AMS HT) and the packed
    form (ams_id << 8 | slot) that some firmware uses. 65535 / 254 / 255 = unused.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v in (65535, 255, 254) or v < 0:
        return None
    if v >= 256:
        ams_id, slot = v >> 8, v & 0xFF
        if ams_id >= HT_BASE:
            return ams_id + slot
        return ams_id * 4 + slot if ams_id < 4 and slot < 4 else None
    if v < 16 or HT_BASE <= v < HT_BASE + 8:
        return v
    return None
