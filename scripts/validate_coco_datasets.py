#!/usr/bin/env python3
"""Validate immutable COCO dataset exports and emit deterministic reports.

The validator is intentionally dependency-free. It never modifies dataset files.
Paths in the registry are resolved from the repository root so the same command can
be used locally and by a remote batch executor with the same directory contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCRIPT_VERSION = "1.0.0"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
CHECKSUM_PATTERN = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


@dataclass
class IssueBucket:
    severity: str
    code: str
    message: str
    count: int = 0
    examples: list[str] = field(default_factory=list)


class IssueLog:
    """Aggregate repeated issues while retaining a bounded set of examples."""

    def __init__(self, max_examples: int = 10) -> None:
        self.max_examples = max_examples
        self._buckets: dict[tuple[str, str, str], IssueBucket] = {}

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        example: str | None = None,
        count: int = 1,
    ) -> None:
        if severity not in SEVERITY_ORDER:
            raise ValueError(f"Unsupported severity: {severity}")
        key = (severity, code, message)
        bucket = self._buckets.setdefault(
            key,
            IssueBucket(severity=severity, code=code, message=message),
        )
        bucket.count += count
        if example is not None and len(bucket.examples) < self.max_examples:
            bucket.examples.append(example)

    def counts(self) -> dict[str, int]:
        totals = {severity: 0 for severity in SEVERITY_ORDER}
        for bucket in self._buckets.values():
            totals[bucket.severity] += bucket.count
        return totals

    def as_list(self) -> list[dict[str, Any]]:
        buckets = sorted(
            self._buckets.values(),
            key=lambda item: (SEVERITY_ORDER[item.severity], item.code, item.message),
        )
        return [
            {
                "severity": bucket.severity,
                "code": bucket.code,
                "message": bucket.message,
                "count": bucket.count,
                "examples": bucket.examples,
            }
            for bucket in buckets
        ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=repository_root / "configs/bootstrap_external/datasets.json",
        help="Dataset registry JSON; relative paths are resolved from repository root.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "phases/00_external_bootstrap/reports/p2-01",
        help="Directory for JSON and Markdown reports.",
    )
    parser.add_argument(
        "--dataset-id",
        action="append",
        dest="dataset_ids",
        help="Validate only this dataset ID; may be passed more than once.",
    )
    parser.add_argument(
        "--skip-checksums",
        action="store_true",
        help="Skip per-file SHA-256 verification.",
    )
    parser.add_argument(
        "--skip-image-headers",
        action="store_true",
        help="Skip dependency-free JPEG/PNG header and dimension checks.",
    )
    return parser.parse_args(argv)


def load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Registry not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid registry JSON at {path}: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise ValueError("Registry schema_version must be 1")
    datasets = payload.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Registry must contain a non-empty datasets list")
    return payload


def resolve_from_repository(repository_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return (repository_root / path).resolve() if not path.is_absolute() else path.resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def numeric_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "min": min(values) if values else None,
        "p25": percentile(values, 0.25),
        "median": percentile(values, 0.5),
        "p75": percentile(values, 0.75),
        "max": max(values) if values else None,
    }


def _jpeg_dimensions(path: Path) -> tuple[int, int]:
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    standalone = {0x01, *range(0xD0, 0xD9)}
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            raise ValueError("missing JPEG SOI marker")
        while True:
            byte = handle.read(1)
            if not byte:
                raise ValueError("JPEG ended before a start-of-frame marker")
            if byte != b"\xff":
                continue
            marker_byte = handle.read(1)
            while marker_byte == b"\xff":
                marker_byte = handle.read(1)
            if not marker_byte:
                raise ValueError("truncated JPEG marker")
            marker = marker_byte[0]
            if marker in standalone:
                continue
            if marker in {0xD9, 0xDA}:
                raise ValueError("JPEG reached image data/end before dimensions")
            raw_length = handle.read(2)
            if len(raw_length) != 2:
                raise ValueError("truncated JPEG segment length")
            segment_length = struct.unpack(">H", raw_length)[0]
            if segment_length < 2:
                raise ValueError("invalid JPEG segment length")
            if marker in start_of_frame:
                frame = handle.read(5)
                if len(frame) != 5:
                    raise ValueError("truncated JPEG start-of-frame segment")
                height, width = struct.unpack(">HH", frame[1:])
                if width <= 0 or height <= 0:
                    raise ValueError("non-positive JPEG dimensions")
                return width, height
            handle.seek(segment_length - 2, 1)


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("invalid PNG signature/header")
    if header[12:16] != b"IHDR":
        raise ValueError("PNG does not start with IHDR")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise ValueError("non-positive PNG dimensions")
    return width, height


def read_image_dimensions(path: Path) -> tuple[int, int]:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(path)
    if suffix == ".png":
        return _png_dimensions(path)
    raise ValueError(f"unsupported image extension: {suffix or '<none>'}")


def is_safe_relative_path(raw_path: str) -> bool:
    if "\\" in raw_path:
        return False
    path = PurePosixPath(raw_path)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts


def duplicate_values(values: Iterable[Any]) -> list[Any]:
    counts = Counter(values)
    return sorted((value for value, count in counts.items() if count > 1), key=str)


def load_coco(path: Path, issues: IssueLog, split: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.add("error", "annotation_file_missing", "COCO annotation file is missing.", f"{split}/{path.name}")
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.add("error", "annotation_file_invalid", "COCO annotation file cannot be parsed.", f"{split}: {exc}")
        return None
    if not isinstance(payload, dict):
        issues.add("error", "invalid_coco_root", "COCO root must be a JSON object.", split)
        return None
    for key in ("images", "annotations", "categories"):
        if not isinstance(payload.get(key), list):
            issues.add("error", "invalid_coco_collection", f"COCO '{key}' must be a list.", split)
            return None
    return payload


def verify_checksum_manifest(
    dataset_root: Path,
    checksum_manifest: Path,
    issues: IssueLog,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "enabled": True,
        "manifest": checksum_manifest.name,
        "manifest_sha256": None,
        "entries": 0,
        "verified": 0,
        "mismatches": 0,
        "missing": 0,
        "unsafe_paths": 0,
        "unlisted_files": 0,
    }
    try:
        lines = checksum_manifest.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
        issues.add("error", "checksum_manifest_unreadable", "Checksum manifest cannot be read.", str(exc))
        return result
    result["manifest_sha256"] = sha256_file(checksum_manifest)
    listed_paths: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        match = CHECKSUM_PATTERN.match(line)
        if not match:
            issues.add(
                "error",
                "checksum_line_invalid",
                "Checksum manifest contains an invalid line.",
                f"line {line_number}",
            )
            continue
        expected_digest, raw_relative = match.groups()
        relative = raw_relative[2:] if raw_relative.startswith("./") else raw_relative
        result["entries"] += 1
        if not is_safe_relative_path(relative):
            result["unsafe_paths"] += 1
            issues.add("error", "checksum_path_unsafe", "Checksum path is not a safe relative path.", relative)
            continue
        listed_paths.add(PurePosixPath(relative).as_posix())
        path = dataset_root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            result["missing"] += 1
            issues.add("error", "checksum_file_missing", "A checksummed file is missing.", relative)
            continue
        actual_digest = sha256_file(path)
        if actual_digest.lower() != expected_digest.lower():
            result["mismatches"] += 1
            issues.add("error", "checksum_mismatch", "A raw file no longer matches its recorded SHA-256.", relative)
            continue
        result["verified"] += 1

    current_files = {
        path.relative_to(dataset_root).as_posix()
        for path in dataset_root.rglob("*")
        if path.is_file()
    }
    for relative in sorted(current_files - listed_paths):
        result["unlisted_files"] += 1
        issues.add("error", "checksum_file_unlisted", "A raw file is absent from the checksum manifest.", relative)
    return result


def validate_dataset(
    entry: dict[str, Any],
    repository_root: Path,
    *,
    skip_checksums: bool = False,
    skip_image_headers: bool = False,
) -> dict[str, Any]:
    dataset_id = entry.get("dataset_id")
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", dataset_id):
        raise ValueError(f"Invalid dataset_id: {dataset_id!r}")
    raw_root = entry.get("root_from_repository")
    if not isinstance(raw_root, str):
        raise ValueError(f"{dataset_id}: root_from_repository must be a string")
    dataset_root = resolve_from_repository(repository_root, raw_root)
    split_names = entry.get("splits", ["train", "valid", "test"])
    if not isinstance(split_names, list) or not all(isinstance(item, str) and item for item in split_names):
        raise ValueError(f"{dataset_id}: splits must be a list of names")
    annotation_filename = entry.get("annotation_filename", "_annotations.coco.json")
    tolerance = float(entry.get("boundary_tolerance_px", 0.05))
    expected = entry.get("expected", {})
    issues = IssueLog()

    if not dataset_root.is_dir():
        issues.add("error", "dataset_root_missing", "Dataset root directory does not exist.", raw_root)
        return build_report(
            dataset_id=dataset_id,
            raw_root=raw_root,
            splits={},
            categories=[],
            class_map=[],
            issues=issues,
            checksum={"enabled": False},
            totals={},
            distributions={},
            header_check_enabled=False,
        )

    split_reports: dict[str, Any] = {}
    category_schemas: list[tuple[str, list[dict[str, Any]]]] = []
    used_category_ids: set[Any] = set()
    class_instances: Counter[Any] = Counter()
    class_images: dict[Any, set[tuple[str, Any]]] = defaultdict(set)
    all_image_names: dict[str, list[str]] = defaultdict(list)
    box_area_ratios: list[float] = []
    box_widths: list[float] = []
    box_heights: list[float] = []
    image_widths: list[int] = []
    image_heights: list[int] = []
    total_images = 0
    total_annotations = 0
    total_zero_annotations = 0
    total_image_files = 0
    header_checked = 0

    header_checks_requested = bool(entry.get("check_image_headers", True)) and not skip_image_headers

    for split in split_names:
        split_dir = dataset_root / split
        if not split_dir.is_dir():
            issues.add("error", "split_directory_missing", "Expected split directory is missing.", split)
            continue
        payload = load_coco(split_dir / annotation_filename, issues, split)
        if payload is None:
            continue
        images = payload["images"]
        annotations = payload["annotations"]
        categories = payload["categories"]
        category_schemas.append((split, categories))

        image_ids = [item.get("id") for item in images if isinstance(item, dict)]
        annotation_ids = [item.get("id") for item in annotations if isinstance(item, dict)]
        for duplicate in duplicate_values(image_ids):
            issues.add("error", "duplicate_image_id", "Image ID is duplicated within a split.", f"{split}: {duplicate}")
        for duplicate in duplicate_values(annotation_ids):
            issues.add("error", "duplicate_annotation_id", "Annotation ID is duplicated within a split.", f"{split}: {duplicate}")

        image_by_id: dict[Any, dict[str, Any]] = {}
        referenced_paths: set[str] = set()
        actual_image_paths = {
            path.relative_to(split_dir).as_posix(): path
            for path in split_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        }
        non_image_files = [
            path.relative_to(split_dir).as_posix()
            for path in split_dir.rglob("*")
            if path.is_file() and path.name != annotation_filename and path.suffix.lower() not in IMAGE_SUFFIXES
        ]
        for relative in sorted(non_image_files):
            issues.add("warning", "unexpected_split_file", "Unexpected non-image file exists inside a split.", f"{split}/{relative}")

        for index, image in enumerate(images):
            if not isinstance(image, dict):
                issues.add("error", "invalid_image_record", "Image record must be an object.", f"{split}: index {index}")
                continue
            image_id = image.get("id")
            if image_id not in image_by_id:
                image_by_id[image_id] = image
            file_name = image.get("file_name")
            if not isinstance(file_name, str) or not is_safe_relative_path(file_name):
                issues.add("error", "unsafe_image_path", "Image file_name is missing or unsafe.", f"{split}: {file_name!r}")
                continue
            normalized_name = PurePosixPath(file_name).as_posix()
            referenced_paths.add(normalized_name)
            all_image_names[normalized_name].append(split)
            width, height = image.get("width"), image.get("height")
            if not isinstance(width, int) or isinstance(width, bool) or width <= 0 or not isinstance(height, int) or isinstance(height, bool) or height <= 0:
                issues.add("error", "invalid_image_dimensions", "Image metadata dimensions must be positive integers.", f"{split}/{normalized_name}: {width}x{height}")
            else:
                image_widths.append(width)
                image_heights.append(height)
            path = actual_image_paths.get(normalized_name)
            if path is None:
                issues.add("error", "referenced_image_missing", "COCO references an image file that is missing.", f"{split}/{normalized_name}")
            elif header_checks_requested and isinstance(width, int) and isinstance(height, int):
                try:
                    actual_width, actual_height = read_image_dimensions(path)
                except (OSError, ValueError) as exc:
                    issues.add("error", "image_header_invalid", "Image header cannot be read.", f"{split}/{normalized_name}: {exc}")
                else:
                    header_checked += 1
                    if (actual_width, actual_height) != (width, height):
                        issues.add(
                            "error",
                            "image_dimension_mismatch",
                            "Image dimensions disagree with COCO metadata.",
                            f"{split}/{normalized_name}: file={actual_width}x{actual_height}, coco={width}x{height}",
                        )

        for relative in sorted(set(actual_image_paths) - referenced_paths):
            issues.add("error", "unreferenced_image_file", "Image file is not referenced by COCO metadata.", f"{split}/{relative}")

        category_by_id = {
            item.get("id"): item.get("name")
            for item in categories
            if isinstance(item, dict)
        }
        annotations_per_image: Counter[Any] = Counter()
        exact_annotation_keys: Counter[tuple[Any, Any, tuple[Any, ...]]] = Counter()
        boundary_precision_count = 0
        boundary_error_count = 0

        for index, annotation in enumerate(annotations):
            if not isinstance(annotation, dict):
                issues.add("error", "invalid_annotation_record", "Annotation record must be an object.", f"{split}: index {index}")
                continue
            image_id = annotation.get("image_id")
            category_id = annotation.get("category_id")
            if image_id not in image_by_id:
                issues.add("error", "invalid_annotation_image_reference", "Annotation references an unknown image ID.", f"{split}: annotation {annotation.get('id')}")
                continue
            if category_id not in category_by_id:
                issues.add("error", "invalid_annotation_category_reference", "Annotation references an unknown category ID.", f"{split}: annotation {annotation.get('id')}")
                continue
            annotations_per_image[image_id] += 1
            used_category_ids.add(category_id)
            class_instances[category_id] += 1
            class_images[category_id].add((split, image_id))
            bbox = annotation.get("bbox")
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in bbox)
            ):
                issues.add("error", "bbox_malformed", "Bounding box must contain four finite numbers.", f"{split}: annotation {annotation.get('id')}")
                continue
            x, y, box_width, box_height = (float(value) for value in bbox)
            if box_width <= 0 or box_height <= 0:
                issues.add("error", "bbox_non_positive", "Bounding box width and height must be positive.", f"{split}: annotation {annotation.get('id')}: {bbox}")
                continue
            exact_annotation_keys[(image_id, category_id, tuple(bbox))] += 1
            image = image_by_id[image_id]
            image_width, image_height = image.get("width"), image.get("height")
            if isinstance(image_width, int) and isinstance(image_height, int) and image_width > 0 and image_height > 0:
                overflow = max(
                    max(0.0, -x),
                    max(0.0, -y),
                    max(0.0, x + box_width - image_width),
                    max(0.0, y + box_height - image_height),
                )
                if overflow > tolerance:
                    boundary_error_count += 1
                    issues.add(
                        "error",
                        "bbox_outside_image",
                        "Bounding box exceeds the image boundary beyond tolerance.",
                        f"{split}: annotation {annotation.get('id')}, overflow={overflow:.6f}px",
                    )
                elif overflow > 0:
                    boundary_precision_count += 1
                    issues.add(
                        "warning",
                        "bbox_boundary_precision",
                        "Bounding box exceeds the image boundary only within numeric tolerance.",
                        f"{split}: annotation {annotation.get('id')}, overflow={overflow:.6f}px",
                    )
                box_area_ratios.append((box_width * box_height) / (image_width * image_height))
            box_widths.append(box_width)
            box_heights.append(box_height)

        for key, count in exact_annotation_keys.items():
            if count > 1:
                image_id, category_id, bbox = key
                issues.add(
                    "warning",
                    "duplicate_annotation_geometry",
                    "Identical class and bounding-box geometry occurs more than once on an image.",
                    f"{split}: image_id={image_id}, category_id={category_id}, bbox={list(bbox)}",
                    count=count - 1,
                )

        zero_annotation_ids = [image_id for image_id in image_by_id if annotations_per_image[image_id] == 0]
        for image_id in zero_annotation_ids:
            image = image_by_id[image_id]
            issues.add(
                "info",
                "zero_annotation_image",
                "Image has zero object annotations and must be treated explicitly as a negative/null sample.",
                f"{split}/{image.get('file_name')}",
            )

        split_report = {
            "images": len(images),
            "image_files": len(actual_image_paths),
            "annotations": len(annotations),
            "zero_annotation_images": len(zero_annotation_ids),
            "boundary_precision_boxes": boundary_precision_count,
            "boundary_error_boxes": boundary_error_count,
        }
        split_reports[split] = split_report
        total_images += len(images)
        total_image_files += len(actual_image_paths)
        total_annotations += len(annotations)
        total_zero_annotations += len(zero_annotation_ids)

    for file_name, locations in sorted(all_image_names.items()):
        if len(set(locations)) > 1:
            issues.add("error", "filename_reused_across_splits", "The same referenced filename occurs in multiple splits.", f"{file_name}: {sorted(set(locations))}")

    categories: list[dict[str, Any]] = []
    if category_schemas:
        categories = category_schemas[0][1]
        canonical = [(item.get("id"), item.get("name"), item.get("supercategory")) for item in categories if isinstance(item, dict)]
        for split, schema in category_schemas[1:]:
            current = [(item.get("id"), item.get("name"), item.get("supercategory")) for item in schema if isinstance(item, dict)]
            if current != canonical:
                issues.add("error", "category_schema_mismatch", "Category schema differs between splits.", split)

    category_ids = [item.get("id") for item in categories if isinstance(item, dict)]
    category_names = [item.get("name") for item in categories if isinstance(item, dict)]
    for duplicate in duplicate_values(category_ids):
        issues.add("error", "duplicate_category_id", "Category ID is duplicated.", str(duplicate))
    for duplicate in duplicate_values(category_names):
        issues.add("warning", "duplicate_category_name", "Category name is duplicated in the COCO schema.", str(duplicate))

    category_by_id = {
        item.get("id"): item.get("name")
        for item in categories
        if isinstance(item, dict)
    }
    for category_id, name in category_by_id.items():
        if category_id not in used_category_ids:
            issues.add("warning", "unused_category", "Category exists in the schema but is unused by annotations.", f"id={category_id}, name={name}")

    class_map = []
    for target_id, source_id in enumerate(sorted(used_category_ids, key=lambda value: (str(type(value)), str(value)))):
        class_map.append(
            {
                "source_category_id": source_id,
                "target_class_id": target_id,
                "name": category_by_id.get(source_id),
                "annotation_instances": class_instances[source_id],
                "images_containing_class": len(class_images[source_id]),
            }
        )

    totals = {
        "images": total_images,
        "image_files": total_image_files,
        "annotations": total_annotations,
        "zero_annotation_images": total_zero_annotations,
    }
    expected_pairs = {
        "images": expected.get("images"),
        "annotations": expected.get("annotations"),
        "zero_annotation_images": expected.get("zero_annotation_images"),
    }
    for key, expected_value in expected_pairs.items():
        if expected_value is not None and totals[key] != expected_value:
            issues.add("error", "expected_total_mismatch", f"Observed {key} does not match registry expectation.", f"expected={expected_value}, observed={totals[key]}")

    expected_classes = expected.get("used_class_names")
    observed_classes = [item["name"] for item in class_map]
    if expected_classes is not None and observed_classes != expected_classes:
        issues.add("error", "expected_class_map_mismatch", "Used class map does not match registry expectation.", f"expected={expected_classes}, observed={observed_classes}")

    expected_splits = expected.get("splits", {})
    for split, split_expected in expected_splits.items():
        observed = split_reports.get(split)
        if observed is None:
            continue
        for key in ("images", "annotations"):
            expected_value = split_expected.get(key)
            if expected_value is not None and observed[key] != expected_value:
                issues.add("error", "expected_split_mismatch", f"Observed split {key} does not match registry expectation.", f"{split}: expected={expected_value}, observed={observed[key]}")

    checksum: dict[str, Any]
    checksum_raw = entry.get("checksum_manifest_from_repository")
    if skip_checksums:
        checksum = {"enabled": False, "reason": "disabled by CLI"}
    elif isinstance(checksum_raw, str):
        checksum = verify_checksum_manifest(dataset_root, resolve_from_repository(repository_root, checksum_raw), issues)
    else:
        checksum = {"enabled": False, "reason": "not configured"}
        issues.add("warning", "checksum_not_configured", "No checksum manifest is configured for this dataset.")

    distributions = {
        "image_width_px": numeric_summary([float(value) for value in image_widths]),
        "image_height_px": numeric_summary([float(value) for value in image_heights]),
        "bbox_width_px": numeric_summary(box_widths),
        "bbox_height_px": numeric_summary(box_heights),
        "bbox_area_ratio": numeric_summary(box_area_ratios),
    }
    return build_report(
        dataset_id=dataset_id,
        raw_root=raw_root,
        splits=split_reports,
        categories=categories,
        class_map=class_map,
        issues=issues,
        checksum=checksum,
        totals=totals,
        distributions=distributions,
        header_check_enabled=header_checks_requested,
        header_checked=header_checked,
    )


def build_report(
    *,
    dataset_id: str,
    raw_root: str,
    splits: dict[str, Any],
    categories: list[dict[str, Any]],
    class_map: list[dict[str, Any]],
    issues: IssueLog,
    checksum: dict[str, Any],
    totals: dict[str, Any],
    distributions: dict[str, Any],
    header_check_enabled: bool,
    header_checked: int = 0,
) -> dict[str, Any]:
    counts = issues.counts()
    status = "FAIL" if counts["error"] else "PASS_WITH_WARNINGS" if counts["warning"] else "PASS"
    return {
        "schema_version": 1,
        "validator": {"name": "validate_coco_datasets.py", "version": SCRIPT_VERSION},
        "dataset_id": dataset_id,
        "dataset_root_from_repository": raw_root,
        "status": status,
        "issue_counts": counts,
        "totals": totals,
        "splits": splits,
        "source_categories": categories,
        "class_map": class_map,
        "distributions": distributions,
        "image_header_check": {"enabled": header_check_enabled, "checked": header_checked},
        "checksum_verification": checksum,
        "issues": issues.as_list(),
    }


def markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        value = f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# P2-01 Validation — `{report['dataset_id']}`",
        "",
        f"**Result:** `{report['status']}`",
        "",
        "Raw files were read and verified but never modified.",
        "",
        "## Inventory",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key, value in report.get("totals", {}).items():
        lines.append(f"| {markdown_cell(key)} | {markdown_cell(value)} |")
    lines.extend(["", "## Source splits", "", "| Split | Images | Files | Annotations | Zero-annotation | Boundary precision | Boundary errors |", "|---|---:|---:|---:|---:|---:|---:|"])
    for split, data in report.get("splits", {}).items():
        lines.append(
            "| " + " | ".join(
                markdown_cell(value)
                for value in (
                    split,
                    data.get("images"),
                    data.get("image_files"),
                    data.get("annotations"),
                    data.get("zero_annotation_images"),
                    data.get("boundary_precision_boxes"),
                    data.get("boundary_error_boxes"),
                )
            ) + " |"
        )
    lines.extend(["", "## Canonical class map", "", "Unused COCO categories are intentionally excluded from the derived contiguous class IDs.", "", "| Source ID | Target ID | Name | Instances | Images containing class |", "|---:|---:|---|---:|---:|"])
    for item in report.get("class_map", []):
        lines.append(
            "| " + " | ".join(
                markdown_cell(value)
                for value in (
                    item.get("source_category_id"),
                    item.get("target_class_id"),
                    item.get("name"),
                    item.get("annotation_instances"),
                    item.get("images_containing_class"),
                )
            ) + " |"
        )
    checksum = report.get("checksum_verification", {})
    header = report.get("image_header_check", {})
    lines.extend(
        [
            "",
            "## Integrity checks",
            "",
            "| Check | Result |",
            "|---|---:|",
            f"| Image headers checked | {markdown_cell(header.get('checked'))} |",
            f"| Checksum entries | {markdown_cell(checksum.get('entries'))} |",
            f"| Checksums verified | {markdown_cell(checksum.get('verified'))} |",
            f"| Checksum mismatches | {markdown_cell(checksum.get('mismatches'))} |",
            f"| Missing checksummed files | {markdown_cell(checksum.get('missing'))} |",
            f"| Unlisted raw files | {markdown_cell(checksum.get('unlisted_files'))} |",
            "",
            "## Issue summary",
            "",
            "| Severity | Count |",
            "|---|---:|",
        ]
    )
    for severity in ("error", "warning", "info"):
        lines.append(f"| {severity} | {report['issue_counts'].get(severity, 0)} |")
    lines.extend(["", "## Broken-label findings", "", "| Severity | Code | Count | Meaning | Examples |", "|---|---|---:|---|---|"])
    issues = report.get("issues", [])
    if not issues:
        lines.append("| — | — | 0 | No issues detected. | — |")
    for item in issues:
        examples = "; ".join(item.get("examples", [])) or "—"
        lines.append(
            "| " + " | ".join(
                markdown_cell(value)
                for value in (
                    item.get("severity"),
                    item.get("code"),
                    item.get("count"),
                    item.get("message"),
                    examples,
                )
            ) + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `error` means the raw export cannot safely proceed without investigation.",
            "- `warning` records a known export quirk or tolerance case; it is not silently repaired.",
            "- `info` records an explicit dataset property such as a zero-annotation image.",
            "- Any normalization or repair must be written to derived storage, never back into raw.",
            "",
        ]
    )
    return "\n".join(lines)


def render_summary(reports: list[dict[str, Any]]) -> str:
    lines = [
        "# P2-01 COCO Dataset Validation Summary",
        "",
        "This report is generated by `scripts/validate_coco_datasets.py`.",
        "Raw dataset files are read-only inputs; reports are safe metadata for Git.",
        "",
        "| Dataset | Result | Images | Annotations | Errors | Warnings | Info |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for report in reports:
        totals = report.get("totals", {})
        counts = report.get("issue_counts", {})
        lines.append(
            "| " + " | ".join(
                markdown_cell(value)
                for value in (
                    report.get("dataset_id"),
                    report.get("status"),
                    totals.get("images"),
                    totals.get("annotations"),
                    counts.get("error"),
                    counts.get("warning"),
                    counts.get("info"),
                )
            ) + " |"
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "P2-01 passes only when every registered dataset has zero material errors. Warnings remain explicit inputs to the clean-data decision.",
            "",
        ]
    )
    return "\n".join(lines)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    config_path = args.config if args.config.is_absolute() else (repository_root / args.config)
    output_dir = args.output_dir if args.output_dir.is_absolute() else (repository_root / args.output_dir)
    try:
        registry = load_registry(config_path.resolve())
        entries = registry["datasets"]
        if args.dataset_ids:
            requested = set(args.dataset_ids)
            entries = [entry for entry in entries if entry.get("dataset_id") in requested]
            found = {entry.get("dataset_id") for entry in entries}
            missing = sorted(requested - found)
            if missing:
                raise ValueError(f"Unknown dataset ID(s): {', '.join(missing)}")
        reports = [
            validate_dataset(
                entry,
                repository_root,
                skip_checksums=args.skip_checksums,
                skip_image_headers=args.skip_image_headers,
            )
            for entry in entries
        ]
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    for report in reports:
        stem = report["dataset_id"]
        write_text(output_dir / f"{stem}.validation.json", json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        write_text(output_dir / f"{stem}.validation.md", render_markdown(report))
        counts = report["issue_counts"]
        print(
            f"{stem}: {report['status']} "
            f"(errors={counts['error']}, warnings={counts['warning']}, info={counts['info']})"
        )
    write_text(output_dir / "SUMMARY.md", render_summary(reports))
    print(f"reports: {output_dir}")
    return 1 if any(report["status"] == "FAIL" for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
