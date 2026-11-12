#!/usr/bin/env python3
"""SERVER KLUster — MK Pemrograman Jaringan Kluster (MKKL1028)

Satu proses = satu NODE dalam kluster. Tugasnya:

  1. TCP  → melayani pengguna (klien laptop): chat & perintah.
  2. UDP  → detak keberadaan (heartbeat) antar node + pengukuran latensi.
  3. Mesh TCP antar node: pesan diteruskan ke node lain supaya chat
     tetap jalan walau klien menyambung ke node yang berbeda.
  4. Dashboard web: mode terang + peta jaringan 3D.
  5. Basis data SQLite: pesan, perintah, kejadian, pengukuran.

Jalankan:
    python3 server.py                                  # node n1, TCP 5000, UDP 5010
    python3 server.py --node n2 --port 5001 --udp 5011 \
                     --tetangga 127.0.0.1:5000:5010    # node kedua di mesin sama

Hentikan dengan Ctrl+C.
"""
from __future__ import annotations

import argparse
import collections
import json
import queue
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# ------------------------------------------------------------------ pengaturan
AKAR = Path(__file__).resolve().parent
DB = AKAR / "data" / "kluster.db"
DIR_STATIS = AKAR / "static"

# Perintah yang boleh dikirim server. Dibatasi (daftar putih) supaya server
# tidak bisa menjalankan perintah bebas di laptop orang lain.
PERINTAH_DIIZINKAN = {"pesan", "kunci", "restart", "matikan", "batal"}

# TCP ke pengguna
PING_TIAP = 20.0        # server memanggil pengguna tiap 20 detik
BATAS_DIAM = 90.0       # putus kalau 90 detik tidak ada kabar sama sekali

# UDP antar node
DETAK_TIAP = 1.0        # detak heartbeat tiap 1 detik
DETAK_MATANG = 3.5      # tetangga dianggap mati kalau 3,5 detik tidak ada detak
PING_UKUR_TIEP = 5.0    # ukur latensi TCP vs UDP tiap 5 detik
GRUP_UDP = "255.255.255.255"

# Identitas node — diisi dari argumen baris perintah.
NODE = {"nama": "n1", "tcp": 5000, "udp": 5010}

KUNCI = threading.Lock()
CETAK: queue.Queue[str] = queue.Queue()

PENGGUNA: dict[str, "Pengguna"] = {}       # klien TCP yang tersambung
TETANGGA: dict[str, "Tetangga"] = {}       # node lain (mesh TCP)
DIKENAL: dict[str, dict] = {}              # node yang diketahui (dari config/gossip/UDP)
TERLIHAT: collections.OrderedDict[str, None] = collections.OrderedDict()  # dedup pesan
KURUNG: dict[str, dict] = {}               # pengukuran latensi per tetangga

SOCK_UDP: socket.socket | None = None


def catat(teks: str) -> None:
    CETAK.put(teks)


def sekarang() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def jam() -> str:
    return datetime.now().strftime("%H:%M:%S")


def kode_baru() -> str:
    return uuid.uuid4().hex[:16]


# ------------------------------------------------------------------ basis data
def siapkan_db() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB, timeout=10) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS node (
            nama     TEXT PRIMARY KEY,
            host     TEXT,
            tcp      INTEGER,
            udp      INTEGER,
            terakhir TEXT
        );
        CREATE TABLE IF NOT EXISTS laptop (
            nama     TEXT PRIMARY KEY,
            sistem   TEXT,
            ip       TEXT,
            node     TEXT,
            terakhir TEXT
        );
        CREATE TABLE IF NOT EXISTS pesan (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            kode     TEXT,
            pengirim TEXT NOT NULL,
            tujuan   TEXT NOT NULL,
            isi      TEXT NOT NULL,
            waktu    TEXT NOT NULL,
            protokol TEXT NOT NULL DEFAULT 'TCP',
            terjamin INTEGER NOT NULL DEFAULT 1,
            node     TEXT
        );
        CREATE TABLE IF NOT EXISTS perintah (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            sasaran   TEXT NOT NULL,
            aksi      TEXT NOT NULL,
            keterangan TEXT,
            dikirim   TEXT NOT NULL,
            dijawab   TEXT,
            berhasil  INTEGER,
            hasil     TEXT
        );
        CREATE TABLE IF NOT EXISTS kejadian (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nama   TEXT NOT NULL,
            jenis  TEXT NOT NULL,
            waktu  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ukur (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nama       TEXT NOT NULL,
            tcp_ms     REAL,
            udp_ms     REAL,
            tcp_kirim  INTEGER DEFAULT 0,
            tcp_terima INTEGER DEFAULT 0,
            udp_kirim  INTEGER DEFAULT 0,
            udp_terima INTEGER DEFAULT 0,
            waktu      TEXT NOT NULL
        );
        """)


def simpan(bentuk: str, nilai: tuple) -> int:
    with KUNCI, sqlite3.connect(DB, timeout=10) as db:
        cur = db.execute(bentuk, nilai)
        return cur.lastrowid or 0


# ------------------------------------------------------------------- pengguna
class Pengguna:
    """Satu laptop pengguna yang tersambung lewat TCP."""

    def __init__(self, koneksi: socket.socket, alamat: tuple) -> None:
        self.koneksi = koneksi
        self.ip = alamat[0]
        self.port = alamat[1]
        self.nama = f"laptop-{alamat[1]}"
        self.nama_tetap = False          # sudah menyebutkan namanya sendiri
        self.sistem = "?"
        self.masuk = time.time()
        self.terakhir_dengar = time.time()
        self.terakhir_kirim = time.time()
        self.kunci_tulis = threading.Lock()

    def kirim(self, data: dict) -> bool:
        """Kirim satu objek JSON. Dikunci supaya dua thread tidak saling sisip."""
        baris = json.dumps(data, ensure_ascii=False) + "\n"
        with self.kunci_tulis:
            try:
                self.koneksi.sendall(baris.encode("utf-8"))
                self.terakhir_kirim = time.time()
                return True
            except OSError:
                return False

    def masih_hidup(self) -> bool:
        """Perlu dipanggil (ping), atau sudah putus?"""
        sekarang_ = time.time()
        # PENTING: kirim ping SEBELUM memeriksa batas diam.
        #
        # Urutan lama (periksa dulu, kirim kemudian) punya cacat: begitu
        # BATAS_DIAM lewat, fungsi langsung balik False tanpa pernah
        # mengirim ping — padahal mungkin klien hanya perlu dipanggil.
        # Untuk klien yang tidak pernah mengirim apa pun sendiri
        # (klien yang cuma menerima), sambungannya mati tiap 90 detik
        # tanpa alasan. Dengan urutan ini, klien selalu dapat kesempatan
        # menjawab dulu.
        if sekarang_ - self.terakhir_kirim > PING_TIAP:
            self.kirim({"tipe": "ping"})
        if sekarang_ - self.terakhir_dengar > BATAS_DIAM:
            return False
        return True

    def lama(self) -> str:
        detik = int(time.time() - self.masuk)
        if detik < 60:
            return f"{detik} detik"
        if detik < 3600:
            return f"{detik // 60} menit"
        return f"{detik // 3600} jam {detik % 3600 // 60} menit"

    def keterangan(self) -> dict:
        return {"nama": self.nama, "sistem": self.sistem, "ip": self.ip,
                "lama": self.lama(), "node": NODE["nama"]}


# ------------------------------------------------------------------- tetangga
class Tetangga:
    """Node lain dalam kluster — sambungan TCP keluar (mesh)."""

    def __init__(self, nama: str, host: str, tcp: int, udp: int,
                 koneksi: socket.socket | None = None) -> None:
        self.nama = nama
        self.host = host
        self.tcp = tcp
        self.udp = udp
        self.sock = koneksi
        self.kunci_tulis = threading.Lock()
        self.masuk = time.time()
        self.terakhir_dengar = time.time()
        self.terakhir_kirim = time.time()
        # True kalau kita sudah memperkenalkan diri. Tanpa ini, dua node
        # saling membalas "node" tanpa henti (ping-pong handshake).
        self.perkenalan = False

    def kirim(self, data: dict) -> bool:
        if not self.sock:
            return False
        baris = json.dumps(data, ensure_ascii=False) + "\n"
        with self.kunci_tulis:
            try:
                self.sock.sendall(baris.encode("utf-8"))
                self.terakhir_kirim = time.time()
                return True
            except OSError:
                return False

    def keterangan(self) -> dict:
        ukur = KURUNG.get(self.nama, {})
        return {"nama": self.nama, "host": self.host, "tcp": self.tcp,
                "udp": self.udp, "hidup": True,
                "tcp_ms": ukur.get("tcp_ms"), "udp_ms": ukur.get("udp_ms"),
                "tcp_kirim": ukur.get("tcp_kirim", 0),
                "tcp_terima": ukur.get("tcp_terima", 0),
                "udp_kirim": ukur.get("udp_kirim", 0),
                "udp_terima": ukur.get("udp_terima", 0)}


# ------------------------------------------------------- daftar & penyiaran
def daftar_pengguna() -> list[dict]:
    with KUNCI:
        return [p.keterangan() for p in PENGGUNA.values()]


def siarkan(data: dict, kecuali: str | None = None) -> int:
    """Kirim ke semua pengguna lokal. Kembalikan jumlah yang berhasil."""
    berhasil = 0
    with KUNCI:
        sasaran = list(PENGGUNA.items())
    for nama, p in sasaran:
        if nama == kecuali:
            continue
        if p.kirim(data):
            berhasil += 1
    return berhasil


def siarkan_tetangga(data: dict, kecuali: str | None = None) -> int:
    """Kirim ke semua node lain dalam kluster (mesh TCP)."""
    berhasil = 0
    with KUNCI:
        sasaran = list(TETANGGA.values())
    for t in sasaran:
        if t.nama == kecuali:
            continue
        if t.kirim(data):
            berhasil += 1
    return berhasil


def kabari_daftar() -> None:
    siarkan({"tipe": "online", "daftar": daftar_pengguna()})


def daftar_node_untuk_pengguna() -> list[dict]:
    """Node hidup yang boleh diketahui pengguna — dipakai untuk failover."""
    hasil = []
    with KUNCI:
        for nama, info in DIKENAL.items():
            hasil.append({"nama": nama, "host": info.get("host", "127.0.0.1"),
                          "tcp": info.get("tcp"), "udp": info.get("udp")})
        for nama, t in TETANGGA.items():
            if not any(h["nama"] == nama for h in hasil):
                hasil.append({"nama": nama, "host": t.host, "tcp": t.tcp, "udp": t.udp})
    return hasil


def ingat_kode(kode: str) -> bool:
    """True kalau kode ini belum pernah seen. Dipakai buat dedup pesan antar node."""
    with KUNCI:
        if kode in TERLIHAT:
            return False
        TERLIHAT[kode] = None
        while len(TERLIHAT) > 2000:
            TERLIHAT.popitem(last=False)
        return True


# --------------------------------------------------------------------- UDP
def kirim_udp(data: dict, alamat: tuple[str, int]) -> bool:
    """Kirim datagram UDP lewat socket yang terikat (supaya balasan kembali ke situ)."""
    if not SOCK_UDP:
        return False
    try:
        SOCK_UDP.sendto(json.dumps(data, ensure_ascii=False).encode("utf-8"), alamat)
        return True
    except OSError:
        return False


def siarkan_udp(data: dict, kecuali: str | None = None) -> int:
    """Siarkan lewat UDP ke semua tetangga yang diketahui."""
    berhasil = 0
    with KUNCI:
        sasaran = list(DIKENAL.values())
    for info in sasaran:
        if info.get("nama") == kecuali:
            continue
        host, port = info.get("host"), info.get("udp")
        if host and port:
            if kirim_udp(data, (host, port)):
                berhasil += 1
    return berhasil


def jalankan_udp() -> None:
    """Thread pengaman UDP: heartbeat, presence klien, pengukuran latensi."""
    global SOCK_UDP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("0.0.0.0", NODE["udp"]))
    s.settimeout(1.0)
    SOCK_UDP = s
    detak_terakhir = 0.0
    ukur_terakhir = 0.0

    while True:
      try:
        sekarang_ = time.time()
        try:
            data, alamat = s.recvfrom(65535)
        except socket.timeout:
            data = None
        except OSError:
            break

        if data:
            try:
                pesan = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pesan = {}
            try:
                tangani_udp(pesan, alamat)
            except Exception as e:      # noqa: BLE001 — thread jangan mati
                catat(f"  [ERROR] tangani_udp() gagal: {e}")

        # heartbeat ke seluruh kluster
        if sekarang_ - detak_terakhir >= DETAK_TIAP:
            detak_terakhir = sekarang_
            detak = {"tipe": "detak", "node": NODE["nama"], "tcp": NODE["tcp"],
                     "udp": NODE["udp"], "waktu": sekarang()}
            kirim_udp(detak, (GRUP_UDP, NODE["udp"]))          # broadcast
            with KUNCI:
                tujuan = [(i.get("host"), i.get("udp")) for i in DIKENAL.values()]
            for host, port in tujuan:
                if host and port:
                    kirim_udp(detak, (host, port))              # unicast

        # pengukuran latensi TCP vs UDP ke setiap tetangga
        if sekarang_ - ukur_terakhir >= PING_UKUR_TIEP:
            ukur_terakhir = sekarang_
            for t in list(TETANGGA.values()):
                t.kirim({"tipe": "ping-tcp", "t": time.time()})
                ukur = KURUNG.setdefault(t.nama, {})
                ukur["tcp_kirim"] = ukur.get("tcp_kirim", 0) + 1
                kirim_udp({"tipe": "ping-udp", "t": time.time()}, (t.host, t.udp))
                ukur["udp_kirim"] = ukur.get("udp_kirim", 0) + 1

        # tetangga yang lama tidak ada detak → dianggap mati
        batas = time.time() - DETAK_MATANG
        for nama, info in list(DIKENAL.items()):
            if info.get("terakhir") and info["terakhir"] < batas:
                info["hidup"] = False
            else:
                info["hidup"] = True
      except Exception as e:      # noqa: BLE001 — loop harus terus hidup
        catat(f"  [ERROR] loop UDP: {e}")
        time.sleep(0.2)


def tangani_udp(pesan: dict, alamat: tuple[str, int]) -> None:
    tipe = pesan.get("tipe")

    if tipe == "detak":
        nama = str(pesan.get("node") or "")
        if not nama or nama == NODE["nama"]:
            return
        with KUNCI:
            info = DIKENAL.setdefault(nama, {})
            info.update({"nama": nama, "host": alamat[0],
                         "tcp": pesan.get("tcp"), "udp": pesan.get("udp"),
                         "terakhir": time.time(), "hidup": True})
        # Node baru ditemukan lewat UDP. Cari dulu berdasarkan HOST — nama
        # di daftar bisa masih placeholder ("127.0.0.1:5098") karena node
        # lain belum sempat memberi tahu nama aslinya.
        with KUNCI:
            ada = any(t.host == alamat[0] for t in TETANGGA.values())
        if ada:
            ganti_nama_tetangga(nama, alamat[0])
        else:
            try_sambung_tetangga(nama, alamat[0],
                                 pesan.get("tcp") or NODE["tcp"],
                                 pesan.get("udp") or NODE["udp"])
        return

    if tipe == "ping-udp":
        kirim_udp({"tipe": "pong-udp", "t": pesan.get("t"),
                   "node": NODE["nama"]}, alamat)
        return

    if tipe == "pong-udp":
        nama = str(pesan.get("node") or "")
        t0 = pesan.get("t")
        if nama and isinstance(t0, (int, float)):
            ukur = KURUNG.setdefault(nama, {})
            ukur["udp_ms"] = round((time.time() - t0) * 1000, 2)
            ukur["udp_terima"] = ukur.get("udp_terima", 0) + 1
        return

    if tipe == "detak-klien":
        # presence dari klien lewat UDP → perbarui tanda hidup di sisi server
        with KUNCI:
            for p in PENGGUNA.values():
                if p.ip == alamat[0]:
                    p.terakhir_dengar = time.time()
        return

    if tipe == "pesan-udp":
        # pesan chat lewat jalur UDP (tanpa jaminan sampai)
        teruskan_pesan_udp(pesan, alamat[0])
        return


# ------------------------------------------------------------- mesh antar node
def ganti_nama_tetangga(nama_asli: str, host: str) -> None:
    """Node yang semula tercatat sebagai 'host:port' lalu datang dengan nama
    asli (lewat heartbeat UDP). Ganti namanya supaya dashboard menampilkan
    nama yang benar, bukan alamat IP."""
    with KUNCI:
        for kunci, t in list(TETANGGA.items()):
            if kunci != nama_asli and t.host == host:
                t.nama = nama_asli
                TETANGGA.pop(kunci, None)
                TETANGGA[nama_asli] = t
                break
        # buang entri DIKENAL yang masih pakai nama placeholder
        for kunci in list(DIKENAL):
            if kunci != nama_asli and DIKENAL[kunci].get("host") == host:
                DIKENAL.pop(kunci, None)
        DIKENAL.setdefault(nama_asli, {}).update(
            {"nama": nama_asli, "host": host, "terakhir": time.time(),
             "hidup": True})


def try_sambung_tetangga(nama: str, host: str, tcp: int, udp: int) -> None:
    """Coba sambung ke node lain lewat TCP. Gagal = diamkan, dicoba lagi nanti."""
    if nama == NODE["nama"]:
        return
    with KUNCI:
        if nama in TETANGGA:
            return
        DIKENAL.setdefault(nama, {}).update(
            {"nama": nama, "host": host, "tcp": tcp, "udp": udp,
             "terakhir": time.time()})
    try:
        s = socket.create_connection((host, int(tcp)), timeout=5)
    except OSError:
        return
    t = Tetangga(nama, host, int(tcp), int(udp), s)
    with KUNCI:
        TETANGGA[nama] = t
    # Perkenalan: sebut nama kita. Node lain akan membalas perkenalan
    # sekaligus memberi tahu nama aslinya.
    t.perkenalan = True
    t.kirim({"tipe": "node", "node": NODE["nama"], "tcp": NODE["tcp"],
             "udp": NODE["udp"]})
    # kirim daftar node yang kita tahu supaya tetrahedron terbentuk
    with KUNCI:
        # Daftar node untuk dissemination mesh. Node lokal TIDAK ikut —
        # frontend memakai `dikenal` untuk menggambar node kluster, dan
        # node lokal sudah digambar terpisah sebagai pusat topologi.
        dikenal = [{"nama": n, "host": i.get("host"), "tcp": i.get("tcp"),
                    "udp": i.get("udp")} for n, i in DIKENAL.items() if n != nama]
    t.kirim({"tipe": "gossip", "node": NODE["nama"], "tcp": NODE["tcp"],
             "udp": NODE["udp"], "dikenal": dikenal})
    catat(f"  [mesh] tersambung ke node {nama} ({host}:{tcp})")
    threading.Thread(target=layani_tetangga, args=(t,), daemon=True).start()


def jalankan_mesh(konfigurasi: list[tuple[str, int, int]]) -> None:
    """Thread pengaman: jaga sambungan ke setiap tetangga tetap hidup."""

    def ungu() -> None:
        while True:
            for host, tcp, udp in konfigurasi:
                nama_placeholder = f"{host}:{tcp}"
                with KUNCI:
                    ada = any(t.host == host and t.tcp == tcp
                              for t in TETANGGA.values())
                if not ada:
                    # nama node belum diketahui → pakai nama sementara sampai
                    # heartbeat UDPmemberi tahu nama aslinya.
                    try_sambung_tetangga(nama_placeholder, host, tcp, udp)
            time.sleep(2.0)

    threading.Thread(target=ungu, daemon=True).start()


def layani_tetangga(t: Tetangga) -> None:
    """Baca pesan dari node lain."""
    t.sock.settimeout(1.0)
    penyangga = b""
    try:
        while True:
            try:
                potongan = t.sock.recv(8192)
            except socket.timeout:
                if time.time() - t.terakhir_dengar > 30:
                    break
                continue
            except OSError:
                break
            if not potongan:
                break
            t.terakhir_dengar = time.time()
            penyangga += potongan
            while b"\n" in penyangga:
                baris, penyangga = penyangga.split(b"\n", 1)
                if not baris.strip():
                    continue
                try:
                    data = json.loads(baris.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                tangani_tetangga(t, data)
    finally:
        with KUNCI:
            if TETANGGA.get(t.nama) is t:
                TETANGGA.pop(t.nama, None)
        catat(f"  [mesh] node {t.nama} terputus")
        try:
            t.sock.close()
        except OSError:
            pass


def tangani_tetangga(t: Tetangga, data: dict) -> None:
    tipe = data.get("tipe")

    if tipe == "ping-tcp":
        # Tetangga mengukur latensi TCP ke kita → balas, supaya angkanya terisi
        # di sisi dia. Tanpa ini, "tcp_ms" selalu kosong di dashboard.
        t.kirim({"tipe": "pong-tcp", "t": data.get("t")})
        return

    if tipe == "pong-tcp":
        t0 = data.get("t")
        if isinstance(t0, (int, float)):
            ukur = KURUNG.setdefault(t.nama, {})
            ukur["tcp_ms"] = round((time.time() - t0) * 1000, 2)
            ukur["tcp_terima"] = ukur.get("tcp_terima", 0) + 1
        return

    if tipe == "node":
        # Node lain memperkenalkan diri. Balas HANYA kalau kita belum pernah
        # membalas perkenalan ke node ini — kalau tidak, dua node saling
        # membalas "node" tanpa henti sampai CPU panas.
        with KUNCI:
            DIKENAL.setdefault(t.nama, {}).update(
                {"nama": t.nama, "host": t.host, "tcp": data.get("tcp"),
                 "udp": data.get("udp"), "terakhir": time.time()})
            baru_perkenalan = not t.perkenalan
            t.perkenalan = True
        if baru_perkenalan:
            t.kirim({"tipe": "node", "node": NODE["nama"], "tcp": NODE["tcp"],
                     "udp": NODE["udp"]})
        return

    if tipe == "gossip":
        for info in data.get("dikenal", []):
            nama = info.get("nama")
            if not nama or nama == NODE["nama"]:
                continue
            with KUNCI:
                DIKENAL.setdefault(nama, {}).update(
                    {"nama": nama, "host": info.get("host"),
                     "tcp": info.get("tcp"), "udp": info.get("udp"),
                     "terakhir": time.time()})
            if nama not in TETANGGA:
                try_sambung_tetangga(nama, info.get("host") or "127.0.0.1",
                                     info.get("tcp") or NODE["tcp"],
                                     info.get("udp") or NODE["udp"])
        return

    if tipe == "pesan-terusan":
        # pesan dari node lain → teruskan ke pengguna lokal
        kode = str(data.get("kode") or "")
        if kode and not ingat_kode(kode):
            return
        isi = str(data.get("isi") or "")[:2000]
        tujuan = str(data.get("tujuan") or "semua")[:40]
        pengirim = str(data.get("pengirim") or "?")[:40]
        waktu = str(data.get("waktu") or sekarang())
        protokol = str(data.get("protokol") or "TCP")
        terjamin = 1 if data.get("terjamin", 1) else 0
        simpan("INSERT INTO pesan (kode, pengirim, tujuan, isi, waktu, protokol,"
               " terjamin, node) VALUES (?,?,?,?,?,?,?,?)",
               (kode, pengirim, tujuan, isi, waktu, protokol, terjamin,
                NODE["nama"]))
        keluaran = {"tipe": "pesan", "dari": pengirim, "ke": tujuan,
                    "isi": isi, "waktu": waktu, "protokol": protokol,
                    "terjamin": bool(terjamin)}
        if tujuan == "semua":
            siarkan(keluaran, kecuali=pengirim)
        else:
            sasaran = PENGGUNA.get(tujuan)
            if sasaran:
                sasaran.kirim(keluaran)
        return

    if tipe == "perintah-terusan":
        tujuan = str(data.get("sasaran") or "")[:40]
        aksi = str(data.get("aksi") or "")
        keterangan = str(data.get("keterangan") or "")[:2000]
        id_perintah = data.get("id")
        if id_perintah:
            simpan("""UPDATE perintah SET dijawab=?, berhasil=?, hasil=?
                      WHERE id=?""", (sekarang(), None, "diteruskan ke node lain",
                                      id_perintah))
        if aksi not in PERINTAH_DIIZINKAN:
            return
        if tujuan in PENGGUNA:
            PENGGUNA[tujuan].kirim({"tipe": "perintah", "aksi": aksi,
                                    "keterangan": keterangan, "id": id_perintah})
        return

    if tipe == "hasil-terusan":
        id_perintah = data.get("id_perintah")
        if id_perintah:
            simpan("""UPDATE perintah SET dijawab=?, berhasil=?, hasil=?
                      WHERE id=?""",
                   (sekarang(), 1 if data.get("berhasil") else 0,
                    str(data.get("pesan") or "")[:300], id_perintah))
        return


# -------------------------------------------------------------- pesan antar node
def teruskan_pesan(pengirim: str, tujuan: str, isi: str,
                    protokol: str = "TCP", terjamin: bool = True,
                    kode: str | None = None) -> dict:
    """Buat pesan, simpan, sebarkan ke pengguna lokal DAN node lain."""
    kode = kode or kode_baru()
    waktu = sekarang()
    simpan("INSERT INTO pesan (kode, pengirim, tujuan, isi, waktu, protokol,"
           " terjamin, node) VALUES (?,?,?,?,?,?,?,?)",
           (kode, pengirim, tujuan, isi, waktu, protokol,
            1 if terjamin else 0, NODE["nama"]))

    keluaran = {"tipe": "pesan", "dari": pengirim, "ke": tujuan, "isi": isi,
                "waktu": waktu, "protokol": protokol, "terjamin": terjamin,
                "kode": kode}
    tentatively_lokal = 0
    if tujuan == "semua":
        tentatively_lokal = siarkan(keluaran, kecuali=pengirim)
    else:
        sasaran = PENGGUNA.get(tujuan)
        if sasaran and sasaran.kirim(keluaran):
            tentatively_lokal = 1

    # teruskan ke node lain lewat mesh TCP
    ke_node = siarkan_tetangga({
        "tipe": "pesan-terusan", "kode": kode, "pengirim": pengirim,
        "tujuan": tujuan, "isi": isi, "waktu": waktu, "protokol": protokol,
        "terjamin": 1 if terjamin else 0})

    return {"lokal": tentatively_lokal, "node": ke_node, "kode": kode}


def teruskan_pesan_udp(pesan: dict, ip_pengirim: str) -> None:
    """Pesan chat yang datang lewat UDP dari klien."""
    pengirim = str(pesan.get("dari") or "?")[:40]
    isi = str(pesan.get("isi") or "")[:1000]
    if not isi.strip():
        return
    # hanya client's yang sudah terdaftar boleh memakai jalur UDP
    with KUNCI:
        dikenal = [p for p in PENGGUNA.values() if p.ip == ip_pengirim]
    if not dikenal:
        return
    pengirim = dikenal[0].nama

    kode = kode_baru()
    waktu = sekarang()
    simpan("INSERT INTO pesan (kode, pengirim, tujuan, isi, waktu, protokol,"
           " terjamin, node) VALUES (?,?,?,?,?,?,?,?)",
           (kode, pengirim, "semua", isi, waktu, "UDP", 0, NODE["nama"]))

    keluaran = {"tipe": "pesan", "dari": pengirim, "ke": "semua", "isi": isi,
                "waktu": waktu, "protokol": "UDP", "terjamin": False,
                "kode": kode}
    siarkan(keluaran, kecuali=pengirim)
    # ke node lain juga lewat UDP (jalur cepat, tanpa jaminan)
    siarkan_udp({"tipe": "pesan-udp-terusan", "kode": kode, "pengirim": pengirim,
                 "isi": isi, "waktu": waktu}, kecuali=NODE["nama"])
    catat(f"  [pesan/UDP] {pengirim} → semua: {isi[:50]}")


# --------------------------------------------------------------- dari pengguna
def terima_pertama(koneksi: socket.socket) -> tuple[bytes, dict | None]:
    """Baca satu baris pertama. menentukan: node lain atau pengguna biasa?"""
    buf = b""
    koneksi.settimeout(15.0)
    try:
        while b"\n" not in buf:
            potongan = koneksi.recv(4096)
            if not potongan:
                return buf, None
            buf += potongan
            if len(buf) > 64_000:
                return buf, None
    except OSError:
        return buf, None
    finally:
        try:
            koneksi.settimeout(1.0)
        except OSError:
            pass
    baris, _, sisa = buf.partition(b"\n")
    try:
        return sisa, json.loads(baris.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return sisa, None


def layani(p: Pengguna, sisa: bytes = b"") -> None:
    """Satu thread untuk satu pengguna, sampai sambungannya putus."""
    penyangga = sisa
    try:
        while True:
            try:
                potongan = p.koneksi.recv(8192)
            except socket.timeout:
                if not p.masih_hidup():
                    catat(f"  [!] {p.nama} tidak menjawab {int(BATAS_DIAM)} detik "
                          f"— dianggap putus")
                    break
                continue
            except OSError:
                break

            if not potongan:
                break                      # sambungan ditutup pengguna

            p.terakhir_dengar = time.time()
            penyangga += potongan

            # Satu baris = satu objek JSON.
            while b"\n" in penyangga:
                baris, penyangga = penyangga.split(b"\n", 1)
                if not baris.strip():
                    continue
                if len(baris) > 64_000:
                    p.kirim({"tipe": "galat", "pesan": "baris terlalu panjang"})
                    continue
                try:
                    data = json.loads(baris.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    p.kirim({"tipe": "galat", "pesan": "data tidak bisa dibaca"})
                    continue
                try:
                    tangani(p, data)
                except Exception as e:      # noqa: BLE001 — server jangan mati
                    catat(f"  [ERROR] tangani() gagal untuk {p.nama}: {e}")
    except OSError:
        pass
    finally:
        lepaskan(p)


def lepaskan(p: Pengguna) -> None:
    """Bersihkan setelah pengguna putus."""
    with KUNCI:
        if PENGGUNA.get(p.nama) is p:
            PENGGUNA.pop(p.nama, None)
    simpan("INSERT INTO kejadian (nama, jenis, waktu) VALUES (?,?,?)",
           (p.nama, "putus", sekarang()))
    catat(f"  [putus] {p.nama}  ({p.ip}) — tersambung {p.lama()}")
    kabari_daftar()
    try:
        p.koneksi.close()
    except OSError:
        pass


def tangani(p: Pengguna, data: dict) -> None:
    tipe = data.get("tipe")

    # --- mendaftar ------------------------------------------------------
    if tipe == "daftar":
        nama = str(data.get("nama") or "").strip()[:40] or p.nama
        p.sistem = str(data.get("sistem") or "?")[:40]

        with KUNCI:
            sudah_ada = nama in PENGGUNA and PENGGUNA[nama] is not p
        if sudah_ada:
            p.kirim({"tipe": "galat",
                     "pesan": f"nama '{nama}' sudah dipakai laptop lain. "
                              f"Pilih nama lain."})
            catat(f"  [!] {p.ip} memakai nama '{nama}' yang sudah dipakai")
            try:
                p.koneksi.shutdown(socket.SHUT_RDWR)
                p.koneksi.close()
            except OSError:
                pass
            return

        p.nama = nama
        p.nama_tetap = True
        with KUNCI:
            PENGGUNA[nama] = p
        simpan("""INSERT INTO laptop (nama, sistem, ip, node, terakhir)
                  VALUES (?,?,?,?,?)
                  ON CONFLICT(nama) DO UPDATE SET
                    sistem=excluded.sistem, ip=excluded.ip,
                    node=excluded.node, terakhir=excluded.terakhir""",
               (nama, p.sistem, p.ip, NODE["nama"], sekarang()))
        simpan("INSERT INTO kejadian (nama, jenis, waktu) VALUES (?,?,?)",
               (nama, "masuk", sekarang()))
        catat(f"  [masuk] {nama}  ({p.sistem}, {p.ip}) → node {NODE['nama']}")

        p.kirim({"tipe": "selamat", "nama_kamu": nama,
                 "node": NODE["nama"], "tcp": NODE["tcp"], "udp": NODE["udp"],
                 "daftar_node": daftar_node_untuk_pengguna(),
                 "online": [x["nama"] for x in daftar_pengguna()]})
        kabari_daftar()
        return

    # --- pesan ----------------------------------------------------------
    if tipe == "pesan":
        if not p.nama_tetap:
            p.kirim({"tipe": "galat", "pesan": "belum mendaftar"})
            return
        tujuan = str(data.get("ke") or "semua")[:40]
        isi = str(data.get("isi") or "")[:2000]
        if not isi.strip():
            p.kirim({"tipe": "galat", "pesan": "pesan kosong"})
            return

        hasil = teruskan_pesan(p.nama, tujuan, isi, "TCP", True)
        if tujuan == "semua":
            p.kirim({"tipe": "info",
                     "pesan": f"terkirim ke {hasil['lokal']} laptop "
                              f"(+{hasil['node']} node)"})
            catat(f"  [pesan] {p.nama} → semua ({hasil['lokal']}): {isi[:50]}")
        else:
            if hasil["lokal"]:
                p.kirim({"tipe": "info", "pesan": f"terkirim ke {tujuan}"})
                catat(f"  [pesan] {p.nama} → {tujuan}: {isi[:50]}")
            else:
                p.kirim({"tipe": "galat",
                         "pesan": f"laptop '{tujuan}' tidak sedang tersambung"})
                catat(f"  [!] {p.nama} gagal kirim ke {tujuan}")
        return

    # --- laporan hasil perintah -----------------------------------------
    if tipe == "hasil":
        aksi = str(data.get("aksi") or "")
        berhasil = 1 if data.get("berhasil") else 0
        pesan = str(data.get("pesan") or "")[:300]
        id_perintah = data.get("id_perintah")

        if id_perintah:
            simpan("""UPDATE perintah SET dijawab=?, berhasil=?, hasil=?
                      WHERE id=?""", (sekarang(), berhasil, pesan, id_perintah))
        tanda = "✓" if berhasil else "✗"
        catat(f"  [hasil] {tanda} {p.nama}: {pesan}")
        # teruskan hasilnya ke node lain supaya dashboard node pengirim ikut tahu
        if id_perintah:
            siarkan_tetangga({"tipe": "hasil-terusan", "id_perintah": id_perintah,
                              "aksi": aksi, "berhasil": bool(berhasil),
                              "pesan": pesan}, kecuali=NODE["nama"])
        return

    if tipe == "pong":
        # PENTING: pong harus memperbarui tanda hidup. Dulu di sini hanya
        # ada `return`, jadi laptop yang rajin menjawab ping TETAP
        # dianggap mati setelah BATAS_DIAM (90 detik) — dashboard
        # menunjukkan laptop "putus" padahal masih tersambung dan aktif
        # mengirim. Ini bug yang membuat demo 6 laptop berantakan.
        p.terakhir_dengar = time.time()
        return

    if tipe == "ping":
        p.kirim({"tipe": "pong"})
        return

    p.kirim({"tipe": "galat", "pesan": f"tipe tidak dikenal: {tipe}"})


# --------------------------------------------------------------- dari dashboard
def kirim_pesan(tujuan: str, isi: str) -> dict:
    """Kirim pesan CHAT dari dashboard (jalur TCP, dijamin sampai)."""
    isi = isi[:2000]
    if not isi.strip():
        return {"ok": False, "pesan": "pesannya masih kosong"}
    tujuan = tujuan[:40] or "semua"

    if tujuan != "semua" and tujuan not in PENGGUNA:
        # mungkin tuplesnya ada di node lain → teruskan ke mesh
        if siarkan_tetangga({"tipe": "pesan-terusan", "kode": kode_baru(),
                             "pengirim": "SERVER", "tujuan": tujuan, "isi": isi,
                             "waktu": sekarang(), "protokol": "TCP",
                             "terjamin": 1}):
            return {"ok": True, "terkirim": 0, "node": 1}
        return {"ok": False, "pesan": f"laptop '{tujuan}' tidak sedang tersambung"}

    hasil = teruskan_pesan("SERVER", tujuan, isi, "TCP", True)
    catat(f"  [pesan] SERVER → {tujuan} ({hasil['lokal']}): {isi[:50]}")
    return {"ok": True, "terkirim": hasil["lokal"], "node": hasil["node"]}


def kirim_pesan_udp(tujuan: str, isi: str) -> dict:
    """Kirim pesan dari dashboard lewat jalur UDP (tanpa jaminan sampai)."""
    isi = isi[:1000]
    if not isi.strip():
        return {"ok": False, "pesan": "pesannya masih kosong"}
    hasil = teruskan_pesan("SERVER", tujuan[:40] or "semua", isi, "UDP", False)
    return {"ok": True, "terkirim": hasil["lokal"], "node": hasil["node"],
            "catatan": "jalur UDP — tidak dijamin sampai"}


def kirim_perintah(sasaran: str, aksi: str, keterangan: str = "") -> dict:
    if aksi not in PERINTAH_DIIZINKAN:
        return {"ok": False, "pesan": f"aksi '{aksi}' tidak diizinkan"}

    if sasaran == "semua":
        with KUNCI:
            nama_sasaran = list(PENGGUNA.keys())
    else:
        nama_sasaran = [sasaran]

    if not nama_sasaran:
        return {"ok": False, "pesan": "tidak ada laptop yang tersambung"}

    terkirim, gagal = [], []
    for nama in nama_sasaran:
        p = PENGGUNA.get(nama)
        if not p:
            gagal.append(nama)
            continue
        id_perintah = simpan(
            """INSERT INTO perintah (sasaran, aksi, keterangan, dikirim)
               VALUES (?,?,?,?)""", (nama, aksi, keterangan[:2000], sekarang()))
        if p.kirim({"tipe": "perintah", "aksi": aksi,
                    "keterangan": keterangan, "id": id_perintah}):
            terkirim.append(nama)
        else:
            gagal.append(nama)
        catat(f"  [perintah] {aksi} → {nama}")

    return {"ok": bool(terkirim), "terkirim": terkirim, "gagal": gagal}


# ------------------------------------------------------------------ dashboard
class Dashboard(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(DIR_STATIS), **k)

    # MIME eksplisit. Tanpa ini, .glb terkirim sebagai application/octet-stream
    # dan beberapa browser menolak memuatnya lewat GLTFLoader.
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".wasm": "application/wasm",
        ".json": "application/json; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }

    def end_headers(self):
        # Aset 3D boleh di-cache lama (nama file statis, isi jarang berubah)
        # supaya tidak diunduh ulang tiap refresh.
        #
        # PENTING: hanya file yang BENAR-BENAR ada yang di-cache. Kalau
        # 404 ikut di-cache (max-age panjang), browser akan tetap meminta
        # file yang sama dari cache walau file-nya sudah dikirim kemudian —
        # hasilnya aset tidak pernah muncul despite sudah ada di disk.
        if self.path.startswith(("/asset/", "/vendor/")):
            ada = (DIR_STATIS / self.path.split("?")[0].lstrip("/")).is_file()
            self.send_header("Cache-Control",
                             "public, max-age=86400" if ada else "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass

    def _json(self, objek):
        isi = json.dumps(objek, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(isi)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        if self.path.startswith("/api/keadaan"):
            with KUNCI, sqlite3.connect(DB, timeout=10) as db:
                db.row_factory = sqlite3.Row
                pesan = [dict(r) for r in db.execute(
                    "SELECT * FROM pesan ORDER BY id DESC LIMIT 40")]
                perintah = [dict(r) for r in db.execute(
                    "SELECT * FROM perintah ORDER BY id DESC LIMIT 20")]
                kejadian = [dict(r) for r in db.execute(
                    "SELECT * FROM kejadian ORDER BY id DESC LIMIT 25")]
                ukur = [dict(r) for r in db.execute(
                    "SELECT * FROM ukur ORDER BY id DESC LIMIT 40")]
                jumlah = {
                    "pesan": db.execute("SELECT COUNT(*) FROM pesan").fetchone()[0],
                    "perintah": db.execute("SELECT COUNT(*) FROM perintah").fetchone()[0],
                }
            with KUNCI:
                tetangga = [t.keterangan() for t in TETANGGA.values()]
                # PENTING: `dikenal` TIDAK boleh memuat node lokal —
                # frontend memakainya untuk MENGGAMBAR node kluster, jadi
                # node lokal akan ter gambar dua kali.
                dikenal = [dict(i) for i in DIKENAL.values()]
            self._json({"node": dict(NODE), "tetangga": tetangga,
                        "dikenal": dikenal, "online": daftar_pengguna(),
                        "pesan": pesan, "perintah": perintah,
                        "kejadian": kejadian, "ukur": ukur,
                        "jumlah": jumlah})
            return

        if self.path.startswith("/api/pesan-udp"):
            q = parse_qs(urlparse(self.path).query)
            self._json(kirim_pesan_udp(q.get("tujuan", ["semua"])[0],
                                       q.get("isi", [""])[0]))
            return

        if self.path.startswith("/api/pesan"):
            q = parse_qs(urlparse(self.path).query)
            self._json(kirim_pesan(q.get("tujuan", ["semua"])[0],
                                   q.get("isi", [""])[0]))
            return

        if self.path.startswith("/api/perintah"):
            q = parse_qs(urlparse(self.path).query)
            self._json(kirim_perintah(q.get("sasaran", ["semua"])[0],
                                      q.get("aksi", [""])[0],
                                      q.get("keterangan", [""])[0]))
            return

        if self.path == "/":
            self.path = "/index.html"
        return super().do_GET()


# ---------------------------------------------------------------------- utama
def alamat_lokal() -> list[str]:
    hasil = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        hasil.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return hasil or ["<ip-mesin-ini>"]


def pencetak() -> None:
    """Satu-satunya thread yang menulis ke layar."""
    while True:
        baris = CETAK.get()
        if baris is None:
            return
        print(baris, flush=True)


def urai_tetangga(teks: str) -> list[tuple[str, int, int]]:
    """'host:tcp[:udp],host2:tcp2' → daftar (host, tcp, udp)."""
    hasil = []
    for bagian in (teks or "").split(","):
        bagian = bagian.strip()
        if not bagian:
            continue
        potong = bagian.split(":")
        host = potong[0]
        tcp = int(potong[1]) if len(potong) > 1 and potong[1].isdigit() else NODE["tcp"]
        udp = int(potong[2]) if len(potong) > 2 and potong[2].isdigit() else tcp + 10
        hasil.append((host, tcp, udp))
    return hasil


def utama() -> int:
    p = argparse.ArgumentParser(description="Server kluster — MK Pemrograman Jaringan Kluster")
    p.add_argument("--node", default="n1", help="nama node (bawaan n1)")
    p.add_argument("--port", type=int, default=5000, help="port TCP pengguna (bawaan 5000)")
    p.add_argument("--web", type=int, default=8081, help="port dashboard (bawaan 8081)")
    p.add_argument("--udp", type=int, default=5010, help="port UDP heartbeat (bawaan 5010)")
    p.add_argument("--tetangga", default="",
                   help="daftar node lain: host:tcp[:udp],host2:tcp2")
    p.add_argument("--db", help="lokasi basis data (bawaan data/kluster.db)")
    a = p.parse_args()

    global DB
    NODE.update({"nama": a.node, "tcp": a.port, "udp": a.udp})
    if a.db:
        DB = Path(a.db)
    else:
        DB = AKAR / "data" / f"kluster_{a.node}.db"

    siapkan_db()
    threading.Thread(target=pencetak, daemon=True).start()
    threading.Thread(target=jalankan_udp, daemon=True).start()
    threading.Thread(target=jalankan_mesh, args=(urai_tetangga(a.tetangga),),
                     daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", a.port))
    srv.listen(30)

    web = HTTPServer(("0.0.0.0", a.web), Dashboard)
    threading.Thread(target=web.serve_forever, daemon=True).start()

    print("=" * 64)
    print("  SERVER KLUster — MK PEMROGRAMAN JARINGAN KLUSTER")
    print("=" * 64)
    print(f"  Node        : {NODE['nama']}")
    print(f"  Port TCP    : {a.port}   (untuk pengguna)")
    print(f"  Port UDP    : {a.udp}   (heartbeat + pengukuran)")
    print(f"  Dashboard   : http://localhost:{a.web}")
    print(f"  Basis data  : {DB}")
    if a.tetangga:
        print(f"  Tetangga    : {a.tetangga}")
    print()
    print("  Alamat untuk laptop pengguna:")
    for ip in alamat_lokal():
        print(f"      {ip}:{a.port}")
    print()
    print("  Tekan Ctrl+C untuk berhenti.")
    print("=" * 64, flush=True)

    try:
        while True:
            koneksi, alamat = srv.accept()
            sisa, data = terima_pertama(koneksi)
            if data and data.get("tipe") == "node":
                nama = str(data.get("node") or f"node-{alamat[1]}")[:40]
                t = Tetangga(nama, alamat[0], int(data.get("tcp") or a.port),
                             int(data.get("udp") or a.udp), koneksi)
                with KUNCI:
                    TETANGGA[nama] = t
                    DIKENAL.setdefault(nama, {}).update(
                        {"nama": nama, "host": alamat[0], "tcp": t.tcp,
                         "udp": t.udp, "terakhir": time.time()})
                threading.Thread(target=layani_tetangga, args=(t,), daemon=True).start()
                continue

            pengguna = Pengguna(koneksi, alamat)
            threading.Thread(target=layani, args=(pengguna, sisa),
                             daemon=True).start()
            if data:
                try:
                    tangani(pengguna, data)
                except Exception as e:      # noqa: BLE001
                    catat(f"  [ERROR] daftar awal gagal: {e}")
    except KeyboardInterrupt:
        print("\n  Server dihentikan.")
    finally:
        with KUNCI:
            for pengguna in PENGGUNA.values():
                pengguna.kirim({"tipe": "info", "pesan": "server berhenti"})
                try:
                    pengguna.koneksi.close()
                except OSError:
                    pass
        srv.close()
        web.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(utama())
