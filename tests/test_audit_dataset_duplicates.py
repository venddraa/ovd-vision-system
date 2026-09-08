from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts/audit_dataset_duplicates.py"
SPEC = importlib.util.spec_from_file_location("audit_dataset_duplicates", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class DuplicateAuditTests(unittest.TestCase):
    def test_original_source_name_has_priority(self) -> None:
        image = {
            "file_name": "rewritten_jpg.rf.0123456789abcdef.jpg",
            "extra": {"name": "VID_20220209_140941-088.JPG"},
        }
        display, key, origin = AUDIT.normalize_source_name(image)
        self.assertEqual(display, "VID_20220209_140941-088.JPG")
        self.assertEqual(key, "vid_20220209_140941-088.jpg")
        self.assertEqual(origin, "coco.extra.name")

    def test_roboflow_filename_is_recovered_when_extra_name_is_missing(self) -> None:
        image = {"file_name": "truck_view_jpeg.rf.abcdef0123456789.jpg"}
        display, key, origin = AUDIT.normalize_source_name(image)
        self.assertEqual(display, "truck_view.jpg")
        self.assertEqual(key, "truck_view.jpg")
        self.assertEqual(origin, "roboflow_export_filename")

    def test_perceptual_and_difference_hashes_are_deterministic(self) -> None:
        gray = bytes((x * 7 + y * 13) % 256 for y in range(8) for x in range(8))
        first_hash, first_bits = AUDIT.perceptual_hash(gray, 8, 8)
        second_hash, second_bits = AUDIT.perceptual_hash(gray, 8, 8)
        self.assertEqual((first_hash, first_bits), (second_hash, second_bits))
        self.assertEqual(first_bits, 63)
        horizontal, horizontal_bits, vertical, vertical_bits = AUDIT.difference_hashes(gray, 8, 8)
        self.assertEqual(horizontal_bits, 56)
        self.assertEqual(vertical_bits, 56)
        self.assertIsInstance(horizontal, int)
        self.assertIsInstance(vertical, int)

    def test_hamming_distance_ratio(self) -> None:
        self.assertEqual(AUDIT.hamming_distance_ratio(0b0000, 0b1111, 4), 1.0)
        self.assertEqual(AUDIT.hamming_distance_ratio(0b1010, 0b1010, 4), 0.0)
        with self.assertRaises(ValueError):
            AUDIT.hamming_distance_ratio(0, 0, 0)

    def test_shifted_ncc_recovers_small_translation(self) -> None:
        width = height = 8
        left = bytearray(width * height)
        for y in range(2, 6):
            for x in range(2, 6):
                left[y * width + x] = 40 + x * 20 + y * 7
        right = bytearray(width * height)
        for y in range(height):
            for x in range(width - 1):
                right[y * width + x + 1] = left[y * width + x]
        score = AUDIT.shifted_normalized_cross_correlation(
            bytes(left), bytes(right), width, height, 1
        )
        self.assertGreater(score, 0.99)

    def test_group_id_and_union_are_order_independent(self) -> None:
        self.assertEqual(
            AUDIT.stable_group_id(["b.jpg", "a.jpg"]),
            AUDIT.stable_group_id(["a.jpg", "b.jpg"]),
        )
        groups = AUDIT.DisjointSet(["a", "b", "c"])
        groups.union("b", "a")
        groups.union("c", "b")
        self.assertEqual(groups.find("a"), groups.find("c"))

    def test_threshold_match_ignores_policy_fields(self) -> None:
        metrics = {
            "aspect_log_delta": 0.10,
            "phash_distance_ratio": 0.05,
            "horizontal_dhash_distance_ratio": 0.10,
            "vertical_dhash_distance_ratio": 0.11,
        }
        thresholds = {
            "auto_merge": False,
            "maximum_aspect_log_delta": 0.18,
            "maximum_phash_distance_ratio": 0.10,
            "maximum_horizontal_dhash_distance_ratio": 0.13,
            "maximum_vertical_dhash_distance_ratio": 0.13,
        }
        self.assertTrue(AUDIT.threshold_match(metrics, thresholds))
        metrics["phash_distance_ratio"] = 0.11
        self.assertFalse(AUDIT.threshold_match(metrics, thresholds))


if __name__ == "__main__":
    unittest.main()
