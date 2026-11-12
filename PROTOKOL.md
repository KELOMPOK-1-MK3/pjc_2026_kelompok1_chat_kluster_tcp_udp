# PROTOCOL — MK Pemrograman Jaringan Kluster (MKKL1028)

Server, node kluster, dan pengguna bertukar pesan dalam bentuk **satu objek JSON
per baris**. Setiap pesan diakhiri karakter baris baru (`\n`).

## Kenapa satu baris per pesan

TCP itu aliran byte, bukan kumpulan pesan. Kalau dua pesan dikirim berurutan
tanpa pemisah yang jelas, penerima tidak punya cara tahu di mana pesan pertama
berakhir — datanya bisa tercampur jadi satu potongan yang tidak bisa dibaca.

Karena itu setiap pesan JSON diakhiri `\n`, dan penerima memotong data yang masuk
pada setiap karakter itu. Cara ini sederhana, bisa dibaca manusia, dan tidak perlu
pustaka tambahan.

## Dua jalur, dua jaminan

| Jalur | Protokol | Dijamin sampai? | Dipakai untuk |
|---|---|---|---|
| **Andal** | TCP | Ya | Chat biasa, perintah kendali, mesh antar node |
| **Cepat** | UDP | **Tidak** | Presence (detak), pengukuran latensi, pesan yang tidak dijamin sampai |

Pesan UDP ditandai `terjamin: 0` di basis data dan di tabel dashboard, supaya
tidak pernah diklaim sampai padahal belum sampai.

---

## Bentuk Umum

```json
{"tipe": "...", ...}
```

Setiap pesan **wajib** punya `tipe`. Sisanya tergantung tipenya.

---

## 1. Pengguna → Server (TCP)

### 1.1 Mendaftar (`daftar`)

Dikirim **sekali**, tepat setelah sambungan terbuka.

```json
{"tipe": "daftar", "nama": "PC-PARIS", "sistem": "Windows 11"}
```

| Kolom | Wajib | Isi |
|---|---|---|
| `nama` | ya | Nama laptop. Kalau kosong, server pakai nama mesin. |
| `sistem` | tidak | Sistem operasi, untuk ditampilkan di dashboard |

### 1.2 Mengirim pesan (`pesan`)

```json
{"tipe": "pesan", "ke": "semua", "isi": "Kumpul jam 3 di lab"}
{"tipe": "pesan", "ke": "PC-DESKI", "isi": "Bagianmu sudah selesai?"}
```

| Kolom | Wajib | Isi |
|---|---|---|
| `ke` | ya | `"semua"` atau satu nama laptop |
| `isi` | ya | Teks pesan, paling banyak 2000 karakter |

### 1.3 Melaporkan hasil perintah (`hasil`)

```json
{"tipe": "hasil", "aksi": "kunci", "berhasil": true,
 "pesan": "layar dikunci", "id_perintah": 12}
```

| Kolom | Wajib | Isi |
|---|---|---|
| `aksi` | ya | Perintah yang dijalankan |
| `berhasil` | ya | `true` / `false` |
| `pesan` | ya | Keterangan singkat untuk ditampilkan di dashboard |
| `id_perintah` | tidak | Nomor perintah di basis data, supaya bisa dicocokkan |

---

## 2. Server → Pengguna (TCP)

### 2.1 Sambutan (`selamat`)

```json
{"tipe": "selamat", "nama_kamu": "PC-PARIS", "node": "n1",
 "tcp": 5000, "udp": 5010,
 "daftar_node": [{"nama": "n2", "host": "192.168.1.8", "tcp": 5001, "udp": 5011}],
 "online": ["PC-PARIS", "PC-SIDIQ"]}
```

`daftar_node` dipakai klien sebagai **calon server lain** kalau node yang
dihubungi mati — ini dasar fitur failover.

### 2.2 Daftar yang tersambung (`online`)

```json
{"tipe": "online", "daftar": [
  {"nama": "PC-PARIS", "sistem": "Windows 11", "ip": "192.168.1.12"}
]}
```

### 2.3 Pesan masuk (`pesan`)

```json
{"tipe": "pesan", "dari": "PC-SIDIQ", "ke": "semua",
 "isi": "Kumpul jam 3 di lab", "waktu": "2026-11-07 14:05:11",
 "protokol": "TCP", "terjamin": true, "kode": "a3f9c1b2"}
```

`kode` = identitas unik pesan, dipakai untuk **dedup** saat pesan melintasi
beberapa node (pesan yang sama bisa sampai via jalur berbeda).

### 2.4 Perintah (`perintah`)

```json
{"tipe": "perintah", "aksi": "kunci", "keterangan": "", "id": 12}
```

### 2.5 Pemberitahuan (`info`) dan galat (`galat`)

```json
{"tipe": "info", "pesan": "terkirim ke 3 laptop (+1 node)"}
{"tipe": "galat", "pesan": "laptop 'PC-WIRA' tidak sedang tersambung"}
```

### 2.6 Ping / Pong

```json
{"tipe": "ping"}     ← server kirim tiap 20 detik
{"tipe": "pong"}     ← klien balas
```

---

## 3. UDP

### 3.1 Detak node (`detak`) — antar node

```json
{"tipe": "detak", "node": "n1", "tcp": 5000, "udp": 5010, "waktu": "..."}
```

Dikirim tiap **1 detik**, lewat broadcast (`255.255.255.255`) **dan** unicast ke
setiap tetangga yang diketahui. Node yang menerima detak langsung mencoba
menyambung balik lewat TCP (mesh).

Kalau sebuah node tidak mengirim detak selama **3,5 detik**, node lain menandainya
mati. Ini deteksi kegagalan yang jauh lebih cepat daripada menunggu timeout TCP.

### 3.2 Detak klien (`detak-klien`) — klien → server

```json
{"tipe": "detak-klien", "nama": "PC-PARIS", "waktu": 1731000000.0}
```

Presence dari klien lewat UDP. Server memperbarui tanda hidup laptop.

### 3.3 Pengukuran latensi

```json
{"tipe": "ping-udp", "t": 1731000000.0}   →  {"tipe": "pong-udp", "t": 1731000000.0, "node": "n1"}
{"tipe": "ping-tcp", "t": 1731000000.0}   →  {"tipe": "pong-tcp", "t": 1731000000.0}
```

Diverifikasi tiap **5 detik** ke setiap tetangga. Angka perbandingan TCP vs UDP
ditampilkan di dashboard — ini bukti bahwa UDP memang lebih cepat untuk pesan
kecil, walau tanpa jaminan.

### 3.4 Pesan UDP dari klien (`pesan-udp`)

```json
{"tipe": "pesan-udp", "dari": "PC-PARIS", "isi": "halo cepat"}
```

Hanya diterima kalau IP pengirim-nya sudah terdaftar sebagai pengguna TCP.

---

## 4. Mesh antar node (TCP)

| Tipe | Arah | Isi |
|---|---|---|
| `node` | dua arah | Perkenalan: nama, port TCP, port UDP |
| `gossip` | dua arah | Daftar node lain yang diketahui — supaya mesh terbentuk penuh |
| `pesan-terusan` | node → node | Pesan chat untuk diteruskan ke pengguna di node lain |
| `perintah-terusan` | node → node | Perintah untuk pengguna di node lain |
| `hasil-terusan` | node → node | Laporan hasil perintah dari node lain |

**Dedup pesan antar node:** setiap pesan punya `kode` unik. Node yang sudah pernah
melihat `kode` itu tidak meneruskannya lagi — tanpa ini, pesan berputar-putar
di antara node dan muncul berkali-kali.

---

## 5. Ringkasan Semua Tipe

| Tipe | Jalur | Arah | Arti |
|---|---|---|---|
| `daftar` | TCP | pengguna → server | Laptop ini baru tersambung |
| `pesan` | TCP | pengguna → server | Ada pesan untuk dikirim |
| `hasil` | TCP | pengguna → server | Perintah sudah dijalankan |
| `pong` | TCP | pengguna → server | Masih hidup |
| `selamat` | TCP | server → pengguna | Pendaftaran diterima |
| `online` | TCP | server → pengguna | Daftar laptop yang aktif |
| `pesan` | TCP | server → pengguna | Ada pesan masuk |
| `perintah` | TCP | server → pengguna | Perintah untuk dijalankan |
| `info` | TCP | server → pengguna | Pemberitahuan biasa |
| `galat` | TCP | server → pengguna | Ada yang tidak berhasil |
| `ping` | TCP | server → pengguna | Menanyakan apakah klien masih hidup |
| `detak` | UDP | node → node | Detak keberadaan node |
| `detak-klien` | UDP | pengguna → server | Presence pengguna |
| `ping-udp` / `pong-udp` | UDP | node ↔ node | Ukur latensi UDP |
| `pesan-udp` | UDP | pengguna → server | Pesan cepat, tanpa jaminan |
| `node` | TCP | node ↔ node | Perkenalan node |
| `gossip` | TCP | node ↔ node | Sebarkan daftar node |
| `pesan-terusan` | TCP | node → node | Teruskan chat ke node lain |
| `perintah-terusan` | TCP | node → node | Teruskan perintah ke node lain |
| `hasil-terusan` | TCP | node → node | Teruskan laporan hasil |

---

## 6. Perintah yang Dikenal

| Aksi | Yang dilakukan klien |
|---|---|
| `pesan` | Menampilkan pesan dari server |
| `kunci` | Mengunci layar |
| `restart` | Menyalakan ulang (jeda 30 detik, bisa dibatalkan) |
| `matikan` | Mematikan laptop (jeda 30 detik, bisa dibatalkan) |
| `batal` | Membatalkan perintah restart/matikan yang sedang menunggu |

Aksi di luar daftar ini **ditolak** oleh klien dan dilaporkan sebagai gagal.

> Perintah `matikan`, `restart`, dan `kunci` **ditolak di macOS** secara bawaan.
> Mac sering dipakai sebagai server atau untuk presentasi, dan tidak boleh ikut
> mati karena perintah yang salah kirim. Di Windows, perintah ini butuh terminal
> **Run as Administrator**.

---

## 7. Batas dan Aturan

| Hal | Batas | Kalau dilanggar |
|---|---|---|
| Panjang isi pesan | 2000 karakter | Dipotong, klien diberi tahu |
| Panjang satu baris JSON | 64 KB | Sambungan diputus |
| Panjang nama laptop | 40 karakter | Dipotong |
| Data yang tidak bisa dibaca | — | Dibalas `galat`, sambungan tidak diputus |
| Klien diam lebih dari 60 detik | — | Server menganggapnya terputus |
| Node tanpa detak lebih dari 3,5 detik | — | Node ditandai mati |
