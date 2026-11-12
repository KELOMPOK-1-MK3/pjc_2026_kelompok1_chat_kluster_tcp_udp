# Panduan Aset 3D (GLB) — Dashboard Kluster PJC

Dokumen ini menjelaskan file apa yang dibutuhkan dashboard untuk memakai
model 3D asli, dan bagaimana cara mengirimkannya.

---

## Status: model sudah masuk ✅

Aset yang sekarang terpasang di `server/static/asset/`:

| File | Asal | Ukuran | Segitiga | Catatan |
|---|---|---|---|---|
| `server.glb` | `server_racking_system.glb` (Sketchfab) | 150 KB | 55.398 | Draco + WebP, sudah dioptimasi |
| `laptop.glb` | `laptop.glb` (Sketchfab) | 112 KB | 388 | Draco + WebP, ada tekstur |

Total: **262 KB** (semula 14,3 MB sebelum optimasi).

Dashboard sudah menampilkan model asli — rak server dan laptop, bukan balok.

---

## Kalau nanti ada model tambahan

**Yang dibutuhkan: file `.glb` (glTF Binary).**

Formatnya: **GLB** — pilih ini di Tripo saat export (bukan OBJ, bukan FBX).

Tiga cara kirim, pilih salah satu:

| Cara | File yang dikirim | Keterangan |
|---|---|---|
| **A. Satu file (paling praktis)** | `scene.glb` | Isi satu scene berisi model **server** + **PC** sekaligus. Loader memisahkan otomatis berdasarkan nama node. |
| **B. Dua file terpisah** | `server.glb` + `pc.glb` | Kalau punya dua model terpisah. |
| **C. Dua file, nama lain** | `server.glb` / `server_racking_system.glb` + `pc.glb` / `laptop.glb` | Loader mencoba beberapa nama. |

Kalau **Cara A** dipakai, node di dalam scene **wajib diberi nama** `server` dan `pc`.

---

## Cara A — satu file gabungan (disarankan)

1. Di Tripo, buka scene yang berisi **model server** dan **model PC**.
2. Pastikan ada dua objek terpisah (bukan satu mesh gabungan).
3. Beri nama objeknya:
   - server → `server`
   - PC → `pc`
4. Export → pilih format **GLB**.
5. Simpan hasilnya sebagai `scene.glb`.
6. Taruh di:

```
PROJECT MK PJC/server/static/asset/scene.glb
```

Selesai. Dashboard otomatis pakai.

**Kalau nama tidak persis**, loader tetap akan mencoba mencari yang
mengandung kata `server`/`rack`/`node` (untuk server) dan
`pc`/`desktop`/`monitor`/`komputer` (untuk PC). Jadi asal mengandung
kata yang mirip, aman.

---

## Cara B — dua file terpisah

Taruh dua file ini di `server/static/asset/`:

```
server.glb
pc.glb
```

---

## Aturan teknis yang perlu dijaga

### 1. Sumbu Y = atas (standar glTF)

Model harus "berdiri" di sumbu Y, bukan Z atau X. Kalau modelnya
terbalik (kepalanya di bawah), dashboard akan menampilkannya
terbalik juga.

### 2. Ukuran asal TIDAK masalah

Dashboard menormalisasi otomatis: tinggi model dihitung, lalu diskalakan
ke tinggi tetap di peta (server 17 unit, PC 14 unit). Jadi model 5 cm
atau 5 meter akan tampil sama proporsinya.

Yang penting **proporsinya**, bukan ukuran fisiknya.

### 3. Proporsi harus masuk akal

- **Server** → rak, bardatar, lebar > tinggi. Boleh bertumpuk drive bay.
- **PC** → dominating tinggi dari lebar. Kalau PC lebih lebar dari
  tingginya, di peta dia akan terlihat seperti "baris datar" dan tidak
  terbaca sebagai komputer.

### 4. Boleh bertekstur

Tekstur ikut terbawa di dalam `.glb` (binary). Tidak perlu file
terpisah. Justru **disarankan** pakai tekstur — model tanpa tekstur
akan terlihat seperti balok warna.

### 5. Ukuran file

- Batas nyaman: **< 15 MB** per file.
- Kalau model Tripo Anda punya 1,9 juta face seperti di screenshot,
  itu terlalu berat untuk dashboard yang harus jalan di 6 laptop
  sekaligus. Turunkan ke **50.000–200.000 face** (face count ada di
  panel kiri Tripo, bagian "Topology").

  Alasannya: dashboard me-render 8 objek (2 server + 6 PC). Kalau
  tiap objek 1,9 juta face, total 15 juta segitiga — laptop kelas
  menengah akan tersendat, dan yang paling penting: file 60 MB per
  model berarti anggota kelompok harus unduh 60 MB saat dashboard
  dibuka.

  Turunkan face count di Tripo lewat fitur decimate/simplify, atau
  export dengan opsi LOD.

### 6. Optimasi otomatis (sudah dipakai di aset ini)

Aset yang terpasang sudah dilewatkan `gltf-transform optimize`:
```bash
npx @gltf-transform/cli optimize <masuk.glb> <keluar.glb> \
  --compress draco --texture-compress webp
```
Hasilnya: server 13,32 MB → **150 KB**, laptop 978 KB → **112 KB**.

Decoder Draco sudah vendored di `server/static/vendor/draco/`, jadi
file hasil kompresi Draco tetap termuat tanpa internet.

Kalau dapat model baru, jalankan perintah yang sama sebelum dipakai.

### 7. Draco / kompresi

Kalau Tripo meng-export dengan kompresi Draco, dashboard **tetap
bisa** membacanya (decoder sudah disiapkan di `static/vendor/draco/`).
Jadi tidak ada langkah tambahan.

---

## Kalau model gagal tampil

Dashboard tidak akan error. Dia otomatis jatuh ke model bawaan (balok
biru) dan menulis catatan di pojok kiri bawah peta:

- `aset 3D: 2/2 siap` → semua aset termuat
- `aset 3D: 1/2 siap · server.glb gagal` → ada yang gagal
- `aset 3D: belum ada (pakai model built-in)` → belum ada file

Pesan error lengkap ada di **Console browser** (F12 → Console).

Penyebab yang paling sering:

| Gejala | Penyebab | Solusi |
|---|---|---|
| 404 di Console | File ada di tempat yang salah | Harus di `server/static/asset/`, bukan di root project |
| `scene.glb gagal` padahal file ada | Browser masih menyimpan 404 lama | Hard refresh: `Ctrl+Shift+R` (Windows) / `Cmd+Shift+R` (Mac) |
| Model tampil terbalik | Sumbu Y model terbalik | Balik di Tripo sebelum export |
| Model terlalu kecil | Normalisasi sudah jalan, tapi model aslinya "tinggi" | Tidak masalah — loader sudah menormalkan tinggi |
| Halaman berat / lambat | Terlalu banyak face | Turunkan face count di Tripo (lihat aturan 5) |

---

## Struktur folder

```
PROJECT MK PJC/server/static/
├── index.html          ← dashboard
├── peta3d.js           ← renderer WebGL (three.js)
├── aset3d.js           ← pemuat GLB
├── vendor/             ← three.js + Draco (lokal, offline)
└── asset/
    ├── server.glb      ← model rak server (terpasang)
    ├── laptop.glb      ← model laptop (terpasang)
    ├── scene.glb       ← (opsional) file gabungan
    └── pc.glb          ← (opsional) PC terpisah
```

Folder `asset/` sudah ada di repo. Cuma taruh file di sana — tidak perlu
mengubah kode sama sekali.

---

## Setelah file dikirim

1. Simpan file ke `server/static/asset/`
2. Restart server:

```bash
python3 server/server.py --node n1 --port 5000 --web 8081 --udp 5010
```

3. Buka dashboard → pojok kiri bawah peta harus berubah dari
   "pakai model built-in" jadi "aset 3D: 2/2 siap"

Kalau masih tertulis "gagal", tunggu 10 detik — dashboard mencoba
memuat ulang otomatis tiap 10 detik. Kalau masih gagal, hard refresh.

---

## Catatan arsitektur

Dashboard punya **dua renderer**:

- **WebGL (three.js)** — dipakai kalau WebGL tersedia. Ini yang
  memakai file GLB.
- **Canvas 2D** — model prosedural (balok), dipakai kalau WebGL
  tidak tersedia (laptop tua, driver bermasalah, atau WebGL dimatikan).

Bisa dipilih manual lewat dropdown di pojok kiri bawah peta.

Artinya: **walaupun file GLB belum dikirim, dashboard TETAP jalan** dan
tidak pernah tampil kosong. Ini penting untuk demo di depan dosen kalau
aset belum siap.
