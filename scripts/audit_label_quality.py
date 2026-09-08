#!/usr/bin/env python3
"""Audit COCO label quality and build a local, reproducible review gallery.

Raw exports are immutable inputs. The script writes Git-safe metadata reports to
P2-03 and renders pixels only into external/interim storage. Heuristic findings
are review candidates, not silent label corrections.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCRIPT_VERSION = "1.1.0"
ROBOFLOW_EXPORT_NAME = re.compile(
    r"^(?P<stem>.+)_(?P<source_ext>jpe?g|png)\.rf\.[0-9a-z]+\.(?:jpe?g|png)$",
    re.IGNORECASE,
)
PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
ACTION_RANK = {
    "REVIEW_IMAGE": 0,
    "QUARANTINE_ANNOTATION": 1,
    "QUARANTINE_IMAGE": 2,
    "RELABEL_IMAGE": 3,
}
CLASS_COLORS = {
    "boxtruck": "#00d7e8",
    "headtruck": "#ffe433",
    "muatan": "#ff35d3",
    "ODOL": "#ff3b30",
    "NOT ODOL": "#36e07d",
}


@dataclass(frozen=True)
class AnnotationRecord:
    annotation_id: Any
    category_name: str
    bbox: tuple[float, float, float, float]


@dataclass
class ImageRecord:
    dataset_id: str
    split: str
    image_id: Any
    file_name: str
    source_name: str
    source_key: str
    width: int
    height: int
    path: Path
    annotations: list[AnnotationRecord] = field(default_factory=list)


class CandidateAccumulator:
    """Consolidate multiple risk signals into one review row per dataset image."""

    def __init__(self, group_for_source: dict[str, str]) -> None:
        self.group_for_source = group_for_source
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def add(
        self,
        record: ImageRecord,
        *,
        reason_code: str,
        priority: str,
        recommended_action: str,
        evidence: dict[str, Any] | None = None,
        annotation_ids: Iterable[Any] = (),
    ) -> None:
        if priority not in PRIORITY_RANK:
            raise ValueError(f"Unsupported priority: {priority}")
        if recommended_action not in ACTION_RANK:
            raise ValueError(f"Unsupported action: {recommended_action}")
        key = (record.dataset_id, record.source_key)
        item = self._items.setdefault(
            key,
            {
                "dataset_id": record.dataset_id,
                "source_key": record.source_key,
                "source_name": record.source_name,
                "source_split": record.split,
                "image_id": record.image_id,
                "file_name": record.file_name,
                "group_id": self.group_for_source.get(record.source_key),
                "priority": priority,
                "recommended_action": recommended_action,
                "reason_codes": [],
                "evidence": {},
                "annotation_ids": [],
            },
        )
        if PRIORITY_RANK[priority] > PRIORITY_RANK[item["priority"]]:
            item["priority"] = priority
        if ACTION_RANK[recommended_action] > ACTION_RANK[item["recommended_action"]]:
            item["recommended_action"] = recommended_action
        if reason_code not in item["reason_codes"]:
            item["reason_codes"].append(reason_code)
            item["reason_codes"].sort()
        if evidence is not None:
            item["evidence"][reason_code] = evidence
        item["annotation_ids"] = sorted(
            {*item["annotation_ids"], *annotation_ids}, key=lambda value: str(value)
        )

    def rows(self) -> list[dict[str, Any]]:
        rows = list(self._items.values())
        for row in rows:
            digest_input = f"{row['dataset_id']}\n{row['source_key']}".encode("utf-8")
            row["candidate_id"] = f"lq-{hashlib.sha256(digest_input).hexdigest()[:16]}"
        rows.sort(
            key=lambda item: (
                -PRIORITY_RANK[item["priority"]],
                item["dataset_id"],
                item["source_key"],
            )
        )
        return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=repository_root / "configs/bootstrap_external/datasets.json",
        help="Dataset registry containing the quality_review section.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "phases/00_external_bootstrap/reports/p2-03",
        help="Directory for Git-safe JSON/JSONL/Markdown reports.",
    )
    parser.add_argument(
        "--gallery-dir",
        type=Path,
        help="Override the external/interim gallery directory from the registry.",
    )
    parser.add_argument(
        "--skip-gallery",
        action="store_true",
        help="Write metadata reports without rendering contact sheets.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="Parallel ImageMagick tile renderers.",
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
    if not isinstance(payload.get("quality_review"), dict):
        raise ValueError("Registry must contain a quality_review object")
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


def normalize_source_name(image: dict[str, Any]) -> tuple[str, str]:
    extra = image.get("extra")
    original = extra.get("name") if isinstance(extra, dict) else None
    if isinstance(original, str) and original.strip():
        source_name = original.strip()
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
        else:
            source_name = base_name
    normalized = unicodedata.normalize("NFC", source_name).replace("\\", "/")
    normalized = PurePosixPath(normalized).as_posix().lstrip("./")
    normalized_path = PurePosixPath(normalized)
    if normalized_path.suffix.casefold() == ".jpeg":
        normalized = f"{normalized_path.with_suffix('')}.jpg"
    return source_name, normalized.casefold()


def load_dataset_records(entry: dict[str, Any], repository_root: Path) -> list[ImageRecord]:
    dataset_id = entry.get("dataset_id")
    root_raw = entry.get("root_from_repository")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ValueError("Every dataset needs a non-empty dataset_id")
    if not isinstance(root_raw, str):
        raise ValueError(f"{dataset_id}: root_from_repository must be a string")
    dataset_root = resolve_from_repository(repository_root, root_raw)
    if not dataset_root.is_dir():
        raise ValueError(f"{dataset_id}: dataset root not found: {root_raw}")
    annotation_name = entry.get("annotation_filename", "_annotations.coco.json")
    records: list[ImageRecord] = []
    seen_source_keys: set[str] = set()
    for split in entry.get("splits", ["train", "valid", "test"]):
        annotation_path = dataset_root / split / annotation_name
        try:
            payload = json.loads(annotation_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{dataset_id}: cannot read {split}/{annotation_name}: {exc}") from exc
        categories = payload.get("categories")
        images = payload.get("images")
        annotations = payload.get("annotations")
        if not isinstance(categories, list) or not isinstance(images, list) or not isinstance(annotations, list):
            raise ValueError(f"{dataset_id}: malformed COCO arrays in {split}")
        category_names = {
            category.get("id"): category.get("name")
            for category in categories
            if isinstance(category, dict) and isinstance(category.get("name"), str)
        }
        annotations_by_image: dict[Any, list[AnnotationRecord]] = defaultdict(list)
        for annotation in annotations:
            if not isinstance(annotation, dict):
                raise ValueError(f"{dataset_id}: non-object annotation in {split}")
            bbox = annotation.get("bbox")
            category_name = category_names.get(annotation.get("category_id"))
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox)
                or not isinstance(category_name, str)
            ):
                raise ValueError(f"{dataset_id}: invalid annotation {annotation.get('id')} in {split}")
            annotations_by_image[annotation.get("image_id")].append(
                AnnotationRecord(
                    annotation_id=annotation.get("id"),
                    category_name=category_name,
                    bbox=tuple(float(value) for value in bbox),
                )
            )
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ValueError(f"{dataset_id}: non-object image #{index} in {split}")
            file_name = image.get("file_name")
            width = image.get("width")
            height = image.get("height")
            if not isinstance(file_name, str):
                raise ValueError(f"{dataset_id}: invalid file_name in {split}")
            relative = PurePosixPath(file_name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in file_name:
                raise ValueError(f"{dataset_id}: unsafe image path: {file_name}")
            if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
                raise ValueError(f"{dataset_id}: invalid dimensions for {split}/{file_name}")
            source_name, source_key = normalize_source_name(image)
            if source_key in seen_source_keys:
                raise ValueError(f"{dataset_id}: duplicate source identity: {source_key}")
            seen_source_keys.add(source_key)
            path = dataset_root.joinpath(split, *relative.parts)
            if not path.is_file():
                raise ValueError(f"{dataset_id}: missing image: {split}/{file_name}")
            records.append(
                ImageRecord(
                    dataset_id=dataset_id,
                    split=split,
                    image_id=image.get("id"),
                    file_name=file_name,
                    source_name=source_name,
                    source_key=source_key,
                    width=width,
                    height=height,
                    path=path,
                    annotations=sorted(
                        annotations_by_image.get(image.get("id"), []),
                        key=lambda item: (item.category_name, str(item.annotation_id)),
                    ),
                )
            )
    records.sort(key=lambda item: item.source_key)
    return records


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Cannot read JSONL file {path}: {exc}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
        rows.append(row)
    return rows


def load_source_groups(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in load_jsonl(path):
        group_id = row.get("group_id")
        source_keys = row.get("source_keys")
        if not isinstance(group_id, str) or not isinstance(source_keys, list):
            raise ValueError(f"Malformed source-group row in {path}")
        for source_key in source_keys:
            if not isinstance(source_key, str):
                raise ValueError(f"Non-string source key in {path}")
            previous = mapping.setdefault(source_key, group_id)
            if previous != group_id:
                raise ValueError(f"Source key occurs in multiple groups: {source_key}")
    return mapping


def box_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    left_x, left_y, left_width, left_height = left
    right_x, right_y, right_width, right_height = right
    intersection_x1 = max(left_x, right_x)
    intersection_y1 = max(left_y, right_y)
    intersection_x2 = min(left_x + left_width, right_x + right_width)
    intersection_y2 = min(left_y + left_height, right_y + right_height)
    intersection = max(0.0, intersection_x2 - intersection_x1) * max(
        0.0, intersection_y2 - intersection_y1
    )
    union = left_width * left_height + right_width * right_height - intersection
    return intersection / union if union > 0 else 0.0


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
        "min": round(min(values), 8) if values else None,
        "p10": round(percentile(values, 0.10), 8) if values else None,
        "median": round(percentile(values, 0.50), 8) if values else None,
        "p90": round(percentile(values, 0.90), 8) if values else None,
        "max": round(max(values), 8) if values else None,
    }


def source_type(source_name: str) -> str:
    if re.match(r"^VID_[0-9]{8}_[0-9]{6}-[0-9]+\.(?:jpe?g|png)$", source_name, re.IGNORECASE):
        return "video_frame"
    if re.match(r"^truk[0-9]+\.(?:jpe?g|png)$", source_name, re.IGNORECASE):
        return "timestamp_named_still"
    return "other_still"


def category_signature(annotations: list[AnnotationRecord]) -> str:
    counts = Counter(annotation.category_name for annotation in annotations)
    if not counts:
        return "<none>"
    return "+".join(f"{name}:{counts[name]}" for name in sorted(counts))


def dataset_statistics(
    records: list[ImageRecord], thresholds: dict[str, Any]
) -> tuple[dict[str, Any], list[tuple[ImageRecord, AnnotationRecord, float]], list[tuple[ImageRecord, Any, Any, float]]]:
    area_ratios: list[float] = []
    category_instances: Counter[str] = Counter()
    images_by_category: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    split_images: Counter[str] = Counter()
    source_types: Counter[str] = Counter()
    edge_boxes = 0
    small_boxes = 0
    near_full_boxes = 0
    micro: list[tuple[ImageRecord, AnnotationRecord, float]] = []
    same_class_overlap: list[tuple[ImageRecord, Any, Any, float]] = []
    micro_threshold = float(thresholds["micro_box_area_ratio"])
    small_threshold = float(thresholds["small_box_area_ratio"])
    near_full_threshold = float(thresholds["near_full_frame_area_ratio"])
    edge_tolerance = float(thresholds["edge_tolerance_px"])
    overlap_threshold = float(thresholds["same_class_duplicate_iou"])
    for record in records:
        split_images[record.split] += 1
        source_types[source_type(record.source_name)] += 1
        signatures[category_signature(record.annotations)] += 1
        present_categories = {annotation.category_name for annotation in record.annotations}
        images_by_category.update(present_categories)
        for annotation in record.annotations:
            category_instances[annotation.category_name] += 1
            x, y, width, height = annotation.bbox
            ratio = width * height / (record.width * record.height)
            area_ratios.append(ratio)
            if ratio <= micro_threshold:
                micro.append((record, annotation, ratio))
            if ratio < small_threshold:
                small_boxes += 1
            if ratio >= near_full_threshold:
                near_full_boxes += 1
            if (
                x <= edge_tolerance
                or y <= edge_tolerance
                or x + width >= record.width - edge_tolerance
                or y + height >= record.height - edge_tolerance
            ):
                edge_boxes += 1
        for index, left in enumerate(record.annotations):
            for right in record.annotations[index + 1 :]:
                if left.category_name != right.category_name:
                    continue
                overlap = box_iou(left.bbox, right.bbox)
                if overlap >= overlap_threshold:
                    same_class_overlap.append(
                        (record, left.annotation_id, right.annotation_id, overlap)
                    )
    statistics = {
        "images": len(records),
        "annotations": sum(len(record.annotations) for record in records),
        "zero_annotation_images": sum(not record.annotations for record in records),
        "source_splits": dict(sorted(split_images.items())),
        "source_types": dict(sorted(source_types.items())),
        "category_instances": dict(sorted(category_instances.items())),
        "images_containing_category": dict(sorted(images_by_category.items())),
        "image_category_signatures": dict(
            sorted(signatures.items(), key=lambda item: (-item[1], item[0]))
        ),
        "bbox_area_ratio": numeric_summary(area_ratios),
        "boxes_below_small_threshold": small_boxes,
        "boxes_at_or_below_micro_threshold": len(micro),
        "boxes_at_or_above_near_full_frame_threshold": near_full_boxes,
        "boxes_touching_image_edge": edge_boxes,
        "same_class_high_iou_pairs": len(same_class_overlap),
    }
    return statistics, micro, same_class_overlap


def validate_manual_decisions(
    decisions: list[dict[str, Any]], record_lookup: dict[tuple[str, str], ImageRecord]
) -> dict[tuple[str, str], dict[str, Any]]:
    allowed = {"RELABEL_REQUIRED", "DROP_ANNOTATION", "KEEP_AS_REVIEWED"}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for row in decisions:
        dataset_id = row.get("dataset_id")
        source_key = row.get("source_key")
        decision = row.get("decision")
        if not isinstance(dataset_id, str) or not isinstance(source_key, str):
            raise ValueError("Manual decision needs string dataset_id and source_key")
        if decision not in allowed:
            raise ValueError(f"Unsupported manual decision: {decision}")
        key = (dataset_id, source_key)
        if key in result:
            raise ValueError(f"Duplicate manual decision for {dataset_id}/{source_key}")
        record = record_lookup.get(key)
        if record is None:
            raise ValueError(f"Manual decision target not found: {dataset_id}/{source_key}")
        annotation_ids = row.get("annotation_ids", [])
        if not isinstance(annotation_ids, list):
            raise ValueError(f"annotation_ids must be a list for {dataset_id}/{source_key}")
        known_annotation_ids = {annotation.annotation_id for annotation in record.annotations}
        if not set(annotation_ids).issubset(known_annotation_ids):
            raise ValueError(f"Manual decision references unknown annotation for {dataset_id}/{source_key}")
        if decision == "DROP_ANNOTATION" and not annotation_ids:
            raise ValueError(f"DROP_ANNOTATION needs annotation_ids for {dataset_id}/{source_key}")
        result[key] = row
    return result


def resolve_duplicate_candidates(
    candidates: list[dict[str, Any]],
    review_config: dict[str, Any],
    queue_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize a complete visual decision for the frozen P2-02 queue."""

    config = review_config.get("duplicate_visual_review")
    if not isinstance(config, dict):
        raise ValueError("quality_review requires duplicate_visual_review")
    expected_checksum = config.get("queue_sha256")
    actual_checksum = sha256_file(queue_path)
    if expected_checksum != actual_checksum:
        raise ValueError(
            "P2-02 review queue changed after visual review: "
            f"expected {expected_checksum}, got {actual_checksum}"
        )
    if config.get("default_decision") != "KEEP_SEPARATE":
        raise ValueError("duplicate visual review default must be KEEP_SEPARATE")
    selected_raw = config.get("group_for_split_candidate_numbers")
    if not isinstance(selected_raw, list) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in selected_raw
    ):
        raise ValueError("group_for_split_candidate_numbers must be a list of integers")
    selected = set(selected_raw)
    if len(selected) != len(selected_raw):
        raise ValueError("group_for_split_candidate_numbers contains duplicates")
    invalid = sorted(value for value in selected if value < 1 or value > len(candidates))
    if invalid:
        raise ValueError(f"Duplicate visual review references invalid candidates: {invalid}")

    rows: list[dict[str, Any]] = []
    for candidate_number, candidate in enumerate(candidates, start=1):
        pair_key = "\n".join(
            sorted([candidate["left_group_id"], candidate["right_group_id"]])
        ).encode("utf-8")
        decision = (
            "GROUP_TOGETHER_FOR_SPLIT"
            if candidate_number in selected
            else "KEEP_SEPARATE"
        )
        evidence = candidate.get("evidence", [])
        if decision == "GROUP_TOGETHER_FOR_SPLIT":
            rationale = (
                "Visual identity is consistent with the same vehicle/event; group "
                "conservatively for leakage-safe splitting without deleting either image."
            )
        elif "timestamp_window_review" in evidence:
            rationale = (
                "Temporal proximity alone is insufficient; visual inspection found a "
                "different vehicle or insufficient same-vehicle identity evidence."
            )
        else:
            rationale = (
                "Shared camera/background similarity is insufficient evidence of the same "
                "vehicle event."
            )
        rows.append(
            {
                "decision_id": f"dr-{hashlib.sha256(pair_key).hexdigest()[:16]}",
                "candidate_number": candidate_number,
                "left_group_id": candidate["left_group_id"],
                "right_group_id": candidate["right_group_id"],
                "left_source_key": candidate["left_source_key"],
                "right_source_key": candidate["right_source_key"],
                "candidate_evidence": evidence,
                "decision": decision,
                "rationale": rationale,
                "review_status": config.get("review_status"),
                "reviewed_on": config.get("reviewed_on"),
            }
        )

    decision_counts = Counter(row["decision"] for row in rows)
    evidence_decision_counts = Counter(
        f"{evidence}/{row['decision']}"
        for row in rows
        for evidence in row["candidate_evidence"]
    )
    parent: dict[str, str] = {}

    def find(group_id: str) -> str:
        parent.setdefault(group_id, group_id)
        if parent[group_id] != group_id:
            parent[group_id] = find(parent[group_id])
        return parent[group_id]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for row in rows:
        find(row["left_group_id"])
        find(row["right_group_id"])
        if row["decision"] == "GROUP_TOGETHER_FOR_SPLIT":
            union(row["left_group_id"], row["right_group_id"])
    transitive_conflicts = [
        row["candidate_number"]
        for row in rows
        if row["decision"] == "KEEP_SEPARATE"
        and find(row["left_group_id"]) == find(row["right_group_id"])
    ]
    if transitive_conflicts:
        raise ValueError(
            "Duplicate visual decisions contain transitive KEEP_SEPARATE conflicts: "
            f"{transitive_conflicts}"
        )
    touched_groups = len(parent)
    resolved_components = len({find(group_id) for group_id in parent})
    summary = {
        "queue_sha256": actual_checksum,
        "candidate_group_pairs": len(candidates),
        "reviewed_group_pairs": len(rows),
        "decision_counts": dict(sorted(decision_counts.items())),
        "evidence_decision_counts": dict(sorted(evidence_decision_counts.items())),
        "review_coverage": config.get("review_coverage"),
        "review_status": config.get("review_status"),
        "status": "VISUALLY_RESOLVED_PROVISIONAL",
        "policy": config.get("decision_semantics"),
        "source_groups_touched": touched_groups,
        "post_review_components_among_touched_groups": resolved_components,
        "source_group_reduction": touched_groups - resolved_components,
        "transitive_keep_separate_conflicts": 0,
    }
    return rows, summary


def build_exclusions(
    component_records: list[ImageRecord],
    manual_decisions: dict[tuple[str, str], dict[str, Any]],
    group_for_source: dict[str, str],
    core_names: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    component_id = component_records[0].dataset_id if component_records else ""
    for record in component_records:
        decision = manual_decisions.get((record.dataset_id, record.source_key))
        counts = Counter(annotation.category_name for annotation in record.annotations)
        core_count_mismatch = bool(record.annotations) and (
            counts[core_names[0]] != counts[core_names[1]]
        )
        relabel_required = bool(decision and decision["decision"] == "RELABEL_REQUIRED")
        if not record.annotations or core_count_mismatch or relabel_required:
            reason_codes = ["component_null_image"] if not record.annotations else []
            if core_count_mismatch:
                reason_codes.append("component_core_count_mismatch")
            if decision:
                reason_codes.extend(decision.get("reason_codes", []))
            if relabel_required:
                disposition = "EXCLUDE_PENDING_RELABEL"
            elif core_count_mismatch:
                disposition = "EXCLUDE_PENDING_COMPONENT_COMPLETENESS_REVIEW"
            else:
                disposition = "EXCLUDE_PENDING_NEGATIVE_OR_RELABEL_REVIEW"
            rows.append(
                {
                    "dataset_id": record.dataset_id,
                    "source_key": record.source_key,
                    "source_name": record.source_name,
                    "source_split": record.split,
                    "file_name": record.file_name,
                    "group_id": group_for_source.get(record.source_key),
                    "scope": "image_for_component_training",
                    "disposition": disposition,
                    "reason_codes": sorted(set(reason_codes)),
                    "manual_review_status": decision.get("review_status") if decision else None,
                }
            )
    for (dataset_id, source_key), decision in manual_decisions.items():
        if dataset_id != component_id or decision["decision"] != "DROP_ANNOTATION":
            continue
        record = next(record for record in component_records if record.source_key == source_key)
        rows.append(
            {
                "dataset_id": dataset_id,
                "source_key": source_key,
                "source_name": record.source_name,
                "source_split": record.split,
                "file_name": record.file_name,
                "group_id": group_for_source.get(source_key),
                "scope": "annotations_for_component_training",
                "annotation_ids": decision["annotation_ids"],
                "disposition": "DROP_ANNOTATION_IN_DERIVED_DATASET",
                "reason_codes": sorted(set(decision.get("reason_codes", []))),
                "manual_review_status": decision.get("review_status"),
            }
        )
    rows.sort(key=lambda item: (item["scope"], item["source_key"]))
    return rows


def build_audit(
    registry: dict[str, Any], repository_root: Path
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[tuple[str, str], ImageRecord],
]:
    review_config = registry["quality_review"]
    entries = {entry.get("dataset_id"): entry for entry in registry["datasets"]}
    component_id = review_config.get("component_dataset_id")
    binary_id = review_config.get("binary_dataset_id")
    if component_id not in entries or binary_id not in entries:
        raise ValueError("quality_review dataset IDs must exist in datasets")
    thresholds = review_config.get("thresholds")
    roles = review_config.get("class_roles")
    if not isinstance(thresholds, dict) or not isinstance(roles, dict):
        raise ValueError("quality_review requires thresholds and class_roles objects")

    records_by_dataset: dict[str, list[ImageRecord]] = {}
    provenance: list[dict[str, Any]] = []
    for dataset_id in (component_id, binary_id):
        entry = entries[dataset_id]
        records = load_dataset_records(entry, repository_root)
        records_by_dataset[dataset_id] = records
        checksum_raw = entry.get("checksum_manifest_from_repository")
        checksum_path = resolve_from_repository(repository_root, checksum_raw)
        provenance.append(
            {
                "dataset_id": dataset_id,
                "dataset_root_from_repository": entry["root_from_repository"],
                "checksum_manifest_from_repository": checksum_raw,
                "checksum_manifest_sha256": sha256_file(checksum_path),
            }
        )

    group_path = resolve_from_repository(
        repository_root, review_config["source_group_map_from_repository"]
    )
    duplicate_queue_path = resolve_from_repository(
        repository_root, review_config["duplicate_review_queue_from_repository"]
    )
    decision_path = resolve_from_repository(
        repository_root, review_config["manual_review_decisions_from_repository"]
    )
    group_for_source = load_source_groups(group_path)
    duplicate_candidates = load_jsonl(duplicate_queue_path)
    duplicate_decisions, duplicate_review_summary = resolve_duplicate_candidates(
        duplicate_candidates, review_config, duplicate_queue_path
    )
    record_lookup = {
        (record.dataset_id, record.source_key): record
        for records in records_by_dataset.values()
        for record in records
    }
    decisions = validate_manual_decisions(load_jsonl(decision_path), record_lookup)
    accumulator = CandidateAccumulator(group_for_source)

    dataset_reports: dict[str, Any] = {}
    for dataset_id, records in records_by_dataset.items():
        statistics, micro_boxes, same_class_overlap = dataset_statistics(records, thresholds)
        dataset_reports[dataset_id] = statistics
        for record, annotation, ratio in micro_boxes:
            accumulator.add(
                record,
                reason_code="micro_bbox",
                priority="critical",
                recommended_action="QUARANTINE_ANNOTATION",
                evidence={
                    "annotation_id": annotation.annotation_id,
                    "category_name": annotation.category_name,
                    "bbox": list(annotation.bbox),
                    "bbox_area_ratio": round(ratio, 10),
                },
                annotation_ids=[annotation.annotation_id],
            )
        for record, left_id, right_id, overlap in same_class_overlap:
            accumulator.add(
                record,
                reason_code="probable_duplicate_same_class_bbox",
                priority="high",
                recommended_action="REVIEW_IMAGE",
                evidence={
                    "annotation_ids": [left_id, right_id],
                    "iou": round(overlap, 6),
                },
                annotation_ids=[left_id, right_id],
            )

    component_records = records_by_dataset[component_id]
    binary_records = records_by_dataset[binary_id]
    component_by_source = {record.source_key: record for record in component_records}
    binary_by_source = {record.source_key: record for record in binary_records}
    core_names = roles.get("component_core")
    if not isinstance(core_names, list) or len(core_names) != 2:
        raise ValueError("quality_review.class_roles.component_core must contain two names")
    for record in component_records:
        counts = Counter(annotation.category_name for annotation in record.annotations)
        if not record.annotations:
            accumulator.add(
                record,
                reason_code="component_null_image",
                priority="high",
                recommended_action="QUARANTINE_IMAGE",
                evidence={"annotation_count": 0},
            )
        if record.annotations and counts[core_names[0]] != counts[core_names[1]]:
            accumulator.add(
                record,
                reason_code="component_core_count_mismatch",
                priority="medium",
                recommended_action="REVIEW_IMAGE",
                evidence={name: counts[name] for name in core_names},
            )

    near_full_threshold = float(thresholds["near_full_frame_area_ratio"])
    for record in binary_records:
        labels = sorted({annotation.category_name for annotation in record.annotations})
        if len(labels) > 1:
            accumulator.add(
                record,
                reason_code="mixed_binary_labels",
                priority="low",
                recommended_action="REVIEW_IMAGE",
                evidence={"labels": labels, "annotation_count": len(record.annotations)},
            )
        near_full = [
            annotation
            for annotation in record.annotations
            if annotation.bbox[2] * annotation.bbox[3] / (record.width * record.height)
            >= near_full_threshold
        ]
        if near_full:
            accumulator.add(
                record,
                reason_code="near_full_frame_bbox",
                priority="medium",
                recommended_action="REVIEW_IMAGE",
                evidence={"annotation_ids": [annotation.annotation_id for annotation in near_full]},
                annotation_ids=[annotation.annotation_id for annotation in near_full],
            )

    shared_keys = sorted(component_by_source.keys() & binary_by_source.keys())
    null_binary_signatures: Counter[str] = Counter()
    component_presence_vs_binary: Counter[str] = Counter()
    for source_key in shared_keys:
        component = component_by_source[source_key]
        binary = binary_by_source[source_key]
        binary_labels = sorted({annotation.category_name for annotation in binary.annotations})
        if not component.annotations:
            signature = "+".join(binary_labels)
            null_binary_signatures[signature] += 1
            if "ODOL" in binary_labels:
                accumulator.add(
                    component,
                    reason_code="component_null_with_binary_odol",
                    priority="critical",
                    recommended_action="QUARANTINE_IMAGE",
                    evidence={"alternate_binary_labels": binary_labels},
                )
        component_labels = {annotation.category_name for annotation in component.annotations}
        key = (
            ("component_present" if component_labels else "component_null")
            + "/"
            + ("muatan_present" if "muatan" in component_labels else "muatan_absent")
            + "/"
            + "+".join(binary_labels)
        )
        component_presence_vs_binary[key] += 1

    for (dataset_id, source_key), decision in decisions.items():
        record = record_lookup[(dataset_id, source_key)]
        if decision["decision"] == "RELABEL_REQUIRED":
            action = "RELABEL_IMAGE"
        elif decision["decision"] == "DROP_ANNOTATION":
            action = "QUARANTINE_ANNOTATION"
        else:
            action = "REVIEW_IMAGE"
        for reason_code in decision.get("reason_codes", ["manual_review_decision"]):
            accumulator.add(
                record,
                reason_code=reason_code,
                priority="critical" if decision["decision"] != "KEEP_AS_REVIEWED" else "low",
                recommended_action=action,
                evidence={
                    "decision": decision["decision"],
                    "review_status": decision.get("review_status"),
                    "notes": decision.get("notes"),
                },
                annotation_ids=decision.get("annotation_ids", []),
            )

    candidate_rows = accumulator.rows()
    for row in candidate_rows:
        decision = decisions.get((row["dataset_id"], row["source_key"]))
        row["manual_decision"] = decision["decision"] if decision else None
        row["manual_review_status"] = decision.get("review_status") if decision else None
    exclusions = build_exclusions(component_records, decisions, group_for_source, core_names)

    priority_counts = Counter(row["priority"] for row in candidate_rows)
    reason_counts = Counter(
        reason for row in candidate_rows for reason in row["reason_codes"]
    )
    duplicate_evidence = Counter(
        evidence
        for row in duplicate_candidates
        for evidence in row.get("evidence", [])
    )
    report = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "audit_date": review_config.get("audit_date"),
        "result": "COMPLETE_WITH_REMEDIATION_REQUIRED" if exclusions else "COMPLETE",
        "raw_data_modified": False,
        "provenance": provenance,
        "methodology": {
            "full_structural_coverage": True,
            "heuristics_are_decision_gates": False,
            "heuristics_create_review_candidates": True,
            "manual_review_status": "provisional_ai_assisted",
            "raw_annotations_are_never_rewritten": True,
            "thresholds": thresholds,
        },
        "datasets": dataset_reports,
        "cross_dataset": {
            "shared_source_identities": len(shared_keys),
            "component_only_source_identities": len(component_by_source.keys() - binary_by_source.keys()),
            "binary_only_source_identities": len(binary_by_source.keys() - component_by_source.keys()),
            "component_null_shared_images_by_binary_label_signature": dict(
                sorted(null_binary_signatures.items())
            ),
            "component_presence_vs_binary_label": dict(
                sorted(component_presence_vs_binary.items())
            ),
        },
        "review_queue": {
            "candidate_images": len(candidate_rows),
            "priority_counts": dict(sorted(priority_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "manual_decisions": len(decisions),
            "manual_decision_counts": dict(
                sorted(Counter(row["decision"] for row in decisions.values()).items())
            ),
        },
        "rule_resolutions": {
            "component_null_image": {
                "candidate_images": reason_counts.get("component_null_image", 0),
                "resolution": "QUARANTINE_PENDING_NEGATIVE_OR_RELABEL_REVIEW",
                "basis": "structural audit plus complete contact-sheet visual scan",
                "review_status": "provisional_ai_assisted",
            },
            "component_core_count_mismatch": {
                "candidate_images": reason_counts.get("component_core_count_mismatch", 0),
                "resolution": "QUARANTINE_PENDING_COMPONENT_COMPLETENESS_REVIEW",
                "basis": "complete contact-sheet visual scan found incomplete or ambiguous component coverage",
                "review_status": "provisional_ai_assisted",
            },
            "micro_bbox": {
                "candidate_images": reason_counts.get("micro_bbox", 0),
                "resolution": "DROP_CONFIRMED_ANNOTATION_IN_DERIVED_DATASET",
                "basis": "full-image visual inspection confirmed an isolated point-like artefact",
                "review_status": "provisional_ai_assisted",
            },
            "near_full_frame_bbox": {
                "candidate_images": reason_counts.get("near_full_frame_bbox", 0),
                "resolution": "RETAIN_PROVISIONALLY_WITH_CONTEXT_RISK_FLAG",
                "basis": "complete contact-sheet visual scan found predominantly legitimate close-cropped vehicles",
                "review_status": "provisional_ai_assisted",
            },
            "mixed_binary_labels": {
                "candidate_images": reason_counts.get("mixed_binary_labels", 0),
                "resolution": "RETAIN_PROVISIONALLY",
                "basis": "complete contact-sheet visual scan found distinct vehicles carrying distinct boxes",
                "review_status": "provisional_ai_assisted",
            },
        },
        "training_exclusions": {
            "rows": len(exclusions),
            "image_scope_rows": sum(row["scope"] == "image_for_component_training" for row in exclusions),
            "annotation_scope_rows": sum(
                row["scope"] == "annotations_for_component_training" for row in exclusions
            ),
            "policy": "Apply only to a derived dataset; raw COCO remains immutable.",
        },
        "duplicate_review_handoff": {
            **duplicate_review_summary,
            "evidence_counts": dict(sorted(duplicate_evidence.items())),
            "image_deletions": 0,
        },
        "interpretation_limits": [
            "ODOL/NOT ODOL is visual-suspicion supervision, not legal or metrological ground truth.",
            "The component labels have no supplied annotation guide, so inferred class semantics remain provisional.",
            "A mixed ODOL/NOT ODOL image can validly contain multiple vehicles and is not automatically an error.",
            "Edge-touching and very large boxes may be legitimate truncation/close-up cases.",
            "AI-assisted visual decisions require human confirmation before a company release dataset.",
        ],
    }
    return (
        report,
        candidate_rows,
        exclusions,
        duplicate_candidates,
        duplicate_decisions,
        record_lookup,
    )


def compact_json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = "".join(f"{compact_json(row)}\n" for row in rows)
    path.write_text(content, encoding="utf-8")


def dataset_alias(dataset_id: str, component_id: str) -> str:
    return "B1" if dataset_id == component_id else "B2"


def render_tile(
    command: str,
    record: ImageRecord,
    overlays: list[AnnotationRecord],
    caption: str,
    output_path: Path,
    width: int,
    height: int,
    quality: int,
) -> None:
    image_height = height - 42
    arguments = [
        command,
        str(record.path),
        "-auto-orient",
        "-resize",
        f"{width}x{image_height}!",
        "-colorspace",
        "sRGB",
    ]
    scale_x = width / record.width
    scale_y = image_height / record.height
    for annotation in overlays:
        x, y, box_width, box_height = annotation.bbox
        x1 = max(0.0, min(width - 1.0, x * scale_x))
        y1 = max(0.0, min(image_height - 1.0, y * scale_y))
        x2 = max(0.0, min(width - 1.0, (x + box_width) * scale_x))
        y2 = max(0.0, min(image_height - 1.0, (y + box_height) * scale_y))
        arguments.extend(
            [
                "-stroke",
                CLASS_COLORS.get(annotation.category_name, "#ffffff"),
                "-strokewidth",
                "3",
                "-fill",
                "none",
                "-draw",
                f"rectangle {x1:.2f},{y1:.2f} {x2:.2f},{y2:.2f}",
            ]
        )
    arguments.extend(
        [
            "-gravity",
            "south",
            "-background",
            "#15171a",
            "-splice",
            "0x42",
            "-gravity",
            "southwest",
            "-fill",
            "white",
            "-stroke",
            "none",
            "-pointsize",
            "11",
            "-annotate",
            "+6+5",
            caption,
            "-quality",
            str(quality),
            str(output_path),
        ]
    )
    environment = os.environ.copy()
    environment["MAGICK_THREAD_LIMIT"] = "1"
    process = subprocess.run(
        arguments, check=False, capture_output=True, text=True, timeout=45, env=environment
    )
    if process.returncode != 0:
        raise RuntimeError(f"ImageMagick tile render failed for {record.source_name}: {process.stderr.strip()}")


def montage_page(
    command: str,
    tiles: list[Path],
    columns: int,
    rows: int,
    output_path: Path,
) -> None:
    process = subprocess.run(
        [
            command,
            "montage",
            *[str(path) for path in tiles],
            "-tile",
            f"{columns}x{rows}",
            "-geometry",
            "+4+4",
            "-background",
            "#202124",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if process.returncode != 0:
        raise RuntimeError(f"ImageMagick montage failed: {process.stderr.strip()}")


def chunked(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def render_gallery(
    gallery_dir: Path,
    review_config: dict[str, Any],
    candidates: list[dict[str, Any]],
    duplicate_candidates: list[dict[str, Any]],
    record_lookup: dict[tuple[str, str], ImageRecord],
    workers: int,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    gallery_config = review_config["gallery"]
    command_name = review_config.get("image_renderer", {}).get("command", "magick")
    command = shutil.which(command_name)
    if command is None:
        raise ValueError(f"Image renderer is unavailable: {command_name}")
    gallery_dir.mkdir(parents=True, exist_ok=True)
    quality_dir = gallery_dir / "quality-sheets"
    duplicate_dir = gallery_dir / "duplicate-pair-sheets"
    quality_dir.mkdir(exist_ok=True)
    duplicate_dir.mkdir(exist_ok=True)
    width = int(gallery_config["tile_width"])
    height = int(gallery_config["tile_height"])
    quality = int(gallery_config["jpeg_quality"])
    component_id = review_config["component_dataset_id"]
    binary_id = review_config["binary_dataset_id"]
    binary_by_source = {
        source_key: record
        for (dataset_id, source_key), record in record_lookup.items()
        if dataset_id == binary_id
    }
    component_by_source = {
        source_key: record
        for (dataset_id, source_key), record in record_lookup.items()
        if dataset_id == component_id
    }

    quality_columns = int(gallery_config["quality_sheet_columns"])
    quality_rows = int(gallery_config["quality_sheet_rows"])
    quality_page_size = quality_columns * quality_rows
    duplicate_columns = int(gallery_config["duplicate_sheet_columns"])
    duplicate_rows = int(gallery_config["duplicate_sheet_rows"])
    if duplicate_columns != 2:
        raise ValueError("Duplicate sheets require exactly two columns")
    quality_outputs: list[Path] = []
    duplicate_outputs: list[Path] = []

    with tempfile.TemporaryDirectory(prefix="p2-03-tiles-", dir=gallery_dir) as temporary:
        temporary_dir = Path(temporary)
        quality_jobs: list[tuple[dict[str, Any], ImageRecord, list[AnnotationRecord], Path, str]] = []
        for index, candidate in enumerate(candidates, start=1):
            record = record_lookup[(candidate["dataset_id"], candidate["source_key"])]
            overlays = record.annotations
            overlay_origin = dataset_alias(record.dataset_id, component_id)
            if record.dataset_id == component_id and not overlays and record.source_key in binary_by_source:
                overlays = binary_by_source[record.source_key].annotations
                overlay_origin = "B2 overlay"
            reason = candidate["reason_codes"][0]
            caption = (
                f"Q{index:03d} {dataset_alias(record.dataset_id, component_id)} "
                f"{candidate['priority'].upper()} | {reason}\n"
                f"{record.source_name[:47]} | boxes: {overlay_origin}"
            )
            tile_path = temporary_dir / f"quality-{index:04d}.jpg"
            quality_jobs.append((candidate, record, overlays, tile_path, caption))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    render_tile,
                    command,
                    record,
                    overlays,
                    caption,
                    tile_path,
                    width,
                    height,
                    quality,
                ): tile_path
                for _, record, overlays, tile_path, caption in quality_jobs
            }
            for future in as_completed(futures):
                future.result()
        quality_tiles = [job[3] for job in quality_jobs]
        for page_number, page_tiles in enumerate(chunked(quality_tiles, quality_page_size), start=1):
            output = quality_dir / f"quality-{page_number:03d}.jpg"
            montage_page(command, page_tiles, quality_columns, quality_rows, output)
            quality_outputs.append(output)

        duplicate_jobs: list[tuple[ImageRecord, Path, str]] = []
        for index, candidate in enumerate(duplicate_candidates, start=1):
            left = component_by_source.get(candidate.get("left_source_key"))
            right = component_by_source.get(candidate.get("right_source_key"))
            if left is None or right is None:
                continue
            evidence = "+".join(candidate.get("evidence", []))
            for side, record in (("L", left), ("R", right)):
                tile_path = temporary_dir / f"duplicate-{index:04d}-{side}.jpg"
                caption = (
                    f"D{index:03d} {side} | support={candidate.get('supporting_pair_count', 1)}\n"
                    f"{record.source_name[:47]} | {evidence[:34]}"
                )
                duplicate_jobs.append((record, tile_path, caption))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    render_tile,
                    command,
                    record,
                    record.annotations,
                    caption,
                    tile_path,
                    width,
                    height,
                    quality,
                ): tile_path
                for record, tile_path, caption in duplicate_jobs
            }
            for future in as_completed(futures):
                future.result()
        duplicate_tiles = [job[1] for job in duplicate_jobs]
        duplicate_page_size = duplicate_columns * duplicate_rows
        for page_number, page_tiles in enumerate(
            chunked(duplicate_tiles, duplicate_page_size), start=1
        ):
            output = duplicate_dir / f"duplicate-{page_number:03d}.jpg"
            montage_page(command, page_tiles, duplicate_columns, duplicate_rows, output)
            duplicate_outputs.append(output)

    index_lines = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>P2-03 label quality gallery</title>",
        "<style>body{font-family:system-ui;background:#111;color:#eee;max-width:1100px;margin:2rem auto;padding:0 1rem}a{color:#8ab4f8}li{margin:.45rem 0}.warn{color:#ffcc66}code{background:#222;padding:.15rem .3rem}</style>",
        "</head><body>",
        "<h1>P2-03 label quality gallery</h1>",
        '<p class="warn">Local review artifact. Do not publish these images or treat ODOL labels as legal ground truth.</p>',
        f"<p>Quality candidates: <strong>{len(candidates)}</strong>. Duplicate group-pair candidates: <strong>{len(duplicate_candidates)}</strong>.</p>",
        "<p>Box colors: cyan=boxtruck, yellow=headtruck, magenta=muatan, red=ODOL, green=NOT ODOL.</p>",
        "<h2>Quality candidate sheets</h2><ol>",
    ]
    for path in quality_outputs:
        relative = path.relative_to(gallery_dir).as_posix()
        index_lines.append(f'<li><a href="{html.escape(relative)}">{html.escape(path.name)}</a></li>')
    index_lines.extend(["</ol>", "<h2>Duplicate pair sheets</h2><ol>"])
    for path in duplicate_outputs:
        relative = path.relative_to(gallery_dir).as_posix()
        index_lines.append(f'<li><a href="{html.escape(relative)}">{html.escape(path.name)}</a></li>')
    index_lines.extend(["</ol></body></html>", ""])
    (gallery_dir / "index.html").write_text("\n".join(index_lines), encoding="utf-8")
    readme = f"""# P2-03 local label-quality gallery

Generated by `scripts/audit_label_quality.py` on {review_config.get('audit_date')}.

- quality candidates: {len(candidates)}
- duplicate pair candidates: {len(duplicate_candidates)}
- quality sheets: {len(quality_outputs)}
- duplicate pair sheets: {len(duplicate_outputs)}

Open `index.html` locally. This directory is outside Git. Raw images were read but
not modified. B2 images remain subject to CC BY 4.0 attribution to Daris Irfan
Putrawanto / ODOL Vehicle Detection Dataset on Roboflow Universe.
"""
    (gallery_dir / "README.md").write_text(readme, encoding="utf-8")
    return {
        "gallery_dir": str(gallery_dir),
        "quality_sheets": len(quality_outputs),
        "duplicate_pair_sheets": len(duplicate_outputs),
        "imagemagick_command": command,
    }


def markdown_summary(report: dict[str, Any], gallery: dict[str, Any] | None) -> str:
    component_id = report["provenance"][0]["dataset_id"]
    binary_id = report["provenance"][1]["dataset_id"]
    component = report["datasets"][component_id]
    binary = report["datasets"][binary_id]
    queue = report["review_queue"]
    exclusions = report["training_exclusions"]
    cross = report["cross_dataset"]
    resolutions = report["rule_resolutions"]
    duplicate_review = report["duplicate_review_handoff"]
    gallery_text = (
        f"`{gallery['gallery_dir_from_repository']}/index.html`"
        if gallery is not None
        else "not rendered (`--skip-gallery`)"
    )
    return f"""# P2-03 Label Quality Audit

**Result:** `{report['result']}`

Raw COCO dibaca tanpa diubah. Keputusan koreksi diterapkan nanti pada derived
dataset, bukan pada snapshot raw.

## Ringkasan

| Metric | B1 component | B2 visual suspicion |
|---|---:|---:|
| Images | {component['images']} | {binary['images']} |
| Annotations | {component['annotations']} | {binary['annotations']} |
| Null images | {component['zero_annotation_images']} | {binary['zero_annotation_images']} |
| Small boxes (< configured threshold) | {component['boxes_below_small_threshold']} | {binary['boxes_below_small_threshold']} |
| Micro boxes | {component['boxes_at_or_below_micro_threshold']} | {binary['boxes_at_or_below_micro_threshold']} |
| Near-full-frame boxes | {component['boxes_at_or_above_near_full_frame_threshold']} | {binary['boxes_at_or_above_near_full_frame_threshold']} |
| Edge-touching boxes | {component['boxes_touching_image_edge']} | {binary['boxes_touching_image_edge']} |
| Same-class high-IoU pairs | {component['same_class_high_iou_pairs']} | {binary['same_class_high_iou_pairs']} |

## Findings yang mengubah keputusan training

- Seluruh **{component['zero_annotation_images']} B1 null images** dikarantina dari
  component training sampai dipastikan valid negative atau selesai direlabel.
- Seluruh **{resolutions['component_core_count_mismatch']['candidate_images']} B1 images**
  dengan jumlah pasangan `boxtruck`/`headtruck` tidak seimbang juga dikarantina
  sampai kelengkapan komponennya dikonfirmasi. Ini mencegah partial annotation
  diperlakukan sebagai negative supervision.
- Dari shared source, null B1 terdiri dari label B2:
  `{cross['component_null_shared_images_by_binary_label_signature']}`. Kehadiran
  ODOL pada B2 bukan bukti legal, tetapi cukup kuat sebagai sinyal missing-label
  untuk review prioritas tinggi.
- Ada **{component['boxes_at_or_below_micro_threshold']} micro bbox** B1. Visual
  review menetapkan annotation 227 pada `VID_20210816_165022-16.jpg` sebagai
  point-like artefact untuk dibuang hanya pada derived dataset.
- Ada **{queue['manual_decisions']} keputusan visual provisional**;
  `{queue['manual_decision_counts']}`. Status AI-assisted ini tetap memerlukan
  sign-off manusia sebelum release perusahaan.
- B2 tetap hanya baseline visual suspicion. Label ODOL/NOT ODOL tidak memiliki
  pengukuran fisik atau definisi legal yang dapat diaudit dari export.

## Resolusi rule-level hasil visual scan

| Candidate rule | Images | Resolusi sementara |
|---|---:|---|
| B1 null image | {resolutions['component_null_image']['candidate_images']} | quarantine; bedakan valid negative vs perlu relabel |
| B1 core-count mismatch | {resolutions['component_core_count_mismatch']['candidate_images']} | quarantine; review kelengkapan komponen |
| B1 micro bbox | {resolutions['micro_bbox']['candidate_images']} | drop annotation terkonfirmasi pada turunan |
| B2 near-full-frame bbox | {resolutions['near_full_frame_bbox']['candidate_images']} | retain provisional; tandai risiko context shortcut |
| B2 mixed labels | {resolutions['mixed_binary_labels']['candidate_images']} | retain provisional; box terlihat milik kendaraan berbeda |

Resolusi ini berasal dari structural audit dan pemeriksaan seluruh 15 contact
sheet candidate. Statusnya `provisional_ai_assisted`, sehingga perubahan label
tetap membutuhkan sign-off manusia sebelum dataset release perusahaan.

## Gate dan artifact

- Review candidates: **{queue['candidate_images']}** images,
  priority `{queue['priority_counts']}`.
- Training exclusion rows: **{exclusions['rows']}**
  ({exclusions['image_scope_rows']} image scope,
  {exclusions['annotation_scope_rows']} annotation scope).
- Seluruh **{duplicate_review['reviewed_group_pairs']}** pasangan kandidat P2-02
  sudah memperoleh keputusan visual provisional:
  `{duplicate_review['decision_counts']}`. Keputusan group hanya berlaku sebagai
  constraint split bebas leakage; tidak ada image yang dihapus.
- Local contact-sheet gallery: {gallery_text}.

## Interpretasi aman

`muatan` berarti bagian muatan yang tampak menurut anotator publik; kelas ini
tidak otomatis berarti pelanggaran dimensi. B1 lebih berguna untuk bootstrap
persepsi komponen/geometry relatif. B2 berguna sebagai comparator eksperimen,
bukan ground truth penindakan atau pengukuran meter.

## Reproduksi

```bash
.venv/bin/python scripts/audit_label_quality.py
```
"""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        registry = load_registry(args.config.resolve())
        (
            report,
            candidates,
            exclusions,
            duplicate_candidates,
            duplicate_decisions,
            record_lookup,
        ) = build_audit(registry, repository_root)
        gallery: dict[str, Any] | None = None
        if not args.skip_gallery:
            gallery_dir = args.gallery_dir
            if gallery_dir is None:
                gallery_dir = resolve_from_repository(
                    repository_root,
                    registry["quality_review"]["gallery_output_from_repository"],
                )
            resolved_gallery_dir = gallery_dir.resolve()
            gallery = render_gallery(
                resolved_gallery_dir,
                registry["quality_review"],
                candidates,
                duplicate_candidates,
                record_lookup,
                args.workers,
            )
            gallery["gallery_dir_from_repository"] = Path(
                os.path.relpath(resolved_gallery_dir, repository_root)
            ).as_posix()
            gallery.pop("gallery_dir", None)
            report["local_gallery"] = gallery
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "label-quality-audit.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_jsonl(args.output_dir / "review-queue.jsonl", candidates)
        write_jsonl(args.output_dir / "training-exclusions.jsonl", exclusions)
        write_jsonl(
            args.output_dir / "duplicate-review-decisions.jsonl", duplicate_decisions
        )
        (args.output_dir / "SUMMARY.md").write_text(
            markdown_summary(report, gallery), encoding="utf-8"
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"P2-03 {report['result']}: {len(candidates)} review candidates, "
        f"{len(exclusions)} exclusion rows"
    )
    if gallery is not None:
        print(f"Local gallery: {resolved_gallery_dir}/index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
