# P2-04 Clean Dataset Decision Manifest

**Result:** `READY_FOR_P3_LEAKAGE_SAFE_SPLIT`

P2-04 menghasilkan kontrak keputusan dan final split-group. Tahap ini tidak
menyalin pixel, tidak membuat augmentation, dan tidak mengubah raw COCO.
Sebanyak **2171 raw files** diverifikasi ulang terhadap integrity
manifest yang juga dibekukan SHA-256 sebelum keputusan dibangun.

## Dataset decision

| Dataset role | Raw records | Eligible sekarang | Quarantine | Annotation tersedia |
|---|---:|---:|---:|---:|
| B1 component | 1095 | 950 | 145 | 2691 |
| B2 visual suspicion | 1066 | 1066 | 0 | 1261 |

Keputusan seluruh record: `{'QUARANTINE': 145, 'REPAIR_AND_USE': 1, 'USE': 949, 'USE_PROVISIONALLY': 1066}`. Satu B1 record berstatus
`REPAIR_AND_USE`: annotation micro artefact dibuang ketika derived dataset
dimaterialisasi. Sebanyak 145 B1 record tetap di
raw storage tetapi tidak masuk kandidat training saat ini.

## Final leakage group

- Base group P2-02: **474**.
- Final group setelah visual constraint: **366**.
- Source identity ter-cover: **1095**.
- Group reduction: **108**.
- Konflik transitif `KEEP_SEPARATE`: **0**.

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
