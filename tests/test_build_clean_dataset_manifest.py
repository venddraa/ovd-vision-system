from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts/build_clean_dataset_manifest.py"
SPEC = importlib.util.spec_from_file_location("build_clean_dataset_manifest", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def source_group(group_id: str, source_key: str) -> dict:
    return {"group_id": group_id, "source_keys": [source_key]}


def duplicate_decision(
    decision_id: str, left: str, right: str, decision: str
) -> dict:
    return {
        "decision_id": decision_id,
        "left_group_id": left,
        "right_group_id": right,
        "decision": decision,
    }


def raw_record(
    source_key: str,
    annotation_ids: tuple[int, ...],
    dataset_id: str = "component",
) -> object:
    return BUILDER.RawRecord(
        dataset_id=dataset_id,
        source_key=source_key,
        source_name=source_key,
        source_split="train",
        image_id=1,
        file_name=f"rf-{source_key}",
        raw_sha256="a" * 64,
        width=100,
        height=100,
        raw_annotation_ids=annotation_ids,
        raw_category_counts={"boxtruck": 1} if annotation_ids else {},
        raw_image_from_repository=f"../storage/{source_key}",
    )


class CleanDatasetManifestTests(unittest.TestCase):
    def test_resolved_groups_are_deterministic_and_complete(self) -> None:
        groups = {
            "sg-a": source_group("sg-a", "a.jpg"),
            "sg-b": source_group("sg-b", "b.jpg"),
            "sg-c": source_group("sg-c", "c.jpg"),
        }
        decisions = [
            duplicate_decision("dr-1", "sg-a", "sg-b", "GROUP_TOGETHER_FOR_SPLIT"),
            duplicate_decision("dr-2", "sg-b", "sg-c", "KEEP_SEPARATE"),
        ]
        rows, mapping, summary = BUILDER.resolve_source_groups(groups, decisions)
        self.assertEqual(len(rows), 2)
        self.assertEqual(mapping["sg-a"], mapping["sg-b"])
        self.assertNotEqual(mapping["sg-b"], mapping["sg-c"])
        self.assertEqual(summary["source_identities"], 3)
        expected = hashlib.sha256(b"a.jpg\nb.jpg").hexdigest()[:16]
        self.assertEqual(mapping["sg-a"], f"fg-{expected}")

    def test_resolved_groups_reject_transitive_keep_conflict(self) -> None:
        groups = {
            "sg-a": source_group("sg-a", "a.jpg"),
            "sg-b": source_group("sg-b", "b.jpg"),
            "sg-c": source_group("sg-c", "c.jpg"),
        }
        decisions = [
            duplicate_decision("dr-1", "sg-a", "sg-b", "GROUP_TOGETHER_FOR_SPLIT"),
            duplicate_decision("dr-2", "sg-b", "sg-c", "GROUP_TOGETHER_FOR_SPLIT"),
            duplicate_decision("dr-3", "sg-a", "sg-c", "KEEP_SEPARATE"),
        ]
        with self.assertRaises(ValueError):
            BUILDER.resolve_source_groups(groups, decisions)

    def test_source_model_rejects_identity_group_mismatch(self) -> None:
        group_rows = [source_group("sg-a", "a.jpg")]
        identity_rows = [
            {
                "group_id": "sg-wrong",
                "source_key": "a.jpg",
                "records": [{"dataset_id": "component"}],
            }
        ]
        with self.assertRaises(ValueError):
            BUILDER.load_source_model(group_rows, identity_rows)

    def test_exclusion_index_rejects_image_annotation_overlap(self) -> None:
        base = {"dataset_id": "component", "source_key": "a.jpg"}
        rows = [
            {**base, "scope": "image_for_component_training"},
            {**base, "scope": "annotations_for_component_training"},
        ]
        with self.assertRaises(ValueError):
            BUILDER.index_exclusions(rows)

    def test_record_decisions_apply_quarantine_and_annotation_repair(self) -> None:
        records = [raw_record("repair.jpg", (1, 2)), raw_record("quarantine.jpg", (3,))]
        identities = {
            ("component", "repair.jpg"): {"group_id": "sg-repair"},
            ("component", "quarantine.jpg"): {"group_id": "sg-quarantine"},
        }
        image_exclusions = {
            ("component", "quarantine.jpg"): {
                "disposition": "EXCLUDE_PENDING_RELABEL",
                "reason_codes": ["missing"],
            }
        }
        annotation_repairs = {
            ("component", "repair.jpg"): {
                "disposition": "DROP_ANNOTATION_IN_DERIVED_DATASET",
                "annotation_ids": [2],
                "reason_codes": ["artefact"],
            }
        }
        rows = BUILDER.build_record_decisions(
            records,
            identities,
            {"sg-repair": "fg-repair", "sg-quarantine": "fg-quarantine"},
            image_exclusions,
            annotation_repairs,
            [],
            "component",
            "binary",
        )
        by_source = {row["source_key"]: row for row in rows}
        self.assertEqual(by_source["repair.jpg"]["decision"], "REPAIR_AND_USE")
        self.assertEqual(by_source["repair.jpg"]["included_annotation_count"], 1)
        self.assertEqual(by_source["repair.jpg"]["dropped_annotation_ids"], [2])
        self.assertEqual(by_source["quarantine.jpg"]["decision"], "QUARANTINE")
        self.assertEqual(by_source["quarantine.jpg"]["included_annotation_count"], 0)

    def test_record_decisions_reject_unknown_annotation_repair(self) -> None:
        records = [raw_record("repair.jpg", (1,))]
        with self.assertRaises(ValueError):
            BUILDER.build_record_decisions(
                records,
                {("component", "repair.jpg"): {"group_id": "sg-repair"}},
                {"sg-repair": "fg-repair"},
                {},
                {
                    ("component", "repair.jpg"): {
                        "disposition": "DROP_ANNOTATION_IN_DERIVED_DATASET",
                        "annotation_ids": [999],
                    }
                },
                [],
                "component",
                "binary",
            )

    def test_upstream_checksum_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact.jsonl"
            artifact.write_text("original\n", encoding="utf-8")
            policy = {
                "schema_version": 1,
                "upstream_artifacts": {
                    "artifact": {
                        "path_from_repository": "artifact.jsonl",
                        "sha256": hashlib.sha256(b"different\n").hexdigest(),
                    }
                },
            }
            with self.assertRaises(ValueError):
                BUILDER.validate_upstream(policy, root)

    def test_raw_integrity_detects_file_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_root = root / "raw"
            image = raw_root / "train" / "image.jpg"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"original")
            checksum = hashlib.sha256(image.read_bytes()).hexdigest()
            manifest = root / "integrity.sha256"
            manifest.write_text(f"{checksum}  ./train/image.jpg\n", encoding="utf-8")
            registry = {
                "datasets": [
                    {
                        "dataset_id": "example",
                        "root_from_repository": "raw",
                        "checksum_manifest_from_repository": "integrity.sha256",
                    }
                ]
            }
            manifest_hashes = {"example": hashlib.sha256(manifest.read_bytes()).hexdigest()}
            result = BUILDER.verify_raw_integrity(registry, manifest_hashes, root)
            self.assertEqual(result[0]["verified_files"], 1)
            image.write_bytes(b"mutated")
            with self.assertRaises(ValueError):
                BUILDER.verify_raw_integrity(registry, manifest_hashes, root)


if __name__ == "__main__":
    unittest.main()
