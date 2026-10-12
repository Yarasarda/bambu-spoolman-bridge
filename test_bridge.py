"""End-to-end test of apply_usage(): print mapping -> spool -> grams deducted.
No printer or Spoolman needed (both are faked)."""
import io
import json
import os
import tempfile
import unittest
import zipfile

os.environ.update(PRINTER_IP="127.0.0.1", PRINTER_SN="TEST", PRINTER_CODE="00000000",
                  BRIDGE_DIR=tempfile.mkdtemp())

import bridge  # noqa: E402


def fake_3mf(filaments):
    """filaments: {slicer_filament_id: grams}"""
    xml = "<config><plate><metadata key='index' value='1'/>" + "".join(
        f"<filament id='{i}' used_g='{g}'/>" for i, g in filaments.items()) + "</plate></config>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Metadata/slice_info.config", xml)
    return zipfile.ZipFile(buf)


class ApplyUsage(unittest.TestCase):
    def run_job(self, grams, mapping, slots, end="FINISH", percent=100, tray_seen=None):
        calls = []
        bridge.fetch_3mf = lambda name: fake_3mf(grams)
        bridge.slot_map = lambda: slots
        bridge.spoolman = lambda method, path, body=None: calls.append((method, path, body))
        bridge.apply_usage({"name": "t", "plate": 1, "mapping": mapping, "end": end,
                            "percent": percent, "tray_seen": tray_seen})
        return calls

    def test_ams_lite_two_colors(self):
        calls = self.run_job({1: 10.0, 3: 5.5}, [0, 65535, 2] + [65535] * 29,
                             {0: 13, 2: 14})
        self.assertEqual(calls, [("PUT", "/spool/13/use", {"use_weight": 10.0}),
                                 ("PUT", "/spool/14/use", {"use_weight": 5.5})])

    def test_failed_print_scales_by_progress(self):
        calls = self.run_job({1: 20.0}, [0] + [65535] * 31, {0: 13}, end="FAILED", percent=25)
        self.assertEqual(calls, [("PUT", "/spool/13/use", {"use_weight": 5.0})])

    def test_second_ams_unit(self):
        calls = self.run_job({1: 8.0}, [6] + [65535] * 31, {6: 42})      # AMS 2-3
        self.assertEqual(calls, [("PUT", "/spool/42/use", {"use_weight": 8.0})])

    def test_packed_mapping_second_ams(self):
        calls = self.run_job({1: 8.0}, [(1 << 8) | 2] + [65535] * 31, {6: 42})
        self.assertEqual(calls, [("PUT", "/spool/42/use", {"use_weight": 8.0})])

    def test_ams_ht(self):
        calls = self.run_job({1: 12.0}, [128] + [65535] * 31, {128: 7})  # AMS HT
        self.assertEqual(calls, [("PUT", "/spool/7/use", {"use_weight": 12.0})])

    def test_single_filament_falls_back_to_tray_now(self):
        calls = self.run_job({1: 3.0}, [65535] * 32, {1: 3}, tray_seen=1)
        self.assertEqual(calls, [("PUT", "/spool/3/use", {"use_weight": 3.0})])

    def test_unassigned_slot_is_skipped_not_crashed(self):
        calls = self.run_job({1: 3.0}, [3] + [65535] * 31, {0: 13})
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
