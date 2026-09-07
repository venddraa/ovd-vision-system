# Tasks — Phase 00 External Bootstrap

**Terakhir diperbarui:** 7 September 2026

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
| P1-01 | Intake dua dataset Roboflow | raw dataset eksternal di storage | P0-03 dan login pengguna | Ready |
| P1-02 | Dokumentasi provenance dataset | source manifest, lisensi, versi, checksum | P1-01 | Backlog |
| P2-01 | Validasi file dan anotasi | statistik file, class map, broken-label report | P1-02 | Backlog |
| P2-02 | Audit exact dan near duplicate | duplicate-group report | P2-01 | Backlog |
| P2-03 | Review visual kualitas label | error gallery dan exclusion list | P2-01 | Backlog |
| P2-04 | Keputusan dataset bersih | clean manifest dan keputusan pakai/perbaiki/buang | P2-02 dan P2-03 | Backlog |
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
- [x] remote tetap kosong.

## Checkpoint aktif

`P1-01 — mengunduh dua dataset Roboflow ke external raw storage tanpa preprocessing/crop tambahan`.

## Evidence foundation

- Branch: `main`.
- Initial commit: `4f44419` (`chore: initialize OVD vision project`).
- Initial commit content: 10 file dokumentasi/configuration aman.
- Git worktree setelah commit: clean.
- Remote repository: belum dikonfigurasi.
