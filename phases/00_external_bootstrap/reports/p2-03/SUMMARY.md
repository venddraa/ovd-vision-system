# P2-03 Label Quality Audit

**Result:** `COMPLETE_WITH_REMEDIATION_REQUIRED`

Raw COCO dibaca tanpa diubah. Keputusan koreksi diterapkan nanti pada derived
dataset, bukan pada snapshot raw.

## Ringkasan

| Metric | B1 component | B2 visual suspicion |
|---|---:|---:|
| Images | 1095 | 1066 |
| Annotations | 2902 | 1261 |
| Null images | 79 | 0 |
| Small boxes (< configured threshold) | 67 | 2 |
| Micro boxes | 1 | 0 |
| Near-full-frame boxes | 0 | 58 |
| Edge-touching boxes | 558 | 377 |
| Same-class high-IoU pairs | 0 | 0 |

## Findings yang mengubah keputusan training

- Seluruh **79 B1 null images** dikarantina dari
  component training sampai dipastikan valid negative atau selesai direlabel.
- Seluruh **66 B1 images**
  dengan jumlah pasangan `boxtruck`/`headtruck` tidak seimbang juga dikarantina
  sampai kelengkapan komponennya dikonfirmasi. Ini mencegah partial annotation
  diperlakukan sebagai negative supervision.
- Dari shared source, null B1 terdiri dari label B2:
  `{'NOT ODOL': 42, 'NOT ODOL+ODOL': 1, 'ODOL': 11}`. Kehadiran
  ODOL pada B2 bukan bukti legal, tetapi cukup kuat sebagai sinyal missing-label
  untuk review prioritas tinggi.
- Ada **1 micro bbox** B1. Visual
  review menetapkan annotation 227 pada `VID_20210816_165022-16.jpg` sebagai
  point-like artefact untuk dibuang hanya pada derived dataset.
- Ada **17 keputusan visual provisional**;
  `{'DROP_ANNOTATION': 1, 'RELABEL_REQUIRED': 16}`. Status AI-assisted ini tetap memerlukan
  sign-off manusia sebelum release perusahaan.
- B2 tetap hanya baseline visual suspicion. Label ODOL/NOT ODOL tidak memiliki
  pengukuran fisik atau definisi legal yang dapat diaudit dari export.

## Resolusi rule-level hasil visual scan

| Candidate rule | Images | Resolusi sementara |
|---|---:|---|
| B1 null image | 79 | quarantine; bedakan valid negative vs perlu relabel |
| B1 core-count mismatch | 66 | quarantine; review kelengkapan komponen |
| B1 micro bbox | 1 | drop annotation terkonfirmasi pada turunan |
| B2 near-full-frame bbox | 58 | retain provisional; tandai risiko context shortcut |
| B2 mixed labels | 86 | retain provisional; box terlihat milik kendaraan berbeda |

Resolusi ini berasal dari structural audit dan pemeriksaan seluruh 15 contact
sheet candidate. Statusnya `provisional_ai_assisted`, sehingga perubahan label
tetap membutuhkan sign-off manusia sebelum dataset release perusahaan.

## Gate dan artifact

- Review candidates: **290** images,
  priority `{'critical': 17, 'high': 64, 'low': 86, 'medium': 123}`.
- Training exclusion rows: **146**
  (145 image scope,
  1 annotation scope).
- Seluruh **257** pasangan kandidat P2-02
  sudah memperoleh keputusan visual provisional:
  `{'GROUP_TOGETHER_FOR_SPLIT': 112, 'KEEP_SEPARATE': 145}`. Keputusan group hanya berlaku sebagai
  constraint split bebas leakage; tidak ada image yang dihapus.
- Local contact-sheet gallery: `../storage/external/interim/p2-03-label-quality-review/index.html`.

## Interpretasi aman

`muatan` berarti bagian muatan yang tampak menurut anotator publik; kelas ini
tidak otomatis berarti pelanggaran dimensi. B1 lebih berguna untuk bootstrap
persepsi komponen/geometry relatif. B2 berguna sebagai comparator eksperimen,
bukan ground truth penindakan atau pengukuran meter.

## Reproduksi

```bash
.venv/bin/python scripts/audit_label_quality.py
```
