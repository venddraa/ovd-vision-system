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

## Audit duplicate dan source sequence P2-02

Dependency sistem: ImageMagick 7 dengan command `magick`. Tidak ada package Python tambahan.

Jalankan dari root repository:

```bash
python scripts/audit_dataset_duplicates.py
```

Audit membaca dua raw export secara read-only, menghitung exact SHA-256 dan fingerprint visual kecil, menyatukan
alternate-label record melalui nama sumber, lalu menulis artifact deterministik ke `reports/p2-02/`.

Kebijakan grouping:

- seluruh frame dengan ID video sumber yang sama wajib satu `group_id`;
- burst `truk<timestamp>` hanya digabung otomatis bila jarak waktu dan dua uji visual lolos threshold;
- kemiripan perceptual lintas video hanya menjadi kandidat review, bukan auto-merge;
- `group_id` adalah unit atomik split P3-01; split bawaan Roboflow tidak digunakan.

Exit code:

- `0`: audit selesai; leakage finding adalah temuan yang harus diperbaiki saat membuat split;
- `2`: data/configuration/renderer gagal dibaca atau fingerprint tidak dapat dibuat.
