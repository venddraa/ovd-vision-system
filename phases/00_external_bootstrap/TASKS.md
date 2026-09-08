# Tasks — Phase 00 External Bootstrap

**Terakhir diperbarui:** 8 September 2026

## Status workflow

- `Backlog`: belum siap dikerjakan.
- `Ready`: dependency sudah tersedia.
- `In Progress`: sedang dikerjakan.
- `Review`: output sedang diperiksa.
- `Blocked`: menunggu dependency atau akses eksternal.
- `Done`: output dan acceptance criteria sudah diverifikasi.

## Aturan kerja

- Maksimal satu kartu utama berstatus `In Progress`.
- Setiap kartu harus menghasilkan artifact yang dapat diperiksa.
- Kartu belum `Done` hanya karena perintah berhasil dijalankan.
- Error, limitation, dan perubahan keputusan harus didokumentasikan.
- Data publik dan internal tidak boleh bercampur tanpa manifest.

## Board aktif

| ID | Pekerjaan | Deliverable | Dependency | Status |
|---|---|---|---|---|
| P0-01 | Scope contract dan decision log | `project-charter.md` dan `DECISIONS.md` | — | Done |
| P0-02 | Audit environment dan aturan eksperimen | `environment-audit.md` dan `experiment-convention.md` | P0-01 | Done |
| P0-03 | Repository, virtual environment, dan Git lokal | struktur folder, `.venv`, `.gitignore`, initial commit | P0-02 | Done |
| P1-01 | Intake dua dataset Roboflow | raw dataset eksternal di storage | P0-03 dan login pengguna | Done |
| P1-02 | Dokumentasi provenance dataset | source manifest, lisensi, versi, checksum | P1-01 | Done |
| P2-01 | Validasi file dan anotasi | statistik file, class map, broken-label report | P1-02 | Done |
| P2-02 | Audit exact dan near duplicate | duplicate-group report | P2-01 | Done |
| P2-03 | Review visual kualitas label | error gallery dan exclusion list | P2-01 | Done |
| P2-04 | Keputusan dataset bersih | clean manifest dan keputusan pakai/perbaiki/buang | P2-02 dan P2-03 | Ready |
| P3-01 | Split bebas leakage | train/validation/test manifest | P2-04 | Backlog |
| P3-02 | Kontrak eksperimen B0/B1/B2 | experiment matrix | P3-01 | Backlog |
| P4-01 | Baseline truck detector dan tracker | model/config/metrics B0 | P3-02 | Backlog |
| P4-02 | Baseline component detector | model/config/metrics B1 | P3-02 | Backlog |
| P4-03 | Baseline visual suspicion | model/config/metrics B2 | P3-02 | Backlog |
| P4-04 | Perbandingan baseline | comparison report dan error taxonomy | P4-01 sampai P4-03 | Backlog |
| P5-01 | Adapter input video file dan RTSP | source interface dan health log | P0-03 | Backlog |
| P5-02 | ROI, tracking, dan event aggregation | satu event per truk | P4-01 dan P5-01 | Backlog |
| P5-03 | Evidence package | snapshot, clip, dan event JSON | P5-02 | Backlog |
| P6-01 | Prototype geometry relatif | geometry features per event | P5-03 | Backlog |
| P6-02 | Quality dan uncertainty gate | REVIEW_REQUIRED dan UNPROCESSABLE | P6-01 | Backlog |
| P7-01 | Rehearsal file ke RTSP | pergantian source melalui config | P5-03 dan P6-02 | Backlog |
| P7-02 | Checklist kesiapan CCTV | ready-for-JMTO package | P7-01 | Backlog |

## Progress P0-03 yang sudah dilakukan lebih awal

- [x] folder repository dipisahkan dari riset dan slide perusahaan;
- [x] storage eksternal dipisahkan dari repository;
- [x] struktur folder produk dibuat;
- [x] `.gitignore` dibuat dan diperiksa;
- [x] `.env.example` dibuat tanpa credential;
- [x] `README.md` dibuat;
- [x] virtual environment dibuat dan interpreter VS Code terverifikasi;
- [x] Git repository lokal diinisialisasi pada branch `main`;
- [x] file aman diperiksa sebelum commit;
- [x] initial commit `4f44419` dibuat;
- [x] remote dibiarkan kosong sampai isi initial commit selesai diperiksa;
- [x] `origin` kemudian diarahkan ke `venddraa/ovd-vision-system` dan `main` berhasil tracking `origin/main`.

## Checkpoint aktif

`P2-04 — membuat clean manifest tanpa mengubah canonical raw export`.

## Progress P1-01

- [x] dataset komponen `boxtruck`/`headtruck`/`muatan` difork ke workspace external-only;
- [x] snapshot v1 dibuat dari 1.095 source image tanpa preprocessing, resize, crop, atau augmentation;
- [x] COCO JSON diekspor dan dipindahkan dari repository ke external raw storage;
- [x] validasi intake awal dan checksum 1.100 file selesai;
- [x] dataset ODOL/NOT ODOL difork, diekspor, dan divalidasi dengan prosedur yang sama;
- [x] overlap sumber antardataset diperiksa dan risiko split leakage dicatat;
- [x] provenance, lisensi, archive checksum, dan extracted-file checksum disimpan dalam manifest Git.

## Evidence P2-01

- Validator: `scripts/validate_coco_datasets.py` (Python standard library, raw read-only).
- Registry: `configs/bootstrap_external/datasets.json`.
- Regression test: enam test untuk happy path, boundary tolerance, missing file, checksum mutation, JPEG, dan PNG.
- Component dataset: `PASS_WITH_WARNINGS`, 0 error, 41 warning, 79 info.
- Binary ODOL dataset: `PASS_WITH_WARNINGS`, 0 error, 30 warning, 0 info.
- Seluruh 2.161 image header dan 2.171 raw file checksum berhasil diverifikasi tanpa mismatch.
- Report machine-readable dan human-readable: `reports/p2-01/`.

## Evidence P2-02

- Auditor: `scripts/audit_dataset_duplicates.py` dengan ImageMagick hanya sebagai renderer fingerprint 24×24.
- Regression test: tujuh test baru; total 13 test project lulus.
- Sebanyak 2.161 record dipetakan menjadi 1.095 source identity; 1.066 identity digunakan oleh kedua ontology.
- Tidak ada exact duplicate internal; ada satu exact-byte pair antardataset dengan source identity yang sama.
- Sebanyak 629 frame terpetakan ke 106 video; 24 video/219 frame sudah melintasi source split Roboflow.
- Burst timestamp tervalidasi menjadi 71 grup probable-near-duplicate yang mencakup 169 image.
- Hasil final sementara: 474 atomic source group; 63 grup/317 canonical image melintasi source split.
- Kemiripan lintas-video tidak di-auto-merge; 1.942 matched pair diringkas menjadi 257 source-group review candidate.
- Source-group map, identity map, machine report, ringkasan, dan review queue: `reports/p2-02/`.

## Evidence P2-03

- Auditor: `scripts/audit_label_quality.py`; raw COCO dan pixel sumber hanya dibaca.
- Regression test: sepuluh test P2-03; total 23 test project lulus.
- Seluruh 290 quality candidate diperiksa melalui 15 contact sheet; gallery pixel disimpan lokal di external interim storage, bukan Git.
- Sebanyak 79 null image dan 66 image dengan pasangan core component tidak lengkap dikarantina; total 145 image exclusion untuk B1.
- Annotation 227 pada `VID_20210816_165022-16.jpg` dikonfirmasi sebagai micro-box artefact dan hanya akan dibuang pada derived dataset.
- Sebanyak 17 keputusan item-level tercatat (`16 RELABEL_REQUIRED`, `1 DROP_ANNOTATION`); semuanya masih `provisional_ai_assisted`.
- Seluruh 257 duplicate candidate pair memperoleh keputusan visual: 112 `GROUP_TOGETHER_FOR_SPLIT`, 145 `KEEP_SEPARATE`, tanpa penghapusan image dan tanpa konflik transitif.
- Machine report, review queue, duplicate decisions, training exclusions, dan ringkasan: `reports/p2-03/`.

## Evidence foundation

- Branch: `main`.
- Initial commit: `4f44419` (`chore: initialize OVD vision project`).
- Initial commit content: 10 file dokumentasi/configuration aman.
- Git worktree setelah commit: clean.
- Remote repository: `https://github.com/venddraa/ovd-vision-system.git`.
- Upstream branch: `main` → `origin/main`.
- Batas remote: hanya codebase dan dokumentasi non-sensitif; izin remote resmi untuk artifact internal JMTO masih terbuka.
