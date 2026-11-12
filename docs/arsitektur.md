# Arsitektur

Satu **node** adalah server penuh: menerima pengguna (TCP), menjalankan detak
antar node (UDP), terhubung ke node lain (mesh TCP), menyimpan basis
data sendiri, dan menyajikan dashboard sendiri.

## Komponen

| Komponen | Berjalan di | Peran | Port |
|---|---|---|---|
| `server.py` | Tiap node | Server TCP pengguna, endpoint UDP, mesh antar node, dashboard | 5000 (TCP), 5010 (UDP), 8081 (HTTP) |
| `static/index.html` | Dilayani server | Dashboard mode terang + peta kluster 3D | — |
| `client.py` | Tiap laptop anggota | Menyambung TCP, membuka jalur UDP, menerima & mengirim pesan | — (menyambung keluar) |
| SQLite (`data/kluster_<node>.db`) | Tiap node | Pesan, perintah, kejadian, pengukuran | — |

## Alur satu pesan TCP

1. Pengguna mengetik pesan; `client.py` mengirimnya ke node sebagai satu baris JSON.
2. Node menerima, menyimpannya ke tabel `pesan` (dengan `kode` unik), lalu
   memeriksa tujuan.
   - Tujuan `"semua"` → diteruskan ke seluruh pengguna **lokal** kecuali pengirim.
   - Tujuan satu nama → hanya dikirim ke laptop itu.
3. Node meneruskan pesan yang sama ke **node lain** lewat mesh TCP, dengan `kode`
   yang sama.
4. Node lain mengecek `kode`: kalau sudah pernah dilihat, **tidak** meneruskan
   lagi. Ini yang mencegah pesan berputar di antara node dan muncul berkali-kali.
5. Node penerima meneruskan ke penggunanya dan menampilkan pesan.

## Alur satu pesan UDP

Sama seperti TCP, tapi:

- Tidak ada penomoran ulang atauTCP handshake — cukup satu datagram.
- Diteruskan ke pengguna lokal, tapi **tidak** dijamin sampai.
- Dicatat dengan `protokol = "UDP"` dan `terjamin = 0`, supaya tidak pernah
  diklaim sampai padahal belum sampai.
- Pesan yang hilang di jalur UDP **tidak** dikirim ulang — inilah perbedaan
  yang harus dibuktikan di laporan.

## Alur deteksi kegagalan node

```
node n1  ──── detak (UDP, tiap 1 detik) ────▶  node n2
node n2  ◀─── tidak ada detak selama 3,5 detik ──  node n1
         → n2 ditandai "mati" di dashboard
```

Detak dikirim dua cara:

- **Broadcast** ke `255.255.255.255` — supaya node baru langsung ditemukan
  tanpa perlu konfigurasi.
- **Unicast** ke setiap tetangga yang sudah dikenal — supaya detak tetap sampai
  kalau jaringan memblokir broadcast.

Begitu sebuah node mendengar detak dari nama yang belum dikenal, dia langsung
**menyambung balik lewat TCP** dan mengirim daftar node yang dia tahu
(`gossip`). Dari situ mesh terbentuk sendiri.

## Keputusan Teknis

| Keputusan | Alasan |
|---|---|
| **Satu thread per pengguna** | Satu laptop yang lambat tidak menahan laptop lain. |
| **Pesan JSON satu baris** | TCP adalah aliran byte. Tanpa pemisah jelas, dua pesan bisa tercampur. |
| **Koneksi dimulai dari laptop ke server** | Koneksi keluar tidak diblokir firewall Windows. |
| **Detak via UDP, bukan TCP** | Deteksi kegagalan jadi 3,5 detik, bukan menunggu timeout TCP. |
| **Kode unik tiap pesan** | Dedup saat pesan melintasi beberapa node. |
| **Perintah dibatasi daftar putih** | Server tidak boleh menjalankan perintah bebas di laptop orang lain. |
| **Perintah daya ditolak di macOS** | Mac dipakai sebagai server/presentasi — tidak boleh ikut mati. |
| **Dashboard pakai HTTP, bukan socket** | Dashboard hanya perlu membaca keadaan sesekali. |
| **Peta 3D tanpa Three.js** | Laptop anggota sering tanpa internet; `npm install` di 6 laptop berminggu-minggu. Proyeksi perspektif sendiri cukup untuk membedakan node, kabel, dan paket. |
| **Penggerak animasi `setInterval`, bukan `requestAnimationFrame`** | rAF mati saat tab tidak fokus — persis saat presentasi di proyektor. Peta jadi kosong di saat paling penting. |
| **Label dicek tabrakannya secara geometri** | Bounding box dihitung dari 8 sudut kubus yang diproyeksikan, bukan dari perkiraan radius — kalau dikira-kira, label menimpa node yang bukan miliknya. |

## Titik Kegagalan yang Diketahui

| Kelemahan | Akibatnya | Kemungkinan perbaikan |
|---|---|---|
| Node yang jadi "líder" mati, node lain belum punya salinan daftar pengguna | Pengguna di node itu harus menyambung ulang | Klien sudah menerima `daftar_node` saat menyambung — tinggal dipakai untuk failover |
| Pesan UDP yang hilang tidak dikirim ulang | Pesan bisa hilang | sending ulang berkala, atau pakai TCP untuk pesan penting |
| Basis data tiap node terpisah | Riwayat tidak terpusat | Replikasi basis data, atau replikasi log |
| Deteksi kegagalan berbasis detak | Bisa salah positives kalau jaringan padat | Sifat UDP: packet loss =&gt; detak hilang =&gt; nodeAlive dianggap mati |
| Tidak ada enkripsi | Pesan bisa dibaca pihak lain di jaringan yang sama | TLS |

Kelemahan-kelemahan ini dicatat terbuka, bukan disembunyikan, karena menjadi bahan
diskusi lanjutan saat presentasi.
