# OVD Vision System

Sistem AI vision untuk screening kendaraan yang terindikasi overdimension dalam konteks JMTO.

## Status

Phase 00 — External Bootstrap.

Dataset publik hanya digunakan untuk warm-start dan pengujian pipeline. Hasilnya bukan ground truth legal dan bukan bukti performa pada CCTV JMTO.

## Scope MVP sementara

- satu kamera dan satu lane ROI;
- kondisi siang hari terlebih dahulu;
- satu keputusan per vehicle event;
- deteksi truk, bak/bodi, dan muatan;
- output NORMAL, SUSPECTED_OVERDIMENSION, REVIEW_REQUIRED, atau UNPROCESSABLE;
- screening/operator alert, bukan penindakan otomatis.

## Keamanan data

Raw dataset, video, model weight, credential, URL RTSP, dan dokumen internal tidak disimpan dalam repository ini.