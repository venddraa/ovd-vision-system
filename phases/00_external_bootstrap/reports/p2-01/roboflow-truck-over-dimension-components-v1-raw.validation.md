# P2-01 Validation — `roboflow-truck-over-dimension-components-v1-raw`

**Result:** `PASS_WITH_WARNINGS`

Raw files were read and verified but never modified.

## Inventory

| Metric | Value |
|---|---:|
| images | 1095 |
| image_files | 1095 |
| annotations | 2902 |
| zero_annotation_images | 79 |

## Source splits

| Split | Images | Files | Annotations | Zero-annotation | Boundary precision | Boundary errors |
|---|---:|---:|---:|---:|---:|---:|
| train | 879 | 879 | 2329 | 65 | 30 | 0 |
| valid | 165 | 165 | 439 | 14 | 3 | 0 |
| test | 51 | 51 | 134 | 0 | 6 | 0 |

## Canonical class map

Unused COCO categories are intentionally excluded from the derived contiguous class IDs.

| Source ID | Target ID | Name | Instances | Images containing class |
|---:|---:|---|---:|---:|
| 1 | 0 | boxtruck | 1104 | 994 |
| 2 | 1 | headtruck | 1127 | 1003 |
| 3 | 2 | muatan | 671 | 609 |

## Integrity checks

| Check | Result |
|---|---:|
| Image headers checked | 1095 |
| Checksum entries | 1100 |
| Checksums verified | 1100 |
| Checksum mismatches | 0 |
| Missing checksummed files | 0 |
| Unlisted raw files | 0 |

## Issue summary

| Severity | Count |
|---|---:|
| error | 0 |
| warning | 41 |
| info | 79 |

## Broken-label findings

| Severity | Code | Count | Meaning | Examples |
|---|---|---:|---|---|
| warning | bbox_boundary_precision | 39 | Bounding box exceeds the image boundary only within numeric tolerance. | train: annotation 225, overflow=0.010000px; train: annotation 251, overflow=0.010000px; train: annotation 280, overflow=0.010000px; train: annotation 324, overflow=0.010000px; train: annotation 402, overflow=0.010000px; train: annotation 473, overflow=0.010000px; train: annotation 494, overflow=0.010000px; train: annotation 500, overflow=0.010000px; train: annotation 513, overflow=0.010000px; train: annotation 594, overflow=0.010000px |
| warning | duplicate_category_name | 1 | Category name is duplicated in the COCO schema. | boxtruck |
| warning | unused_category | 1 | Category exists in the schema but is unused by annotations. | id=0, name=boxtruck |
| info | zero_annotation_image | 79 | Image has zero object annotations and must be treated explicitly as a negative/null sample. | train/VID_20220208_141840-09_jpg.rf.a258a11ea3813e3fbcd34c9fdb55156f.jpg; train/VID_20220208_142510-17_jpg.rf.b4c65ebc6fec242b35dd5b5ce7b1586d.jpg; train/VID_20220208_142209-15_jpg.rf.9aa0c5e1eaa07129992d9e228582a876.jpg; train/VID_20220208_142510-19_jpg.rf.733a6a75d3610874bad4aea7b269bc7c.jpg; train/VID_20220208_142510-18_jpg.rf.68b0e8711af3b45af7407f72c6747d63.jpg; train/VID_20220208_141840-08_jpg.rf.39aa7b780c14db614a3596ded4bf31ca.jpg; train/VID_20220210_124050-16_jpg.rf.04e36c39da6f2414370744ced8d4a411.jpg; train/VID_20220210_124050-20_jpg.rf.adc797063831cdcbac6ef6fef9f16dd0.jpg; train/VID_20220209_144546-10_jpg.rf.7ca63aa378306820d42eb2b78856c197.jpg; train/VID_20220212_101626-14_jpg.rf.3c15e14dabf4a94026acf2b6b798a7bc.jpg |

## Interpretation

- `error` means the raw export cannot safely proceed without investigation.
- `warning` records a known export quirk or tolerance case; it is not silently repaired.
- `info` records an explicit dataset property such as a zero-annotation image.
- Any normalization or repair must be written to derived storage, never back into raw.
