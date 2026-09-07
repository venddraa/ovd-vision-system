# Environment Audit — OVD Vision System

**Tanggal audit:** 3 September 2026<br>
**Phase:** 00 — External Bootstrap<br>
**Perangkat:** Laptop development lokal<br>
**Status:** Initial audit

## 1. Ringkasan perangkat

| Komponen | Hasil |
|---|---|
| Operating system | Fedora Linux |
| Kernel | `7.1.9-200.fc44.x86_64` |
| Architecture | `x86_64` |
| CPU | Intel Core i5-13500H |
| Logical CPU | 16 |
| RAM | 15 GiB |
| Swap | 8 GiB |
| Storage workspace | 475 GiB |
| Storage terpakai saat audit | sekitar 123 GiB |
| Storage tersedia saat audit | sekitar 350 GiB |
| GPU | Intel Iris Xe Graphics |
| NVIDIA GPU | tidak terdeteksi |
| CUDA | tidak tersedia |

## 2. Tooling yang tersedia

| Tool | Status | Versi/keterangan |
|---|---|---|
| Git | tersedia | `2.55.0` |
| VS Code | tersedia | Python, Pylance, Jupyter, Ruff, dan Error Lens terpasang |
| Python | tersedia | `3.12.14` |
| Python venv | tersedia | modul `venv` dapat digunakan |
| Python pip bootstrap | tersedia | `ensurepip 26.0.1` |
| FFmpeg | tersedia | `8.1.2` |
| FFprobe | tersedia | bagian dari FFmpeg |
| Docker | tersedia | `29.7.2` |
| Podman | executable tersedia | belum diperlukan pada Phase 00 |
| Make | tersedia | dapat digunakan untuk command automation |
| Git LFS | belum terdeteksi | belum diperlukan karena weights dan data tidak masuk Git |

## 3. Perintah audit

Audit dapat diulang dengan:

```bash
git --version
python3 --version
python3 -m ensurepip --version
ffmpeg -version
ffprobe -version
lscpu
free -h
df -h .
lspci | rg -i 'vga|3d|display'
nvidia-smi
```

## 4. Kelayakan pekerjaan lokal

  Laptop dinilai layak untuk:

  - pengembangan kode;
  - audit dan pembersihan dataset;
  - visualisasi anotasi;
  - unit test;
  - pemrosesan video dengan FFmpeg;
  - inference model ringan;
  - smoke test pada subset kecil;
  - pembangunan pipeline offline.

  Laptop tidak menjadi compute utama untuk:

  - training model penuh;
  - hyperparameter sweep;
  - foundation-model segmentation yang berat;
  - inference real-time banyak stream;
  - simulasi deployment produksi.

## 5. Strategi compute hybrid

  Proyek menggunakan pembagian berikut:

### Local development

  Laptop menjadi source of truth untuk:

  - source code;
  - konfigurasi;
  - dataset manifest;
  - split manifest;
  - unit test;
  - laporan eksperimen;
  - model evaluation;
  - artifact registry.

### Kaggle Notebook

  Kaggle menjadi remote training executor utama untuk dataset eksternal.

  Accelerator yang dipilih adalah T4 x2. Eksperimen awal menggunakan satu GPU terlebih dahulu. P100 tidak digunakan karena
  sedang dihentikan Kaggle dan memiliki risiko kompatibilitas dengan default PyTorch image.

### Google Colab

  Colab menjadi alternatif untuk debugging atau eksperimen ketika Kaggle tidak tersedia. Colab bukan source of truth dan
  ketersediaan GPU tidak dianggap terjamin.

### Company-approved compute

  Training atau fine-tuning yang melibatkan rekaman dan anotasi internal JMTO hanya dilakukan pada infrastruktur yang disetujui
  perusahaan.

## 6. Kontrak remote training

  Setiap remote training wajib menerima:

  - training script atau notebook;
  - model configuration;
  - dataset source dan version;
  - split manifest;
  - random seed;
  - preprocessing dan augmentation;
  - dependency version;
  - experiment ID.

  Setiap remote training wajib menghasilkan:

  - best dan last model weights;
  - training configuration;
  - metrics CSV/JSON;
  - training curves;
  - stdout/stderr log;
  - confusion matrix jika relevan;
  - qualitative prediction samples;
  - environment/package information;
  - limitation dan failure notes.

  Output Kaggle/Colab harus diunduh ke `../storage/artifacts` dan dicatat pada experiment registry lokal.

## 7. Model penggunaan Kaggle API

  Kaggle API/CLI digunakan sebagai remote job submission, bukan sebagai raw GPU service.

  Alurnya:

  ```text
  local package
      -> Kaggle kernel push
      -> remote GPU execution
      -> kernel status
      -> kernel output download
      -> local artifact verification
  ```

  API tidak membuat GPU Kaggle muncul sebagai CUDA device pada laptop lokal.

  Implementasi CLI dilakukan setelah notebook training pertama terbukti dapat berjalan secara manual dan reproducible.

## 8. Aturan keamanan cloud notebook

  - Hanya data eksternal berizin yang boleh digunakan pada bootstrap Kaggle/Colab.
  - Notebook dibuat private selama pengembangan.
  - API token tidak ditulis di notebook atau repository.
  - Secret menggunakan Kaggle Secrets atau Colab Secrets.
  - Data CCTV dan dokumen internal JMTO tidak diunggah tanpa izin tertulis.
  - Output diperiksa agar tidak menyimpan credential atau data sensitif.
  - Remote notebook bukan penyimpanan permanen.
  - Semua artifact penting harus disalin kembali ke storage lokal.

## 9. Risiko dan mitigasi

| Risiko | Mitigasi |
|---|---|
| Session Kaggle/Colab berhenti | simpan checkpoint dan gunakan run yang dapat dilanjutkan |
| GPU quota habis | gunakan GPU hanya saat training dan matikan setelah selesai |
| Hardware berubah | catat GPU pada setiap run |
| T4 x2 hanya memakai satu GPU | mulai single-GPU; aktifkan multi-GPU hanya setelah divalidasi |
| Artifact hilang dari runtime | simpan ke working output dan segera download |
| Environment remote berubah | catat package version dan dependency lock |
| Data internal bocor | batasi cloud notebook pada data eksternal sampai mendapat izin |

## 10. Keputusan compute sementara

  - Semua pekerjaan yang layak dijalankan lokal tetap dilakukan lokal.
  - Smoke test dilakukan lokal sebelum memakai GPU cloud.
  - Full baseline training data eksternal menggunakan Kaggle T4.
  - Google Colab menjadi fallback.
  - P100 tidak digunakan.
  - Multi-GPU tidak digunakan pada eksperimen pertama.
  - Kaggle CLI/API baru dipakai setelah notebook manual stabil.
  - Data JMTO tidak masuk Kaggle atau Colab tanpa persetujuan perusahaan.

## 11. Kesimpulan

  Environment lokal cukup untuk development, audit data, pipeline video, dan pengujian ringan.

  Strategi compute proyek adalah local-first dengan Kaggle T4 sebagai remote batch-training executor untuk data eksternal.
  Colab menjadi fallback, sedangkan data internal JMTO menunggu infrastruktur yang disetujui perusahaan.

## 12. Referensi layanan compute

- Kaggle Notebooks: https://www.kaggle.com/docs/notebooks
- Kaggle CLI kernel commands: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md
- Kaggle kernel accelerator metadata: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md
- Penghentian P100 pada 15 September 2026: https://www.kaggle.com/discussions/product-announcements/735239
- Google Colab resource limits: https://research.google.com/colaboratory/faq.html

Informasi accelerator dan quota bersifat time-sensitive. Periksa kembali dokumentasi serta tampilan akun sebelum setiap rangkaian eksperimen besar.
