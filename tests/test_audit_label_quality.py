from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts/audit_label_quality.py"
SPEC = importlib.util.spec_from_file_location("audit_label_quality", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def record(
    source_key: str = "example.jpg",
    annotations: list | None = None,
    dataset_id: str = "component",
) -> object:
    return AUDIT.ImageRecord(
        dataset_id=dataset_id,
        split="train",
        image_id=1,
        file_name="example.jpg",
        source_name=source_key,
        source_key=source_key,
        width=100,
        height=100,
        path=Path("/not/read/by/unit/tests.jpg"),
        annotations=annotations or [],
    )


class LabelQualityAuditTests(unittest.TestCase):
    def test_source_name_normalization_prefers_original_name(self) -> None:
        display, key = AUDIT.normalize_source_name(
            {
                "file_name": "changed_jpg.rf.0123456789abcdef.jpg",
                "extra": {"name": "VID_20220212_101626-17.JPEG"},
            }
        )
        self.assertEqual(display, "VID_20220212_101626-17.JPEG")
        self.assertEqual(key, "vid_20220212_101626-17.jpg")

    def test_box_iou_handles_overlap_and_separation(self) -> None:
        self.assertAlmostEqual(AUDIT.box_iou((0, 0, 10, 10), (5, 5, 10, 10)), 25 / 175)
        self.assertEqual(AUDIT.box_iou((0, 0, 2, 2), (5, 5, 2, 2)), 0.0)

    def test_candidate_accumulator_keeps_strongest_action_and_priority(self) -> None:
        sample = record()
        queue = AUDIT.CandidateAccumulator({"example.jpg": "sg-example"})
        queue.add(
            sample,
            reason_code="needs_review",
            priority="medium",
            recommended_action="REVIEW_IMAGE",
        )
        queue.add(
            sample,
            reason_code="confirmed_missing",
            priority="critical",
            recommended_action="RELABEL_IMAGE",
            annotation_ids=[9],
        )
        rows = queue.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priority"], "critical")
        self.assertEqual(rows[0]["recommended_action"], "RELABEL_IMAGE")
        self.assertEqual(rows[0]["group_id"], "sg-example")
        self.assertEqual(rows[0]["reason_codes"], ["confirmed_missing", "needs_review"])
        self.assertEqual(rows[0]["annotation_ids"], [9])

    def test_dataset_statistics_finds_micro_and_same_class_overlap(self) -> None:
        annotations = [
            AUDIT.AnnotationRecord(1, "muatan", (1.0, 1.0, 0.1, 0.1)),
            AUDIT.AnnotationRecord(2, "boxtruck", (0.0, 0.0, 20.0, 20.0)),
            AUDIT.AnnotationRecord(3, "boxtruck", (1.0, 1.0, 20.0, 20.0)),
        ]
        thresholds = {
            "micro_box_area_ratio": 0.00001,
            "small_box_area_ratio": 0.001,
            "near_full_frame_area_ratio": 0.98,
            "edge_tolerance_px": 1.0,
            "same_class_duplicate_iou": 0.75,
        }
        statistics, micro, overlaps = AUDIT.dataset_statistics(
            [record(annotations=annotations)], thresholds
        )
        self.assertEqual(statistics["boxes_at_or_below_micro_threshold"], 1)
        self.assertEqual(len(micro), 1)
        self.assertEqual(len(overlaps), 1)
        self.assertGreater(overlaps[0][3], 0.75)

    def test_manual_decision_rejects_unknown_annotation(self) -> None:
        sample = record(
            annotations=[AUDIT.AnnotationRecord(1, "muatan", (1.0, 1.0, 2.0, 2.0))]
        )
        decision = {
            "dataset_id": "component",
            "source_key": "example.jpg",
            "decision": "DROP_ANNOTATION",
            "annotation_ids": [999],
        }
        with self.assertRaises(ValueError):
            AUDIT.validate_manual_decisions(
                [decision], {(sample.dataset_id, sample.source_key): sample}
            )

    def test_exclusion_builder_keeps_image_and_annotation_scope_separate(self) -> None:
        null = record("null.jpg")
        labelled = record(
            "labelled.jpg",
            [
                AUDIT.AnnotationRecord(5, "boxtruck", (1.0, 1.0, 20.0, 20.0)),
                AUDIT.AnnotationRecord(6, "headtruck", (1.0, 1.0, 10.0, 10.0)),
                AUDIT.AnnotationRecord(7, "muatan", (1.0, 1.0, 2.0, 2.0)),
            ],
        )
        decisions = {
            ("component", "labelled.jpg"): {
                "dataset_id": "component",
                "source_key": "labelled.jpg",
                "decision": "DROP_ANNOTATION",
                "annotation_ids": [7],
                "reason_codes": ["artefact"],
                "review_status": "provisional",
            }
        }
        rows = AUDIT.build_exclusions(
            [null, labelled],
            decisions,
            {"null.jpg": "sg-null", "labelled.jpg": "sg-labelled"},
            ["boxtruck", "headtruck"],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["scope"] for row in rows},
            {"image_for_component_training", "annotations_for_component_training"},
        )

    def test_exclusion_builder_quarantines_component_count_mismatch(self) -> None:
        incomplete = record(
            "incomplete.jpg",
            [AUDIT.AnnotationRecord(8, "headtruck", (1.0, 1.0, 10.0, 10.0))],
        )
        rows = AUDIT.build_exclusions(
            [incomplete], {}, {"incomplete.jpg": "sg-incomplete"}, ["boxtruck", "headtruck"]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["disposition"],
            "EXCLUDE_PENDING_COMPONENT_COMPLETENESS_REVIEW",
        )
        self.assertIn("component_core_count_mismatch", rows[0]["reason_codes"])

    def test_source_type_is_explicit(self) -> None:
        self.assertEqual(AUDIT.source_type("VID_20220212_101626-17.jpg"), "video_frame")
        self.assertEqual(AUDIT.source_type("truk1644986759.jpg"), "timestamp_named_still")
        self.assertEqual(AUDIT.source_type("news-image.jpg"), "other_still")

    def test_duplicate_visual_review_materializes_every_frozen_candidate(self) -> None:
        candidates = [
            {
                "left_group_id": "sg-a",
                "right_group_id": "sg-b",
                "left_source_key": "a.jpg",
                "right_source_key": "b.jpg",
                "evidence": ["timestamp_window_review"],
            },
            {
                "left_group_id": "sg-c",
                "right_group_id": "sg-d",
                "left_source_key": "c.jpg",
                "right_source_key": "d.jpg",
                "evidence": ["global_perceptual_review"],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.jsonl"
            queue_path.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
            )
            config = {
                "duplicate_visual_review": {
                    "queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
                    "default_decision": "KEEP_SEPARATE",
                    "group_for_split_candidate_numbers": [1],
                    "review_status": "provisional",
                    "reviewed_on": "2026-09-08",
                    "review_coverage": "all",
                    "decision_semantics": "split only",
                }
            }
            rows, summary = AUDIT.resolve_duplicate_candidates(
                candidates, config, queue_path
            )
        self.assertEqual(
            [row["decision"] for row in rows],
            ["GROUP_TOGETHER_FOR_SPLIT", "KEEP_SEPARATE"],
        )
        self.assertEqual(summary["reviewed_group_pairs"], 2)
        self.assertEqual(summary["decision_counts"]["GROUP_TOGETHER_FOR_SPLIT"], 1)
        self.assertEqual(summary["transitive_keep_separate_conflicts"], 0)

    def test_duplicate_visual_review_rejects_transitive_keep_conflict(self) -> None:
        candidates = [
            {
                "left_group_id": "sg-a",
                "right_group_id": "sg-b",
                "left_source_key": "a.jpg",
                "right_source_key": "b.jpg",
                "evidence": ["timestamp_window_review"],
            },
            {
                "left_group_id": "sg-b",
                "right_group_id": "sg-c",
                "left_source_key": "b.jpg",
                "right_source_key": "c.jpg",
                "evidence": ["timestamp_window_review"],
            },
            {
                "left_group_id": "sg-a",
                "right_group_id": "sg-c",
                "left_source_key": "a.jpg",
                "right_source_key": "c.jpg",
                "evidence": ["global_perceptual_review"],
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            queue_path = Path(temporary) / "queue.jsonl"
            queue_path.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates), encoding="utf-8"
            )
            config = {
                "duplicate_visual_review": {
                    "queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest(),
                    "default_decision": "KEEP_SEPARATE",
                    "group_for_split_candidate_numbers": [1, 2],
                    "review_status": "provisional",
                    "reviewed_on": "2026-09-08",
                    "review_coverage": "all",
                    "decision_semantics": "split only",
                }
            }
            with self.assertRaises(ValueError):
                AUDIT.resolve_duplicate_candidates(candidates, config, queue_path)


if __name__ == "__main__":
    unittest.main()
