import unittest

from slotparse import decode_tray, parse_location_slot, slot_label


class ParseLocation(unittest.TestCase):
    def test_v1_syntax_unchanged(self):
        for loc, idx in [("AMS 1", 0), ("AMS 2", 1), ("ams 3", 2), ("AMS-4", 3),
                         ("AMS Yuva 2", 1), ("Slot 3", 2), ("Yuva 4", 3),
                         ("AMS Lite 1", 0), ("1", 0), ("#3", 2)]:
            self.assertEqual(parse_location_slot(loc), idx, loc)

    def test_multi_ams(self):
        self.assertEqual(parse_location_slot("AMS 2-1"), 4)
        self.assertEqual(parse_location_slot("AMS2-3"), 6)
        self.assertEqual(parse_location_slot("AMS 4-4"), 15)

    def test_ams_ht(self):
        self.assertEqual(parse_location_slot("AMS HT"), 128)
        self.assertEqual(parse_location_slot("ams ht 1"), 128)
        self.assertEqual(parse_location_slot("HT2"), 129)
        self.assertEqual(parse_location_slot("AMS-HT 3"), 130)

    def test_not_a_slot(self):
        for loc in [None, "", "Dolap", "Kutu 1", "Raf 2", "Masa 4", "Shelf"]:
            self.assertIsNone(parse_location_slot(loc), loc)

    def test_label_roundtrip(self):
        for idx in list(range(16)) + [128, 129, 135]:
            self.assertEqual(parse_location_slot(slot_label(idx)), idx, idx)

    def test_decode_tray(self):
        self.assertEqual(decode_tray(0), 0)
        self.assertEqual(decode_tray("5"), 5)
        self.assertEqual(decode_tray(128), 128)
        self.assertEqual(decode_tray((1 << 8) | 2), 6)       # packed: AMS 2, slot 3
        self.assertEqual(decode_tray((128 << 8) | 0), 128)   # packed: AMS HT
        for bad in (65535, 255, 254, -1, None, "x", 77):
            self.assertIsNone(decode_tray(bad), bad)


if __name__ == "__main__":
    unittest.main()
