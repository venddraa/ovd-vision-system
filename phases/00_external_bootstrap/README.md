# Phase 00 — External Bootstrap

## Tujuan

Menyiapkan data, baseline AI, dan pipeline video per-event menggunakan sumber non-internal sebelum CCTV JMTO tersedia.

## Input yang diizinkan

- dataset publik dengan lisensi dan provenance tercatat;
- video uji yang hak penggunaannya jelas;
- konfigurasi dummy/nonrahasia;
- paper dan referensi teknis sebagai dasar desain.

## Batas klaim

Hasil fase ini tidak boleh disebut:

- performa lapangan JMTO;
- keputusan overdimension legal;
- pengukuran fisik dalam meter tanpa calibration dan ground truth;
- sistem penindakan otomatis.

## Exit package

- audited external datasets;
- split bebas leakage;
- reproducible baselines B0/B1/B2;
- offline vehicle-event pipeline;
- evidence package;
- geometry dan quality-gate prototype;
- CCTV/VPN/RTSP intake checklist;
- handoff menuju Phase 01.

## Dokumen aktif

- `TASKS.md`: board pekerjaan dan checkpoint.
- `DECISIONS.md`: keputusan serta open decisions.
- `reports/`: laporan audit, baseline, dan handoff fase.

## Validasi dataset P2-01

Jalankan dari root repository:

```bash
python scripts/validate_coco_datasets.py
```

Registry dataset berada di `configs/bootstrap_external/datasets.json`. Validator membaca raw dataset dan checksum secara
read-only, lalu menulis laporan deterministik ke `reports/p2-01/`.

Exit code:

- `0`: seluruh dataset lolos; warning tetap dapat muncul dan harus ditindaklanjuti;
- `1`: sedikitnya satu material data error ditemukan;
- `2`: registry atau argumen konfigurasi tidak valid.
