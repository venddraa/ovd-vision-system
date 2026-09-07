# Experiment Convention — OVD Vision System

**Versi:** 0.1<br>
**Tanggal:** 3 September 2026<br>
**Berlaku untuk:** Phase 00 — External Bootstrap

## 1. Tujuan

Dokumen ini memastikan setiap eksperimen dapat ditelusuri, dibandingkan, dan dijalankan ulang. Nilai eksperimen bukan hanya model weight, tetapi hubungan yang jelas antara data, split, kode, konfigurasi, environment, dan hasil evaluasi.

## 2. Prinsip utama

- Laptop lokal menjadi source of truth untuk kode, konfigurasi, manifest, dan laporan.
- Raw data bersifat immutable.
- Kaggle/Colab hanya menjadi remote executor untuk data eksternal yang diizinkan.
- Satu perubahan hipotesis utama per eksperimen.
- Test set tidak digunakan untuk memilih epoch, model, hyperparameter, atau threshold.
- Hasil dataset eksternal dan JMTO kelak selalu dilaporkan terpisah.
- Confidence model bukan ukuran akurasi dan bukan keputusan legal.
- Eksperimen gagal tetap dicatat jika menghasilkan insight yang relevan.

## 3. Baseline ID

| ID | Nama | Tujuan |
|---|---|---|
| B0 | Truck detection and tracking | memastikan setiap truk dalam ROI menghasilkan satu vehicle event |
| B1 | Component detection | mendeteksi bagian kendaraan, bak/bodi, dan muatan untuk geometry |
| B2 | Visual-suspicion baseline | membandingkan label visual `ODOL/NOT ODOL` tanpa klaim legal |

## 4. Experiment ID

Format:

```text
<phase>-<baseline>-<dataset>-<model>-<yyyymmdd>-r<sequence>
```

Contoh hipotetis:

```text
p00-b1-truck-components-model-small-20260910-r001
```

Aturan:

- gunakan huruf kecil;
- gunakan tanda hubung;
- jangan memakai spasi;
- `sequence` tiga digit dan tidak digunakan ulang;
- nama model aktual baru ditulis setelah stack diputuskan;
- experiment ID tidak diubah setelah run dimulai.

## 5. Dataset ID dan provenance

Format dataset:

```text
<origin>-<project>-v<source-version>-clean<revision>
```

Contoh hipotetis:

```text
roboflow-truck-components-v6-clean001
```

Manifest minimum:

- dataset ID;
- source URL;
- source owner;
- license;
- tanggal diperoleh;
- source version;
- jumlah file dan annotation;
- class map;
- preprocessing dari sumber;
- checksum;
- exclusion list;
- duplicate-group reference;
- allowed use;
- sensitivity classification.

## 6. Split contract

- Unit split adalah source scene, video, atau vehicle event; bukan frame acak.
- Exact dan near duplicate berada dalam split yang sama.
- Jika grouping tidak dapat diketahui, tandai risiko leakage secara eksplisit.
- Test set dibekukan sebelum model comparison.
- Validation set digunakan untuk model selection dan threshold tuning.
- Test set hanya dibuka untuk evaluasi checkpoint yang sudah ditetapkan.
- Internal JMTO test set kelak tidak pernah digabung dengan external test set.
- Setiap split mempunyai ID, manifest, seed, dan checksum.

Format split ID:

```text
<dataset-id>-split<revision>-seed<seed>
```

## 7. Seed policy

- Seed baseline awal: `42`.
- Semua sumber random harus menerima seed jika framework mendukung.
- Benchmark eksploratif cukup satu seed dan harus diberi label exploratory.
- Perbandingan kandidat final menggunakan minimal tiga seed jika biaya compute memungkinkan.
- Mean, standard deviation, serta seluruh hasil individual dilaporkan; jangan hanya hasil terbaik.

## 8. Konfigurasi minimum run

Setiap run harus mencatat:

- experiment ID dan tujuan;
- baseline ID;
- dataset ID dan split ID;
- model architecture dan pretrained weight;
- image size;
- batch size;
- epoch;
- optimizer dan learning rate;
- scheduler;
- preprocessing;
- augmentation;
- early stopping;
- random seed;
- package/environment version;
- device, GPU, dan VRAM;
- code version/commit;
- waktu mulai dan selesai;
- status `completed`, `failed`, atau `aborted`.

## 9. Local-to-remote contract

### Sebelum remote run

1. Lakukan syntax/import test lokal.
2. Jalankan smoke test pada data kecil bila memungkinkan.
3. Pastikan tidak ada secret dalam notebook, script, config, atau output cell.
4. Pastikan dataset dan notebook mempunyai hak penggunaan yang sesuai.
5. Bekukan experiment ID, config, dataset ID, dan split ID.

### Di Kaggle/Colab

- validasi device dengan framework, bukan hanya keberadaan command GPU;
- catat nama GPU dan jumlah device;
- eksperimen pertama memakai satu T4;
- tulis seluruh output penting ke direktori kerja yang akan dipersist;
- jangan mengandalkan state interaktif yang tidak terekam dalam notebook/script;
- jangan memasukkan data JMTO tanpa persetujuan perusahaan.

### Setelah remote run

1. Unduh weight, metrics, plots, config, dan log.
2. Simpan artifact besar di `../storage/artifacts/<experiment-id>/`.
3. Verifikasi ukuran dan checksum artifact.
4. Simpan metadata aman dan laporan ringkas di repository.
5. Catat run gagal beserta error dan keputusan retry.

## 10. Struktur artifact

```text
../storage/artifacts/<experiment-id>/
├── weights/
│   ├── best.*
│   └── last.*
├── metrics/
│   ├── metrics.json
│   └── metrics.csv
├── plots/
├── predictions/
├── logs/
├── run-config.yaml
├── environment.txt
└── CHECKSUMS.sha256
```

Artifact besar tidak masuk Git.

Metadata aman yang boleh masuk repository:

```text
experiments/<phase>/<experiment-id>/
├── README.md
├── run.yaml
├── metrics-summary.json
└── error-analysis.md
```

## 11. Metrik minimum

### B0 — Detection dan tracking

- precision dan recall detection;
- mAP50 dan mAP50-95 bila ground truth mendukung;
- event capture recall;
- duplicate event rate;
- missed-event rate;
- ID switches/track fragmentation;
- latency, FPS, dan dropped frames.

### B1 — Component detection

- per-class precision, recall, AP;
- confusion antarkomponen;
- clipped-object failure rate;
- boundary/extreme-point error bila anotasi mendukung;
- processable coverage;
- error taxonomy per sudut, occlusion, dan tipe muatan.

### B2 — Visual-suspicion

- precision, recall, macro F1, dan confusion matrix;
- suspected-class recall;
- false alerts per jumlah kendaraan;
- calibration/reliability jika probabilitas dipakai;
- hasil harus disebut visual suspicion, bukan legal OVD accuracy.

### Event-level system

- satu event per kendaraan;
- false alerts per 1.000 truck events atau per jam;
- `REVIEW_REQUIRED` rate;
- `UNPROCESSABLE` rate;
- latency dari masuk ROI sampai event selesai;
- metrik per kondisi, bukan agregat saja.

## 12. Model selection

Urutan prioritas pemilihan model:

1. memenuhi recall/coverage sesuai tujuan screening;
2. false alert masih dapat ditangani operator;
3. kegagalan dapat dijelaskan;
4. inference memenuhi kebutuhan runtime;
5. ukuran dan dependency layak dideploy;
6. baru kemudian memilih skor agregat tertinggi.

Model tidak dipilih hanya berdasarkan satu nilai mAP atau accuracy.

## 13. Run failure policy

Sebuah run dihentikan jika:

- dataset/split salah;
- secret atau data terlarang terdeteksi;
- loss menjadi NaN/inf;
- device tidak sesuai kontrak;
- output penting tidak dapat dipersist;
- estimasi waktu melampaui session tanpa checkpoint;
- konfigurasi tidak tercatat.

Untuk run gagal, catat:

- experiment ID;
- tahap kegagalan;
- pesan error;
- artifact yang sempat dihasilkan;
- dugaan penyebab;
- keputusan `retry`, `revise`, atau `stop`.

Jangan menimpa run gagal. Retry mendapat sequence baru.

## 14. Definition of Done eksperimen

Eksperimen dinyatakan selesai jika:

- tujuan/hypothesis tertulis;
- data dan split dapat ditelusuri;
- config serta environment tercatat;
- run dapat dijalankan ulang;
- metrics dan artifact telah disalin dari runtime sementara;
- test protocol tidak dilanggar;
- contoh keberhasilan dan kegagalan tersedia;
- limitation serta next decision ditulis;
- tidak ada data atau secret yang melanggar kebijakan.

## 15. Perubahan konvensi

Perubahan pada naming, metric, split, seed, atau artifact contract harus dicatat dalam decision log. Run lama tidak diganti namanya agar provenance tetap terjaga.
