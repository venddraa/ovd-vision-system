# Project Charter — OVD Vision System

**Project:** AI Vision untuk Deteksi Kendaraan Overdimension<br>
**Konteks:** Proyek magang JM/JMTO<br>
**Tim:** 2 orang<br>
**Status:** Working assumptions untuk Phase 00<br>
**Terakhir diperbarui:** 3 September 2026<br>
**Persetujuan mentor:** Belum

## 1. Latar belakang

WIM telah digunakan untuk mendeteksi atau menyaring aspek overloading berdasarkan berat kendaraan.

Proyek ini difokuskan pada aspek overdimension menggunakan AI/computer vision. Sistem akan mengolah video kendaraan dan menghasilkan event yang dapat diperiksa operator.

## 2. Problem statement

Belum tersedia sistem vision yang terbukti dapat mengamati kendaraan dan muatannya, memperkirakan indikasi penyimpangan dimensi, serta menghasilkan evidence per kendaraan pada kondisi CCTV JMTO.

## 3. Tujuan produk

Membangun pipeline AI vision yang:

- mendeteksi dan melacak kendaraan barang;
- memisahkan bagian kendaraan, bak/bodi, dan muatan;
- mengevaluasi indikasi overdimension secara visual atau geometris;
- membuat satu keputusan untuk setiap kendaraan yang melintas;
- menyimpan evidence untuk review operator;
- dapat menerima input video file dan nantinya RTSP CCTV.

## 4. Posisi sistem

Sistem vision menangani indikasi overdimension.

WIM tetap menjadi sumber aspek overloading. LiDAR, laser, atau pengukuran manual dapat digunakan sebagai comparator atau ground truth, bukan sensor utama MVP.

## 5. Unit keputusan

Unit keputusan adalah satu `vehicle_event`, bukan satu frame.

Beberapa frame dari kendaraan yang sama harus digabung melalui tracking dan temporal aggregation.

## 6. Output event

Sistem menggunakan empat status:

1. `NORMAL`
2. `SUSPECTED_OVERDIMENSION`
3. `REVIEW_REQUIRED`
4. `UNPROCESSABLE`

`REVIEW_REQUIRED` digunakan ketika hasil dekat ambang atau uncertainty tinggi.

`UNPROCESSABLE` digunakan ketika kendaraan terpotong, terlalu kecil, blur, tertutup, atau geometri kamera tidak mendukung.

## 7. Scope sementara Phase 00

Phase 00 menggunakan data non-internal sebelum CCTV JMTO tersedia.

Yang termasuk scope:

- dua dataset publik Roboflow;
- audit label, lisensi, provenance, dan duplikasi;
- baseline deteksi kendaraan;
- baseline deteksi bak/bodi dan muatan;
- baseline visual `ODOL/NOT ODOL` sebagai pembanding;
- video file sebagai input awal;
- ROI, tracking, dan event aggregation;
- snapshot, clip, dan event JSON;
- prototype geometry relatif;
- quality gate dan uncertainty;
- interface input yang siap diganti menjadi RTSP.

## 8. Scope MVP sementara

- satu kamera;
- satu lane atau road ROI;
- kondisi siang hari terlebih dahulu;
- kendaraan barang/truk;
- screening/operator alert;
- evaluasi per vehicle event;
- satu dimensi atau jenis pelanggaran lebih dahulu setelah view CCTV diketahui.

Dimensi awal yang paling masuk akal untuk diuji adalah tinggi atau vertical cargo envelope. Keputusan final menunggu audit posisi kamera.

## 9. Out of scope Phase 00

Phase 00 tidak mencakup:

- penindakan hukum otomatis;
- klaim akurasi pada CCTV JMTO;
- klaim pengukuran dalam meter tanpa calibration;
- estimasi berat dari gambar;
- integrasi produksi dengan WIM, ANPR, RFID, atau BLUe;
- upload data internal ke layanan publik;
- pengujian seluruh lokasi, cuaca, malam, dan semua jenis kendaraan;
- penggunaan confidence model sebagai bukti akurasi.

## 10. Arsitektur kerja

```text
Video file / RTSP
        ↓
Camera and stream health check
        ↓
Road/lane ROI
        ↓
Truck detection
        ↓
Multi-object tracking
        ↓
Vehicle/body/cargo perception
        ↓
Geometry and temporal aggregation
        ↓
Quality and uncertainty gate
        ↓
Rule engine
        ↓
Vehicle event and evidence package

```

## 11. Baseline yang dibandingkan

### B0 — Truck detector dan tracker

Tujuan: memastikan setiap truk dalam ROI menghasilkan satu event.

### B1 — Component detector

Mendeteksi `headtruck`, `boxtruck`, dan `muatan`.

B1 menjadi kandidat jalur utama karena output komponennya dapat digunakan oleh geometry engine.

### B2 — Visual-suspicion baseline

Menggunakan label publik `ODOL/NOT ODOL`.

B2 hanya menjadi pembanding atau triage. Output-nya tidak disebut keputusan legal overdimension.

## 12. Batas penggunaan dataset publik

Dataset publik hanya digunakan untuk:

- warm-start;
- pengujian pipeline;
- baseline model;
- eksplorasi error;
- persiapan sebelum data internal tersedia.

Dataset publik tidak dianggap sebagai:

- ground truth legal;
- bukti pengukuran fisik;
- bukti performa CCTV JMTO;
- pengganti independent local test set.

## 13. Data dan keamanan

- Raw data dan model weight disimpan di luar repository Git.
- Rekaman CCTV JMTO tidak boleh diunggah ke workspace publik.
- Credential VPN, token, dan URL RTSP tidak boleh masuk Git.
- Data internal hanya digunakan setelah storage, akses, dan retensinya disetujui.
- Repository hanya menyimpan kode, dokumentasi aman, konfigurasi nonrahasia, dan manifest.

## 14. Pertanyaan yang belum terjawab

1. Target OVD final: konstruksi kendaraan, cargo envelope, clearance, atau kombinasi?
2. Dimensi prioritas: tinggi, lebar, panjang, atau protrusion?
3. Apakah produk cukup screening atau harus menghasilkan ukuran meter?
4. Kamera yang diberikan fixed atau PTZ?
5. Apakah stream asli tersedia melalui RTSP?
6. Apakah data BLUe/KIR dapat diakses?
7. Apakah ground truth ukuran fisik tersedia?
8. Siapa yang memvalidasi label pelanggaran?
9. Di mana video internal boleh disimpan?
10. Apa tindakan operator setelah menerima alert?

## 15. Exit criteria Phase 00

Phase 00 selesai apabila tersedia:

- external dataset audit report;
- split dataset yang bebas leakage;
- baseline B0, B1, dan B2 yang reproducible;
- pipeline video offline per-event;
- evidence package;
- prototype geometry dan quality gate;
- CCTV/VPN/RTSP intake checklist;
- daftar keterbatasan dan kebutuhan Phase 01.

## 16. Perubahan scope

Semua perubahan target, label, dimensi, sumber data, atau klaim produk harus dicatat dalam decision log.

Keputusan sementara dapat diganti setelah ada bukti CCTV atau arahan mentor, tetapi hasil lama tidak dihapus.
