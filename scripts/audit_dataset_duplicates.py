#!/usr/bin/env python3
"""Audit exact duplicates, source identity, and near duplicates across COCO exports.

The audit is read-only with respect to raw datasets. It treats the configured
canonical dataset as the visual inventory, then links alternate-label exports by
their original source names. ImageMagick is used only to render a deterministic,
small grayscale fingerprint; all comparison and grouping logic lives here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCRIPT_VERSION = "1.0.0"
ROBOFLOW_EXPORT_NAME = re.compile(
    r"^(?P<stem>.+)_(?P<source_ext>jpe?g|png)\.rf\.[0-9a-z]+\.(?:jpe?g|png)$",
    re.IGNORECASE,
)


@dataclass
class ImageRecord:
    dataset_id: str
    split: str
    image_id: Any
    file_name: str
    source_name: str
    source_name_origin: str
    source_key: str
    width: int
    height: int
    path: Path
    raw_sha256: str = ""
    gray: bytes = b""
    phash: int = 0
    phash_bits: int = 0
    horizontal_dhash: int = 0
    horizontal_dhash_bits: int = 0
    vertical_dhash: int = 0
    vertical_dhash_bits: int = 0


class DisjointSet:
    """Small deterministic union-find over source identity keys."""

    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}
        self.rank = {value: 0 for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        left_rank = self.rank[left_root]
        right_rank = self.rank[right_root]
        if left_rank < right_rank or (left_rank == right_rank and left_root > right_root):
            left_root, right_root = right_root, left_root
            left_rank, right_rank = right_rank, left_rank
        self.parent[right_root] = left_root
        if left_rank == right_rank:
            self.rank[left_root] += 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=repository_root / "configs/bootstrap_external/datasets.json",
        help="Dataset registry containing the duplicate_audit section.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "phases/00_external_bootstrap/reports/p2-02",
        help="Directory for deterministic audit reports.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="Parallel ImageMagick renderer processes.",
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
    if not isinstance(payload.get("datasets"), list) or not payload["datasets"]:
        raise ValueError("Registry must contain a non-empty datasets list")
    if not isinstance(payload.get("duplicate_audit"), dict):
        raise ValueError("Registry must contain a duplicate_audit object")
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


def normalize_source_name(image: dict[str, Any]) -> tuple[str, str, str]:
    """Return display name, stable case-folded key, and provenance of the name."""

    extra = image.get("extra")
    original = extra.get("name") if isinstance(extra, dict) else None
    if isinstance(original, str) and original.strip():
        source_name = original.strip()
        origin = "coco.extra.name"
    else:
        file_name = image.get("file_name")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("Image record has neither extra.name nor a valid file_name")
        base_name = PurePosixPath(file_name.replace("\\", "/")).name
        match = ROBOFLOW_EXPORT_NAME.fullmatch(base_name)
        if match:
            extension = match.group("source_ext").lower()
            extension = "jpg" if extension in {"jpg", "jpeg"} else extension
            source_name = f"{match.group('stem')}.{extension}"
            origin = "roboflow_export_filename"
        else:
            source_name = base_name
            origin = "export_filename"

    normalized = unicodedata.normalize("NFC", source_name).replace("\\", "/")
    normalized = PurePosixPath(normalized).as_posix().lstrip("./")
    path = PurePosixPath(normalized)
    suffix = path.suffix.casefold()
    if suffix == ".jpeg":
        normalized = f"{path.with_suffix('')}.jpg"
    key = normalized.casefold()
    return source_name, key, origin


def load_dataset_records(
    entry: dict[str, Any], repository_root: Path
) -> tuple[list[ImageRecord], dict[str, Any]]:
    dataset_id = entry.get("dataset_id")
    root_raw = entry.get("root_from_repository")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Every dataset needs a non-empty dataset_id")
    if not isinstance(root_raw, str):
        raise ValueError(f"{dataset_id}: root_from_repository must be a string")
    dataset_root = resolve_from_repository(repository_root, root_raw)
    if not dataset_root.is_dir():
        raise ValueError(f"{dataset_id}: dataset root not found: {root_raw}")
    splits = entry.get("splits", ["train", "valid", "test"])
    annotation_name = entry.get("annotation_filename", "_annotations.coco.json")
    records: list[ImageRecord] = []
    split_counts: Counter[str] = Counter()
    for split in splits:
        annotation_path = dataset_root / split / annotation_name
        try:
            payload = json.loads(annotation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{dataset_id}: cannot read {split}/{annotation_name}: {exc}") from exc
        images = payload.get("images")
        if not isinstance(images, list):
            raise ValueError(f"{dataset_id}: {split} COCO images must be a list")
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"{dataset_id}: {split} image #{index} is not an object")
            file_name = image.get("file_name")
            width = image.get("width")
            height = image.get("height")
            if not isinstance(file_name, str):
                raise ValueError(f"{dataset_id}: {split} image #{index} has invalid file_name")
            relative = PurePosixPath(file_name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in file_name:
                raise ValueError(f"{dataset_id}: unsafe image path: {file_name}")
            if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ValueError(f"{dataset_id}: invalid dimensions for {split}/{file_name}")
            image_path = dataset_root.joinpath(split, *relative.parts)
            if not image_path.is_file():
                raise ValueError(f"{dataset_id}: missing image: {split}/{file_name}")
            source_name, source_key, source_name_origin = normalize_source_name(image)
            records.append(
                ImageRecord(
                    dataset_id=dataset_id,
                    split=split,
                    image_id=image.get("id"),
                    file_name=file_name,
                    source_name=source_name,
                    source_name_origin=source_name_origin,
                    source_key=source_key,
                    width=width,
                    height=height,
                    path=image_path,
                )
            )
            split_counts[split] += 1
    records.sort(key=lambda item: (item.source_key, item.split, str(item.image_id), item.file_name))
    provenance: dict[str, Any] = {
        "dataset_id": dataset_id,
        "dataset_root_from_repository": root_raw,
        "images": len(records),
        "source_splits": dict(sorted(split_counts.items())),
    }
    checksum_raw = entry.get("checksum_manifest_from_repository")
    if isinstance(checksum_raw, str):
        checksum_path = resolve_from_repository(repository_root, checksum_raw)
        if not checksum_path.is_file():
            raise ValueError(f"{dataset_id}: checksum manifest not found: {checksum_raw}")
        provenance["checksum_manifest_from_repository"] = checksum_raw
        provenance["checksum_manifest_sha256"] = sha256_file(checksum_path)
    return records, provenance


@lru_cache(maxsize=None)
def dct_cosine_table(length: int, low_frequency_size: int) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(math.cos(math.pi * (2 * position + 1) * frequency / (2 * length)) for position in range(length))
        for frequency in range(low_frequency_size)
    )


def perceptual_hash(gray: bytes, width: int, height: int, low_frequency_size: int = 8) -> tuple[int, int]:
    if len(gray) != width * height:
        raise ValueError("Grayscale buffer length does not match configured dimensions")
    if low_frequency_size < 2 or low_frequency_size > min(width, height):
        raise ValueError("Invalid perceptual hash low-frequency size")
    cos_x = dct_cosine_table(width, low_frequency_size)
    cos_y = dct_cosine_table(height, low_frequency_size)
    row_projection = [
        [
            sum(gray[y * width + x] * cos_x[u][x] for x in range(width))
            for u in range(low_frequency_size)
        ]
        for y in range(height)
    ]
    coefficients: list[float] = []
    for v in range(low_frequency_size):
        for u in range(low_frequency_size):
            coefficients.append(sum(row_projection[y][u] * cos_y[v][y] for y in range(height)))
    values = coefficients[1:]
    threshold = statistics.median(values)
    result = 0
    for value in values:
        result = (result << 1) | int(value > threshold)
    return result, len(values)


def difference_hashes(gray: bytes, width: int, height: int) -> tuple[int, int, int, int]:
    horizontal = 0
    for y in range(height):
        offset = y * width
        for x in range(width - 1):
            horizontal = (horizontal << 1) | int(gray[offset + x] < gray[offset + x + 1])
    vertical = 0
    for y in range(height - 1):
        offset = y * width
        next_offset = (y + 1) * width
        for x in range(width):
            vertical = (vertical << 1) | int(gray[offset + x] < gray[next_offset + x])
    return horizontal, height * (width - 1), vertical, (height - 1) * width


def render_fingerprint(
    record: ImageRecord,
    *,
    command: str,
    width: int,
    height: int,
    colorspace: str,
    resize_filter: str,
) -> tuple[str, bytes, int, int, int, int, int, int]:
    environment = os.environ.copy()
    environment["MAGICK_THREAD_LIMIT"] = "1"
    process = subprocess.run(
        [
            command,
            str(record.path),
            "-auto-orient",
            "-colorspace",
            colorspace,
            "-filter",
            resize_filter,
            "-resize",
            f"{width}x{height}!",
            "-depth",
            "8",
            "gray:-",
        ],
        check=False,
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ImageMagick failed for {record.dataset_id}/{record.split}/{record.file_name}: {message}")
    gray = process.stdout
    if len(gray) != width * height:
        raise RuntimeError(
            f"ImageMagick returned {len(gray)} bytes instead of {width * height} for "
            f"{record.dataset_id}/{record.split}/{record.file_name}"
        )
    phash, phash_bits = perceptual_hash(gray, width, height)
    horizontal, horizontal_bits, vertical, vertical_bits = difference_hashes(gray, width, height)
    return (
        sha256_file(record.path),
        gray,
        phash,
        phash_bits,
        horizontal,
        horizontal_bits,
        vertical,
        vertical_bits,
    )


def fingerprint_records(
    records: list[ImageRecord], renderer: dict[str, Any], workers: int
) -> tuple[str, str]:
    command = renderer.get("command", "magick")
    if not isinstance(command, str) or not command:
        raise ValueError("duplicate_audit.image_renderer.command must be a string")
    executable = shutil.which(command)
    if executable is None:
        raise ValueError(f"Image renderer is unavailable: {command}")
    version_process = subprocess.run(
        [executable, "-version"], check=False, capture_output=True, text=True, timeout=10
    )
    if version_process.returncode != 0:
        raise ValueError(f"Cannot read ImageMagick version: {version_process.stderr.strip()}")
    version_line = version_process.stdout.splitlines()[0].strip()
    width = int(renderer.get("width", 24))
    height = int(renderer.get("height", 24))
    colorspace = renderer.get("colorspace", "Gray")
    resize_filter = renderer.get("resize_filter", "Lanczos")
    if width < 8 or height < 8:
        raise ValueError("Fingerprint dimensions must both be at least 8")
    if workers < 1:
        raise ValueError("--workers must be at least 1")

    keyword = {
        "command": executable,
        "width": width,
        "height": height,
        "colorspace": str(colorspace),
        "resize_filter": str(resize_filter),
    }
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(render_fingerprint, record, **keyword): record
            for record in records
        }
        try:
            for future in as_completed(futures):
                record = futures[future]
                (
                    record.raw_sha256,
                    record.gray,
                    record.phash,
                    record.phash_bits,
                    record.horizontal_dhash,
                    record.horizontal_dhash_bits,
                    record.vertical_dhash,
                    record.vertical_dhash_bits,
                ) = future.result()
        except Exception:
            for future in futures:
                future.cancel()
            raise
    return executable, version_line


def hamming_distance_ratio(left: int, right: int, bit_count: int) -> float:
    if bit_count <= 0:
        raise ValueError("bit_count must be positive")
    return (left ^ right).bit_count() / bit_count


def visual_distances(left: ImageRecord, right: ImageRecord) -> dict[str, float]:
    return {
        "aspect_log_delta": abs(math.log((left.width / left.height) / (right.width / right.height))),
        "phash_distance_ratio": hamming_distance_ratio(left.phash, right.phash, left.phash_bits),
        "horizontal_dhash_distance_ratio": hamming_distance_ratio(
            left.horizontal_dhash, right.horizontal_dhash, left.horizontal_dhash_bits
        ),
        "vertical_dhash_distance_ratio": hamming_distance_ratio(
            left.vertical_dhash, right.vertical_dhash, left.vertical_dhash_bits
        ),
    }


def shifted_normalized_cross_correlation(
    left: bytes,
    right: bytes,
    width: int,
    height: int,
    maximum_shift: int,
) -> float:
    """Maximum grayscale NCC across small x/y translations."""

    if len(left) != width * height or len(right) != width * height:
        raise ValueError("NCC buffers do not match dimensions")
    if maximum_shift < 0 or maximum_shift >= min(width, height):
        raise ValueError("Invalid maximum NCC shift")
    best = -1.0
    for delta_y in range(-maximum_shift, maximum_shift + 1):
        y_start = max(0, -delta_y)
        y_end = min(height, height - delta_y)
        for delta_x in range(-maximum_shift, maximum_shift + 1):
            x_start = max(0, -delta_x)
            x_end = min(width, width - delta_x)
            count = (y_end - y_start) * (x_end - x_start)
            left_sum = right_sum = left_square = right_square = product = 0.0
            for y in range(y_start, y_end):
                left_index = y * width + x_start
                right_index = (y + delta_y) * width + x_start + delta_x
                for _ in range(x_start, x_end):
                    left_value = left[left_index]
                    right_value = right[right_index]
                    left_sum += left_value
                    right_sum += right_value
                    left_square += left_value * left_value
                    right_square += right_value * right_value
                    product += left_value * right_value
                    left_index += 1
                    right_index += 1
            left_variance = left_square - left_sum * left_sum / count
            right_variance = right_square - right_sum * right_sum / count
            if left_variance <= 0 or right_variance <= 0:
                correlation = 1.0 if left == right else 0.0
            else:
                correlation = (product - left_sum * right_sum / count) / math.sqrt(
                    left_variance * right_variance
                )
            best = max(best, correlation)
    return max(-1.0, min(1.0, best))


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
        "min": round(min(values), 6) if values else None,
        "p10": round(percentile(values, 0.10), 6) if values else None,
        "median": round(percentile(values, 0.50), 6) if values else None,
        "p90": round(percentile(values, 0.90), 6) if values else None,
        "max": round(max(values), 6) if values else None,
    }


def pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def stable_group_id(source_keys: Iterable[str]) -> str:
    joined = "\n".join(sorted(source_keys)).encode("utf-8")
    return f"sg-{hashlib.sha256(joined).hexdigest()[:16]}"


def public_record(record: ImageRecord) -> dict[str, Any]:
    return {
        "dataset_id": record.dataset_id,
        "source_split": record.split,
        "image_id": record.image_id,
        "file_name": record.file_name,
        "source_name": record.source_name,
        "source_name_origin": record.source_name_origin,
        "width": record.width,
        "height": record.height,
        "raw_sha256": record.raw_sha256,
    }


def rounded_metrics(metrics: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in sorted(metrics.items())}


def threshold_match(metrics: dict[str, float], thresholds: dict[str, Any], prefix: str = "maximum_") -> bool:
    mapping = {
        f"{prefix}aspect_log_delta": "aspect_log_delta",
        f"{prefix}phash_distance_ratio": "phash_distance_ratio",
        f"{prefix}horizontal_dhash_distance_ratio": "horizontal_dhash_distance_ratio",
        f"{prefix}vertical_dhash_distance_ratio": "vertical_dhash_distance_ratio",
    }
    for threshold_name, metric_name in mapping.items():
        if threshold_name in thresholds and metrics[metric_name] > float(thresholds[threshold_name]):
            return False
    return True


def build_audit(
    registry: dict[str, Any], repository_root: Path, workers: int
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    audit_config = registry["duplicate_audit"]
    canonical_dataset_id = audit_config.get("canonical_dataset_id")
    entries = {entry.get("dataset_id"): entry for entry in registry["datasets"]}
    if canonical_dataset_id not in entries:
        raise ValueError("duplicate_audit canonical_dataset_id is not in datasets")

    all_records: list[ImageRecord] = []
    provenance: list[dict[str, Any]] = []
    by_dataset: dict[str, list[ImageRecord]] = {}
    for entry in registry["datasets"]:
        records, dataset_provenance = load_dataset_records(entry, repository_root)
        by_dataset[entry["dataset_id"]] = records
        all_records.extend(records)
        provenance.append(dataset_provenance)

    renderer = audit_config.get("image_renderer")
    if not isinstance(renderer, dict):
        raise ValueError("duplicate_audit.image_renderer must be an object")
    executable, imagemagick_version = fingerprint_records(all_records, renderer, workers)
    fingerprint_width = int(renderer.get("width", 24))
    fingerprint_height = int(renderer.get("height", 24))

    identities: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in all_records:
        identities[record.source_key].append(record)
    identity_keys = sorted(identities)
    dsu = DisjointSet(identity_keys)
    automatic_edges: dict[tuple[str, str], dict[str, Any]] = {}

    def add_edge(left: str, right: str, evidence: str, metrics: dict[str, float] | None = None) -> None:
        if left == right:
            return
        key = pair_key(left, right)
        edge = automatic_edges.setdefault(
            key,
            {"left_source_key": key[0], "right_source_key": key[1], "evidence": [], "metrics": {}},
        )
        if evidence not in edge["evidence"]:
            edge["evidence"].append(evidence)
            edge["evidence"].sort()
        if metrics:
            edge["metrics"].update(rounded_metrics(metrics))
        dsu.union(left, right)

    patterns = audit_config.get("source_patterns", {})
    try:
        video_pattern = re.compile(patterns["video_frame"], re.IGNORECASE)
        timestamp_pattern = re.compile(patterns["timestamp_image"], re.IGNORECASE)
    except (KeyError, re.error) as exc:
        raise ValueError(f"Invalid duplicate-audit source pattern: {exc}") from exc

    canonical_records = by_dataset[canonical_dataset_id]
    canonical_by_key: dict[str, ImageRecord] = {}
    canonical_key_collisions: dict[str, int] = {}
    canonical_candidates: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in canonical_records:
        canonical_candidates[record.source_key].append(record)
    for key, records in canonical_candidates.items():
        canonical_by_key[key] = records[0]
        if len(records) > 1:
            canonical_key_collisions[key] = len(records)

    video_groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    timestamp_records: list[tuple[int, str]] = []
    pattern_counts: Counter[str] = Counter()
    source_sequence_for_key: dict[str, str] = {}
    for key, record in canonical_by_key.items():
        video_match = video_pattern.fullmatch(record.source_name)
        if video_match:
            sequence = video_match.group("sequence").casefold()
            frame = int(video_match.group("frame"))
            video_groups[sequence].append((frame, key))
            source_sequence_for_key[key] = sequence
            pattern_counts["video_frame"] += 1
            continue
        timestamp_match = timestamp_pattern.fullmatch(record.source_name)
        if timestamp_match:
            timestamp_records.append((int(timestamp_match.group("timestamp")), key))
            pattern_counts["timestamp_image"] += 1
        else:
            pattern_counts["other"] += 1

    for sequence, members in sorted(video_groups.items()):
        ordered = sorted(members)
        anchor = ordered[0][1]
        for _, key in ordered[1:]:
            add_edge(anchor, key, "source_video_sequence")

    hash_records: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in all_records:
        hash_records[record.raw_sha256].append(record)
    exact_groups: list[dict[str, Any]] = []
    for digest, records in sorted(hash_records.items()):
        if len(records) < 2:
            continue
        source_keys = sorted({record.source_key for record in records})
        for left, right in zip(source_keys, source_keys[1:]):
            add_edge(left, right, "exact_file_sha256")
        exact_groups.append(
            {
                "sha256": digest,
                "record_count": len(records),
                "dataset_ids": sorted({record.dataset_id for record in records}),
                "source_keys": source_keys,
                "records": [public_record(record) for record in sorted(records, key=lambda x: (x.dataset_id, x.split, x.file_name))],
            }
        )

    high = audit_config.get("high_confidence_global", {})
    review_config = audit_config.get("review_candidate", {})
    review_candidates: dict[tuple[str, str], dict[str, Any]] = {}

    def add_review(left: str, right: str, evidence: str, metrics: dict[str, float]) -> None:
        key = pair_key(left, right)
        candidate = review_candidates.setdefault(
            key,
            {"left_source_key": key[0], "right_source_key": key[1], "evidence": [], "metrics": {}},
        )
        if evidence not in candidate["evidence"]:
            candidate["evidence"].append(evidence)
            candidate["evidence"].sort()
        candidate["metrics"].update(rounded_metrics(metrics))

    canonical_items = sorted(canonical_by_key.items())
    for (left_key, left), (right_key, right) in combinations(canonical_items, 2):
        if source_sequence_for_key.get(left_key) == source_sequence_for_key.get(right_key) and left_key in source_sequence_for_key:
            continue
        metrics = visual_distances(left, right)
        if threshold_match(metrics, high):
            if bool(high.get("auto_merge", False)):
                add_edge(left_key, right_key, "high_confidence_global_perceptual", metrics)
            else:
                add_review(left_key, right_key, "high_confidence_global_perceptual_review", metrics)
        elif threshold_match(metrics, review_config):
            add_review(left_key, right_key, "global_perceptual_review", metrics)

    temporal = audit_config.get("temporal_near_duplicate", {})
    maximum_gap = int(temporal.get("maximum_timestamp_gap", 10))
    maximum_shift = int(temporal.get("maximum_shift_pixels", 3))
    minimum_ncc = float(temporal.get("minimum_shifted_ncc", 0.80))
    review_minimum_ncc = float(review_config.get("minimum_temporal_shifted_ncc", 0.70))
    timestamp_records.sort()
    temporal_pairs_examined = 0
    for index, (left_timestamp, left_key) in enumerate(timestamp_records):
        for right_timestamp, right_key in timestamp_records[index + 1 :]:
            gap = right_timestamp - left_timestamp
            if gap > maximum_gap:
                break
            temporal_pairs_examined += 1
            left = canonical_by_key[left_key]
            right = canonical_by_key[right_key]
            metrics = visual_distances(left, right)
            metrics["timestamp_gap"] = float(gap)
            ncc = shifted_normalized_cross_correlation(
                left.gray,
                right.gray,
                fingerprint_width,
                fingerprint_height,
                maximum_shift,
            )
            metrics["shifted_ncc"] = ncc
            visual_gate = (
                metrics["aspect_log_delta"] <= float(temporal.get("maximum_aspect_log_delta", 0.50))
                and metrics["phash_distance_ratio"] <= float(temporal.get("maximum_phash_distance_ratio", 0.35))
            )
            if visual_gate and ncc >= minimum_ncc:
                add_edge(left_key, right_key, "timestamp_window_visual_match", metrics)
            elif visual_gate and ncc >= review_minimum_ncc:
                add_review(left_key, right_key, "timestamp_window_review", metrics)

    # Review pairs already connected by stronger evidence no longer need manual review.
    pending_review = [
        candidate
        for key, candidate in review_candidates.items()
        if dsu.find(key[0]) != dsu.find(key[1])
    ]
    for candidate in pending_review:
        metrics = candidate["metrics"]
        if "shifted_ncc" in metrics:
            candidate["ranking_score"] = round(
                (1.0 - metrics["shifted_ncc"]) + metrics.get("phash_distance_ratio", 0.0), 6
            )
        else:
            candidate["ranking_score"] = round(
                metrics.get("phash_distance_ratio", 1.0)
                + metrics.get("horizontal_dhash_distance_ratio", 1.0)
                + metrics.get("vertical_dhash_distance_ratio", 1.0),
                6,
            )
    pending_review.sort(
        key=lambda item: (item["ranking_score"], item["left_source_key"], item["right_source_key"])
    )
    roots: dict[str, list[str]] = defaultdict(list)
    for key in identity_keys:
        roots[dsu.find(key)].append(key)
    key_to_group: dict[str, str] = {}
    group_members: list[tuple[str, list[str]]] = []
    for members in roots.values():
        ordered = sorted(members)
        group_id = stable_group_id(ordered)
        group_members.append((group_id, ordered))
        for key in ordered:
            key_to_group[key] = group_id
    group_members.sort()

    # A pairwise matcher can emit many frame pairs for the same two source groups.
    # Collapse those into one review item so P2-03 reviews a hypothesis once, using
    # the best-ranked frame pair as its representative evidence.
    review_pair_count_before_aggregation = len(pending_review)
    review_by_group_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in pending_review:
        left_source = candidate["left_source_key"]
        right_source = candidate["right_source_key"]
        left_group = key_to_group[left_source]
        right_group = key_to_group[right_source]
        if left_group == right_group:
            continue
        group_pair = pair_key(left_group, right_group)
        if left_group != group_pair[0]:
            left_source, right_source = right_source, left_source
        existing = review_by_group_pair.get(group_pair)
        if existing is None:
            review_by_group_pair[group_pair] = {
                "left_group_id": group_pair[0],
                "right_group_id": group_pair[1],
                "left_source_key": left_source,
                "right_source_key": right_source,
                "supporting_pair_count": 1,
                "evidence": list(candidate["evidence"]),
                "metrics": dict(candidate["metrics"]),
                "ranking_score": candidate["ranking_score"],
            }
            continue
        existing["supporting_pair_count"] += 1
        existing["evidence"] = sorted(set(existing["evidence"]) | set(candidate["evidence"]))
        if candidate["ranking_score"] < existing["ranking_score"]:
            existing["left_source_key"] = left_source
            existing["right_source_key"] = right_source
            existing["metrics"] = dict(candidate["metrics"])
            existing["ranking_score"] = candidate["ranking_score"]

    pending_review = sorted(
        review_by_group_pair.values(),
        key=lambda item: (
            item["ranking_score"],
            item["left_group_id"],
            item["right_group_id"],
        ),
    )
    review_group_pair_count_before_limit = len(pending_review)
    review_limit = int(review_config.get("maximum_reported_pairs", 500))
    pending_review = pending_review[:review_limit]

    edge_evidence_by_group: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in automatic_edges.values():
        group_id = key_to_group[edge["left_source_key"]]
        for evidence in edge["evidence"]:
            edge_evidence_by_group[group_id][evidence] += 1

    groups: list[dict[str, Any]] = []
    for group_id, members in group_members:
        records = [record for key in members for record in identities[key]]
        dataset_counts = Counter(record.dataset_id for record in records)
        dataset_splits: dict[str, Counter[str]] = defaultdict(Counter)
        for record in records:
            dataset_splits[record.dataset_id][record.split] += 1
        canonical_splits = dataset_splits.get(canonical_dataset_id, Counter())
        sequence_ids = sorted(
            {source_sequence_for_key[key] for key in members if key in source_sequence_for_key}
        )
        groups.append(
            {
                "group_id": group_id,
                "source_identity_count": len(members),
                "source_keys": members,
                "record_count": len(records),
                "dataset_image_counts": dict(sorted(dataset_counts.items())),
                "dataset_source_splits": {
                    dataset_id: dict(sorted(counts.items()))
                    for dataset_id, counts in sorted(dataset_splits.items())
                },
                "sequence_ids": sequence_ids,
                "evidence_edge_counts": dict(sorted(edge_evidence_by_group[group_id].items())),
                "canonical_cross_split": len(canonical_splits) > 1,
                "all_records_cross_split": len({record.split for record in records}) > 1,
            }
        )

    identity_map: list[dict[str, Any]] = []
    for key in identity_keys:
        records = sorted(identities[key], key=lambda item: (item.dataset_id, item.split, item.file_name))
        identity_map.append(
            {
                "source_key": key,
                "group_id": key_to_group[key],
                "records": [public_record(record) for record in records],
            }
        )

    if len(identity_map) != len(identity_keys):
        raise RuntimeError("Internal audit invariant failed: source identity coverage")
    if sum(group["source_identity_count"] for group in groups) != len(identity_keys):
        raise RuntimeError("Internal audit invariant failed: source group coverage")
    if len({group["group_id"] for group in groups}) != len(groups):
        raise RuntimeError("Internal audit invariant failed: duplicate stable group ID")
    if any(
        key_to_group[edge["left_source_key"]] != key_to_group[edge["right_source_key"]]
        for edge in automatic_edges.values()
    ):
        raise RuntimeError("Internal audit invariant failed: automatic edge crosses final groups")
    if any(item["left_group_id"] == item["right_group_id"] for item in pending_review):
        raise RuntimeError("Internal audit invariant failed: review candidate is already grouped")

    pairwise_relationships: list[dict[str, Any]] = []
    for left_id, right_id in combinations(sorted(by_dataset), 2):
        left_by_key: dict[str, ImageRecord] = {}
        right_by_key: dict[str, ImageRecord] = {}
        for record in by_dataset[left_id]:
            left_by_key.setdefault(record.source_key, record)
        for record in by_dataset[right_id]:
            right_by_key.setdefault(record.source_key, record)
        shared = sorted(set(left_by_key) & set(right_by_key))
        phash_distances: list[float] = []
        horizontal_distances: list[float] = []
        vertical_distances: list[float] = []
        same_dimensions = same_raw_sha = same_split = 0
        for key in shared:
            left = left_by_key[key]
            right = right_by_key[key]
            metrics = visual_distances(left, right)
            phash_distances.append(metrics["phash_distance_ratio"])
            horizontal_distances.append(metrics["horizontal_dhash_distance_ratio"])
            vertical_distances.append(metrics["vertical_dhash_distance_ratio"])
            same_dimensions += (left.width, left.height) == (right.width, right.height)
            same_raw_sha += left.raw_sha256 == right.raw_sha256
            same_split += left.split == right.split
        pairwise_relationships.append(
            {
                "left_dataset_id": left_id,
                "right_dataset_id": right_id,
                "shared_source_identities": len(shared),
                "left_only_source_identities": len(set(left_by_key) - set(right_by_key)),
                "right_only_source_identities": len(set(right_by_key) - set(left_by_key)),
                "shared_with_same_dimensions": same_dimensions,
                "shared_with_exact_file_sha256": same_raw_sha,
                "shared_in_same_source_split": same_split,
                "shared_in_different_source_split": len(shared) - same_split,
                "shared_visual_distance": {
                    "phash": numeric_summary(phash_distances),
                    "horizontal_dhash": numeric_summary(horizontal_distances),
                    "vertical_dhash": numeric_summary(vertical_distances),
                },
            }
        )

    video_cross_split_groups = 0
    video_images_in_cross_split_groups = 0
    video_group_sizes: list[int] = []
    for members in video_groups.values():
        keys = [key for _, key in members]
        splits = {canonical_by_key[key].split for key in keys}
        video_group_sizes.append(len(keys))
        if len(splits) > 1:
            video_cross_split_groups += 1
            video_images_in_cross_split_groups += len(keys)

    canonical_leaking_groups = [group for group in groups if group["canonical_cross_split"]]
    all_records_leaking_groups = [group for group in groups if group["all_records_cross_split"]]
    timestamp_match_groups = [
        group
        for group in groups
        if "timestamp_window_visual_match" in group["evidence_edge_counts"]
    ]
    canonical_images_in_leaking_groups = sum(
        group["dataset_image_counts"].get(canonical_dataset_id, 0)
        for group in canonical_leaking_groups
    )
    edge_type_counts = Counter(
        evidence for edge in automatic_edges.values() for evidence in edge["evidence"]
    )
    exact_internal_counts = {
        dataset_id: sum(
            1
            for records in hash_records.values()
            if sum(record.dataset_id == dataset_id for record in records) > 1
        )
        for dataset_id in sorted(by_dataset)
    }
    exact_cross_dataset_count = sum(
        1 for records in hash_records.values() if len({record.dataset_id for record in records}) > 1
    )
    exact_different_source_identity_count = sum(
        1 for records in hash_records.values() if len({record.source_key for record in records}) > 1
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "audit": {"name": "audit_dataset_duplicates.py", "version": SCRIPT_VERSION},
        "status": "COMPLETE_WITH_LEAKAGE_FINDINGS" if canonical_leaking_groups else "COMPLETE",
        "canonical_dataset_id": canonical_dataset_id,
        "raw_data_mutated": False,
        "input_provenance": provenance,
        "renderer": {
            "executable_name": Path(executable).name,
            "version": imagemagick_version,
            "width": fingerprint_width,
            "height": fingerprint_height,
            "colorspace": renderer.get("colorspace", "Gray"),
            "resize_filter": renderer.get("resize_filter", "Lanczos"),
        },
        "thresholds": {
            "high_confidence_global": high,
            "temporal_near_duplicate": temporal,
            "review_candidate": review_config,
        },
        "inventory": {
            "dataset_records": {key: len(value) for key, value in sorted(by_dataset.items())},
            "total_records": len(all_records),
            "unique_source_identities": len(identity_keys),
            "canonical_unique_source_identities": len(canonical_by_key),
            "canonical_source_key_collisions": canonical_key_collisions,
            "source_name_origin_counts": dict(
                sorted(Counter(record.source_name_origin for record in all_records).items())
            ),
        },
        "exact_duplicate_audit": {
            "exact_file_groups_total": len(exact_groups),
            "internal_exact_file_groups_by_dataset": exact_internal_counts,
            "cross_dataset_exact_file_groups": exact_cross_dataset_count,
            "exact_file_groups_with_different_source_identities": exact_different_source_identity_count,
            "groups": exact_groups,
        },
        "dataset_relationships": pairwise_relationships,
        "source_sequence_audit": {
            "canonical_name_pattern_counts": dict(sorted(pattern_counts.items())),
            "video_sequence_groups": len(video_groups),
            "video_sequence_images": sum(video_group_sizes),
            "video_sequence_size": numeric_summary([float(value) for value in video_group_sizes]),
            "video_groups_crossing_source_splits": video_cross_split_groups,
            "video_images_in_cross_split_groups": video_images_in_cross_split_groups,
            "timestamp_images": len(timestamp_records),
            "timestamp_pairs_examined_within_window": temporal_pairs_examined,
            "timestamp_visual_match_groups": len(timestamp_match_groups),
            "timestamp_images_in_visual_match_groups": sum(
                group["source_identity_count"] for group in timestamp_match_groups
            ),
        },
        "near_duplicate_audit": {
            "automatic_edge_count": len(automatic_edges),
            "automatic_edge_counts_by_evidence": dict(sorted(edge_type_counts.items())),
            "automatic_edges": sorted(
                automatic_edges.values(),
                key=lambda item: (item["left_source_key"], item["right_source_key"]),
            ),
            "pending_review_pair_count": review_pair_count_before_aggregation,
            "pending_review_candidate_count": review_group_pair_count_before_limit,
            "reported_review_candidate_count": len(pending_review),
            "review_candidates_truncated": review_group_pair_count_before_limit > len(pending_review),
        },
        "grouping_result": {
            "recommended_split_unit": "group_id",
            "source_groups": len(groups),
            "singleton_source_groups": sum(group["source_identity_count"] == 1 for group in groups),
            "largest_source_group_identities": max(
                (group["source_identity_count"] for group in groups), default=0
            ),
            "canonical_groups_crossing_source_splits": len(canonical_leaking_groups),
            "canonical_images_in_cross_split_groups": canonical_images_in_leaking_groups,
            "all_record_groups_crossing_source_splits": len(all_records_leaking_groups),
        },
        "artifacts": {
            "source_groups": "source-groups.jsonl",
            "source_identity_map": "source-identity-map.jsonl",
            "review_candidates_jsonl": "review-candidates.jsonl",
            "review_candidates_markdown": "REVIEW-CANDIDATES.md",
        },
        "interpretation": [
            "The Roboflow source splits are provenance only and are not approved experiment splits.",
            "Every source identity and every automatic duplicate/sequence group must remain in one derived split.",
            "Alternate-label records sharing a source identity are not independent visual evidence.",
            "Review candidates are hypotheses for P2-03 and are not merged automatically.",
            "Perceptual similarity is a leakage-control aid, not OVD ground truth.",
        ],
    }
    return report, groups, identity_map, pending_review


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, items: Iterable[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in items
    )
    atomic_write_text(path, content)


def markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        value = f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_review_markdown(
    candidates: list[dict[str, Any]], total: int, underlying_pair_count: int
) -> str:
    lines = [
        "# P2-02 Review Candidates",
        "",
        "Pasangan di bawah belum digabung otomatis. Periksa secara visual pada P2-03; "
        "keputusan review harus dicatat sebagai merge atau keep-separate.",
        "",
        f"- Total pending source-group pair: **{total}**",
        f"- Underlying matched image pairs: **{underlying_pair_count}**",
        f"- Ditampilkan di artifact machine-readable/Markdown: **{len(candidates)}**",
        "",
        "| Rank | Left group | Right group | Support | Representative left | Representative right | Evidence | pHash | dHash-H | dHash-V | NCC | Time gap |",
        "|---:|---|---|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for index, candidate in enumerate(candidates, start=1):
        metrics = candidate["metrics"]
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    index,
                    candidate["left_group_id"],
                    candidate["right_group_id"],
                    candidate["supporting_pair_count"],
                    candidate["left_source_key"],
                    candidate["right_source_key"],
                    ", ".join(candidate["evidence"]),
                    metrics.get("phash_distance_ratio"),
                    metrics.get("horizontal_dhash_distance_ratio"),
                    metrics.get("vertical_dhash_distance_ratio"),
                    metrics.get("shifted_ncc"),
                    metrics.get("timestamp_gap"),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Catatan: urutan adalah prioritas review algoritmik, bukan probabilitas bahwa pasangan pasti sama.",
            "",
        ]
    )
    return "\n".join(lines)


def render_summary(report: dict[str, Any], groups: list[dict[str, Any]]) -> str:
    inventory = report["inventory"]
    exact = report["exact_duplicate_audit"]
    sequence = report["source_sequence_audit"]
    near = report["near_duplicate_audit"]
    grouping = report["grouping_result"]
    lines = [
        "# P2-02 Duplicate and Source-Sequence Audit",
        "",
        f"**Result:** `{report['status']}`",
        "",
        "Raw dataset dibaca untuk hashing/fingerprint dan tidak diubah.",
        "",
        "## Ringkasan",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total dataset records | {inventory['total_records']} |",
        f"| Unique source identities | {inventory['unique_source_identities']} |",
        f"| Exact file duplicate groups | {exact['exact_file_groups_total']} |",
        f"| Video source groups | {sequence['video_sequence_groups']} |",
        f"| Video images | {sequence['video_sequence_images']} |",
        f"| Video groups crossing source splits | {sequence['video_groups_crossing_source_splits']} |",
        f"| Automatic grouping edges | {near['automatic_edge_count']} |",
        f"| Pending visual-review group pairs | {near['pending_review_candidate_count']} |",
        f"| Underlying visual-review image pairs | {near['pending_review_pair_count']} |",
        f"| Final recommended split groups | {grouping['source_groups']} |",
        f"| Canonical groups crossing source splits | {grouping['canonical_groups_crossing_source_splits']} |",
        f"| Canonical images inside leaking groups | {grouping['canonical_images_in_cross_split_groups']} |",
        "",
        "## Hubungan antardataset",
        "",
        "| Left | Right | Shared source | Left only | Right only | Same dimensions | Exact bytes | Different source split |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for relationship in report["dataset_relationships"]:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    relationship["left_dataset_id"],
                    relationship["right_dataset_id"],
                    relationship["shared_source_identities"],
                    relationship["left_only_source_identities"],
                    relationship["right_only_source_identities"],
                    relationship["shared_with_same_dimensions"],
                    relationship["shared_with_exact_file_sha256"],
                    relationship["shared_in_different_source_split"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Kesimpulan: label komponen dan label ODOL/NOT ODOL adalah dua ontology pada visual yang "
            "hampir seluruhnya sama, bukan dua test set independen.",
            "",
            "## Bukti pengelompokan otomatis",
            "",
            "| Evidence | Edges |",
            "|---|---:|",
        ]
    )
    for evidence, count in near["automatic_edge_counts_by_evidence"].items():
        lines.append(f"| {markdown_cell(evidence)} | {count} |")
    lines.extend(
        [
            "",
            "## Grup source-split yang paling terdampak",
            "",
            "| Group ID | Source identities | Canonical split counts | Evidence |",
            "|---|---:|---|---|",
        ]
    )
    canonical_id = report["canonical_dataset_id"]
    leaking = sorted(
        (group for group in groups if group["canonical_cross_split"]),
        key=lambda item: (-item["source_identity_count"], item["group_id"]),
    )
    for group in leaking[:25]:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(value)
                for value in (
                    group["group_id"],
                    group["source_identity_count"],
                    json.dumps(group["dataset_source_splits"].get(canonical_id, {}), sort_keys=True),
                    ", ".join(group["evidence_edge_counts"]),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Keputusan untuk fase berikutnya",
            "",
            "- Abaikan assignment train/valid/test bawaan Roboflow untuk eksperimen final.",
            "- Gunakan `group_id` dari `source-groups.jsonl` sebagai unit atomik pada P3-01.",
            "- Review pasangan pada `REVIEW-CANDIDATES.md` saat P2-03; jangan menggabungkannya diam-diam.",
            "- Perlakukan dataset ODOL sebagai alternate-label view/B2, bukan validasi independen B1.",
            "- Jangan menafsirkan perceptual similarity sebagai label OVD atau bukti legal.",
            "",
            "## Reproduksi",
            "",
            "```bash",
            ".venv/bin/python scripts/audit_dataset_duplicates.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    output_dir: Path,
    report: dict[str, Any],
    groups: list[dict[str, Any]],
    identity_map: list[dict[str, Any]],
    review_candidates: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    group_path = output_dir / "source-groups.jsonl"
    identity_path = output_dir / "source-identity-map.jsonl"
    review_path = output_dir / "review-candidates.jsonl"
    write_jsonl(group_path, groups)
    write_jsonl(identity_path, identity_map)
    write_jsonl(review_path, review_candidates)
    report["artifacts"]["source_groups_sha256"] = sha256_file(group_path)
    report["artifacts"]["source_identity_map_sha256"] = sha256_file(identity_path)
    report["artifacts"]["review_candidates_sha256"] = sha256_file(review_path)
    atomic_write_text(
        output_dir / "duplicate-audit.json",
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    atomic_write_text(output_dir / "SUMMARY.md", render_summary(report, groups))
    atomic_write_text(
        output_dir / "REVIEW-CANDIDATES.md",
        render_review_markdown(
            review_candidates,
            report["near_duplicate_audit"]["pending_review_candidate_count"],
            report["near_duplicate_audit"]["pending_review_pair_count"],
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        registry = load_registry(args.config.resolve())
        report, groups, identity_map, review_candidates = build_audit(
            registry, repository_root, args.workers
        )
        write_reports(args.output_dir.resolve(), report, groups, identity_map, review_candidates)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"P2-02 {report['status']}: {report['inventory']['total_records']} records, "
        f"{report['grouping_result']['source_groups']} split groups, "
        f"{report['grouping_result']['canonical_groups_crossing_source_splits']} current leakage groups."
    )
    print(f"Reports: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
