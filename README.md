# Sistem Chat Kluster — MK Pemrograman Jaringan Kluster (MKKL1028)

**Mata kuliah:** Pemrograman Jaringan Kluster · Program Studi Ilmu Komputer UBBG

Sistem chat antar laptop yang berjalan di atas **dua jalur protokol sekaligus**:

- **TCP** — andal, dijamin sampai. Dipakai untuk chat biasa, perintah kendali, dan
  mesh antar node.
- **UDP** — cepat, tanpa jaminan. Dipakai untuk detak keberadaan (heartbeat),
  pengukuran latensi, dan pesan yang boleh hilang.

Bedanya tidak cuma teori: **dashboard mengukur keduanya dan menampilkan angkanya
bersamaan**, supaya perbedaan TCP dan UDP terlihat dengan angka, bukan klaim.

## Arsitektur

```
        PC01 ─┐
        PC02 ─┤            ┌──────────┐        ┌──────────┐
        PC03 ─┼── TCP ────▶│  node n1 │◀─ TCP ─│  node n2 │◀── TCP ── PC04
        PC05 ─┤            │ (server) │  mesh  │ (server) │              PC06
        PC06 ─┘            └────┬─────┘        └──────────┘
                                 │ UDP
                                 └──── detak heartbeat (1 detik)
                                       pengukuran latensi (5 detik)

  Pengguna ──TCP──▶ node  ──TCP mesh──▶ node lain ──TCP──▶ Pengguna di node lain
           ──UDP──▶       ──UDP──▶
```

Tiap node adalah **server penuh**: menerima pengguna, menyimpan basis data, dan
menyajikan dashboard-nya sendiri. Node saling menemukan lewat detak UDP, lalu
membentuk mesh TCP.

## Yang membedakan dari sistem chat biasa

| Aspek | Chat biasa (satu server) | Sistem ini |
|---|---|---|
| Titik pusat | Satu server = kalau mati, semua mati | Banyak node, mesh TCP antar node |
| Deteksi kegagalan | Tunggu timeout TCP (puluhan detik) | Detak UDP, **3,5 detik** |
| Jalur pesan | Satu | TCP (dijamin) **dan** UDP (cepat, tanpa jaminan) |
| Pengukuran | Tidak ada | TCP vs UDP dibandingkan tiap 5 detik |
| Dedup pesan | — | Tiap pesan punya `kode` unik, dicek saat melintasi node |

## ⚠️ Sebelum lanjut baca

Dokumentasi lengkap + status + jebakan ada di vault Obsidian:

```
Documents/PENYIMPANAN IDE/Project/TUGAS MK PJC/
├── README.md          ← index
└── HANDOFF.md         ← MULAI DI SINI: status, next steps, 24 jebakan
```

Folder ini **belum git repo**. Kalau mau pushed ke GitHub: `MKKL1028_2026_kelompok1_<judul>`.

## Peta 3D (WebGL + aset GLB)

Dashboard punya **dua renderer peta**:

| Renderer | Dipakai kalau | Model |
|---|---|---|
| **WebGL (three.js)** | WebGL tersedia (default) | File `.glb` — model asli dari Tripo |
| **Canvas 2D** | WebGL tidak tersedia / dipilih manual | Model prosedural (balok) |

Jadi **dashboard tidak pernah kosong** — kalau file GLB belum dikirim,
model bawaan tetap jalan.

**Aset yang sudah terpasang** (model asli, bukan placeholder):

| File | Asal | Ukuran | Segitiga |
|---|---|---|---|
| `server.glb` | Sketchfab — *server racking system* | 150 KB | 55.398 |
| `laptop.glb` | Sketchfab — *laptop* | 112 KB | 388 |

Total 262 KB (semula 14,3 MB sebelum dioptimasi dengan Draco + WebP).

Cara mengirim aset 3D berikutnya: baca **`PANDUAN-ASET-3D.md`**.

Ringkasnya:
- Format: **GLB** (bukan OBJ/FBX)
- Taruh di `server/static/asset/`
- Nama yang dikenali: `server.glb` / `server_racking_system.glb` dan
  `pc.glb` / `laptop.glb`, atau satu `scene.glb` gabungan
- three.js + Draco sudah vendored di `server/static/vendor/` → **offline**
- Utamakan `gltf-transform optimize --compress draco --texture-compress webp`
  supaya file tetap ringan (1,9 juta face → 55 ribu)

Library lokal di `server/static/vendor/`:
- `three.module.js` (three.js r160)
- `GLTFLoader.js`, `DRACOLoader.js`, `BufferGeometryUtils.js`
- `draco/` (decoder WASM)

## Struktur folder

```
PROJECT MK PJC/
├── PROTOKOL.md          bentuk pesan yang dipertukarkan (baca ini dulu)
├── README.md            file ini
├── server/              ← satu node kluster
│   ├── server.py        server TCP + UDP + mesh + dashboard
│   ├── static/index.html dashboard mode terang + peta 3D
│   ├── uji-cepat.py     18 pemeriksaan otomatis
│   └── docs/bukti/      tangkapan layar dashboard
├── user/                ← program yang dijalankan tiap laptop
│   ├── client.py
│   ├── nama.txt         nama laptop ini
│   └── server.txt       alamat node server
└── docs/
    ├── arsitektur.md    komponen & keputusan teknis
    └── pengujian.md     apa yang sudah diuji dan caranya
```

## Cara Menjalankan

### Kebutuhan

- Python 3.10 atau lebih baru
- Tidak ada `pip install` — semuanya modul bawaan (`socket`, `threading`,
  `json`, `sqlite3`, `http.server`)

### 1. Jalankan node server (di laptop ketua)

```bash
cd server
python3 server.py
```

Keluarannya seperti ini:

```
================================================================
  SERVER KLUster — MK PEMROGRAMAN JARINGAN KLUSTER
================================================================
  Node        : n1
  Port TCP    : 5000   (untuk pengguna)
  Port UDP    : 5010   (heartbeat + pengukuran)
  Dashboard   : http://localhost:8081
  Basis data  : .../server/data/kluster_n1.db

  Alamat untuk laptop pengguna:
      192.168.1.7:5000
================================================================
```

### 2. Jalankan node kedua (node kluster lain)

Di laptop atau mesin lain dalam jaringan yang sama:

```bash
cd server
python3 server.py --node n2 --port 5001 --web 8082 --udp 5011 \
                  --tetangga 192.168.1.7:5000:5010
```

Node kedua otomatis ditemukan oleh node pertama lewat detak UDP — jadi
`--tetangga` hanya perlu diisi **salah satu arah**.

### 3. Jalankan di tiap laptop anggota

```bash
cd user
python3 client.py --server 192.168.1.7
```

Nama laptop dibaca dari `nama.txt`. Alamat server dibaca dari `server.txt`
(jadi anggota tidak perlu mengetik IP).

### 4. Buka dashboard

**http://localhost:8081**

## Cara Memakai Terminal Pengguna

```
halo semua              → kirim ke semua orang (TCP)
/ke PC04 (Deski) halo   → kirim ke satu orang saja
/udp pesan cepat        → kirim lewat UDP (tidak dijamin sampai)
/online                 → lihat siapa saja yang aktif
/keluar                 → keluar
```

## Menguji Semua Sekaligus

```bash
cd server
python3 uji-cepat.py
```

Menyalakan dua node sendiri (di port uji 5099/5098 dan 8199/8198) lalu
memeriksa 18 hal: sambungan, pesan TCP, privasi pesan, jalur UDP,
penemuan node lewat heartbeat, mesh antar node, perintah, dan penolakan nama
kembar. Tidak mengganggu server yang sedang jalan di port 5000.

## Dashboard

- **Mode terang** (bawaan) + **mode gelap** — tombol di kanan atas, pilihan
  disimpan di `localStorage`.
- **Peta kluster 3D** — kubus node server di tengah, node kluster lain di
  cincin luar, laptop mengelilingi. Seret untuk memutar, gulir untuk zoom.
  Render pakai `<canvas>` + proyeksi perspektif sendiri, **tanpa Three.js** —
  laptop anggota sering tanpa internet dan `npm install` di enam laptop
  berminggu-minggu.
- **Paket animasi** — paket bergerak pengirim → server → penerima, warna
  berbeda: biru untuk TCP, ungu untuk UDP.
- **Angka latensi** TCP vs UDP ke node tetangga, diperbarui tiap 5 detik.
- **Kirim pesan dari dashboard** — bisa pilih jalur TCP atau UDP.
- **Unduh riwayat CSV**.

## Pengaman Penting

Perintah **matikan, restart, dan kunci ditolak di macOS** secara bawaan.
Pengamannya ada **di dalam kode**, bukan hanya dihindari oleh skrip uji — jadi
perintah daya tetap ditolak walau dikirim dari mana pun.

Di Windows, perintah daya butuh terminal **Run as Administrator**.

## Yang Belum Diuji

Dicatat terbuka, bukan disembunyikan:

1. **Enam laptop sungguhan yang terpisah.** Pengujian masih di satu mesin.
2. **Perintah daya di Windows.** Baru dibuktikan bahwa perintahnya *ditolak*
   di macOS.
3. **WiFi kampus.** Perlu diuji dengan dua laptop lebih dahulu sebelum hari
   presentasi — kampus sering memasang client isolation.
4. **Failover otomatis.** Klien sudah menerima daftar node lain saat menyambung,
   tapi belum ada percobaan sambung ulang otomatis saat node mati.
5. **Beban tinggi.** Belum diukur berapa pengguna yang bisa dilayani bersamaan.
