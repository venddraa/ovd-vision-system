# P2-01 Validation — `roboflow-odol-vehicle-detection-binary-v1-raw`

**Result:** `PASS_WITH_WARNINGS`

Raw files were read and verified but never modified.

## Inventory

| Metric | Value |
|---|---:|
| images | 1066 |
| image_files | 1066 |
| annotations | 1261 |
| zero_annotation_images | 0 |

## Source splits

| Split | Images | Files | Annotations | Zero-annotation | Boundary precision | Boundary errors |
|---|---:|---:|---:|---:|---:|---:|
| train | 853 | 853 | 1002 | 0 | 24 | 0 |
| valid | 160 | 160 | 199 | 0 | 5 | 0 |
| test | 53 | 53 | 60 | 0 | 0 | 0 |

## Canonical class map

Unused COCO categories are intentionally excluded from the derived contiguous class IDs.

| Source ID | Target ID | Name | Instances | Images containing class |
|---:|---:|---|---:|---:|
| 1 | 0 | NOT ODOL | 672 | 615 |
| 2 | 1 | ODOL | 589 | 537 |

## Integrity checks

| Check | Result |
|---|---:|
| Image headers checked | 1066 |
| Checksum entries | 1071 |
| Checksums verified | 1071 |
| Checksum mismatches | 0 |
| Missing checksummed files | 0 |
| Unlisted raw files | 0 |

## Issue summary

| Severity | Count |
|---|---:|
| error | 0 |
| warning | 30 |
| info | 0 |

## Broken-label findings

| Severity | Code | Count | Meaning | Examples |
|---|---|---:|---|---|
| warning | bbox_boundary_precision | 29 | Bounding box exceeds the image boundary only within numeric tolerance. | train: annotation 58, overflow=0.010000px; train: annotation 82, overflow=0.010000px; train: annotation 118, overflow=0.010000px; train: annotation 146, overflow=0.010000px; train: annotation 200, overflow=0.010000px; train: annotation 242, overflow=0.010000px; train: annotation 270, overflow=0.010000px; train: annotation 284, overflow=0.010000px; train: annotation 287, overflow=0.010000px; train: annotation 302, overflow=0.010000px |
| warning | unused_category | 1 | Category exists in the schema but is unused by annotations. | id=0, name=Truck-ODOL-Reannotated |

## Interpretation

- `error` means the raw export cannot safely proceed without investigation.
- `warning` records a known export quirk or tolerance case; it is not silently repaired.
- `info` records an explicit dataset property such as a zero-annotation image.
- Any normalization or repair must be written to derived storage, never back into raw.
