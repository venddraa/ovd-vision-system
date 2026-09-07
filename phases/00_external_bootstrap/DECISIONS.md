# Decision Log — Phase 00 External Bootstrap

Dokumen ini mencatat keputusan teknis dan produk beserta alasannya. Keputusan tidak dihapus; jika berubah, buat keputusan baru yang menggantikan keputusan lama.

## Status

- `Active`: sedang berlaku.
- `Provisional`: dipakai sementara sambil menunggu bukti.
- `Superseded`: sudah digantikan keputusan baru.
- `Rejected`: pernah dipertimbangkan tetapi tidak digunakan.

## Decisions

| ID | Tanggal | Keputusan | Alasan | Status |
|---|---|---|---|---|
| D-001 | 2026-09-03 | Dataset AI-Hub Korea dikeluarkan dari critical path | Akses tidak praktis dan proyek tidak boleh bergantung pada satu sumber yang sulit diperoleh | Active |
| D-002 | 2026-09-03 | Menggunakan satu codebase untuk semua fase | Pipeline pra-CCTV harus dapat digunakan ulang ketika data JMTO tersedia | Active |
| D-003 | 2026-09-03 | Data, konfigurasi, eksperimen, model, dan laporan dipisahkan berdasarkan fase dan provenance | Mencegah data publik dan internal tercampur serta menjaga auditability | Active |
| D-004 | 2026-09-03 | Phase 00 menggunakan dataset eksternal sebagai bootstrap | CCTV dan ground truth JMTO belum tersedia | Active |
| D-005 | 2026-09-03 | Output awal adalah screening/operator alert | Belum tersedia dasar legal, metrologi, dan validasi untuk penindakan otomatis | Provisional |
| D-006 | 2026-09-03 | Unit keputusan adalah satu vehicle event | Keputusan per-frame dapat menghitung kendaraan yang sama berkali-kali dan tidak stabil | Active |
| D-007 | 2026-09-03 | B1 component detector menjadi kandidat jalur utama | Boundary bak/bodi/muatan lebih berguna untuk geometry dan lebih explainable | Provisional |
| D-008 | 2026-09-03 | Model ODOL/NOT ODOL hanya menjadi visual-suspicion baseline | Label publik belum membuktikan pelanggaran legal atau ukuran fisik | Active |
| D-009 | 2026-09-03 | Git lokal dibuat sebelum remote GitHub/GitLab | Remote harus menunggu kanal dan kebijakan perusahaan | Active |
| D-010 | 2026-09-03 | Raw data, video, model weight, dan hasil sensitif disimpan di luar repository | Mengurangi risiko kebocoran dan accidental commit | Active |
| D-011 | 2026-09-03 | Folder internal JMTO belum dibuat | Lokasi storage, izin akses, dan kebijakan retensi belum disetujui | Active |
| D-012 | 2026-09-03 | LiDAR/laser/manual measurement diposisikan sebagai comparator atau ground truth | Produk utama harus menggunakan AI vision; WIM tetap menangani berat | Active |
| D-013 | 2026-09-03 | Strategi compute memakai local-first dan remote batch training | Laptop menjadi source of truth; penggunaan GPU dibatasi pada pekerjaan yang memperoleh manfaat nyata | Active |
| D-014 | 2026-09-03 | Kaggle T4 menjadi executor utama untuk full training data eksternal; Colab menjadi fallback | Laptop tidak memiliki NVIDIA GPU, sedangkan resource Colab tidak dijamin tetap | Active |
| D-015 | 2026-09-03 | P100 tidak digunakan untuk workflow baru | Kaggle menghentikan P100 pada 15 September 2026 dan default PyTorch image memiliki risiko kompatibilitas | Active |
| D-016 | 2026-09-03 | Kaggle CLI/API baru diotomatisasi setelah notebook manual reproducible | API berfungsi sebagai remote job submission, bukan raw GPU yang dipinjam proses lokal | Active |

## Open decisions

Keputusan berikut belum dapat diselesaikan sebelum diskusi mentor atau audit CCTV:

| ID | Pertanyaan | Bukti yang dibutuhkan |
|---|---|---|
| OD-001 | Definisi OVD final yang menjadi target | keputusan mentor/domain owner |
| OD-002 | Dimensi pertama: tinggi, lebar, panjang, atau protrusion | view dan observability CCTV |
| OD-003 | Screening atau pengukuran dalam meter | kebutuhan operator dan legal |
| OD-004 | Kamera fixed atau PTZ | metadata dan akses kamera |
| OD-005 | Metode physical ground truth | akses lapangan dan alat ukur |
| OD-006 | Remote repository yang diizinkan | keputusan perusahaan/IT |
| OD-007 | Compute untuk training/fine-tuning data internal dan deployment | kebijakan data serta resource perusahaan |
