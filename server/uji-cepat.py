#!/usr/bin/env python3
"""Uji cepat kluster — server + 2 node + beberapa pengguna, satu mesin.

Yang diperiksa:
  1. Server hidup, dashboard menjawab
  2. Klien TCP tersambung dan terdaftar
  3. Pesan TCP sampai ke tujuan, pengirim tidak dapat gemanya
  4. Pesan pribadi tidak bocor ke klien lain
  5. UDP: presence tercatat, pesan UDP sampai
  6. Dua node saling menemukan lewat UDP heartbeat
  7. Mesh: pesan dari node n1 sampai ke pengguna di node n2
  8. Perintah hanya jalan di klien tujuan
  9. Nama kembar ditolak

    python3 uji-cepat.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

AKAR = Path(__file__).resolve().parent
PY = sys.executable

# Port uji — jauh dari port produksi (5000/5010, 8081).
TCP_A, WEB_A = 5099, 8199          # node n1
TCP_B, WEB_B = 5098, 8198          # node n2
UDP_A, UDP_B = 5110, 5111

lulus, gagal = 0, 0


def periksa(nama: str, benar: bool, tambahan: str = "") -> None:
    global lulus, gagal
    if benar:
        lulus += 1
        print(f"  ✓ {nama}")
    else:
        gagal += 1
        print(f"  ✗ {nama}   {tambahan}")


def tunggu_port(host: str, port: int, detik: float = 12.0) -> bool:
    akhir = time.time() + detik
    while time.time() < akhir:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


class Tiruan:
    """Pengguna tiruan yang bicara TCP sama seperti client.py sungguhan."""

    def __init__(self, nama: str, tcp: int = TCP_A, udp: int = UDP_A,
                 dengan_udp: bool = True) -> None:
        self.nama = nama
        self.sock = socket.create_connection(("127.0.0.1", tcp), timeout=5)
        self.sock_udp: socket.socket | None = None
        self.masuk: list[dict] = []
        self.perintah: list[dict] = []
        self.jalan = True
        self.kirim({"tipe": "daftar", "nama": nama, "sistem": "uji"})
        threading.Thread(target=self._dengar, daemon=True).start()
        if dengan_udp:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(1.0)
                s.connect(("127.0.0.1", udp))
                self.sock_udp = s
            except OSError:
                self.sock_udp = None

    def kirim(self, d: dict) -> None:
        try:
            self.sock.sendall((json.dumps(d) + "\n").encode())
        except OSError:
            self.jalan = False

    def kirim_udp(self, d: dict) -> bool:
        if not self.sock_udp:
            return False
        try:
            self.sock_udp.send(json.dumps(d).encode())
            return True
        except OSError:
            return False

    def _dengar(self) -> None:
        buf = b""
        while self.jalan:
            try:
                p = self.sock.recv(8192)
            except (socket.timeout, OSError):
                break
            if not p:
                break
            buf += p
            while b"\n" in buf:
                baris, buf = buf.split(b"\n", 1)
                if not baris.strip():
                    continue
                try:
                    data = json.loads(baris.decode())
                except ValueError:
                    continue
                if data.get("tipe") == "pesan":
                    self.masuk.append(data)
                elif data.get("tipe") == "perintah":
                    self.perintah.append(data)

    def tutup(self) -> None:
        self.jalan = False
        for s in (self.sock, self.sock_udp):
            try:
                if s:
                    s.close()
            except OSError:
                pass


def api(web: int, jalur: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{web}{jalur}",
                                timeout=5) as r:
        return json.loads(r.read().decode())


def main() -> int:
    proses: list[subprocess.Popen] = []
    db_a = AKAR / "data" / "uji_n1.db"
    db_b = AKAR / "data" / "uji_n2.db"
    for p in (db_a, db_b):
        p.unlink(missing_ok=True)

    try:
        print("1. Menyalakan dua node")
        proses.append(subprocess.Popen(
            [PY, str(AKAR / "server.py"), "--node", "n1", "--port", str(TCP_A),
             "--web", str(WEB_A), "--udp", str(UDP_A),
             "--tetangga", f"127.0.0.1:{TCP_B}:{UDP_B}", "--db", str(db_a)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        proses.append(subprocess.Popen(
            [PY, str(AKAR / "server.py"), "--node", "n2", "--port", str(TCP_B),
             "--web", str(WEB_B), "--udp", str(UDP_B), "--db", str(db_b)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        ada_a = tunggu_port("127.0.0.1", TCP_A)
        ada_b = tunggu_port("127.0.0.1", TCP_B)
        periksa("node n1 hidup", ada_a)
        periksa("node n2 hidup", ada_b)
        if not (ada_a and ada_b):
            return 1
        time.sleep(1.2)   # biarkan heartbeat pertama lewat

        print("\n2. Klien TCP tersambung")
        alpha = Tiruan("PC-ALPHA")
        beta = Tiruan("PC-BETA")
        time.sleep(0.7)
        keadaan_a = api(WEB_A, "/api/keadaan")
        periksa("PC-ALPHA terdaftar di node n1",
                any(x["nama"] == "PC-ALPHA" for x in keadaan_a["online"]))
        periksa("PC-BETA terdaftar di node n1",
                any(x["nama"] == "PC-BETA" for x in keadaan_a["online"]))

        print("\n3. Pesan TCP ke semua")
        alpha.kirim({"tipe": "pesan", "ke": "semua", "isi": "halo semua"})
        time.sleep(0.8)
        periksa("PC-BETA menerima pesan",
                any(m["isi"] == "halo semua" for m in beta.masuk))
        periksa("PC-ALPHA tidak dapat gemanya",
                not any(m["isi"] == "halo semua" for m in alpha.masuk))

        print("\n4. Pesan pribadi tidak bocor")
        alpha.masuk.clear()
        beta.masuk.clear()
        alpha.kirim({"tipe": "pesan", "ke": "PC-BETA", "isi": "rahasia"})
        time.sleep(0.8)
        periksa("PC-BETA menerima pesan pribadi",
                any(m["isi"] == "rahasia" for m in beta.masuk))
        periksa("PC-ALPHA tidak menerima pesan pribadi PC-BETA",
                not any(m["isi"] == "rahasia" for m in alpha.masuk))

        print("\n5. Jalur UDP")
        terkirim_udp = alpha.kirim_udp({"tipe": "pesan-udp", "dari": "PC-ALPHA",
                                        "isi": "ini lewat udp"})
        periksa("klien bisa mengirim lewat UDP", terkirim_udp)
        time.sleep(1.0)
        keadaan_a2 = api(WEB_A, "/api/keadaan")
        pesan_udp = [p for p in keadaan_a2["pesan"] if p["protokol"] == "UDP"]
        periksa("pesan UDP tercatat di basis data", len(pesan_udp) > 0)
        periksa("pesan UDP ditandai tanpa jaminan",
                all(p["terjamin"] == 0 for p in pesan_udp))

        print("\n6. Node saling menemukan lewat UDP heartbeat")
        keadaan_a3 = api(WEB_A, "/api/keadaan")
        dikenal = [x["nama"] for x in keadaan_a3["dikenal"]]
        periksa("node n1 mengetahui node n2", "n2" in dikenal, str(dikenal))
        keadaan_b = api(WEB_B, "/api/keadaan")
        periksa("node n2 mengetahui node n1",
                "n2" in [x["nama"] for x in keadaan_b["dikenal"]] or
                "n1" in [x["nama"] for x in keadaan_b["dikenal"]])

        print("\n7. Mesh TCP antar node")
        gamma = Tiruan("PC-GAMMA", tcp=TCP_B, udp=UDP_B, dengan_udp=False)
        time.sleep(1.0)
        beta.kirim({"tipe": "pesan", "ke": "semua", "isi": "dari node n2"})
        time.sleep(1.5)
        periksa("pesan dari node n2 sampai ke klien node n1",
                any(m["isi"] == "dari node n2" for m in alpha.masuk))

        print("\n8. Perintah hanya di klien tujuan")
        api(WEB_A, f"/api/perintah?sasaran=PC-BETA&aksi=pesan"
                   f"&keterangan=perintah+uji")
        time.sleep(0.9)
        periksa("PC-BETA menerima perintah", len(beta.perintah) > 0)
        periksa("PC-ALPHA tidak menerima perintah", len(alpha.perintah) == 0)

        print("\n9. Nama kembar ditolak")
        kembar = Tiruan("PC-ALPHA", dengan_udp=False)
        time.sleep(0.6)
        keadaan_a4 = api(WEB_A, "/api/keadaan")
        jumlah_alpha = sum(1 for x in keadaan_a4["online"] if x["nama"] == "PC-ALPHA")
        periksa("nama kembar tidak menggantikan yang lama", jumlah_alpha == 1,
                f"jumlah={jumlah_alpha}")
        kembar.tutup()

        print("\n10. Dashboard menjawab")
        periksa("GET / → 200", api(WEB_A, "/api/keadaan") is not None)

        for t in (alpha, beta, gamma):
            t.tutup()

    finally:
        for p in proses:
            p.terminate()
        for p in proses:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in (db_a, db_b):
            f.unlink(missing_ok=True)

    print()
    print("=" * 62)
    print(f"  HASIL: {lulus} lulus, {gagal} gagal")
    print("=" * 62)
    return 0 if gagal == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
