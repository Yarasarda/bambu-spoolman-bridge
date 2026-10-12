# Changelog

## v0.2.0 (unreleased)

### Added
- **Multiple AMS units** (`AMS 2-3` = unit 2, slot 3, up to `AMS 4-4`) and **AMS HT** (`AMS HT`, `AMS HT 2`, …)
  via Spoolman locations. Requested by [u/M346ZCP](https://www.reddit.com/user/M346ZCP) on Reddit. *AMS HT / multi-AMS are untested on real hardware.*
- `python3 bridge.py --dump` diagnostic: prints the AMS layout the printer reports and how it maps to slots.
- `slot.py` accepts the new location syntax (`python3 slot.py "AMS HT" 7`).
- Proxmox LXC install guide.
- Unit tests (`test_slotparse.py`, `test_bridge.py`) incl. an end-to-end `apply_usage` test with a fake printer/Spoolman.

### Changed
- Slot parsing moved to `slotparse.py`; slots are now the printer's global tray index (`0-15`, AMS HT `128+`).
- Warnings for unassigned slots now suggest the exact Spoolman location to set.

### Compatibility
- v0.1 location names (`AMS 1`..`AMS 4`) and `config.json` fallback keys (`"0"`..`"3"`) behave exactly as before.

## v0.1.0
- Initial release: MQTT + FTPS bridge, Spoolman Kanban location mapping, proportional deduction for cancelled prints.
