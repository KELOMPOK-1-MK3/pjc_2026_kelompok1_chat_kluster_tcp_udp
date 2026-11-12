#!/usr/bin/env python3
"""PENGGUNA — MK Pemrograman Jaringan Kluster (MKKL1028)

Program ini jalan di laptop tiap anggota. Tugasnya:

  1. Menyambung ke node server lewat TCP (dijamin sampai).
  2. Membuka juga jalur UDP untuk pesan cepat (tidak dijamin sampai) dan
     presence (detak kalau-kalive).
  3. Menerima dan menampilkan pesan yang masuk.
  4. Mengirim pesan ke semua orang atau ke satu orang saja.
  5. Mengirim pesan lewat UDP kalau pengirim menekan tombol UDP.

Jalankan:
    python3 client.py --server 192.168.1.7

Nama laptop diambil dari berkas `nama.txt` di folder ini. Kalau berkas itu
belum ada, program akan menanyakannya sekali dan menyimpannya.

Hanya mendengarkan, tidak perlu mengetik apa pun (bagus untuk demo):
    python3 client.py --server 192.168.1.7 --tunggu

PERINTAHAN DAYA (matikan / restart / kunci):
  macOS   : DITOLAK secara bawaan, supaya Mac yang dipakai jadi server atau
            untuk presentasi tidak pernah ikut mati/kunci. Kalau memang mau,
            tambahkan --izinkan-daya.
  Windows : jalankan terminal sebagai ADMIN (Run as Administrator).
"""
from __future__ import annotations

import sys


def _amankan_layar() -> None:
    """Cegah program berhenti mendadak di Windows saat menampilkan simbol.

    Kenapa ini ada:
        Terminal Windows (cmd.exe) memakai pengodean lama (cp1252/cp437) yang
        TIDAK mengenal karakter seperti ⚡ ✓ ✗ 💬 → dan garis kotak ┌ │ └ ─.
        Tanpa pengaman ini, print("  ⚡ PERINTAH...") langsung berhenti dengan
        UnicodeEncodeError — dan itu justru terjadi di laptop anggota, bukan
        di Mac tempat programnya dibuat.

        Kalau karakter tidak bisa ditampilkan, ia diganti tanda tanya. Program
        tetap jalan; yang hilang cuma hiasannya.
    """
    for aliran in (sys.stdout, sys.stderr):
        try:
            aliran.reconfigure(errors="replace")
        except Exception:
            pass          # Python lama atau aliran tidak bisa diubah — tidak fatal


_amankan_layar()

import argparse
import json
import pathlib
import platform
import queue
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

# Perintah yang mengubah keadaan daya laptop.
PERINTAH_DAYA = {"matikan", "restart", "kunci"}

IZINKAN_DAYA_DI_MAC = False     # diisi dari --izinkan-daya

# Berkas tempat nama laptop ini disimpan. Namanya sengaja TIDAK di-hardcode di
# dalam program, supaya berkas yang sama bisa dipakai di laptop anggota mana
# pun — setiap orang tinggal mengubah isi nama.txt di laptopnya sendiri.
BERKAS_NAMA = pathlib.Path(__file__).resolve().parent / "nama.txt"

# Berkas berisi alamat server. Anggota tidak perlu mengetik IP di terminal —
# cukup ubah isi berkas ini kalau alamat server berubah.
BERKAS_SERVER = pathlib.Path(__file__).resolve().parent / "server.txt"

PORT_BAWAAN = 5000
PORT_UDP_BAWAAN = 5010

DETAK_TIEP = 5.0        # presence UDP tiap 5 detik
ANTRIAN_UDP: "queue.Queue[dict]" = queue.Queue()


def alamat_sah(host: str) -> bool:
    """Alamat bisa dipakai? Angka IP, nama mesin lokal, atau nama yang bisa dicari."""
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
        return True
    except OSError:
        return False


def baca_alamat(dari_argumen: str | None, port_argumen: int | None) -> tuple[str, int, str]:
    """Tentukan alamat server.

    Mengembalikan (host, port, sumber). Sumber ditampilkan di layar supaya
    jelas alamatnya datang dari mana kalau ternyata salah.

    Urutan: --server di terminal, lalu server.txt, lalu tanya sekali.
    """
    if dari_argumen:
        return dari_argumen.strip(), (port_argumen or PORT_BAWAAN), "perintah --server"

    # server.txt isinya boleh "192.168.1.7" saja, atau "192.168.1.7:5000".
    if BERKAS_SERVER.exists():
        isi = ""
        for baris in BERKAS_SERVER.read_text(encoding="utf-8").splitlines():
            baris = baris.strip()
            if baris and not baris.startswith("#"):
                isi = baris
                break
        if isi:
            host, _, port_teks = isi.partition(":")
            host = host.strip()
            try:
                port = int(port_teks.strip()) if port_teks.strip() else PORT_BAWAAN
            except ValueError:
                port = PORT_BAWAAN
            if host and alamat_sah(host):
                return host, (port_argumen or port), BERKAS_SERVER.name.split("/")[-1]
            if host:
                print(f"  [!] Alamat '{host}' di server.txt tidak dikenal — cek lagi.")

    # Belum ada server.txt — tanyakan sekali, lalu simpan supaya tidak diulang.
    print()
    print("  Alamat laptop server belum diatur.")
    print("  Ini alamat yang DICETAK server saat dijalankan, misalnya 192.168.1.7")
    print("  (kalau server ada di laptop ini juga, isi: 127.0.0.1)")
    print()
    try:
        jawab = input("  Alamat server: ").strip()
    except (EOFError, KeyboardInterrupt):
        jawab = ""
    if not jawab:
        jawab = "127.0.0.1"
    host, _, port_teks = jawab.partition(":")
    host = host.strip() or "127.0.0.1"
    while not alamat_sah(host):
        print(f"  [!] '{host}' tidak dikenal sebagai alamat. Tulis angka IP, mis. 192.168.1.7")
        try:
            jawab = input("  Alamat server: ").strip()
        except (EOFError, KeyboardInterrupt):
            jawab = ""
        if not jawab:
            jawab = "127.0.0.1"
        host, _, port_teks = jawab.partition(":")
        host = host.strip() or "127.0.0.1"
    try:
        port = int(port_teks.strip()) if port_teks.strip() else PORT_BAWAAN
    except ValueError:
        port = PORT_BAWAAN
    # Jangan menyimpan kalau tidak ada jawaban yang dipakai — supaya
    # 127.0.0.1 tidak ikut tersimpan dan dipakai di laptop anggota.
    if jawab:
        try:
            BERKAS_SERVER.write_text(f"{host}:{port}\n", encoding="utf-8")
            sumber = f"{BERKAS_SERVER.name} (baru disimpan)"
        except Exception as e:
            sumber = f"jawaban langsung (gagal menyimpan: {e})"
    else:
        sumber = "default (tidak disimpan)"
    return host, (port_argumen or port), sumber


def baca_nama(dari_argumen: str | None) -> tuple[str, str]:
    """Tentukan nama laptop ini.

    Mengembalikan (nama, sumber) — sumber dipakai untuk ditampilkan di layar,
    supaya jelas nama itu datang dari mana kalau ternyata bukan yang diharapkan.

    Urutan: dari argumen --nama, lalu dari nama.txt, lalu tanya sekali.
    """
    if dari_argumen:
        return dari_argumen.strip()[:40], "argumen --nama"

    if BERKAS_NAMA.exists():
        isi = BERKAS_NAMA.read_text(encoding="utf-8").strip()
        if isi:
            return isi[:40], BERKAS_NAMA.name

    # Belum ada nama.txt — tanyakan sekali, lalu simpan.
    bawaan = platform.node() or "laptop-tanpa-nama"
    print()
    print("  Laptop ini belum punya nama.")
    print(f"  Kalau dibiarkan kosong, namanya jadi: {bawaan}")
    print()
    print("  Contoh penulisan yang rapi:")
    print("      PC01 (Yogi)")
    print("      PC04 (Deski)")
    print()
    print("  Nama ini akan disimpan di nama.txt, jadi tidak perlu diisi lagi.")
    print()
    try:
        jawab = input("  Nama laptop ini: ").strip()
    except (EOFError, KeyboardInterrupt):
        jawab = ""

    nama = (jawab or bawaan)[:40]
    # Kalau tidak ada yang diketik (mis. dijalankan tanpa terminal), jangan
    # TULIS nama mesin ke nama.txt. Kalau ditulis, semua laptop akan memakai
    # nama yang sama, lalu server menolaknya sebagai nama kembar — dan
    # anggota mengira programnya yang rusak.
    if not jawab:
        print("  [!] Nama tidak diisi — memakai nama mesin untuk sesi ini saja.")
        print("      (nama.txt TIDAK diubah. Isi nama.txt supaya nama ini tetap.)")
        return nama, "nama mesin (sesi ini saja)"
    try:
        BERKAS_NAMA.write_text(nama + "\n", encoding="utf-8")
        return nama, f"{BERKAS_NAMA.name} (baru disimpan)"
    except OSError as e:
        print(f"  [!] Tidak bisa menyimpan nama: {e}")
        return nama, "nama mesin"


# --------------------------------------------------------- menjalankan perintah
def jalankan(aksi: str, keterangan: str = "") -> tuple[bool, str]:
    """Jalankan perintah di laptop ini.

    Mengembalikan (berhasil, pesan) — pesan inilah yang dikirim balik ke
    server untuk ditampilkan di dashboard.
    """
    sistem = platform.system()

    # --- pengaman macOS -------------------------------------------------
    if sistem == "Darwin" and aksi in PERINTAH_DAYA and not IZINKAN_DAYA_DI_MAC:
        return False, (f"ditolak di macOS (mode aman). Mac ini dipakai sebagai "
                       f"server/untuk presentasi.")

    if aksi not in {"pesan", "kunci", "restart", "matikan", "batal"}:
        return False, f"perintah '{aksi}' tidak dikenal"

    if aksi == "pesan":
        print()
        print("  ┌" + "─" * 60)
        for baris in (keterangan or "(kosong)").split("\n"):
            print(f"  │ {baris}")
        print("  └" + "─" * 60)
        print("  > ", end="", flush=True)
        return True, "pesan ditampilkan"

    if aksi == "matikan":
        if sistem == "Windows":
            subprocess.Popen("shutdown /s /t 30", shell=True)
            return True, "laptop akan mati dalam 30 detik (bisa dibatalkan)"
        if sistem == "Darwin":
            subprocess.Popen(["osascript", "-e",
                              'tell app "System Events" to shut down'])
            return True, "laptop akan mati"
        subprocess.Popen("shutdown -h +1", shell=True)
        return True, "laptop akan mati dalam 1 menit"

    if aksi == "restart":
        if sistem == "Windows":
            subprocess.Popen("shutdown /r /t 30", shell=True)
            return True, "laptop akan restart dalam 30 detik"
        if sistem == "Darwin":
            subprocess.Popen(["osascript", "-e",
                              'tell app "System Events" to restart'])
            return True, "laptop akan restart"
        subprocess.Popen("shutdown -r +1", shell=True)
        return True, "laptop akan restart dalam 1 menit"

    if aksi == "batal":
        if sistem == "Windows":
            hasil = subprocess.run("shutdown /a", shell=True,
                                   capture_output=True, text=True)
            if hasil.returncode == 0:
                return True, "perintah mati/restart dibatalkan"
            return False, "tidak ada perintah mati/restart yang sedang menunggu"
        return False, f"pembatalan otomatis belum didukung di {sistem}"

    if aksi == "kunci":
        if sistem == "Windows":
            subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
            return True, "layar dikunci"
        if sistem == "Darwin":
            subprocess.Popen([
                "osascript", "-e",
                'tell application "System Events" '
                'to keystroke "q" using {control down, command down}'])
            return True, "layar dikunci"
        return False, f"perintah kunci belum didukung di {sistem}"

    return False, "perintah tidak dikenal"


# ------------------------------------------------------------------- pengguna
class Pengguna:
    def __init__(self, host: str, port: int, nama: str, port_udp: int) -> None:
        self.host = host
        self.port = port
        self.port_udp = port_udp
        self.nama = nama
        self.sistem = f"{platform.system()} {platform.release()}"
        self.sock: socket.socket | None = None
        self.sock_udp: socket.socket | None = None
        self.jalan = True
        self.online: list[str] = []
        self.node = "-"
        self.tampil = queue.Queue()      # baris yang perlu ditampilkan
        self._kunci = threading.Lock()
        # Riwayat pesan untuk jembatan web (mode --web). Diisi juga saat
        # mode terminal supaya satu jalur kode dipakai keduanya — tidak ada
        # perilaku berbeda antara UI terminal dan UI web.
        self.riwayat: list = []
        self._nomor = 0
        self.sambung_terputus = False
        self._riwayat_terakhir = None

    def catat_riwayat(self, dari: str, ke: str, isi: str,
                      waktu: str, protokol: str) -> None:
        """Simpan satu pesan untuk dibaca jembatan web.

        Dedup: pesan yang sama persis (pengirim + isi) dalam 5 detik
        terakhir tidak dicatat dua kali. Ini perlu karena pesan UDP
        dipantulkan server kembali ke pengirim — tanpa penjagaan ini,
        pesan sendiri muncul dua kali di layar.
        """
        # Dedup berdasarkan ISI + protokol saja, bukan nama pengirim.
        #
        # Alasannya: untuk pesan UDP, server menetapkan pengirim dari IP
        # pengirim, bukan dari field `dari` yang dikirim klien. Akibatnya
        # pantulan pesan kita sendiri bisa berlabel nama LAIN (misalnya
        # "PC02" saat kita "PC01 (Yogi)") — dan kalau nama ikut dibandingkan,
        # dedup gagal dan pesan tampil dua kali.
        sekarang = time.time()
        kunci = (isi, protokol)
        if self._riwayat_terakhir and self._riwayat_terakhir[0] == kunci \
                and sekarang - self._riwayat_terakhir[1] < 4:
            return
        self._riwayat_terakhir = (kunci, sekarang)
        self._nomor += 1
        self.riwayat.append(SimpleNamespace(
            nomor=self._nomor, dari=dari, ke=ke, isi=isi,
            waktu=waktu, protokol=protokol))
        # Jaga memori: dashboard tidak perlu ribuan pesan lama, dan demo
        # panjang (kiriman tiap detik) bisa menghabiskan RAM kalau
        # dibiarkan tumbuh tanpa batas.
        if len(self.riwayat) > 500:
            del self.riwayat[:200]

    # --- sambungan -------------------------------------------------------
    def sambung(self) -> bool:
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=8)
        except OSError as e:
            print()
            print(f"  ✗ Gagal menyambung ke {self.host}:{self.port}")
            print(f"    Sebab: {e}")
            print()
            print("    Periksa tiga hal ini:")
            print("      1. Server sudah dijalankan di laptop ketua?")
            print("      2. Alamat IP-nya sudah benar? (lihat yang dicetak server)")
            print("      3. Kedua laptop ada di jaringan WiFi yang sama?")
            return False

        # PENTING: batas waktu 8 detik di atas hanya untuk proses menyambung.
        # Kalau dibiarkan, socket akan terus menganggap "diam 8 detik" sebagai
        # kesalahan, dan aplikasi chat MEMANG sering diam lama. Akibatnya
        # program memutus dirinya sendiri padahal tidak ada masalah — jadi
        # batas waktunya dihapus setelah sambungan berhasil.
        self.sock.settimeout(None)

        self.kirim({"tipe": "daftar", "nama": self.nama, "sistem": self.sistem})
        self.sambung_udp()
        return True

    def sambung_udp(self) -> bool:
        """Buka jalur UDP ke node server untuk presence + pesan cepat."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.0)
            s.connect((self.host, self.port_udp))
            self.sock_udp = s
        except OSError:
            self.sock_udp = None
            return False
        threading.Thread(target=loop_udp, args=(self,), daemon=True).start()
        return True

    def jaga_sambungan(self) -> None:
        """Coba sambung lagi selama program hidup. Thread terpisah.

        Tanpa ini, mode --web mati permanen begitu server sempat putus
        (misalnya server di-restart saat demo), dan satu-satunya jalan
        keluar adalah menutup jendela terminal lalu menjalankan ulang.
        """
        while self.jalan:
            time.sleep(3)
            if not self.jalan:
                break
            if not self.sambung_terputus:
                continue
            try:
                self.sock = socket.create_connection(
                    (self.host, self.port), timeout=6)
            except OSError:
                continue                      # server belum siap, coba lagi
            self.sock.settimeout(None)
            self.kirim({"tipe": "daftar", "nama": self.nama,
                        "sistem": self.sistem})
            self.sambung_terputus = False
            self.tampil.put("  ✓ tersambung lagi.")
            self.tampil.put("")
            threading.Thread(target=self.dengarkan, daemon=True).start()

    def kirim(self, data: dict) -> bool:
        if not self.sock:
            return False
        baris = json.dumps(data, ensure_ascii=False) + "\n"
        with self._kunci:
            try:
                self.sock.sendall(baris.encode("utf-8"))
                return True
            except OSError:
                self.jalan = False
                return False

    def kirim_udp(self, data: dict) -> bool:
        """Kirim lewat UDP. Tidak dijamin sampai — itulah bedanya dengan TCP."""
        if not self.sock_udp:
            return False
        try:
            self.sock_udp.send(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return True
        except OSError:
            return False

    def putus(self, sebab: str) -> None:
        """Tandai sambungan putus TANPA mematikan program.

        PENTING: dulu kegagalan recv langsung menyetel self.jalan = False.
        Di mode terminal itu wajar (program memang berakhir), tapi di mode
        --web akibatnya fatal: jembatan HTTP ikut mati, browser kehilangan
        sambungan ke localhost, dan pengguna melihat "Jembatan lokal tidak
        menjawab" terus-menerus tanpa cara memperbaikinya selain menutup
        jendela. Sekarang program tetap hidup dan mencoba menyambung lagi.
        """
        with self._kunci:
            self.sambung_terputus = True
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        self.tampil.put("")
        self.tampil.put(f"  [!] {sebab}")
        self.tampil.put("  · mencoba menyambung lagi tiap 3 detik…")
        self.tampil.put("")

    # --- menerima --------------------------------------------------------
    def dengarkan(self) -> None:
        """Thread terpisah: terus membaca apa pun yang dikirim server."""
        penyangga = b""
        while self.jalan:
            try:
                potongan = self.sock.recv(8192)
            except socket.timeout:
                # Bukan kesalahan — cuma belum ada data. Lanjut menunggu.
                continue
            except OSError as e:
                # Kalau pengguna sendiri yang keluar (/keluar), socket sudah
                # ditutup oleh utama() → recv melempar Bad file descriptor.
                # Itu bukan gangguan, jadi jangan dicetak sebagai peringatan;
                # dulu anggota melihat "[!] Sambungan terputus" persis
                # setelah mengetik /keluar dan mengira programnya rusak.
                if not self.jalan:
                    return
                self.putus(f"Sambungan terputus ({e}).")
                return

            if not potongan:
                self.putus("Server menutup sambungan.")
                return

            penyangga += potongan
            while b"\n" in penyangga:
                baris, penyangga = penyangga.split(b"\n", 1)
                if not baris.strip():
                    continue
                try:
                    self.tangani(json.loads(baris.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    pass

    def tangani(self, data: dict) -> None:
        tipe = data.get("tipe")

        if tipe == "selamat":
            self.online = data.get("online", [])
            self.node = data.get("node", "-")
            self.tampil.put(f"  ✓ Tersambung. Nama kamu di server: "
                            f"{data.get('nama_kamu')}")
            self.tampil.put(f"    Node server : {self.node}")
            self.tampil.put(f"    Laptop lain yang aktif: "
                            f"{', '.join(x for x in self.online if x != self.nama) or '(belum ada)'}")
            self.tampil.put("")

        elif tipe == "online":
            self.online = [x["nama"] for x in data.get("daftar", [])]

        elif tipe == "pesan":
            tujuan = data.get("ke", "semua")
            tanda = "siaran" if tujuan == "semua" else "pribadi"
            protokol = data.get("protokol", "TCP")
            label = "TCP" if protokol == "TCP" else "UDP (tidak dijamin)"
            # Simpan untuk jembatan web. Dicatat SEBELUM dicetak supaya UI
            # web dan terminal menampilkan isi yang sama persis.
            #
            # PENGECUALIAN PENTING: pesan yang PENGIRIMNYA diri sendiri
            # tidak dicatat di sini. Jalur UDP memantulkan pesan kembali
            # (server menyiarkan ke semua termasuk pengirim), sementara
            # jembatan sudah mencatatnya saat dikirim — akibatnya pesan
            # sendiri muncul DUA KALI di layar. Jalur TCP tidak terkena
            # karena server sengaja tidak menggemakan pesan ke pengirim.
            dari = str(data.get("dari") or "?")
            if dari != self.nama:
                self.catat_riwayat(dari, tujuan, str(data.get("isi") or ""),
                                   str(data.get("waktu") or ""), protokol)
            self.tampil.put("")
            self.tampil.put(f"  💬 [{data.get('waktu', '')[11:]}] "
                            f"{data.get('dari')} ({tanda}) via {label}:")
            for b in (data.get("isi") or "").split("\n"):
                self.tampil.put(f"       {b}")
            self.tampil.put("")

        elif tipe == "info":
            self.tampil.put(f"  · {data.get('pesan')}")

        elif tipe == "galat":
            pesan = data.get("pesan", "")
            self.tampil.put(f"  ✗ {pesan}")
            # Kalau server menolak nama (duplikat), hentikan program dengan
            # pesan yang jelas supaya pengguna tahu harus ganti nama.
            if "sudah dipakai" in pesan:
                self.tampil.put("")
                self.tampil.put("  >> Ganti nama di src/user/nama.txt lalu jalankan ulang.")
                self.tampil.put("")
                self.jalan = False

        elif tipe == "ping":
            self.kirim({"tipe": "pong"})

        elif tipe == "perintah":
            aksi = data.get("aksi", "")
            self.tampil.put("")
            self.tampil.put(f"  ⚡ PERINTAH DARI SERVER: {aksi}")
            berhasil, pesan = jalankan(aksi, data.get("keterangan", ""))
            tanda = "✓" if berhasil else "✗"
            self.tampil.put(f"     {tanda} {pesan}")
            self.kirim({"tipe": "hasil", "aksi": aksi, "berhasil": berhasil,
                        "pesan": pesan, "id_perintah": data.get("id")})

    # --- mengirim --------------------------------------------------------
    def menu(self) -> None:
        print("  Cara memakai:")
        print("     halo semua              → kirim ke semua orang (TCP)")
        print("     /ke PC-DESKI halo       → kirim ke satu orang saja")
        print("     /udp halo               → kirim lewat UDP (tidak dijamin)")
        print("     /online                 → lihat siapa saja yang aktif")
        print("     /keluar                 → keluar")
        print()

    def tunggu_masukan(self) -> None:
        while self.jalan:
            try:
                baris = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not baris:
                continue
            if baris in ("/keluar", "/exit", "/quit"):
                break

            if baris == "/online":
                orang = [x for x in self.online if x != self.nama]
                if not orang:
                    print("  (belum ada laptop lain yang tersambung)")
                for x in orang:
                    print(f"    • {x}")
                continue

            if baris.startswith("/ke "):
                sisa = baris[4:].strip()
                tujuan, _, isi = sisa.partition(" ")
                if not isi:
                    print("  ✗ formatnya: /ke NAMA pesan")
                    continue
                self.kirim({"tipe": "pesan", "ke": tujuan, "isi": isi})
                continue

            if baris.startswith("/udp "):
                isi = baris[5:].strip()
                if not isi:
                    print("  ✗ formatnya: /udp pesan")
                    continue
                if self.kirim_udp({"tipe": "pesan-udp", "dari": self.nama,
                                   "isi": isi}):
                    print("  · dikirim via UDP (tidak dijamin sampai)")
                else:
                    print("  ✗ jalur UDP belum siap")
                continue

            self.kirim({"tipe": "pesan", "ke": "semua", "isi": baris})

        self.jalan = False


# ------------------------------------------------------ jalur UDP (klien)
def loop_udp(p: Pengguna) -> None:
    """Kirim presence UDP secara berkala + terima jawaban server."""
    terakhir = 0.0
    while p.jalan:
        sekarang = time.time()
        if sekarang - terakhir >= DETAK_TIEP:
            terakhir = sekarang
            p.kirim_udp({"tipe": "detak-klien", "nama": p.nama,
                         "waktu": sekarang})
        try:
            data = p.sock_udp.recv(65535)
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            pesan = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if pesan.get("tipe") == "pesan-udp-terusan":
            isi = str(pesan.get("isi") or "")
            if isi:
                p.tampil.put("")
                p.tampil.put(f"  💬 [{str(pesan.get('waktu', ''))[11:]}] "
                             f"{pesan.get('pengirim')} (siaran) via UDP:")
                for b in isi.split("\n"):
                    p.tampil.put(f"       {b}")
                p.tampil.put("")


# ------------------------------------------------------ penampil (1 thread saja)
def penampil(p: Pengguna) -> None:
    """Cetak antrean tampilan. Satu thread supaya tulisan tidak saling menimpa."""
    while p.jalan or not p.tampil.empty():
        try:
            baris = p.tampil.get(timeout=0.3)
        except queue.Empty:
            continue
        print(baris, flush=True)


# ---------------------------------------------------------------------- utama
def utama() -> int:
    q = argparse.ArgumentParser(description="Pengguna chat — MK Pemrograman Jaringan Kluster")
    q.add_argument("--server", help="IP laptop server, mis. 192.168.1.7 "
                                    "(bawaan: isi server.txt)")
    q.add_argument("--port", type=int, help=f"port server (bawaan {PORT_BAWAAN})")
    q.add_argument("--udp", type=int, help=f"port UDP server (bawaan {PORT_UDP_BAWAAN})")
    q.add_argument("--nama", help="nama laptop ini, sekali jalan saja "
                                  "(bawaan: isi nama.txt)")
    q.add_argument("--tunggu", action="store_true",
                   help="hanya menerima, tidak perlu mengetik apa pun")
    q.add_argument("--web", metavar="PORT", nargs="?", const="7788",
                   help="buka antarmuka web di browser (bawaan port 7788). "
                        "Program tetap memegang TCP/UDP; halaman web cuma "
                        "jembatan ke localhost.")
    q.add_argument("--izinkan-daya", action="store_true",
                   help="izinkan perintah matikan/restart/kunci di macOS "
                        "(bawaan: DITOLAK)")
    a = q.parse_args()

    global IZINKAN_DAYA_DI_MAC
    IZINKAN_DAYA_DI_MAC = a.izinkan_daya

    nama, sumber = baca_nama(a.nama)
    host, port, sumber_alamat = baca_alamat(a.server, a.port)
    p = Pengguna(host, port, nama, a.udp or PORT_UDP_BAWAAN)

    print("=" * 64)
    print("  MK PEMROGRAMAN JARINGAN KLUSTER — PENGGUNA")
    print("=" * 64)
    print(f"  Laptop   : {nama}")
    print(f"  Nama dari: {sumber}")
    if sumber == "argumen --nama":
        # Berkas nama boleh saja belum ada — anggota baru sering langsung
        # memakai --nama sebelum sempat mengisi nama.txt. Dulu baris ini
        # membaca berkasnya tanpa memeriksa, dan programnya berhenti dengan
        # FileNotFoundError hanya karena berkas itu tidak ada.
        if BERKAS_NAMA.exists():
            isi = BERKAS_NAMA.read_text(encoding="utf-8").strip()
            print(f"             (nama.txt berisi '{isi}' — argumen menang."
                  f" Jalankan tanpa --nama untuk memakai isi nama.txt.)")
        else:
            print("             (nama.txt belum ada — pakai --nama atau "
                  "jalankan tanpa --nama untuk mengisinya.)")
    print(f"  Sistem   : {p.sistem}")
    print(f"  Server   : {host}:{port} (TCP)  ·  {host}:{p.port_udp} (UDP)")
    print(f"             (dari: {sumber_alamat})")
    print("=" * 64, flush=True)

    if not p.sambung():
        return 1

    # Simpan referensi thread penampil: daemon thread yang masih menulis ke
    # stdout saat interpreter dimatikan memicu
    # "Fatal Python error: _enter_buffered_busy" — pesan seram yang muncul
    # tepat setelah pengguna mengetik /keluar. Thread-nya ditunggu selesai
    # di akhir utama().
    t_dengar = threading.Thread(target=p.dengarkan, daemon=True)
    t_dengar.start()
    threading.Thread(target=p.jaga_sambungan, daemon=True).start()
    t_tampil = threading.Thread(target=penampil, args=(p,), daemon=True)
    t_tampil.start()

    if a.web:
        # Mode web: jembatan HTTP di localhost. Browser membuka halaman,
        # sementara program ini tetap memegang sambungan TCP/UDP — browser
        # tidak pernah menyentuh jaringan kluster.
        try:
            from jembatan_web import Jembatan
        except ImportError:
            print("  \u2717 jembatan_web.py tidak ditemukan di folder yang sama.")
            return 1
        j = Jembatan(p)
        try:
            port_web = j.pasang(int(a.web))
        except (OSError, ValueError) as e:
            print(f"  \u2717 gagal membuka web di port {a.web}: {e}")
            print("    Coba port lain:  python3 client.py --server ... --web 7789")
            return 1
        alamat = f"http://127.0.0.1:{port_web}/"
        print()
        print("=" * 64)
        print(f"  ANTARMUKA WEB SIAP  \u2192  {alamat}")
        print("=" * 64)
        print("  Buka alamat itu di browser. Tekan Ctrl+C di sini untuk keluar.")
        print()
        # Buka browser otomatis kalau memungkinkan; kalau gagal, alamatnya
        # sudah tercetak di atas sehingga pengguna tetap bisa membukanya.
        try:
            import webbrowser
            webbrowser.open(alamat)
        except Exception:
            pass
        try:
            while p.jalan:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    elif not a.tunggu:
        p.menu()
        p.tunggu_masukan()
    else:
        try:
            while p.jalan:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    p.jalan = False
    try:
        if p.sock:
            p.sock.close()
        if p.sock_udp:
            p.sock_udp.close()
    except OSError:
        pass
    # Tunggu thread penampil selesai menguras antrean SEBELUM interpreter
    # dimatikan. Tanpa ini, print() dari thread itu bertabrakan dengan
    # shutdown dan mencetak "Fatal Python error" — terlihat seperti crash
    # padahal keluar normal.
    try:
        t_tampil.join(timeout=1.5)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(utama())
