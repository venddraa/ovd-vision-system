from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts/validate_coco_datasets.py"
SPEC = importlib.util.spec_from_file_location("validate_coco_datasets", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class CocoValidatorTests(unittest.TestCase):
    def make_dataset(self, root: Path, *, broken: bool = False) -> dict[str, object]:
        split = root / "train"
        split.mkdir(parents=True)
        (split / "positive.jpg").write_bytes(b"test-positive")
        (split / "negative.jpg").write_bytes(b"test-negative")
        images = [
            {"id": 1, "file_name": "positive.jpg", "width": 100, "height": 80},
            {"id": 2, "file_name": "negative.jpg", "width": 100, "height": 80},
        ]
        annotations = [
            {
                "id": 1,
                "image_id": 1,
                "category_id": 1,
                "bbox": [-0.01 if not broken else -2.0, 10.0, 50.0, 30.0],
            }
        ]
        categories = [
            {"id": 0, "name": "truck", "supercategory": "none"},
            {"id": 1, "name": "truck", "supercategory": "truck"},
        ]
        (split / "_annotations.coco.json").write_text(
            json.dumps({"images": images, "annotations": annotations, "categories": categories}),
            encoding="utf-8",
        )
        return {
            "dataset_id": "fixture-v1-raw",
            "root_from_repository": str(root),
            "splits": ["train"],
            "annotation_filename": "_annotations.coco.json",
            "boundary_tolerance_px": 0.05,
            "check_image_headers": False,
            "expected": {
                "images": 2,
                "annotations": 1,
                "zero_annotation_images": 1,
                "used_class_names": ["truck"],
                "splits": {"train": {"images": 2, "annotations": 1}},
            },
        }

    def test_valid_dataset_reports_export_quirks_without_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = self.make_dataset(Path(temporary))
            report = VALIDATOR.validate_dataset(
                entry,
                REPOSITORY_ROOT,
                skip_checksums=True,
                skip_image_headers=False,
            )
        self.assertEqual(report["status"], "PASS_WITH_WARNINGS")
        self.assertEqual(report["issue_counts"]["error"], 0)
        self.assertEqual(report["totals"]["zero_annotation_images"], 1)
        self.assertEqual(report["class_map"][0]["source_category_id"], 1)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("bbox_boundary_precision", codes)
        self.assertIn("duplicate_category_name", codes)
        self.assertIn("unused_category", codes)

    def test_material_bbox_overflow_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = self.make_dataset(Path(temporary), broken=True)
            report = VALIDATOR.validate_dataset(
                entry,
                REPOSITORY_ROOT,
                skip_checksums=True,
                skip_image_headers=True,
            )
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["issue_counts"]["error"], 0)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("bbox_outside_image", codes)

    def test_missing_referenced_image_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = self.make_dataset(root)
            (root / "train/positive.jpg").unlink()
            report = VALIDATOR.validate_dataset(
                entry,
                REPOSITORY_ROOT,
                skip_checksums=True,
                skip_image_headers=True,
            )
        self.assertEqual(report["status"], "FAIL")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("referenced_image_missing", codes)

    def test_checksum_mutation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            entry = self.make_dataset(root)
            checksum_path = Path(temporary) / "files.sha256"
            lines = []
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                lines.append(f"{digest}  ./{path.relative_to(root).as_posix()}")
            checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            entry["checksum_manifest_from_repository"] = str(checksum_path)
            (root / "train/positive.jpg").write_bytes(b"mutated")
            report = VALIDATOR.validate_dataset(
                entry,
                REPOSITORY_ROOT,
                skip_checksums=False,
                skip_image_headers=True,
            )
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["checksum_verification"]["mismatches"], 1)
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("checksum_mismatch", codes)

    def test_jpeg_dimension_reader(self) -> None:
        minimal_header = b"\xff\xd8\xff\xc0\x00\x07\x08\x00\x18\x00\x20"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "image.jpg"
            path.write_bytes(minimal_header)
            self.assertEqual(VALIDATOR.read_image_dimensions(path), (32, 24))

    def test_png_dimension_reader(self) -> None:
        header = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\rIHDR"
            + (32).to_bytes(4, "big")
            + (24).to_bytes(4, "big")
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "image.png"
            path.write_bytes(header)
            self.assertEqual(VALIDATOR.read_image_dimensions(path), (32, 24))


if __name__ == "__main__":
    unittest.main()
