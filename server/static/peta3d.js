/* ======================================================================
   PETA 3D — renderer berbasis Three.js (WebGL).

   Berdampingan dengan renderer Canvas-2D yang lama (gambar3d).
   Modul ini dipakai kalau WebGL tersedia; kalau tidak, dashboard tetap
   jalan dengan renderer 2D.

   Aset GLB (dari Tripo / model eksternal):
     - Dimuat lewat Aset3D (aset3d.js).
     - Kalau file belum ada → fallback ke model prosedural.
     - Kalau WebGL tidak tersedia → fallback ke renderer 2D.

   Konvensi nama file di server/static/asset/:
     pc.glb      — model PC desktop
     server.glb  — model server
   ====================================================================== */

import * as THREE from "./vendor/three.module.js";
import { Aset3D, SKALA_ASET, pecahSceneGabung } from "./aset3d.js";
import { RoomEnvironment } from "./vendor/RoomEnvironment.js";

export class Peta3D {
  constructor(kanvas, opsi = {}) {
    this.kanvas = kanvas;
    this.opsi = opsi;
    this.aset = new Aset3D();
    this.siap = false;
    this.pakaiAset = false;
    this.asetGabung = null;        /* { server, pc } dari scene.glb */
    this.node = new Map();          /* nama node -> THREE.Group */
    this.kabelList = [];
    this.paket = [];                /* paket cahaya yang sedang berjalan */
    this._pcLama = new Set();       /* nama laptop yang sudah tampil sebelumnya */
    this._kabelLama = new Set();    /* kabel yang sudah pernah tampil */
    this._animasi = [];             /* animasi masuk yang sedang berjalan */
    this.labelEls = new Map();      /* nama node -> elemen DOM label */
    this.lapisanLabel = null;       /* div overlay untuk label */
    this.rotasiOtomatis = true;
    this.yaw = 0.62;
    /* pitch POSITIF = kamera di atas lantai, melihat ke bawah.
       Sebelumnya -0.42 → kamera di BAWAH lantai, sehingga disc lantai
       dan bayangan (yang menghadap ke atas) tak pernah terlihat dan
       semua device tampak melayang. */
    this.pitch = 0.40;
    this.yawSasaran = this.yaw;      /* nilai yang dikejar — drag mengubah ini */
    this.pitchSasaran = this.pitch;
    this.zoom = 1;
    this.drag = false;
    this.frustumKotor = true;        /* hitung ulang hanya saat perlu */
    this.frustumBasis = null;        /* { cx, cy, halfH } tanpa zoom */
    this.terpasang = false;
  }

  /* ------------------------------------------------------------------
     Inisialisasi: buat renderer, scene, kamera, lampu, grid lantai.
     ------------------------------------------------------------------ */
  async init() {
    const renderer = new THREE.WebGLRenderer({
      canvas: this.kanvas, antialias: true, alpha: true,
      /* preserveDrawingBuffer: buffer tidak langsung dihapus setelah
         di-composite. Tanpa ini, canvas WebGL jadi kosong saat mau
         diambil gambarnya (toDataURL / screenshot) — sehingga bukti
         visual tidak bisa dibuat. */
      preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    /* Exposure moderat. Env map sudah memberi banyak pantulan, kalau
       exposure terlalu tinggi seluruh scene jadi washed-out (putih di
       atas putih = model lenyap). */
    renderer.toneMappingExposure = 1.05;
    this.renderer = renderer;

    this.scene = new THREE.Scene();
    this.scene.background = null;

    /* Kamera ORTOGRAFIS (bukan perspektif) supaya semua objek sama besar
       — perspektif membuat node jauh terlihat kecil dan peta jadi bingung.
       Kamera diletakkan di posisi bola (yaw, pitch) dan selaluLookAt pusat.
       Jauhnya tetap besar supaya tidak ada yang terpotong. */
    this.kamera = new THREE.OrthographicCamera(-100, 100, 100, -100, 0.1, 5000);
    this.jarakKamera = 1200;
    this.perbaruiKamera();

    /* Pencahayaan.

       Model dari Sketchfab/Tripo BERTEKSTUR dan metalness tinggi.
       Tanpa environment map, material metal cuma memantulkan directional
       light — hasilnya hampir hitam dan bentuknya tak terbaca (blob).
       RoomEnvironment memberi pantulan lingkungan, sehingga metal jadi
       punya volume dan gradasi.

       Susunannya:
         env (RoomEnvironment) — pantulan lingkungan untuk metal
         ambient              — supaya sisi gelap tidak jadi hitam total
         key                  — arah utama dari atas-kiri
         fill                 — dari bawah-kanan, mengisi bayangan
         rim                  — dari belakang, memisahkan model dari latar
    */
    try {
      const pmrem = new THREE.PMREMGenerator(renderer);
      this.envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
      this.scene.environment = this.envRT.texture;
      pmrem.dispose();
    } catch (e) {
      console.warn("RoomEnvironment gagal, lanjut tanpa env map", e);
    }

    const ambient = new THREE.AmbientLight(0xffffff, 0.9);
    this.scene.add(ambient);
    /* Rim light tambahan: di tema gelap, sisi gelap model biru jatuh ke
       ~2:1 terhadap latar sehingga bentuknya jadi gumpalan. Dua lampu
       tepi dari depan-bawah menjaga tepi model tetap terbaca di kedua
       tema tanpa mengubah material asli. */
    const tepi1 = new THREE.DirectionalLight(0xdbe7ff, 0.75);
    tepi1.position.set(-160, 40, 90);
    this.scene.add(tepi1);
    const tepi2 = new THREE.DirectionalLight(0xdbe7ff, 0.55);
    tepi2.position.set(150, 30, -110);
    this.scene.add(tepi2);
    const key = new THREE.DirectionalLight(0xffffff, 1.35);
    key.position.set(-90, 140, 110);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.6);
    fill.position.set(120, -40, -80);
    this.scene.add(fill);
    /* Rim light dari belakang-kamera — memisahkan model dari latar. */
    const rim = new THREE.DirectionalLight(0xe8f0ff, 1.1);
    rim.position.set(60, 90, -220);
    this.scene.add(rim);

    /* Grid lantai — memberi konteks kedalaman tanpa garis penuh. */
    /* Grid lantai: TIPIS dan redup. Kalau terlalu menonjol, grid jadi
       "bintang" di peta dan perhatiannya bergeser dari perangkat — device
       justru kelihatan kecil. */
    /* Kontak bayangan: SATU disc gradien di bawah SETIAP node, ditambahkan
       di tambahNode(). Disc lantai besar di sini dihapus karena warnanya
       nyaris sama dengan latar panel — tidak terbaca sebagai lantai,
       cuma bikin scene terlihat pudar. Yang memberi kesan "menapak"
       adalah bayangan tepat di bawah tiap device, bukan lantai luas. */
    const kanvasLantai = document.createElement("canvas");
    kanvasLantai.width = kanvasLantai.height = 256;
    const g2 = kanvasLantai.getContext("2d");
    const grad = g2.createRadialGradient(128, 128, 10, 128, 128, 128);
    grad.addColorStop(0, "rgba(100,116,139,.34)");
    grad.addColorStop(0.5, "rgba(100,116,139,.16)");
    grad.addColorStop(1, "rgba(100,116,139,0)");
    g2.fillStyle = grad;
    g2.fillRect(0, 0, 256, 256);
    /* Disc "tanah" diperkecil dan digelapkan supaya benar-benar terbaca
       sebagai lantai: lebih kecil dari cincin PC, jadi setiap device
       terlihat menapak di bidang yang sama. */
    const texLantai = new THREE.CanvasTexture(kanvasLantai);
    const lantai = new THREE.Mesh(
      new THREE.CircleGeometry(175, 64),
      new THREE.MeshBasicMaterial({
        map: texLantai, transparent: true, depthWrite: false, opacity: 0.55,
      })
    );
    lantai.rotation.x = -Math.PI / 2;
    lantai.position.y = -0.6;
    this.scene.add(lantai);

    this.grupNode = new THREE.Group();
    /* Kabel ditambahkan SEBELUM node supaya digambar lebih dulu —
       kalau tidak, kabel menembus badan server & laptop (z-order). */
    this.grupKabel = new THREE.Group();
    this.scene.add(this.grupKabel);
    this.scene.add(this.grupNode);
    this.grupPaket = new THREE.Group();
    this.scene.add(this.grupPaket);

    this.siap = true;
    this.pasangKontrol();
    this.kanvas.style.cursor = "grab";
    return true;
  }

  /* ------------------------------------------------------------------
     Muat aset GLB. Kembalikan status:哪些 aset siap.
     ------------------------------------------------------------------ */
  async muatAset() {
    /* Urutan usaha:
         1. scene.glb — SATU file gabungan (paling praktis untuk anggota)
         2. file terpisah: server.glb / server_racking_system.glb untuk
            server, dan pc.glb / laptop.glb untuk PC
       Keduanya boleh ada; yang pertama menang kalau ada.
    */
    const scene = await this.aset.muat("scene.glb");
    if (scene) {
      const pecah = pecahSceneGabung(scene, { server: "server", pc: "pc" });
      if (pecah.server || pecah.pc) {
        this.asetGabung = pecah;
        this.pakaiAset = true;
        const ket = `aset 3D: scene.glb (${pecah.server ? "server" : "—"}/${pecah.pc ? "pc" : "—"})`;
        return { berhasil: (pecah.server ? 1 : 0) + (pecah.pc ? 1 : 0), gagal: [], ket };
      }
    }

    /* File terpisah — coba semua kandidat per jenis. */
    let berhasil = 0;
    const dipakai = [];
    for (const jenis of ["server", "pc"]) {
      for (const f of Peta3D.KANDIDAT[jenis]) {
        if (await this.aset.muat(f)) {
          berhasil++;
          dipakai.push(f);
          break;
        }
      }
    }
    this.pakaiAset = berhasil > 0;
    const ket = berhasil > 0
      ? `aset 3D: ${dipakai.join(" + ")}`
      : "aset 3D: belum ada (pakai model built-in)";

    /* Scene.glb memang OPSIONAL.

       Begitu juga kandidat yang dilewati: kalau pc.glb tidak ada lalu
       laptop.glb berhasil dimuat, pc.glb SUDAH tidak relevan. Sebelumnya
       ia tetap muncul sebagai "pc.glb gagal" dan terbaca seperti kerusakan
       padahal modelnya tampil normal. Jadi buang semua kandidat dari jenis
       yang akhirnya berhasil. */
    const terpakai = new Set(dipakai);
    const diabaikan = new Set(["scene.glb"]);
    for (const jenis of ["server", "pc"]) {
      const menang = Peta3D.KANDIDAT[jenis].find((f) => terpakai.has(f));
      if (menang) {
        for (const f of Peta3D.KANDIDAT[jenis]) if (f !== menang) diabaikan.add(f);
      }
    }
    return {
      berhasil,
      gagal: this.aset.daftarGagal().filter((f) => !diabaikan.has(f)),
      ket,
    };
  }

  /* Coba ulang memuat aset yang gagal. Dipanggil berkala — aset bisa
     saja baru saja dikirim ke server, jadi jangan menyerah setelah
     satu kali gagal. */
  async muatUlang() {
    if (!this.aset.daftarGagal().length) return null;
    const scene = await this.aset.muat("scene.glb");
    if (scene) {
      const pecah = pecahSceneGabung(scene, { server: "server", pc: "pc" });
      if (pecah.server || pecah.pc) {
        this.asetGabung = pecah;
        this.pakaiAset = true;
        return { berhasil: (pecah.server ? 1 : 0) + (pecah.pc ? 1 : 0), gagal: [] };
      }
    }
    for (const f of ["server.glb", "pc.glb"]) {
      await this.aset.muat(f);
    }
    return { berhasil: this.aset.cache.size, gagal: this.aset.daftarGagal() };
  }

  /* Kandidat nama file untuk tiap jenis model, dari yang paling
     spesifik. Loader mencoba satu per satu sampai ada yang termuat.
       server : server.glb  -> server_racking_system.glb -> scene.glb
       pc     : pc.glb      -> laptop.glb             -> scene.glb
  */
  static KANDIDAT = {
    server: ["server.glb", "server_racking_system.glb"],
    pc: ["pc.glb", "laptop.glb", "desktop.glb"],
  };

  /* Ambil SALINAN template model untuk jenis tertentu (server / pc).
     PENTIK: selalu harus clone. Kalau objek asli dipakai langsung, semua
     node berbagi object yang sama — memindahkan satu node ikut memindahkan
     semua node lain (semua menumpuk di satu titik). */
  template(jenis) {
    /* 1. dari scene gabungan */
    if (this.asetGabung && this.asetGabung[jenis]) {
      return this.asetGabung[jenis].clone(true);
    }
    /* 2. dari file terpisah */
    for (const file of Peta3D.KANDIDAT[jenis] || []) {
      const t = this.aset.instantiate(file);
      if (t) return t;
    }
    return null;
  }

  /* Rotasi & skala per jenis model.
     Model dari Sketchfab/Tripo tidak seragam: sumbu panjangnya bisa X, Y,
     atau Z, dan tidak semua "berdiri" tegak. Nilai di bawah dikoreksi
     setelah model asli dipakai. */
  static POSTUR = {
    /* "skala" = ukuran TERPANJANG model dalam unit dunia (bukan tinggi).
       Laptop aslinya memang lebih lebar dari tinggi (24.7 x 14 pada
       bounding box), jadi kalau dinormalisasi dari TINGGI, laptop jadi
       bardatar dan tidak terbaca. Normalisasi dari sisi terpanjang
       menjaga proporsi aslinya.
       "yaw" = putaran horizontal supaya model menghadap kamera. */
    /* "skala" = ukuran TERPANJANG model dalam unit dunia (bukan tinggi).
       Dinaikkan dari 32/30 → 44/38 supaya device tidak tampil seperti
       perangko di panel lebar. Ring tetap di 110–125, jadi jarak antar
       device masih ~2x lebarnya. */
    server: { yaw: 0, skala: 44 },
    pc: { yaw: 0.42, skala: 38 },
  };

  /* Normalisasi model agar:
       1. TIDAK tergeletak. Model dari Sketchfab/Tripo tidak seragam:
          rack server punya sumbu panjang di Z (dimensi 740 x 1013 x 2304),
          laptopnya di Y. Kalau tidak diputar, rak server tergeletak.
       2. Ukuran terpanjang = 1 unit (skala ditentukan terpisah).
       3. Alas rata di y = 0, tengah horizontal di titik asal.

     Deteksi sumbu panjang dari bounding box, jadi otomatis untuk model
     apa pun — tidak perlu tahu asal-usul file.

     CATATAN: normalisasi dari TINGGI bukan pilihan yang benar umum.
     Laptop asli punya lebar 24.7 dan tinggi 14 — kalau dinormalisasi dari
     tinggi, hasilnya bardatar dan tidak terbaca sebagai laptop. Sisi
     terpanjang lebih aman: proporsi model asli terjaga. */
  normalisasiModel(obj) {
    const kotak = new THREE.Box3().setFromObject(obj);
    if (kotak.isEmpty()) return;

    /* --- 1. putar kalau modelnya "tergeletak" --- */
    const u = kotak.getSize(new THREE.Vector3());
    const terpanjang = Math.max(u.x, u.y, u.z);
    /* Model rak: panjangnya di Z dan vertikalnya (Y) kecil dibanding
       panjang. Kalau Y < 40% sisi terpanjang, anggap tergeletak. */
    if (u.y < terpanjang * 0.45) {
      if (u.z >= u.x) obj.rotation.x = Math.PI / 2;   /* panjang di Z */
      else obj.rotation.z = -Math.PI / 2;               /* panjang di X */
    }

    /* Box3 harus dihitung ulang setelah rotasi. */
    obj.updateMatrixWorld(true);
    const kotak2 = new THREE.Box3().setFromObject(obj);
    const u2 = kotak2.getSize(new THREE.Vector3());
    const sisi = Math.max(u2.x, u2.y, u2.z) || 1;

    /* --- 2. skala supaya sisi terpanjang = 1 --- */
    obj.scale.multiplyScalar(1 / sisi);

    /* --- 3. alas rata di y=0, tengah horizontal di titik asal --- */
    obj.updateMatrixWorld(true);
    const kotak3 = new THREE.Box3().setFromObject(obj);
    const pusat = kotak3.getCenter(new THREE.Vector3());
    obj.position.x -= pusat.x;
    obj.position.z -= pusat.z;
    obj.position.y -= kotak3.min.y;
  }

  /* ------------------------------------------------------------------
     Bangun ulang isi scene dari data keadaan server.
     data: { node:{nama}, online:[{nama}], dikenal:[{nama}], tetangga:[] }
     ------------------------------------------------------------------ */
  perbarui(data) {
    if (!this.siap) return;

    /* Bangun ulang HANYA kalau daftar node berubah.

       Tanpa penjagaan ini, `perbarui()` dipanggil tiap 1,2 detik dan
       menghancurkan seluruh isi scene (termasuk paket cahaya yang baru
       saja dibuat) — hasilnya animasi tidak pernah terlihat karena
       selalu dihapus sebelum sempat bergerak. Selain itu, memutus dan
       membuat ulang mesh 8 model tiap 1,2 detik memboroskan CPU. */
    const tanda = JSON.stringify({
      u: data.node && data.node.nama,
      o: (data.online || []).map((x) => x.nama + "@" + (x.node || "")).sort(),
      k: (data.dikenal || []).map((x) => x.nama).sort(),
    });
    if (tanda === this._tandaScene) return;
    this._tandaScene = tanda;

    /* Bersihkan scene lama */
    while (this.grupNode.children.length) {
      const c = this.grupNode.children.pop();
      c.traverse?.((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) o.material.dispose();
      });
    }
    while (this.grupKabel.children.length) {
      const c = this.grupKabel.children.pop();
      c.geometry?.dispose();
      c.material?.dispose();
    }
    /* Reset daftar kabel juga — kalau tidak, tiap refresh menambah
       entri baru dan daftar membengkak (315 kabel setelah 39 refresh). */
    this.kabelList.length = 0;
    this.node.clear();
    /* Kabel lama sudah hilang. Buang catatan pasangan yang endpoint-nya
       sudah tidak ada, supaya laptop yang keluar lalu masuk lagi tetap
       mendapat animasi "baru tersambung". Pasangan yang masih hidup
       DIPERTAHANKAN — kalau set dikosongkan seluruhnya, semua kabel akan
       beranimasi ulang tiap kali ada satu laptop bergabung. */
    this._animasi.length = 0;
    /* Label lama ikut dibuang — kalau tidak, label node yang sudah mati
       tetap menempel di layar dan berduplikasi dengan yang baru. */
    if (this.lapisanLabel) this.lapisanLabel.innerHTML = "";
    this.labelEls.clear();

    const nodeUtama = data.node || { nama: "n1" };
    const serverWarna = 0x2563eb;
    const pcWarna = 0x2563eb;

    /* --- node utama (server) --- */
    this.nodeUtama = nodeUtama.nama;
    this.tambahNode(nodeUtama.nama, "server", serverWarna, 0, 0, -40, true);

    /* --- node kluster lain ---
       Cincin LUAR (radius 215 > PC 130) supaya tidak berebut tempat.
       Sudut 215° dipilih supaya berada di celah antar-PC, tapi bukan
       berseberangan dengan hub — kalau segaris dengan PC, kabelnya
       bertumpuk dan topologinya tak terbaca. */
    const kluster = data.dikenal || [];
    const CELAH_KLUSTER = [215, 335, 95];
    kluster.forEach((k, i) => {
      const sudut = ((CELAH_KLUSTER[i % CELAH_KLUSTER.length]) * Math.PI) / 180;
      this.tambahNode(k.nama, "server", serverWarna,
        Math.cos(sudut) * 215, 0, Math.sin(sudut) * 215, true);
    });

    /* --- PC ---
       SEMUA PC pada radius yang SAMA. Diperkecil dari 200 → 130 supaya
       rasio ukuran device : jarak masuk akal (sebelumnya device tampil
       sekecil perangko di panel lebar). Sudut dipilih manual supaya
       jarak antar-PC cukup lebar dan tidak ada yang menutupi server. */
    /* --- PC ---
       Slot ditentukan oleh NAMA, bukan urutan array.

       Sebelumnya posisi diambil dari indeks (`SUDUT_PC[i]`), jadi saat
       PC02 keluar, PC03 langsung melompat ke slot PC02 — laptop yang
       tidak berubah ikut berpindah. Dengan memakai nama sebagai kunci,
       tiap laptop selalu kembali ke slotnya sendiri walau yang lain
       datang-pergi.

       Kalau jumlahnya melebihi jumlah slot tetap, slot tambahan dihitung
       melingkar dengan radius sedikit lebih besar supaya tidak menimpa
       slot dasar. Jadi user ke-7, ke-8, dst tetap dapat tempat — tidak
       bertumpuk di satu titik. */
    const online = data.online || [];
    const SUDUT_PC = [205, 325, 85, 145, 265, 25];
    const RADIUS_PC = 130;

    /* Kunci slot PERMANEN per nama laptop.

       Memakai urutan alfabet (percobaan sebelumnya) tetap salah: begitu
       PC05 keluar, PC06 dan seterusnya naik satu slot — laptop yang tidak
       berubah ikut bergeser. Solusinya nama dipegang ke slot yang sudah
       pernah diberikan, dan slot itu tidak pernah dipakai ulang selama
       halaman masih terbuka. Jadi laptop yang datang-pergi selalu kembali
       ke tempatnya sendiri. */
    if (!this._slotPC) this._slotPC = new Map();
    const namaUrut = online.map((p) => p.nama).slice().sort();
    for (const n of namaUrut) {
      if (this._slotPC.has(n)) continue;
      /* Cari nomor slot terkecil yang masih kosong. */
      let i = 0;
      while ([...this._slotPC.values()].includes(i)) i++;
      this._slotPC.set(n, i);
    }
    /* Pemetaan sengaja TIDAK dibuang walau laptop sudah pergi: kalau
       dibuang, laptop yang kembali akan mendapat slot baru dan seluruh
       peta bergeser. Ukurannya kecil (beberapa lusin nama), jadi
       dibiarkan selama halaman terbuka. */

    online.forEach((p) => {
      const i = this._slotPC.get(p.nama) ?? 0;
      const dasar = SUDUT_PC[i % SUDUT_PC.length];
      /* Putaran ke-2, ke-3, ... digeser setengah celah dan radiusnya
         dinaikkan, supaya tidak menumpuk di slot yang sama. */
      const putaran = Math.floor(i / SUDUT_PC.length);
      const sudut = ((dasar + putaran * 40) * Math.PI) / 180;
      /* Cincin luar dinaikkan 70 (bukan 55) supaya laptop ke-7/8 tidak
         menempel ke laptop cincin dalam — pada 55 jaraknya tinggal 77 unit
         sementara lebar model 38, labelnya mulai bertabrakan. */
      const radius = RADIUS_PC + putaran * 70;
      const g = this.tambahNode(p.nama, "pc", pcWarna,
        Math.cos(sudut) * radius, 0, Math.sin(sudut) * radius, true);
      if (g) g.userData.node = p.node || this.nodeUtama;
      /* Laptop yang BARU masuk dianimasikan tumbuh dari skala 0 +
         cincin denyut di alasnya. Tanpa ini, laptop baru muncul
         mendadak — penonton tidak melihat bahwa ada user bergabung. */
      if (g && !this._pcLama.has(p.nama)) this.animasiMasuk(g, p.nama);
    });
    /* Ingat siapa saja yang sudah ada, untuk membedakan "baru" dan "lama"
       di refresh berikutnya. */
    this._pcLama = new Set(online.map((p) => p.nama));

    /* --- kabel --- */
    this.gambarKabel(nodeUtama.nama, online.map((p) => p.nama), kluster.map((k) => k.nama));

    /* Isi scene berubah → ukuran optimal berubah → hitung ulang sekali.
       Kabel lama sudah dibuang, jadi paket yang masih melintas di kurva
       lama harus ikut dibersihkan — kalau tidak, paketnya membeku di
       posisi terakhir sambil menunjuk kurva yang sudah tidak ada. */
    this.bersihkanPaket();
    this.frustumKotor = true;
  }

  /* ------------------------------------------------------------------
     Tambah satu node ke scene.
     ------------------------------------------------------------------ */
  tambahNode(nama, jenis, warna, x, y, z, hidup) {
    let g = null;

    /* Coba pakai aset GLB dulu */
    const inst = this.template(jenis);
    if (inst) {
      /* Normalisasi dulu (sumbu panjang -> Y, tinggi = 1, alas di y=0,
         tengah horizontal di titik asal), baru diskalakan ke tinggi akhir. */
      this.normalisasiModel(inst);
      const postur = Peta3D.POSTUR[jenis] || {};
      const skala = postur.skala || SKALA_ASET[jenis] || 20;
      inst.scale.multiplyScalar(skala);
      /* Rotasi Y: server menghadap frontal, PC sedikit miring 3/4. */
      inst.rotation.y += postur.rotY || 0;
      g = inst;
    }

    /* Fallback: balok sederhana (kalau GLB belum ada).
       Tingginya dibuat SAMA dengan versi GLB (SKALA_ASET) supaya
       perbandingan ukuran server vs PC konsisten walau asetnya berubah. */
    if (!g) {
      const tinggi = jenis === "server" ? SKALA_ASET.server : SKALA_ASET.pc;
      const w = jenis === "server" ? tinggi * 1.7 : tinggi * 0.5;
      const d = jenis === "server" ? tinggi * 0.75 : tinggi * 0.5;
      const geo = new THREE.BoxGeometry(w * 2, tinggi, d * 2);
      const mat = new THREE.MeshLambertMaterial({
        color: hidup ? warna : 0x94a3b8,
        emissive: hidup ? warna : 0x000000,
        emissiveIntensity: hidup ? 0.18 : 0,
      });
      g = new THREE.Group();
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.y = tinggi / 2;
      g.add(mesh);
      g.position.y = 0;
      /* penanda jelas: node hidup punya halo */
      if (hidup) {
        const halo = new THREE.Mesh(
          new THREE.RingGeometry(w * 1.7, w * 1.95, 32),
          new THREE.MeshBasicMaterial({
            color: warna, transparent: true, opacity: 0.28, side: THREE.DoubleSide,
          })
        );
        halo.rotation.x = -Math.PI / 2;
        halo.position.y = 0.6;
        g.add(halo);
      }
    }

    g.position.set(x, y, z);
    g.userData = { nama, jenis, hidup, node: this.nodeUtama };
    this.grupNode.add(g);

    /* Contact shadow: blob gelap rata di alas tepat di bawah model.
       Tanpa ini, model terlihat melayang — terutama kalau modelnya
       berwarna putih di atas lantai putih.

       PENTING: shadow HARUS di dalam group TERPISA, bukan child dari
       group model. Group model di-scale ~32x, jadi child-nya ikut
       32x — radius 16 jadi 512 unit dan bayangannya tak terlihat
       (atau jadi berantakan). */
    const bayang = new THREE.Mesh(
      new THREE.CircleGeometry(jenis === "server" ? 26 : 20, 32),
      new THREE.MeshBasicMaterial({
        color: 0x64748b, transparent: true, opacity: 0.38, depthWrite: false,
      })
    );
    bayang.rotation.x = -Math.PI / 2;
    bayang.position.set(x, 0.12, z);
    bayang.renderOrder = -1;
    this.grupNode.add(bayang);

    this.node.set(nama, g);
    this.buatLabel(nama, jenis, hidup);
    return g;
  }

  /* ------------------------------------------------------------------
     Kabel: garis lurus (garis tipis) antara node.
     ------------------------------------------------------------------ */
  gambarKabel(namaA, daftarPc, daftarKluster) {
    /* Warna kabel ikut tema. Tema gelap butuh kabel lebih terang —
       abu-abu gelap di atas latar hitam tak terbedakan. */
    const gelap = document.documentElement.dataset.tema === "gelap";
    /* Radius alas per jenis node — dipakai untuk menghentikan kabel di
       tepi model, bukan di tengahnya. */
    const jenisRadius = (nama) => {
      const g = this.node.get(nama);
      const jenis = g && g.userData ? g.userData.jenis : "pc";
      return jenis === "server" ? 22 : 16;
    };
    const buat = (a, b) => {
      const ga = this.node.get(a), gb = this.node.get(b);
      if (!ga || !gb) return;
      const pa = new THREE.Vector3(); ga.getWorldPosition(pa);
      const pb = new THREE.Vector3(); gb.getWorldPosition(pb);
      /* Kabel menyambung di ALAS dan di TEPI model, bukan di tengah badan.
         Sebelumnya y = tinggiLabel*0.35 ≈ 20 untuk server setinggi 44 —
         persis tengah badan, jadi tabung kabel menembus model. Di alas
         (y=1) kabel terbaca sebagai kabel lantai, bukan garis tembus. */
      pa.y = 1; pb.y = 1;
      const arah = pb.clone().sub(pa);
      const jarak = arah.length();
      if (jarak < 0.01) return;
      arah.normalize();
      /* Mundur sejauh radius alas supaya ujungnya berhenti di tepi model. */
      pa.addScaledVector(arah, jenisRadius(a) * 0.75);
      pb.addScaledVector(arah, -jenisRadius(b) * 0.75);
      /* Kabel digambar sebagai LENGKUNGAN (arc) dari TUBE.
         Dua alasan:
           1. LineBasicMaterial.linewidth diabaikan WebGL (selalu 1px)
              -> kabel jadi nyaris tak terlihat.
           2. Garis lurus dari A ke B terlihat seperti "garis diagram",
              bukan kabel. Lengkungan di tengah jalan langsung terbaca
              sebagai kabel yang menghubungkan dua perangkat. */
      const panjang = pa.distanceTo(pb);
      if (panjang < 0.01) return;

      /* Titik tengah dikelurkan sedikit: kabel membesar di tengah jalan
         (kabel menggantung), TAPI tidak lebih tinggi dari ujungnya —
         kalau busur terlalu tinggi, kabel terlihat melayang jauh dari
         perangkat dan tidak menyiratkan koneksi. */
      const tengah = pa.clone().add(pb).multiplyScalar(0.5);
      tengah.y += Math.min(6, panjang * 0.04);

      const kurva = new THREE.QuadraticBezierCurve3(pa, tengah, pb);
      const segmen = Math.max(12, Math.min(48, Math.round(panjang / 12)));
      const geo = new THREE.TubeGeometry(kurva, segmen, 0.8, 8, false);
      /* Warna kabel SENGAJA beda dari objek (abu-abu gelap), bukan biru
         seperti node. Kalau warna sama, kabel hilang di antara objek dan
         topologi jadi tak terbaca. */
      const mat = new THREE.MeshLambertMaterial({
        /* Abu-abu TERANG, dan di tema gelap dinaikkan lagi: kabel abu
           gelap di atas latar hitam praktis tak terlihat. */
        color: gelap ? 0xc3d2ea : 0x9aa8bf,
        transparent: true, opacity: gelap ? 0.85 : 1,
        emissive: gelap ? 0x4a6a9c : 0x2c3a4f,
        emissiveIntensity: 0.5,
      });
      const line = new THREE.Mesh(geo, mat);
      /* TubeGeometry sudah memakai koordinat dunia langsung,
         jadi tidak perlu posisi/quaternion tambahan. */
      this.grupKabel.add(line);
      const rek = { a, b, line, kurva, segmen };
      this.kabelList.push(rek);

      /* Kabel ke laptop BARU "tumbuh" dari ujung server ke ujung laptop.
         Caranya: geometri diganti tiap frame dengan potongan kurva yang
         makin panjang. Skala tidak bisa dipakai — TubeGeometry memakai
         koordinat dunia, jadi mengecilkan scale justru menggeser posisinya. */
      const kunci = a + ">" + b;
      if (!this._kabelLama.has(kunci) && !this._kabelLama.has(b + ">" + a)) {
        this._animasi.push({ kabel: rek, t: 0, durasi: 0.6, segmen });
      }
      this._kabelLama.add(kunci);
    };

    for (const p of daftarPc) buat(namaA, p);
    for (const k of daftarKluster) buat(namaA, k);
    /* mesh antar node kluster */
    for (let i = 0; i < daftarKluster.length; i++) {
      for (let j = i + 1; j < daftarKluster.length; j++) {
        buat(daftarKluster[i], daftarKluster[j]);
      }
    }
  }

  /* ------------------------------------------------------------------
     PAKET CAHAYA — bola bercahaya yang berjalan di atas kabel.

     Arah warna mengikuti arti pesan, bukan hiasan:
       KUNING  = laptop → server   (pesan naik / dikirim)
       BIRU    = server → laptop   (pesan turun / diterima)

     Kenapa pakai kurva kabel: paket harus mengikuti jalur yang SAMA
     dengan kabelnya. Kalau dihitung garis lurus sendiri, paket akan
     melayang di luar kabel dan terbaca sebagai efek tempelan.

     Catatan performa: setiap paket satu mesh kecil. Kalau ada 6 laptop
     dan tiap pesan memicu 2 paket, tumpukan bisa puluhan. Batas
     MAKS_PAKET mencegah scene penuh dan frame drop di laptop kelas
     menengah saat demo.
     ------------------------------------------------------------------ */
  static MAKS_PAKET = 40;

  /* Cari kurva kabel antara dua node. Balikkan null kalau tidak ada. */
  kurvaAntara(a, b) {
    for (const k of this.kabelList) {
      if ((k.a === a && k.b === b) || (k.a === b && k.b === a)) return k.kurva;
    }
    return null;
  }

  /* Tekstur cahaya: gradient radial putih → transparan. Dipakai sebagai
     SPRITE, bukan bola 3D.

     Alasan: bola MeshBasicMaterial tanpa bloom hanya terbaca sebagai
     titik warna keras. Sprite ber-gradient selalu menghadap kamera dan
     tepinya melunak, jadi terbaca sebagai sumber cahaya — dan biayanya
     lebih murah (satu quad, bukan 96 segitiga). Dibuat SEKALI lalu
     dipakai bersama semua paket. */
  teksturCahaya() {
    if (this._teksturCahaya) return this._teksturCahaya;
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0.00, "rgba(255,255,255,1)");
    gr.addColorStop(0.28, "rgba(255,255,255,1)");
    gr.addColorStop(0.55, "rgba(255,255,255,0.55)");
    gr.addColorStop(0.82, "rgba(255,255,255,0.12)");
    gr.addColorStop(1.00, "rgba(255,255,255,0)");
    g.fillStyle = gr;
    g.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    this._teksturCahaya = t;
    return t;
  }

  /* Kirim satu paket cahaya dari `dari` ke `ke`.
     warna: 0xf59e0b (kuning, naik) | 0x38bdf8 (biru, turun). */
  terbangkan(dari, ke, warna, opsi = {}) {
    if (!this.siap || this.jeda) return;
    const kurva = this.kurvaAntara(dari, ke);
    if (!kurva) return;
    if (this.paket.length >= Peta3D.MAKS_PAKET) return;

    const ukuran = opsi.besar ? 20 : 16;
    const tex = this.teksturCahaya();

    const bola = new THREE.Sprite(new THREE.SpriteMaterial({
      map: tex, color: warna,
      transparent: true, depthTest: false,
      blending: THREE.AdditiveBlending,
      opacity: 1,
    }));
    bola.scale.setScalar(ukuran);
    bola.renderOrder = 999;

    /* Ekor: sprite lebih kecil yang mengikuti posisi paket beberapa saat
       sebelumnya. Ini yang membuat ARAH gerak terbaca walau dilihat
       sekilas — tanpa ekor, arah hanya bisa ditebak dari frame berikutnya. */
    const ekor = [];
    for (let e = 1; e <= 4; e++) {
      const m = new THREE.Sprite(new THREE.SpriteMaterial({
        map: tex, color: warna,
        transparent: true, depthTest: false,
        blending: THREE.AdditiveBlending,
        opacity: 0.55 - e * 0.11,
      }));
      m.scale.setScalar(ukuran * (1 - e * 0.18));
      m.renderOrder = 998;
      this.grupPaket.add(m);
      ekor.push(m);
    }

    this.grupPaket.add(bola);
    this.paket.push({
      bola, kurva, ekor,
      t: 0,
      /* Laju = fraksi kurva per detik. Kabel panjang dan pendek jadi
         terlihat bergerak dengan kecepatan berbeda — itu wajar dan justru
         membantu mata membedakan rute. */
      laju: opsi.laju || 0.75,
      hidup: true,
    });
  }

  /* Majukan semua paket. Dipanggil tiap frame dari tick(). */
  majukanPaket(dt) {
    for (let i = this.paket.length - 1; i >= 0; i--) {
      const p = this.paket[i];
      p.t += dt * p.laju;
      if (p.t >= 1) {
        this.grupPaket.remove(p.bola);
        p.bola.material.dispose();
        /* Ekor ikut dibuang — kalau tertinggal, scene penuh titik kecil
           yang tidak pernah hilang. */
        for (const m of p.ekor) {
          this.grupPaket.remove(m);
          m.material.dispose();
        }
        this.paket.splice(i, 1);
        continue;
      }
      p.kurva.getPointAt(p.t, p.bola.position);
      /* Ekor ditempel di fraksi t yang lebih kecil (di belakang). */
      p.ekor.forEach((m, idx) => {
        const tb = Math.max(0, p.t - 0.022 * (idx + 1));
        p.kurva.getPointAt(tb, m.position);
      });
    }
  }

  /* Buang semua paket (dipanggil saat scene dibangun ulang). */
  bersihkanPaket() {
    for (const p of this.paket) {
      this.grupPaket.remove(p.bola);
      p.bola.material.dispose();
      for (const m of p.ekor) {
        this.grupPaket.remove(m);
        m.material.dispose();
      }
    }
    this.paket.length = 0;
  }

  /* ------------------------------------------------------------------
     ANIMASI MASUK — laptop yang baru bergabung tumbuh dari skala 0
     dan memancarkan cincin di alasnya.

     Kenapa skala (bukan fade): model GLB punya banyak material; mengatur
     opacity tiap material berarti menyalin material per instance dan
     menambah draw call. Skala cukup satu properti di group, murah, dan
     efeknya justru lebih terbaca di proyektor kelas.

     Kenapa durasi pendek (~0,55 s): dashboard menyegarkan tiap 1,2 detik.
     Animasi yang lebih panjang dari itu akan bertumpuk dengan refresh
     berikutnya dan terlihat berkedip.
     ------------------------------------------------------------------ */
  animasiMasuk(g, nama) {
    /* Simpan skala akhir; group di-skala dari 0 lalu dinaikkan bertahap. */
    const skalaAkhir = g.scale.clone();
    g.scale.setScalar(0.001);

    /* Cincin di alas: satu mesh, memudar sambil membesar. */
    const cincin = new THREE.Mesh(
      new THREE.RingGeometry(6, 9, 40),
      new THREE.MeshBasicMaterial({
        color: 0x22c55e, transparent: true, opacity: 0.9,
        side: THREE.DoubleSide, depthWrite: false,
      })
    );
    cincin.rotation.x = -Math.PI / 2;
    const pos = g.position;
    cincin.position.set(pos.x, 0.4, pos.z);
    this.grupPaket.add(cincin);

    this._animasi.push({
      g, nama, cincin, skalaAkhir,
      t: 0,
      durasi: 0.55,
    });
  }

  /* Majukan animasi masuk. Dipanggil dari tick(). */
  majukanAnimasi(dt) {
    for (let i = this._animasi.length - 1; i >= 0; i--) {
      const a = this._animasi[i];
      a.t += dt;
      const p = Math.min(1, a.t / a.durasi);

      /* --- kabel yang tumbuh --- */
      if (a.kabel) {
        const rek = a.kabel;
        const potong = Math.max(0.02, p);
        const kurvaPotong = new THREE.QuadraticBezierCurve3(
          rek.kurva.getPointAt(0),
          potong > 0.5 ? rek.kurva.getPointAt(0.5) : rek.kurva.getPointAt(potong * 0.5),
          rek.kurva.getPointAt(potong)
        );
        const geoBaru = new THREE.TubeGeometry(
          kurvaPotong, Math.max(4, Math.round(a.segmen * potong)),
          rek.line.geometry.parameters.radius || 0.8, 8, false
        );
        rek.line.geometry.dispose();
        rek.line.geometry = geoBaru;
        if (p >= 1) {
          /* Pasang geometri penuh supaya hasil akhirnya persis sama
             dengan kabel biasa (bukan potongan yang dibekukan). */
          rek.line.geometry.dispose();
          rek.line.geometry = new THREE.TubeGeometry(
            rek.kurva, a.segmen, geoBaru.parameters.radius || 0.8, 8, false
          );
          this._animasi.splice(i, 1);
        }
        continue;
      }

      /* --- laptop yang tumbuh + cincin --- */
      /* Skala pakai kurva "kembali sedikit" (overshoot halus): cepat
         naik lalu mendarat. Skala linear terasa kaku. */
      const e = 1 - Math.pow(1 - p, 3);
      const denyut = 1 + Math.sin(p * Math.PI) * 0.12;
      a.g.scale.copy(a.skalaAkhir).multiplyScalar(Math.max(0.001, e * denyut));

      /* Cincin membesar dan memudar. */
      const cr = 1 + p * 3.2;
      a.cincin.scale.setScalar(cr);
      a.cincin.material.opacity = Math.max(0, 0.9 * (1 - p));

      if (p >= 1) {
        a.g.scale.copy(a.skalaAkhir);
        this.grupPaket.remove(a.cincin);
        a.cincin.geometry.dispose();
        a.cincin.material.dispose();
        this._animasi.splice(i, 1);
      }
    }
  }

  /* ------------------------------------------------------------------
     Animasi: putar otomatis + render.
     ------------------------------------------------------------------ */
  tick(dt = 0.016) {
    if (!this.siap) return;

    /* Putar otomatis HANYA saat tidak ada drag dan tidak ada sisa
       kelembaman. Kalau keduanya jalan bersamaan, nilai yaw ditarik dua
       arah (tick menambah, drag menimpa) — hasilnya getar. */
    if (this.rotasiOtomatis && !this.jeda && !this.drag
        && Math.abs(this.yaw - this.yawSasaran) < 0.002
        && !this.lembam && !this.putarLagi) {
      this.yawSasaran += dt * 0.10;
      this.yaw = this.yawSasaran;
    }

    /* Kejar nilai sasaran dengan pelembutan (bukan lompat) — gerakan
       kursor yang patah-patah jadi mulus. */
    if (!this.rotasiOtomatis || this.drag) {
      const k = 1 - Math.pow(0.001, dt);   /* ~0.1 s sampai dekat */
      this.yaw += (this.yawSasaran - this.yaw) * k;
      this.pitch += (this.pitchSasaran - this.pitch) * k;
    }

    this.perbaruiKamera();
    /* Frustum hanya dihitung ulang kalau datanya berubah ATAU saat drag
       selesai. Kalau dihitung tiap frame, bounding box ikut berubah
       tiap derajat rotasi → skala scene mengembang-kempis → goyang. */
    if (this.frustumKotor) this.sesuaikanFrustum();
    else this.terapkanZoom();
    /* Paket cahaya dimajukan SEBELUM render, sesudah kamera — posisinya
       di koordinat dunia, jadi tidak terpengaruh perubahan kamera. */
    this.majukanPaket(dt);
    this.majukanAnimasi(dt);
    this.renderer.render(this.scene, this.kamera);
    this.perbaruiLabel();
  }

  /* Letakkan kamera di posisi bola (yaw, pitch) dan arahkan ke PUSAT isi
     (bukan selalu 0,0,0 — kalau pusatnya bergeser karena jumlah PC
     berubah, peta tidak lagi terpotong di tepi). */
  perbaruiKamera() {
    const d = this.jarakKamera;
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const p = this.frustumBasis ? this.frustumBasis.pusat : { x: 0, y: 0, z: 0 };
    this.kamera.position.set(
      p.x + d * sy * cp,
      p.y + d * sp,
      p.z + d * cy * cp
    );
    this.kamera.up.set(0, 1, 0);
    this.kamera.lookAt(p.x, p.y, p.z);
  }

  /* Simpan pusat + RADIUS HORIZONTAL + TINGGI isi.

     Kenapa bukan bola pembungkus: scene ini GEPENG (lebar 300 unit, tinggi
     32). Bola pembungkus memakai radius terbesar (= lebar), jadi seluruh
     tinggi panel dipakai untuk ruang kosong di atas-bawah — device tampil
     sekecil perangko. Yang benar: lebar dipakai untuk sumbu horizontal,
     tinggi untuk sumbu vertikal, masing-masing dihitung sendiri. */
  sesuaikanFrustum() {
    if (!this.grupNode.children.length) return;
    const kotak = new THREE.Box3().setFromObject(this.grupNode);
    if (kotak.isEmpty()) return;

    const pusat = kotak.getCenter(new THREE.Vector3());

    /* Radius horizontal dihitung dari POSISI TIAP NODE, bukan dari sudut
       bounding box. Sudut box = r * sqrt(2), jadi memakai sudut box membuat
       kamera mundur 41% terlalu jauh dan scene hanya mengisi ~58% lebar
       panel. */
    let rH = 1;
    const v = new THREE.Vector3();
    for (const g of this.node.values()) {
      g.getWorldPosition(v);
      rH = Math.max(rH, Math.hypot(v.x - pusat.x, v.z - pusat.z));
    }
    /* + separuh lebar model (label butuh ruang di tepi). */
    rH += 40;

    const tinggi = Math.max(1, kotak.max.y - kotak.min.y);

    this.frustumBasis = { pusat, rH, tinggi };
    this.frustumKotor = false;
    this.terapkanZoom();
  }

  /* Terapkan zoom ke frustum. Murah — tidak menjelajah geometri.
     Rentang vertikal ikut pitch: makin mendatar dilihat, makin pipih
     bidangnya di layar, jadi makin sedikit ruang vertikal yang dibutuh. */
  terapkanZoom() {
    if (!this.frustumBasis) return;
    const { pusat, rH, tinggi } = this.frustumBasis;
    const aspect = Math.max(0.2,
      (this.kanvas.clientWidth || 800) / Math.max(1, this.kanvas.clientHeight || 500));

    const sp = Math.abs(Math.sin(this.pitch));
    const cp = Math.abs(Math.cos(this.pitch));
    /* Setengah-tinggi dunia yang benar-benar terpakai di layar. */
    const setengahV = rH * sp + (tinggi / 2) * cp;

    let halfH = setengahV * 1.06 / this.zoom;
    /* Kalau horizontalnya lebih lebar dari rasio panel, vertikal ikut
       dilebarkan — kalau tidak, PC di sisi kiri/kanan terpotong.
       ‌CATATAN: halfH TIDAK boleh dibesarkan melebihi kebutuhan vertikal
       kalau rH sudah muat; margin horizontal diatur terpisah di bawah. */
    if (rH > halfH * aspect) halfH = (rH * 1.02) / aspect;

    const halfW = halfH * aspect;
    this.kamera.left = -halfW;
    this.kamera.right = halfW;
    this.kamera.top = halfH;
    this.kamera.bottom = -halfH;
    this.kamera.updateProjectionMatrix();
    this._pusatPandang = pusat;
  }

  /* ------------------------------------------------------------------
     LABEL — overlay DOM di atas canvas WebGL.

     Kenapa DOM, bukan gambar di canvas: label harus punya teks yang BISA
     DIBACA (nama PC, nama node). Kalau digambar di canvas WebGL, teksnya
     ikut ter-raster dan jadi buram saat di-zoom. DOM juga otomatis
     tajam di semua resolusi layar retina.
     ------------------------------------------------------------------ */
  siapkanLapisanLabel() {
    if (this.lapisanLabel) return;
    const div = document.createElement("div");
    div.className = "lapisan-label";
    this.kanvas.parentElement.appendChild(div);
    this.lapisanLabel = div;
  }

  buatLabel(nama, jenis, hidup) {
    if (!this.lapisanLabel) this.siapkanLapisanLabel();
    const el = document.createElement("div");
    el.className = "label-3d" + (jenis === "server" ? " label-server" : "");
    /* Label server ditulis lengkap "SERVER n1" — supaya pembaca langsung
       tahu itu node server, bukan cuma "n1" yang sama saja dengan nama
       PC. */
    el.textContent = jenis === "server" ? "SERVER " + nama : nama;
    if (!hidup) el.classList.add("label-mati");
    this.lapisanLabel.appendChild(el);
    this.labelEls.set(nama, el);
    return el;
  }

  /* Hitung posisi layar tiap label dan pasang. Dipanggil tiap frame. */
  perbaruiLabel() {
    if (!this.lapisanLabel) return;
    const w = this.kanvas.clientWidth, h = this.kanvas.clientHeight;
    const v = new THREE.Vector3();

    /* Kumpulkan posisi dulu, baru selesaikan tabrakan. Kalau label
       digeser satu per satu sambil digambar, geseran label pertama
       mengubah keputusan untuk label berikutnya — hasilnya berkedip. */
    const daftar = [];
    for (const [nama, g] of this.node) {
      const el = this.labelEls.get(nama);
      if (!el) continue;
      g.getWorldPosition(v);
      /* Naik ke PUNCAK model + jarak tetap 8 unit. tinggiLabel() kembalikan
         max.y dunia (bukan tinggi relatif) — memakai tinggi relatif dulu
         membuat label turun 87 px dan menempel di badan model. */
      v.y = this.tinggiLabel(nama) + 8;
      v.project(this.kamera);
      if (v.z > 1) { el.style.visibility = "hidden"; continue; }
      el.style.visibility = "visible";
      daftar.push({
        el,
        x: (v.x * 0.5 + 0.5) * w,
        y: (-v.y * 0.5 + 0.5) * h,
      });
    }

    /* Tinggi label ~23 px, lebar bervariasi. Tabrakan diperiksa sebagai
       kotak perkiraan: cukup untuk mencegah label bertindih, tanpa perlu
       mengukur offsetWidth tiap frame (mahal, memicu reflow). */
    const TW = 50, TH = 24;
    /* Urut tetap supaya hasilnya stabil antar frame (bukan acak urutan Map). */
    daftar.sort((a, b) => a.y - b.y || a.x - b.x);
    for (let i = 1; i < daftar.length; i++) {
      for (let j = 0; j < i; j++) {
        const a = daftar[j], b = daftar[i];
        if (Math.abs(a.x - b.x) < TW && Math.abs(a.y - b.y) < TH) {
          /* Geser ke atas (label selalu di atas model), satu tingkat. */
          b.y = a.y - TH;
        }
      }
    }

    for (const d of daftar) {
      /* Cegah label keluar kanvas di tepi atas. */
      const y = Math.max(TH * 0.5 + 2, d.y);
      d.el.style.transform =
        `translate(-50%,-50%) translate(${d.x.toFixed(1)}px,${y.toFixed(1)}px)`;
    }
  }

  /* Puncak model dalam koordinat dunia (bukan tinggi relatif). */
  tinggiLabel(nama) {
    const g = this.node.get(nama);
    if (!g) return 0;
    return new THREE.Box3().setFromObject(g).max.y;
  }

  /* ------------------------------------------------------------------
     Kontrol: seret untuk memutar, gulir untuk zoom.
     ------------------------------------------------------------------ */
  pasangKontrol() {
    const c = this.kanvas;
    let seret = false, x0 = 0, y0 = 0, yaw0 = 0, pitch0 = 0, gerak = 0;

    /* Sensitivitas: 0,35° per piksel. Seluruh lebar kanvas (~1000 px)
       = ~350° — cukup untuk memutar penuh, tidak sampai berputar-putar
       hanya karena salah geser sedikit. (Sebelumnya 0,006 rad/px =
       0,34°/px tapi yaw ditimpa absolut sambil auto-rotate jalan →
       getar; lihat tick().) */
    const SENS_YAW = 0.0035;
    const SENS_PITCH = 0.0030;

    c.addEventListener("pointerdown", (e) => {
      seret = true; gerak = 0; x0 = e.clientX; y0 = e.clientY;
      yaw0 = this.yawSasaran; pitch0 = this.pitchSasaran;
      this.drag = true;
      this.rotasiOtomatis = false;   /* berhenti total saat dipegang */
      try { c.setPointerCapture(e.pointerId); } catch (_) {}
      c.style.cursor = "grabbing";
    });

    c.addEventListener("pointermove", (e) => {
      if (!seret) return;
      gerak += Math.abs(e.movementX || 0) + Math.abs(e.movementY || 0);
      this.yawSasaran = yaw0 - (e.clientX - x0) * SENS_YAW;
      this.pitchSasaran = Math.max(0.08,
        Math.min(1.15, pitch0 - (e.clientY - y0) * SENS_PITCH));
    });

    const lepas = (e) => {
      if (!seret) return;
      seret = false;
      this.drag = false;
      try { c.releasePointerCapture(e.pointerId); } catch (_) {}
      c.style.cursor = "grab";
      /* Setelah drag selesai, otomatis putar lagi hanya kalau tadi benar-
         benar tidak menggeser. Kalau digeser, biarkan berhenti di posisi
         yang dipilih pengguna — jangan direbut lagi. */
      if (gerak < 4) this.rotasiOtomatis = true;
    };
    c.addEventListener("pointerup", lepas);
    c.addEventListener("pointercancel", lepas);

    /* Zoom memakai FOKUS SAAT INI, bukan lompatan 8,7% per klik roda —
       trackpad mengirim banyak event kecil, jadi 8,7% per event membuat
       peta melompat liar. */
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const delta = e.deltaY * unit;
      this.zoom = Math.max(0.5, Math.min(3.5,
        this.zoom * Math.pow(0.9992, delta)));
      this.terapkanZoom();
    }, { passive: false });
  }

  /* Ukuran kanvas berubah → sesuaikan */
  sesuaikanUkuran() {
    if (!this.siap) return;
    const w = this.kanvas.clientWidth || 800;
    const h = this.kanvas.clientHeight || 500;
    this.renderer.setSize(w, h, false);
  }
}
