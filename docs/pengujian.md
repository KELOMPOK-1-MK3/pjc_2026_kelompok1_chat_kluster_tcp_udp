# Pengujian

Dokumen ini mencatat apa yang sudah diuji, hasilnya, dan cara mengulanginya.

## Cara mengulang seluruh pengujian

```bash
cd server
python3 uji-cepat.py           # 18 pemeriksaan: protokol, UDP, mesh, kegagalan
python3 uji-klien-gescom.py    # 9 pemeriksaan dengan klien ASLI (client.py)
```

Kedua skrip menyalakan servernya sendiri di port uji (5099/5399 dan 8199/8399),
jadi cukup satu perintah dan server yang sedang dipakai di port 5000 tidak
terganggu.

---

## Lingkungan pengujian

| | |
|---|---|
| Tanggal | 7 November 2026 |
| Mesin | macOS (Darwin 25.6.0), **satu mesin** |
| Klien | Klien tiruan (otomatis) **dan** `client.py` asli yang dijalankan nyata |
| Node | 2 node (n1, n2) di satu mesin, port berbeda |
| Basis data | SQLite, dibuat ulang sebelum setiap rangkaian uji |

> **Batas yang wajib disebutkan saat melaporkan.** Seluruh pengujian di bawah
> dijalankan di **satu mesin**, bukan dengan enam laptop yang benar-benar
> terpisah. Yang sudah terbukti adalah **kebenaran logika, protokol, dan
> interaksinya** — pesan sampai ke tujuan yang benar, pesan pribadi tidak bocor,
> UDP dicatat sebagai "tanpa jaminan", node saling menemukan lewat detak, dan
> pesan melintasi mesh antar node. Yang **belum** terbukti adalah perilaku
> jaringan sungguhan: latensi sebenarnya, pengaruh WiFi, dan perbedaan sistem
> operasi antar laptop.

---

## 1. Uji cepat otomatis (18 pemeriksaan)

```bash
python3 uji-cepat.py
```

```
1. Menyalakan dua node
  ✓ node n1 hidup
  ✓ node n2 hidup

2. Klien TCP tersambung
  ✓ PC-ALPHA terdaftar di node n1
  ✓ PC-BETA terdaftar di node n1

3. Pesan TCP ke semua
  ✓ PC-BETA menerima pesan
  ✓ PC-ALPHA tidak dapat gemanya

4. Pesan pribadi tidak bocor
  ✓ PC-BETA menerima pesan pribadi
  ✓ PC-ALPHA tidak menerima pesan pribadi PC-BETA

5. Jalur UDP
  ✓ klien bisa mengirim lewat UDP
  ✓ pesan UDP tercatat di basis data
  ✓ pesan UDP ditandai tanpa jaminan

6. Node saling menemukan lewat UDP heartbeat
  ✓ node n1 mengetahui node n2
  ✓ node n2 mengetahui node n1

7. Mesh TCP antar node
  ✓ pesan dari node n2 sampai ke klien node n1

8. Perintah hanya di klien tujuan
  ✓ PC-BETA menerima perintah
  ✓ PC-ALPHA tidak menerima perintah

9. Nama kembar ditolak
  ✓ nama kembar tidak menggantikan yang lama

10. Dashboard menjawab
  ✓ GET / → 200

==============================================================
  HASIL: 18 lulus, 0 gagal
==============================================================
```

### Yang dibuktikan tiap bagian

**Pesan TCP ke semua** — pengirim **tidak** menerima gemanya. Perilaku yang
disengaja supaya pengirim tidak melihat pesannya sendiri dua kali.

**Pesan pribadi tidak bocor** — ini bagian paling penting untuk dibuktikan,
karena kesalahan dalam PENYALURAN pesan adalah kegagalan paling serius pada
sistem seperti ini.
Klien yang bukan tujuan menerima **nol** pesan.

**Jalur UDP** — pesan UDP tercatat di basis data dengan `protokol = "UDP"` dan
`terjamin = 0`. Ini yang membuat dashboard bisa membedakan "sampai" dari
"tidak dijamin sampai", dan mencegah klaim palsu di laporan.

**Penemuan node lewat detak UDP** — kedua node saling menemukan **tanpa** ada
yang dikonfigurasi manual (hanya `--tetangga` di salah satu arah), lalu saling
menyambung lewat mesh TCP.

**Mesh antar node** — pesan yang dikirim dari node n2 sampai ke pengguna yang
tersambung di node n1. Ini yang membedakan sistem kluster dari sistem chat
satu server biasa.

**Nama kembar ditolak** — dua laptop dengan nama sama tidak akan menggantikan
laptop yang sudah tersambung.

---

## 2. Uji dengan klien asli (9 pemeriksaan)

```bash
python3 uji-klien-gescom.py
```

Skrip ini menjalankan `client.py` **sungguhan** — bukan tiruan — supaya jalur yang
dipakai anggota benar-benar teruji: membaca `nama.txt`, membaca `server.txt`,
mode `--tunggu`, dan folder siap-kirim.

```
2. Klien asli menyambung (baca nama.txt & server.txt)
  ✓ klien asli terdaftar (node n1)
  ✓ klien asli terdaftar (node n2)

3. Klien di folder FILE USER (persis yang dikirim ke anggota)
  ✓ klien dari folder FILE USER tersambung

4. Pesan TCP antar node lewat klien asli
  ✓ klien di node lain menerima pesan TCP

5. Jalur UDP dari dashboard
  ✓ pesan UDP tercatat
  ✓ pesan UDP ditandai tanpa jaminan

6. Pengukuran latensi TCP vs UDP
  ✓ latensi TCP terukur
  ✓ latensi UDP terukur
      → TCP 0.38 ms · UDP 0.39 ms

==============================================================
  HASIL: 9 lulus, 0 gagal
==============================================================
```

**Yang dibuktikan:** folder `FILE USER/3. PC04 (Deski)/` — folder yang benar-benar
dikirim ke anggota — langsung jalan tanpa disentuh. Nama terbaca dari
`nama.txt`, alamat dari `server.txt`.

**Angka latensi** diukur otomatis tiap 5 detik dan ditampilkan berdampingan,
sehingga perbedaan TCP dan UDP terlihat dengan angka, bukan klaim.

---

## 3. Bukti visual

| Berkas | Isi |
|---|---|
| `server/docs/bukti/kanvas-3d-terang.png` | Peta kluster 3D mode terang: kubus node server, node kluster lain, laptop mengelilingi, kabel + titik target + leader line |
| `server/docs/bukti/dashboard-kluster-3d.png` | Dashboard penuh mode gelap |

---

## Yang belum diuji

Hal-hal berikut **belum** dibuktikan dan tidak boleh diklaim sudah:

1. **Enam laptop sungguhan yang terpisah.** Pengujian masih satu mesin. Ini
   langkah berikutnya, dan yang paling penting sebelum presentasi.
2. **Perintah daya di Windows.** Baru dibuktikan bahwa perintahnya *ditolak* di
   macOS. Apakah di Windows benar-benar berjalan — termasuk apakah perlu hak
   administrator — masih perlu dibuktikan.
3. **WiFi kampus.** Ada kemungkinan jaringan WiFi memisahkan antar-laptop
   sehingga laptop tidak dapat saling menyambung. Ini **harus diuji lebih
   dahulu** dengan dua laptop, jauh sebelum hari presentasi. Kalau terjadi, pakai
   hotspot dari ponsel.
4. **Beban tinggi.** Belum diukur berapa klien yang bisa dilayani bersamaan
   sebelum server mulai lambat.
5. **Sambung ulang otomatis.** Kalau node mati, klien harus menyambung ke node
   lain. Klien **sudah menerima** daftar node lain saat menyambung, tapi belum
   ada percobaan menyambung ulang secara otomatis.
6. **Perilaku saat packet loss.** Belum diuji apa yang terjadi kalau detak UDP
   hilang di tengah jalan — apakah node masih ditandai hidup.

---

## Cara melihat isi basis data

```bash
sqlite3 server/data/kluster_n1.db "SELECT * FROM pesan ORDER BY id DESC LIMIT 10;"
sqlite3 server/data/kluster_n1.db "SELECT * FROM ukur ORDER BY id DESC LIMIT 10;"
```
