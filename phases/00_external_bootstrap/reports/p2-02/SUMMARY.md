# P2-02 Duplicate and Source-Sequence Audit

**Result:** `COMPLETE_WITH_LEAKAGE_FINDINGS`

Raw dataset dibaca untuk hashing/fingerprint dan tidak diubah.

## Ringkasan

| Metric | Value |
|---|---:|
| Total dataset records | 2161 |
| Unique source identities | 1095 |
| Exact file duplicate groups | 1 |
| Video source groups | 106 |
| Video images | 629 |
| Video groups crossing source splits | 24 |
| Automatic grouping edges | 631 |
| Pending visual-review group pairs | 257 |
| Underlying visual-review image pairs | 1942 |
| Final recommended split groups | 474 |
| Canonical groups crossing source splits | 63 |
| Canonical images inside leaking groups | 317 |

## Hubungan antardataset

| Left | Right | Shared source | Left only | Right only | Same dimensions | Exact bytes | Different source split |
|---|---|---:|---:|---:|---:|---:|---:|
| roboflow-odol-vehicle-detection-binary-v1-raw | roboflow-truck-over-dimension-components-v1-raw | 1066 | 0 | 29 | 1066 | 1 | 363 |

Kesimpulan: label komponen dan label ODOL/NOT ODOL adalah dua ontology pada visual yang hampir seluruhnya sama, bukan dua test set independen.

## Bukti pengelompokan otomatis

| Evidence | Edges |
|---|---:|
| source_video_sequence | 523 |
| timestamp_window_visual_match | 108 |

## Grup source-split yang paling terdampak

| Group ID | Source identities | Canonical split counts | Evidence |
|---|---:|---|---|
| sg-611af19cbaaeb6b3 | 26 | {"train": 18, "valid": 8} | source_video_sequence |
| sg-be51f058672c8988 | 22 | {"test": 2, "train": 18, "valid": 2} | source_video_sequence |
| sg-caf5950e19ef62f4 | 19 | {"train": 10, "valid": 9} | source_video_sequence |
| sg-d8888694cf44d341 | 16 | {"train": 10, "valid": 6} | source_video_sequence |
| sg-83391a054655460a | 15 | {"train": 10, "valid": 5} | source_video_sequence |
| sg-78566667e838a252 | 11 | {"test": 1, "train": 7, "valid": 3} | source_video_sequence |
| sg-21cb39729a7f32b7 | 10 | {"train": 7, "valid": 3} | source_video_sequence |
| sg-43bac910d739f818 | 10 | {"test": 1, "train": 6, "valid": 3} | source_video_sequence |
| sg-f638e79b8a693536 | 9 | {"train": 6, "valid": 3} | source_video_sequence |
| sg-2362f1eee2081157 | 8 | {"train": 5, "valid": 3} | source_video_sequence |
| sg-7be1453cbc9490c5 | 8 | {"train": 6, "valid": 2} | source_video_sequence |
| sg-f5ac94bd6916de45 | 8 | {"train": 6, "valid": 2} | source_video_sequence |
| sg-5fa04d1fa3366dad | 6 | {"train": 5, "valid": 1} | source_video_sequence |
| sg-7164989ef471f72b | 6 | {"train": 5, "valid": 1} | source_video_sequence |
| sg-a52bfb44d75a14cb | 6 | {"train": 3, "valid": 3} | source_video_sequence |
| sg-1011397848952457 | 5 | {"test": 1, "train": 3, "valid": 1} | source_video_sequence |
| sg-6854ba550ba82bcc | 5 | {"train": 4, "valid": 1} | source_video_sequence |
| sg-8eb678871b06462b | 5 | {"train": 2, "valid": 3} | source_video_sequence |
| sg-040423a26b907fc9 | 4 | {"train": 3, "valid": 1} | source_video_sequence |
| sg-20a8330edc468ec8 | 4 | {"train": 2, "valid": 2} | timestamp_window_visual_match |
| sg-45fa1939197591ed | 4 | {"train": 2, "valid": 2} | timestamp_window_visual_match |
| sg-48cc8e2a90772a0b | 4 | {"train": 3, "valid": 1} | timestamp_window_visual_match |
| sg-4ebb0739026d9a6e | 4 | {"train": 3, "valid": 1} | source_video_sequence |
| sg-7ed8aa9785a703f1 | 4 | {"train": 3, "valid": 1} | source_video_sequence |
| sg-a3a26329ccc53746 | 4 | {"train": 3, "valid": 1} | source_video_sequence |

## Keputusan untuk fase berikutnya

- Abaikan assignment train/valid/test bawaan Roboflow untuk eksperimen final.
- Gunakan `group_id` dari `source-groups.jsonl` sebagai unit atomik pada P3-01.
- Review pasangan pada `REVIEW-CANDIDATES.md` saat P2-03; jangan menggabungkannya diam-diam.
- Perlakukan dataset ODOL sebagai alternate-label view/B2, bukan validasi independen B1.
- Jangan menafsirkan perceptual similarity sebagai label OVD atau bukti legal.

## Reproduksi

```bash
.venv/bin/python scripts/audit_dataset_duplicates.py
```
