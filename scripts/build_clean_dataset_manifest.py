#!/usr/bin/env python3
"""Build the P2-04 clean-dataset contract without copying or mutating pixels.

The output is a decision manifest, not a materialized training dataset. It joins
the audited raw COCO records, P2-02 source groups, and P2-03 visual decisions;
then it emits final split-group constraints and per-record USE/REPAIR/QUARANTINE
actions for the next phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "1.0.0"
ALLOWED_RECORD_DECISIONS = {"USE", "REPAIR_AND_USE", "QUARANTINE", "USE_PROVISIONALLY"}
ALLOWED_DUPLICATE_DECISIONS = {"GROUP_TOGETHER_FOR_SPLIT", "KEEP_SEPARATE"}


@dataclass(frozen=True)
class RawRecord:
    dataset_id: str
    source_key: str
    source_name: str
    source_split: str
    image_id: Any
    file_name: str
    raw_sha256: str
    width: int
    height: int
    raw_annotation_ids: tuple[Any, ...]
    raw_category_counts: dict[str, int]
    raw_image_from_repository: str


class UnionFind:
    def __init__(self, values: Iterable[str] = ()) -> None:
        self.parent: dict[str, str] = {value: value for value in values}

    def find(self, value: str) -> str:
        if value not in self.parent:
            raise ValueError(f"Unknown source group: {value}")
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=repository_root / "configs/bootstrap_external/p2-04-clean-policy.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "phases/00_external_bootstrap/reports/p2-04",
    )
    return parser.parse_args(argv)


def resolve_path(repository_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return (repository_root / path).resolve() if not path.is_absolute() else path.resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Required file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}")
    return payload


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"Expected an object at {path}:{line_number}")
                rows.append(row)
    except FileNotFoundError as exc:
        raise ValueError(f"Required file is missing: {path}") from exc
    return rows


def compact_json(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text("".join(f"{compact_json(row)}\n" for row in rows), encoding="utf-8")


def validate_upstream(
    policy: dict[str, Any], repository_root: Path
) -> tuple[dict[str, Path], list[dict[str, str]]]:
    if policy.get("schema_version") != 1:
        raise ValueError("Clean policy schema_version must be 1")
    artifacts = policy.get("upstream_artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Clean policy requires upstream_artifacts")
    paths: dict[str, Path] = {}
    provenance: list[dict[str, str]] = []
    for name, entry in sorted(artifacts.items()):
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid upstream artifact entry: {name}")
        relative_path = entry.get("path_from_repository")
        expected = entry.get("sha256")
        if not isinstance(relative_path, str) or not isinstance(expected, str):
            raise ValueError(f"Upstream artifact {name} needs path and sha256")
        path = resolve_path(repository_root, relative_path)
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"Upstream artifact changed: {name}; expected {expected}, got {actual}"
            )
        paths[name] = path
        provenance.append(
            {"artifact": name, "path_from_repository": relative_path, "sha256": actual}
        )
    return paths, provenance


def verify_raw_integrity(
    registry: dict[str, Any],
    expected_manifest_hashes: dict[str, Any],
    repository_root: Path,
) -> list[dict[str, Any]]:
    datasets = registry.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError("Dataset registry has no datasets")
    dataset_ids = {row.get("dataset_id") for row in datasets}
    if set(expected_manifest_hashes) != dataset_ids:
        raise ValueError("Raw integrity hash coverage must exactly match registered datasets")
    summaries: list[dict[str, Any]] = []
    for dataset in datasets:
        dataset_id = dataset["dataset_id"]
        root_raw = dataset.get("root_from_repository")
        manifest_raw = dataset.get("checksum_manifest_from_repository")
        if not isinstance(root_raw, str) or not isinstance(manifest_raw, str):
            raise ValueError(f"Dataset {dataset_id} lacks raw integrity paths")
        if Path(root_raw).is_absolute() or Path(manifest_raw).is_absolute():
            raise ValueError(f"Dataset {dataset_id} integrity paths must be repository-relative")
        root = resolve_path(repository_root, root_raw)
        manifest_path = resolve_path(repository_root, manifest_raw)
        expected_manifest_sha = expected_manifest_hashes[dataset_id]
        if not isinstance(expected_manifest_sha, str):
            raise ValueError(f"Dataset {dataset_id} integrity manifest hash must be a string")
        actual_manifest_sha = sha256_file(manifest_path)
        if actual_manifest_sha != expected_manifest_sha:
            raise ValueError(
                f"Raw integrity manifest changed for {dataset_id}: expected "
                f"{expected_manifest_sha}, got {actual_manifest_sha}"
            )

        entries: dict[str, str] = {}
        for line_number, line in enumerate(
            manifest_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                checksum, relative_raw = line.split("  ", 1)
            except ValueError as exc:
                raise ValueError(
                    f"Malformed checksum manifest at {manifest_path}:{line_number}"
                ) from exc
            if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
                raise ValueError(f"Invalid SHA-256 at {manifest_path}:{line_number}")
            relative = Path(relative_raw.removeprefix("./"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe checksum path at {manifest_path}:{line_number}")
            key = relative.as_posix()
            if key in entries:
                raise ValueError(f"Duplicate checksum entry: {dataset_id}/{key}")
            entries[key] = checksum

        actual_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        if set(entries) != actual_files:
            missing = sorted(actual_files - set(entries))
            stale = sorted(set(entries) - actual_files)
            raise ValueError(
                f"Raw integrity coverage mismatch for {dataset_id}; "
                f"unlisted={missing[:3]}, missing_files={stale[:3]}"
            )
        for relative, expected_file_sha in entries.items():
            actual_file_sha = sha256_file(root / relative)
            if actual_file_sha != expected_file_sha:
                raise ValueError(
                    f"Raw file checksum mismatch for {dataset_id}/{relative}: "
                    f"expected {expected_file_sha}, got {actual_file_sha}"
                )
        summaries.append(
            {
                "dataset_id": dataset_id,
                "checksum_manifest_from_repository": manifest_raw,
                "checksum_manifest_sha256": actual_manifest_sha,
                "verified_files": len(entries),
                "status": "PASS",
            }
        )
    return sorted(summaries, key=lambda row: row["dataset_id"])


def load_source_model(
    source_group_rows: list[dict[str, Any]],
    identity_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    groups: dict[str, dict[str, Any]] = {}
    source_to_group: dict[str, str] = {}
    for row in source_group_rows:
        group_id = row.get("group_id")
        source_keys = row.get("source_keys")
        if not isinstance(group_id, str) or not isinstance(source_keys, list) or not source_keys:
            raise ValueError("Every source-group row needs group_id and non-empty source_keys")
        if group_id in groups:
            raise ValueError(f"Duplicate source group: {group_id}")
        groups[group_id] = row
        for source_key in source_keys:
            if source_key in source_to_group:
                raise ValueError(f"Source identity occurs in multiple groups: {source_key}")
            source_to_group[source_key] = group_id

    identities: dict[tuple[str, str], dict[str, Any]] = {}
    seen_source_keys: set[str] = set()
    for row in identity_rows:
        source_key = row.get("source_key")
        group_id = row.get("group_id")
        records = row.get("records")
        if not isinstance(source_key, str) or not isinstance(group_id, str):
            raise ValueError("Every identity row needs source_key and group_id")
        if source_to_group.get(source_key) != group_id:
            raise ValueError(f"Identity/source-group mismatch for {source_key}")
        if source_key in seen_source_keys:
            raise ValueError(f"Duplicate identity row: {source_key}")
        seen_source_keys.add(source_key)
        if not isinstance(records, list) or not records:
            raise ValueError(f"Identity has no dataset records: {source_key}")
        for record in records:
            dataset_id = record.get("dataset_id")
            if not isinstance(dataset_id, str):
                raise ValueError(f"Identity record lacks dataset_id: {source_key}")
            key = (dataset_id, source_key)
            if key in identities:
                raise ValueError(f"Duplicate dataset/source record: {dataset_id}/{source_key}")
            identities[key] = {**record, "source_key": source_key, "group_id": group_id}
    if seen_source_keys != set(source_to_group):
        missing = sorted(set(source_to_group) - seen_source_keys)
        extra = sorted(seen_source_keys - set(source_to_group))
        raise ValueError(f"Source model coverage mismatch; missing={missing[:3]}, extra={extra[:3]}")
    return groups, identities


def resolve_source_groups(
    groups: dict[str, dict[str, Any]],
    duplicate_decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    union_find = UnionFind(groups)
    seen_decision_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for row in duplicate_decisions:
        decision_id = row.get("decision_id")
        decision = row.get("decision")
        left = row.get("left_group_id")
        right = row.get("right_group_id")
        if not all(isinstance(value, str) for value in (decision_id, decision, left, right)):
            raise ValueError("Malformed duplicate decision row")
        if decision_id in seen_decision_ids:
            raise ValueError(f"Duplicate duplicate-review decision ID: {decision_id}")
        seen_decision_ids.add(decision_id)
        if decision not in ALLOWED_DUPLICATE_DECISIONS:
            raise ValueError(f"Unsupported duplicate decision: {decision}")
        union_find.find(left)
        union_find.find(right)
        pair = tuple(sorted((left, right)))
        if pair in seen_pairs:
            raise ValueError(f"Duplicate duplicate-review group pair: {pair}")
        seen_pairs.add(pair)
        if decision == "GROUP_TOGETHER_FOR_SPLIT":
            union_find.union(left, right)

    conflicts = [
        row["decision_id"]
        for row in duplicate_decisions
        if row["decision"] == "KEEP_SEPARATE"
        and union_find.find(row["left_group_id"]) == union_find.find(row["right_group_id"])
    ]
    if conflicts:
        raise ValueError(f"Transitive KEEP_SEPARATE conflicts: {conflicts[:10]}")

    component_groups: dict[str, list[str]] = defaultdict(list)
    for group_id in groups:
        component_groups[union_find.find(group_id)].append(group_id)

    resolved_rows: list[dict[str, Any]] = []
    base_to_final: dict[str, str] = {}
    all_source_keys: set[str] = set()
    final_group_ids: set[str] = set()
    for base_group_ids in component_groups.values():
        base_group_ids.sort()
        source_keys = sorted(
            source_key
            for group_id in base_group_ids
            for source_key in groups[group_id]["source_keys"]
        )
        overlap = all_source_keys.intersection(source_keys)
        if overlap:
            raise ValueError(f"Resolved source identity overlap: {sorted(overlap)[:3]}")
        all_source_keys.update(source_keys)
        digest = hashlib.sha256("\n".join(source_keys).encode("utf-8")).hexdigest()[:16]
        final_group_id = f"fg-{digest}"
        if final_group_id in final_group_ids:
            raise ValueError(f"Resolved group ID collision: {final_group_id}")
        final_group_ids.add(final_group_id)
        for group_id in base_group_ids:
            base_to_final[group_id] = final_group_id
        resolved_rows.append(
            {
                "final_group_id": final_group_id,
                "base_group_ids": base_group_ids,
                "base_group_count": len(base_group_ids),
                "source_keys": source_keys,
                "source_identity_count": len(source_keys),
                "grouping_scope": "leakage_safe_split_only",
            }
        )
    resolved_rows.sort(key=lambda row: row["final_group_id"])
    summary = {
        "base_source_groups": len(groups),
        "resolved_source_groups": len(resolved_rows),
        "source_group_reduction": len(groups) - len(resolved_rows),
        "source_identities": len(all_source_keys),
        "duplicate_decisions": len(duplicate_decisions),
        "duplicate_decision_counts": dict(
            sorted(Counter(row["decision"] for row in duplicate_decisions).items())
        ),
        "transitive_keep_separate_conflicts": 0,
    }
    return resolved_rows, base_to_final, summary


def load_raw_records(
    registry: dict[str, Any],
    repository_root: Path,
    identities: dict[tuple[str, str], dict[str, Any]],
) -> list[RawRecord]:
    records: list[RawRecord] = []
    seen_identity_keys: set[tuple[str, str]] = set()
    for dataset in registry.get("datasets", []):
        dataset_id = dataset.get("dataset_id")
        if not isinstance(dataset_id, str):
            raise ValueError("Dataset registry entry lacks dataset_id")
        root_raw = dataset.get("root_from_repository")
        if not isinstance(root_raw, str):
            raise ValueError(f"Dataset {dataset_id} lacks root_from_repository")
        if Path(root_raw).is_absolute():
            raise ValueError(f"Dataset {dataset_id} root must be repository-relative")
        root = resolve_path(repository_root, root_raw)
        annotation_filename = dataset.get("annotation_filename")
        if not isinstance(annotation_filename, str):
            raise ValueError(f"Dataset {dataset_id} lacks annotation_filename")
        dataset_identity_locators: dict[tuple[str, str], tuple[tuple[str, str], dict[str, Any]]] = {}
        for key, row in identities.items():
            if key[0] != dataset_id:
                continue
            locator = (row.get("source_split"), row.get("file_name"))
            if not all(isinstance(value, str) for value in locator):
                raise ValueError(f"Identity record lacks split/file locator: {key}")
            if locator in dataset_identity_locators:
                raise ValueError(f"Duplicate identity split/file locator: {dataset_id}/{locator}")
            dataset_identity_locators[locator] = (key, row)
        for split in dataset.get("splits", []):
            annotation_path = root / split / annotation_filename
            coco = load_json(annotation_path)
            images = coco.get("images")
            annotations = coco.get("annotations")
            categories = coco.get("categories")
            if not all(isinstance(value, list) for value in (images, annotations, categories)):
                raise ValueError(f"Malformed COCO arrays: {annotation_path}")
            image_by_id = {image["id"]: image for image in images}
            if len(image_by_id) != len(images):
                raise ValueError(f"Duplicate COCO image IDs: {annotation_path}")
            category_by_id = {category["id"]: category["name"] for category in categories}
            annotations_by_image: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for annotation in annotations:
                image_id = annotation.get("image_id")
                if image_id not in image_by_id:
                    raise ValueError(f"Annotation references unknown image: {annotation_path}")
                if annotation.get("category_id") not in category_by_id:
                    raise ValueError(f"Annotation references unknown category: {annotation_path}")
                annotations_by_image[image_id].append(annotation)

            for image in images:
                locator = (split, image["file_name"])
                match = dataset_identity_locators.get(locator)
                if match is None:
                    raise ValueError(f"Raw COCO image missing from identity map: {dataset_id}/{locator}")
                identity_key, identity = match
                if identity_key in seen_identity_keys:
                    raise ValueError(f"Raw record joined twice: {identity_key}")
                seen_identity_keys.add(identity_key)
                image_annotations = annotations_by_image.get(image["id"], [])
                category_counts = Counter(
                    category_by_id[annotation["category_id"]]
                    for annotation in image_annotations
                )
                image_path = Path(root_raw) / split / image["file_name"]
                records.append(
                    RawRecord(
                        dataset_id=dataset_id,
                        source_key=identity_key[1],
                        source_name=identity["source_name"],
                        source_split=split,
                        image_id=image["id"],
                        file_name=image["file_name"],
                        raw_sha256=identity["raw_sha256"],
                        width=image["width"],
                        height=image["height"],
                        raw_annotation_ids=tuple(
                            sorted(
                                (annotation["id"] for annotation in image_annotations),
                                key=lambda value: str(value),
                            )
                        ),
                        raw_category_counts=dict(sorted(category_counts.items())),
                        raw_image_from_repository=image_path.as_posix(),
                    )
                )
    if seen_identity_keys != set(identities):
        missing = sorted(set(identities) - seen_identity_keys)
        raise ValueError(f"Identity records missing from raw COCO: {missing[:3]}")
    records.sort(key=lambda row: (row.dataset_id, row.source_key))
    return records


def index_exclusions(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    image_exclusions: dict[tuple[str, str], dict[str, Any]] = {}
    annotation_repairs: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("dataset_id"), row.get("source_key"))
        if not all(isinstance(value, str) for value in key):
            raise ValueError("Malformed training exclusion key")
        scope = row.get("scope")
        target = (
            image_exclusions
            if scope == "image_for_component_training"
            else annotation_repairs
            if scope == "annotations_for_component_training"
            else None
        )
        if target is None:
            raise ValueError(f"Unsupported training exclusion scope: {scope}")
        if key in target:
            raise ValueError(f"Duplicate exclusion scope for {key}")
        target[key] = row
    overlap = set(image_exclusions).intersection(annotation_repairs)
    if overlap:
        raise ValueError(f"Image and annotation exclusion overlap: {sorted(overlap)[:3]}")
    return image_exclusions, annotation_repairs


def next_action_for_quarantine(disposition: str) -> str:
    mapping = {
        "EXCLUDE_PENDING_RELABEL": "RELABEL_COMPONENTS_AND_REVIEW",
        "EXCLUDE_PENDING_COMPONENT_COMPLETENESS_REVIEW": (
            "REVIEW_AND_COMPLETE_COMPONENT_LABELS"
        ),
        "EXCLUDE_PENDING_NEGATIVE_OR_RELABEL_REVIEW": (
            "CONFIRM_VALID_NEGATIVE_OR_RELABEL"
        ),
    }
    if disposition not in mapping:
        raise ValueError(f"Unsupported image exclusion disposition: {disposition}")
    return mapping[disposition]


def build_record_decisions(
    raw_records: list[RawRecord],
    identities: dict[tuple[str, str], dict[str, Any]],
    base_to_final: dict[str, str],
    image_exclusions: dict[tuple[str, str], dict[str, Any]],
    annotation_repairs: dict[tuple[str, str], dict[str, Any]],
    quality_rows: list[dict[str, Any]],
    component_dataset_id: str,
    binary_dataset_id: str,
) -> list[dict[str, Any]]:
    quality_flags: dict[tuple[str, str], list[str]] = {}
    for row in quality_rows:
        key = (row.get("dataset_id"), row.get("source_key"))
        if not all(isinstance(value, str) for value in key):
            raise ValueError("Malformed quality review row")
        if key in quality_flags:
            raise ValueError(f"Duplicate quality review row: {key}")
        quality_flags[key] = sorted(set(row.get("reason_codes", [])))

    raw_keys = {(row.dataset_id, row.source_key) for row in raw_records}
    unknown_quality_rows = set(quality_flags) - raw_keys
    if unknown_quality_rows:
        raise ValueError(
            f"Quality review references unknown raw records: {sorted(unknown_quality_rows)[:3]}"
        )
    unknown_exclusions = (set(image_exclusions) | set(annotation_repairs)) - raw_keys
    if unknown_exclusions:
        raise ValueError(f"Exclusions reference unknown raw records: {sorted(unknown_exclusions)[:3]}")

    decisions: list[dict[str, Any]] = []
    for record in raw_records:
        key = (record.dataset_id, record.source_key)
        identity = identities[key]
        base_group_id = identity["group_id"]
        final_group_id = base_to_final[base_group_id]
        image_exclusion = image_exclusions.get(key)
        annotation_repair = annotation_repairs.get(key)
        dropped_annotation_ids: list[Any] = []
        reasons = list(quality_flags.get(key, []))
        if image_exclusion is not None:
            decision = "QUARANTINE"
            eligible = False
            disposition = image_exclusion["disposition"]
            next_action = next_action_for_quarantine(disposition)
            reasons = sorted(set(reasons + image_exclusion.get("reason_codes", [])))
            post_repair_annotation_count = len(record.raw_annotation_ids)
            included_annotation_count = 0
        elif annotation_repair is not None:
            decision = "REPAIR_AND_USE"
            eligible = True
            disposition = annotation_repair["disposition"]
            next_action = "DROP_LISTED_ANNOTATIONS_WHEN_MATERIALIZING_DERIVED_DATASET"
            dropped_annotation_ids = annotation_repair.get("annotation_ids", [])
            if not isinstance(dropped_annotation_ids, list) or not dropped_annotation_ids:
                raise ValueError(f"Annotation repair lacks annotation IDs: {key}")
            if len(set(dropped_annotation_ids)) != len(dropped_annotation_ids):
                raise ValueError(f"Annotation repair repeats annotation IDs: {key}")
            unknown_ids = set(dropped_annotation_ids) - set(record.raw_annotation_ids)
            if unknown_ids:
                raise ValueError(f"Annotation repair references unknown IDs for {key}: {unknown_ids}")
            reasons = sorted(set(reasons + annotation_repair.get("reason_codes", [])))
            post_repair_annotation_count = len(record.raw_annotation_ids) - len(
                set(dropped_annotation_ids)
            )
            included_annotation_count = post_repair_annotation_count
        elif record.dataset_id == component_dataset_id:
            decision = "USE"
            eligible = True
            disposition = "USE_COMPONENT_LABELS_AS_AUDITED"
            next_action = "NONE"
            post_repair_annotation_count = len(record.raw_annotation_ids)
            included_annotation_count = post_repair_annotation_count
        elif record.dataset_id == binary_dataset_id:
            decision = "USE_PROVISIONALLY"
            eligible = True
            disposition = "USE_AS_VISUAL_SUSPICION_COMPARATOR_ONLY"
            next_action = "PRESERVE_INTERPRETATION_LIMIT"
            post_repair_annotation_count = len(record.raw_annotation_ids)
            included_annotation_count = post_repair_annotation_count
        else:
            raise ValueError(f"Dataset has no clean-decision policy: {record.dataset_id}")
        if decision not in ALLOWED_RECORD_DECISIONS:
            raise AssertionError(decision)
        decisions.append(
            {
                "dataset_id": record.dataset_id,
                "source_key": record.source_key,
                "source_name": record.source_name,
                "source_split_for_provenance_only": record.source_split,
                "image_id": record.image_id,
                "file_name": record.file_name,
                "raw_image_from_repository": record.raw_image_from_repository,
                "raw_sha256": record.raw_sha256,
                "width": record.width,
                "height": record.height,
                "base_group_id": base_group_id,
                "final_group_id": final_group_id,
                "decision": decision,
                "eligible_for_dataset_variant": eligible,
                "disposition": disposition,
                "next_action": next_action,
                "quality_flags": reasons,
                "raw_annotation_count": len(record.raw_annotation_ids),
                "raw_category_counts": record.raw_category_counts,
                "dropped_annotation_ids": sorted(dropped_annotation_ids, key=lambda value: str(value)),
                "post_repair_annotation_count": post_repair_annotation_count,
                "included_annotation_count": included_annotation_count,
            }
        )
    if len(decisions) != len(raw_records):
        raise AssertionError("Every raw record must receive exactly one decision")
    return decisions


def enrich_resolved_groups(
    resolved_rows: list[dict[str, Any]], record_decisions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in record_decisions:
        by_group[record["final_group_id"]].append(record)
    output: list[dict[str, Any]] = []
    for row in resolved_rows:
        records = by_group.get(row["final_group_id"], [])
        dataset_counts = Counter(record["dataset_id"] for record in records)
        eligible_counts = Counter(
            record["dataset_id"]
            for record in records
            if record["eligible_for_dataset_variant"]
        )
        decision_counts = Counter(record["decision"] for record in records)
        output.append(
            {
                **row,
                "dataset_record_counts": dict(sorted(dataset_counts.items())),
                "dataset_eligible_record_counts": dict(sorted(eligible_counts.items())),
                "record_decision_counts": dict(sorted(decision_counts.items())),
                "contains_quarantine": any(
                    record["decision"] == "QUARANTINE" for record in records
                ),
            }
        )
    return output


def dataset_summaries(record_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for dataset_id in sorted({row["dataset_id"] for row in record_decisions}):
        rows = [row for row in record_decisions if row["dataset_id"] == dataset_id]
        output[dataset_id] = {
            "records": len(rows),
            "eligible_records": sum(row["eligible_for_dataset_variant"] for row in rows),
            "quarantined_records": sum(row["decision"] == "QUARANTINE" for row in rows),
            "repair_and_use_records": sum(row["decision"] == "REPAIR_AND_USE" for row in rows),
            "decision_counts": dict(sorted(Counter(row["decision"] for row in rows).items())),
            "raw_annotations": sum(row["raw_annotation_count"] for row in rows),
            "included_annotations_after_repair": sum(
                row["included_annotation_count"] for row in rows
            ),
            "annotations_excluded_with_quarantined_images": sum(
                row["raw_annotation_count"]
                for row in rows
                if row["decision"] == "QUARANTINE"
            ),
            "annotations_dropped_by_repair": sum(
                len(row["dropped_annotation_ids"]) for row in rows
            ),
            "eligible_final_groups": len(
                {
                    row["final_group_id"]
                    for row in rows
                    if row["eligible_for_dataset_variant"]
                }
            ),
        }
    return output


def validate_expected(
    expected: dict[str, Any],
    record_decisions: list[dict[str, Any]],
    group_summary: dict[str, Any],
    component_dataset_id: str,
    binary_dataset_id: str,
) -> None:
    dataset_stats = dataset_summaries(record_decisions)
    actual = {
        "dataset_records": len(record_decisions),
        "source_identities": group_summary["source_identities"],
        "base_source_groups": group_summary["base_source_groups"],
        "resolved_source_groups": group_summary["resolved_source_groups"],
        "duplicate_decisions": group_summary["duplicate_decisions"],
        "component_records": dataset_stats[component_dataset_id]["records"],
        "component_quarantined_records": dataset_stats[component_dataset_id][
            "quarantined_records"
        ],
        "component_included_records": dataset_stats[component_dataset_id]["eligible_records"],
        "component_repair_and_use_records": dataset_stats[component_dataset_id][
            "repair_and_use_records"
        ],
        "component_raw_annotations": dataset_stats[component_dataset_id]["raw_annotations"],
        "component_included_annotations": dataset_stats[component_dataset_id][
            "included_annotations_after_repair"
        ],
        "binary_records": dataset_stats[binary_dataset_id]["records"],
        "binary_included_records": dataset_stats[binary_dataset_id]["eligible_records"],
        "binary_raw_annotations": dataset_stats[binary_dataset_id]["raw_annotations"],
        "binary_included_annotations": dataset_stats[binary_dataset_id][
            "included_annotations_after_repair"
        ],
        "annotation_repairs": sum(
            len(row["dropped_annotation_ids"]) for row in record_decisions
        ),
        "transitive_keep_separate_conflicts": group_summary[
            "transitive_keep_separate_conflicts"
        ],
    }
    mismatch = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if actual.get(key) != value
    }
    if mismatch:
        raise ValueError(f"Clean manifest acceptance mismatch: {mismatch}")


def markdown_summary(report: dict[str, Any]) -> str:
    component_id = report["datasets_role"]["component"]
    binary_id = report["datasets_role"]["binary"]
    component = report["datasets"][component_id]
    binary = report["datasets"][binary_id]
    groups = report["source_groups"]
    decisions = report["record_decision_counts"]
    verified_raw_files = sum(row["verified_files"] for row in report["raw_integrity"])
    return f"""# P2-04 Clean Dataset Decision Manifest

**Result:** `{report['result']}`

P2-04 menghasilkan kontrak keputusan dan final split-group. Tahap ini tidak
menyalin pixel, tidak membuat augmentation, dan tidak mengubah raw COCO.
Sebanyak **{verified_raw_files} raw files** diverifikasi ulang terhadap integrity
manifest yang juga dibekukan SHA-256 sebelum keputusan dibangun.

## Dataset decision

| Dataset role | Raw records | Eligible sekarang | Quarantine | Annotation tersedia |
|---|---:|---:|---:|---:|
| B1 component | {component['records']} | {component['eligible_records']} | {component['quarantined_records']} | {component['included_annotations_after_repair']} |
| B2 visual suspicion | {binary['records']} | {binary['eligible_records']} | {binary['quarantined_records']} | {binary['included_annotations_after_repair']} |

Keputusan seluruh record: `{decisions}`. Satu B1 record berstatus
`REPAIR_AND_USE`: annotation micro artefact dibuang ketika derived dataset
dimaterialisasi. Sebanyak {component['quarantined_records']} B1 record tetap di
raw storage tetapi tidak masuk kandidat training saat ini.

## Final leakage group

- Base group P2-02: **{groups['base_source_groups']}**.
- Final group setelah visual constraint: **{groups['resolved_source_groups']}**.
- Source identity ter-cover: **{groups['source_identities']}**.
- Group reduction: **{groups['source_group_reduction']}**.
- Konflik transitif `KEEP_SEPARATE`: **{groups['transitive_keep_separate_conflicts']}**.

`final_group_id` adalah unit atomik untuk P3-01. Semua record dengan ID tersebut
wajib berada pada split yang sama, termasuk alternate ontology B1/B2.

## Artifact

- `clean-dataset-manifest.json`: summary, policy, dan frozen provenance.
- `record-decisions.jsonl`: keputusan untuk seluruh raw dataset record.
- `resolved-source-groups.jsonl`: constraint grouping final untuk split.
- `quarantine-queue.jsonl`: pekerjaan review/relabel yang tidak boleh masuk training.
- `repair-actions.jsonl`: transformasi annotation eksplisit untuk derived data.

## Batas klaim

B2 tetap comparator visual suspicion, bukan ground truth legal OVD. Seluruh
keputusan visual masih provisional dan membutuhkan sign-off manusia sebelum
release perusahaan. P2-04 tidak mengubah batas klaim produk menjadi alat bukti.

## Reproduksi

```bash
.venv/bin/python scripts/build_clean_dataset_manifest.py
```
"""


def build(policy_path: Path, output_dir: Path, repository_root: Path) -> dict[str, Any]:
    policy = load_json(policy_path)
    paths, provenance = validate_upstream(policy, repository_root)
    registry = load_json(paths["dataset_registry"])
    expected_raw_manifest_hashes = policy.get("raw_integrity_manifest_sha256")
    if not isinstance(expected_raw_manifest_hashes, dict):
        raise ValueError("Clean policy requires raw_integrity_manifest_sha256")
    raw_integrity = verify_raw_integrity(
        registry, expected_raw_manifest_hashes, repository_root
    )
    source_group_rows = load_jsonl(paths["source_groups"])
    identity_rows = load_jsonl(paths["source_identity_map"])
    exclusion_rows = load_jsonl(paths["training_exclusions"])
    duplicate_decisions = load_jsonl(paths["duplicate_review_decisions"])
    quality_rows = load_jsonl(paths["quality_review_queue"])

    groups, identities = load_source_model(source_group_rows, identity_rows)
    resolved_rows, base_to_final, group_summary = resolve_source_groups(
        groups, duplicate_decisions
    )
    raw_records = load_raw_records(registry, repository_root, identities)
    image_exclusions, annotation_repairs = index_exclusions(exclusion_rows)
    component_id = policy.get("component_dataset_id")
    binary_id = policy.get("binary_dataset_id")
    if not isinstance(component_id, str) or not isinstance(binary_id, str):
        raise ValueError("Clean policy needs component_dataset_id and binary_dataset_id")
    record_decisions = build_record_decisions(
        raw_records,
        identities,
        base_to_final,
        image_exclusions,
        annotation_repairs,
        quality_rows,
        component_id,
        binary_id,
    )
    resolved_rows = enrich_resolved_groups(resolved_rows, record_decisions)
    expected = policy.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("Clean policy requires expected acceptance counts")
    validate_expected(
        expected, record_decisions, group_summary, component_id, binary_id
    )

    dataset_stats = dataset_summaries(record_decisions)
    record_decision_counts = dict(
        sorted(Counter(row["decision"] for row in record_decisions).items())
    )
    report = {
        "schema_version": 1,
        "script_version": SCRIPT_VERSION,
        "policy_date": policy.get("policy_date"),
        "result": "READY_FOR_P3_LEAKAGE_SAFE_SPLIT",
        "raw_data_modified": False,
        "pixels_copied": False,
        "datasets_role": {"component": component_id, "binary": binary_id},
        "decision_policy": policy.get("decision_policy"),
        "upstream_provenance": provenance,
        "raw_integrity": raw_integrity,
        "source_groups": group_summary,
        "record_decision_counts": record_decision_counts,
        "datasets": dataset_stats,
        "output_coverage": {
            "record_decisions": len(record_decisions),
            "resolved_source_groups": len(resolved_rows),
            "quarantine_rows": sum(
                row["decision"] == "QUARANTINE" for row in record_decisions
            ),
            "repair_action_rows": sum(
                row["decision"] == "REPAIR_AND_USE" for row in record_decisions
            ),
        },
        "interpretation_limits": [
            "USE means eligible for the named bootstrap dataset variant, not legally verified OVD.",
            "USE_PROVISIONALLY restricts B2 to a visual-suspicion comparator.",
            "GROUP_TOGETHER_FOR_SPLIT is a leakage constraint and does not delete images.",
            "QUARANTINE retains raw data while preventing unreviewed supervision from training.",
            "Human sign-off remains required before a company release dataset.",
        ],
    }

    quarantine_rows = [
        row for row in record_decisions if row["decision"] == "QUARANTINE"
    ]
    repair_rows = [
        {
            "dataset_id": row["dataset_id"],
            "source_key": row["source_key"],
            "file_name": row["file_name"],
            "final_group_id": row["final_group_id"],
            "action": row["next_action"],
            "dropped_annotation_ids": row["dropped_annotation_ids"],
            "reason_codes": row["quality_flags"],
            "raw_annotation_count": row["raw_annotation_count"],
            "post_repair_annotation_count": row["post_repair_annotation_count"],
        }
        for row in record_decisions
        if row["decision"] == "REPAIR_AND_USE"
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "clean-dataset-manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_jsonl(output_dir / "record-decisions.jsonl", record_decisions)
    write_jsonl(output_dir / "resolved-source-groups.jsonl", resolved_rows)
    write_jsonl(output_dir / "quarantine-queue.jsonl", quarantine_rows)
    write_jsonl(output_dir / "repair-actions.jsonl", repair_rows)
    (output_dir / "SUMMARY.md").write_text(markdown_summary(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        report = build(args.policy.resolve(), args.output_dir.resolve(), repository_root)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"P2-04 {report['result']}: "
        f"{report['output_coverage']['record_decisions']} record decisions, "
        f"{report['source_groups']['resolved_source_groups']} final groups"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
