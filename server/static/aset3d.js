/* ======================================================================
   ASET 3D — pemuat GLB (glTF Binary) untuk dashboard kluster.

   Kenapa modul terpisah:
     Model prosedural (balok) SELALU tersedia sebagai fallback. Kalau file
     GLB belum dikirim anggota kelompok, dashboard tetap jalan dengan
     model terprogram — tidak ada error, tidak ada layar kosong.

   Konvensi nama file (taruh di server/static/asset/):
     pc.glb        — model PC  (desktop)
     server.glb    — model server
     laptop.glb    — opsional, model laptop kalau mau dibedakan

   Aturan pemuatan:
     - GLB dimuat SEKALI lalu di-cache; tiap node memakai instance yang sama
       (clone) supaya tidak mengunduh ulang.
     - Ukuran mesh otomatis dinormalisasi (dimensi terpanjang = 1) lalu
       diskalakan ke ukuran target. Jadi file Tripo bisa diekspor dalam
       skala apa pun tanpa harus Diedit dulu.
     - Sumbu Y = atas (standar glTF). Kalau model terbalik atau Recipient
       duduk tenggelam, ada tombol "balik sumbu Y" di panel.

   Fallback: kalau GLB gagal dimuat → pakai model prosedural, dan tampil
   catatan kecil di bawah peta (bukan alert yang mengganggu).
   ====================================================================== */

import * as THREE from "../vendor/three.module.js";
import { GLTFLoader } from "../vendor/GLTFLoader.js";
import { DRACOLoader } from "../vendor/DRACOLoader.js";

export class Aset3D {
  constructor() {
    this.loader = new GLTFLoader();
    try {
      const draco = new DRACOLoader();
      draco.setDecoderPath("vendor/draco/");
      this.loader.setDRACOLoader(draco);
    } catch (e) {
      /* Draco opsional — kalau decoder gagal, GLB non-kompres tetap jalan. */
    }
    this.cache = new Map();      /* nama file -> THREE.Group (template) */
    this.gagal = new Set();
    this.pernahGagal = new Set(); /* file yang pernah gagal -> cache-bust */
    this.sedang = new Map();    /* nama file -> Promise */
  }

  /* Muat GLB (dengan cache). Return Promise<THREE.Group|null>
     PENTING: file yang gagal dimuat TIDAK di-cache sebagai gagal permanen.
     Aset bisa saja belum ada saat halaman pertama dibuka (misal sedang
     disalin ke laptop anggota) — jadi percobaan berikutnya harus tetap
     mencoba. */
  async muat(namaFile) {
    if (this.cache.has(namaFile)) return this.cache.get(namaFile);
    if (this.sedang.has(namaFile)) return this.sedang.get(namaFile);

    /* Cache-bust HANYA saat ini percobaan kedua: kalau file sempat 404
       (belum dikirim) lalu belakangan ada, browser TIDAK akan meminta
       ulang karena 404-nya ke-cache. Percobaan pertama tetap polos
       (file berhasil di-cache server, jadi unduhan kedua tidak diulang).
       Sisa file yang sukses sudah di-cache di this.cache, jadi
       muat() berikutnya tidak menyentuh jaringan sama sekali. */
    const url = this.pernahGagal.has(namaFile)
      ? `asset/${namaFile}?v=${Date.now()}`
      : `asset/${namaFile}`;
    const p = new Promise((selesai) => {
      const selesaiDanBersih = (hasil) => {
        /* Hapus dari `sedang` supaya percobaan berikutnya benar-benar
           memuat ulang, bukan mengembalikan promise lama yang sudah
           selesai (hasilnya null). */
        this.sedang.delete(namaFile);
        selesai(hasil);
      };
      this.loader.load(
        url,
        (gltf) => {
          const g = gltf.scene;
          /* Normalisasi TINGGI (sumbu Y), bukan dimensi terpanjang.
             Kalau dinormalisasi dari sisi terpanjang, model PC (yang
             tersebar lebar: monitor + tower + keyboard + mouse) jadi
             sangat lebar tapi TINGGI-nya kecil — hasilnya hanya baris
             datar di layar, tidak terbaca sebagai computer.
             Normalisasi dari tinggi menjaga proporsi model apa adanya. */
          const kotak = new THREE.Box3().setFromObject(g);
          const ukuran = kotak.getSize(new THREE.Vector3());
          const tinggi = ukuran.y || 1;
          g.scale.setScalar(1 / tinggi);
          /* Posisikan asal di titik tengah horizontal, alas di y=0. */
          const kotak2 = new THREE.Box3().setFromObject(g);
          const pusat = kotak2.getCenter(new THREE.Vector3());
          g.position.x -= pusat.x;
          g.position.z -= pusat.z;
          g.position.y -= kotak2.min.y;
          this.cache.set(namaFile, g);
          selesaiDanBersih(g);
        },
        undefined,
        (err) => {
          console.warn("gagal muat aset", namaFile, err);
          this.gagal.add(namaFile);
          this.pernahGagal.add(namaFile);
          selesaiDanBersih(null);
        }
      );
    });
    this.sedang.set(namaFile, p);
    return p;
  }

  /* Coba lagi memuat aset yang sebelumnya gagal (dipanggil tiap refresh).
     Aset bisa saja baru saja dikirim — jadi jangan tandai permanen. */
  async cobaLagi() {
    for (const nama of [...this.gagal]) {
      this.gagal.delete(nama);
      this.sedang.delete(nama);
      const g = await this.muat(nama);
      if (g) return g;
    }
    return null;
  }

  /* Buang cache aset supaya file baru bisa dimuat (dipakai saat
     anggota kelompok menyalin file baru ke server). */
  bersihkanCache() {
    this.cache.clear();
    this.gagal.clear();
    this.pernahGagal.clear();
    this.sedang.clear();
  }

  /* Klon template yang bisa dipakai di scene (untuk hindari berbagi material) */
  instantiate(namaFile) {
    const t = this.cache.get(namaFile);
    return t ? t.clone(true) : null;
  }

  /* Apakah aset sudah siap dipakai? */
  ada(namaFile) {
    return this.cache.has(namaFile);
  }

  /* Daftar aset yang gagal dimuat — supaya UI bisa beri tahu */
  daftarGagal() {
    return [...this.gagal];
  }
}

/* ----------------------------------------------------------------------
   SKALA & POSISI MODEL ASET DI PETA.

   Model prosedural punya satuan "dunia" sendiri (server ±28, PC ±26).
   Aset GLB dinormalisasi ke 1 unit terpanjang, jadi harus dikalikan
   faktor supaya ukurannya sama persis dengan model prosedural —
   supaya perbandingan ukuran server vs PC tetap masuk akal di layar.
   ---------------------------------------------------------------------- */
export const SKALA_ASET = {
  /* Nilai = TINGGI model dalam unit dunia (bukan skala pengali).
     Karena aset dinormalisasi tinggi = 1, angka ini langsung jadi
     tinggi akhir model di peta.
     Server lebih tinggi dari PC supaya hierarkinya terbaca. */
  server: 17,
  pc: 14,
  laptop: 11,
};

/* Daftar file aset yang HARUS ada supaya dashboard tetap tanpa fallback:
   dipakai dokumentasi + preflight. */
export const ASET_WAJIB = ["pc.glb", "server.glb"];

/* ----------------------------------------------------------------------
   SKEMA "SATU FILE GABUNGAN" (opsional, sangat disarankan).

   Tripo bisa export satu scene berisi beberapa objek lalu di-export jadi
   satu .glb. Kalau begitu, cukup kirim SATU file:

     scene.glb  — berisi node bernama:  "server",  "pc"

   Loader di bawah akan memindai node di dalam file dan memisahkan
   berdasarkan namanya. Jadi anggota kelompok bebas kirim:
     - 2 file terpisah (server.glb + pc.glb), atau
     - 1 file gabungan (scene.glb)

   Dua-duanya jalan.
   ---------------------------------------------------------------------- */

/* Pisahkan scene gabungan jadi per-jenis, berdasarkan nama node. */
export function pecahSceneGabung(scene, petaNama) {
  /* petaNama: { "server": "server", "pc": "pc" } */
  const hasil = {};
  for (const [jenis, namaDi] of Object.entries(petaNama)) {
    const ketemu = scene.getObjectByName(namaDi);
    if (ketemu) {
      hasil[jenis] = ketemu;
    }
  }
  /* Kalau nama tidak persis, coba cari yang mengandung kata kunci. */
  if (!hasil.server || !hasil.pc) {
    scene.traverse((o) => {
      const n = (o.name || "").toLowerCase();
      if (!hasil.server && /server|rak|rack|node/.test(n)) hasil.server = o;
      else if (!hasil.pc && /pc|desktop|komputer|monitor|mesin/.test(n)) hasil.pc = o;
    });
  }
  return hasil;
}
