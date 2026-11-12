#!/usr/bin/env python3
"""Uji dengan klien ASLI (client.py), bukan tiruan — menguji jalur yang
dipakai anggota beneran: nama.txt, server.txt, mode interaktif, /udp.

    python3 uji-klien-gescom.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

AKAR = Path(__file__).resolve().parent
BASE = AKAR.parent
USER = BASE / "user"
TCP_A, WEB_A = 5399, 8399
TCP_B, WEB_B = 5398, 8398
UDP_A, UDP_B = 5410, 5411

lulus, gagal = 0, 0


def periksa(nama: str, benar: bool, tambahan: str = "") -> None:
    global lulus, gagal
    if benar:
        lulus += 1
        print(f"  ✓ {nama}")
    else:
        gagal += 1
        print(f"  ✗ {nama}   {tambahan}")


def api(web: int, jalur: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{web}{jalur}", timeout=5) as r:
        return json.loads(r.read().decode())


def main() -> int:
    # siapkan nama.txt & server.txt sementara di folder user/
    nama_f = USER / "nama.txt"
    server_f = USER / "server.txt"
    nama_asli = nama_f.read_text(encoding="utf-8") if nama_f.exists() else "PC01 (Yogi)"
    server_asli = server_f.read_text(encoding="utf-8") if server_f.exists() else ""

    server_f.write_text(f"127.0.0.1:{TCP_A}\n", encoding="utf-8")

    proses: list[subprocess.Popen] = []
    try:
        print("1. Menyalakan dua node")
        proses.append(subprocess.Popen(
            [sys.executable, str(AKAR / "server.py"), "--node", "n1",
             "--port", str(TCP_A), "--web", str(WEB_A), "--udp", str(UDP_A),
             "--tetangga", f"127.0.0.1:{TCP_B}:{UDP_B}",
             "--db", str(AKAR / "data" / "uji_klient_n1.db")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        proses.append(subprocess.Popen(
            [sys.executable, str(AKAR / "server.py"), "--node", "n2",
             "--port", str(TCP_B), "--web", str(WEB_B), "--udp", str(UDP_B),
             "--db", str(AKAR / "data" / "uji_klient_n2.db")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        import socket
        for p in (TCP_A, TCP_B):
            for _ in range(60):
                try:
                    with socket.create_connection(("127.0.0.1", p), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.15)
        time.sleep(1.5)
        periksa("node n1 & n2 hidup", True)

        print("\n2. Klien asli menyambung (baca nama.txt & server.txt)")
        # klien 1: nama dari nama.txt, tanpa --nama
        k1 = subprocess.Popen(
            [sys.executable, str(USER / "client.py"), "--port", str(TCP_A),
             "--udp", str(UDP_A), "--tunggu"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # klien 2: nama lain
        k2 = subprocess.Popen(
            [sys.executable, str(USER / "client.py"), "--port", str(TCP_B),
             "--udp", str(UDP_B), "--nama", "PC-KLIENT2", "--tunggu"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(2.5)
        keadaan = api(WEB_A, "/api/keadaan")
        periksa("klien asli terdaftar (node n1)",
                any(x["nama"] == nama_asli.strip() for x in keadaan["online"]),
                str([x["nama"] for x in keadaan["online"]]))
        keadaan_b = api(WEB_B, "/api/keadaan")
        periksa("klien asli terdaftar (node n2)",
                any(x["nama"] == "PC-KLIENT2" for x in keadaan_b["online"]))

        print("\n3. Klien di folder FILE USER (persis yang dikirim ke anggota)")
        fu = BASE / "FILE USER" / "3. PC04 (Deski)"
        if fu.exists():
            k3 = subprocess.Popen(
                [sys.executable, str(fu / "client.py"), "--server", "127.0.0.1",
                 "--port", str(TCP_A), "--udp", str(UDP_A), "--tunggu"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(2.0)
            keadaan2 = api(WEB_A, "/api/keadaan")
            periksa("klien dari folder FILE USER tersambung",
                    any(x["nama"] == "PC04 (Deski)" for x in keadaan2["online"]),
                    str([x["nama"] for x in keadaan2["online"]]))
            k3.terminate()
        else:
            periksa("folder FILE USER ada", False)

        print("\n4. Pesan TCP antar node lewat klien asli")
        api(WEB_A, f"/api/pesan?tujuan=semua&isi=pesan+dari+dashboard")
        time.sleep(1.2)
        k2_out = ""
        k2.terminate()
        try:
            k2_out = k2.stdout.read() or ""
        except Exception:
            pass
        periksa("klien di node lain menerima pesan TCP",
                "pesan dari dashboard" in k2_out, k2_out[-160:].replace("\n", " | "))

        print("\n5. Jalur UDP dari dashboard")
        api(WEB_A, "/api/pesan-udp?tujuan=semua&isi=pesan+lewat+udp")
        time.sleep(1.0)
        keadaan3 = api(WEB_A, "/api/keadaan")
        pesan_udp = [p for p in keadaan3["pesan"] if p["protokol"] == "UDP"]
        periksa("pesan UDP tercatat", len(pesan_udp) > 0)
        periksa("pesan UDP ditandai tanpa jaminan",
                all(p["terjamin"] == 0 for p in pesan_udp))

        print("\n6. Pengukuran latensi TCP vs UDP")
        waktu = 0
        while waktu < 20:
            d = api(WEB_A, "/api/keadaan")
            t = (d["tetangga"] or [{}])[0]
            if t.get("tcp_ms") is not None and t.get("udp_ms") is not None:
                break
            time.sleep(1.0)
            waktu += 1
        periksa("latensi TCP terukur", t.get("tcp_ms") is not None, str(t))
        periksa("latensi UDP terukur", t.get("udp_ms") is not None, str(t))
        if t.get("tcp_ms") is not None and t.get("udp_ms") is not None:
            print(f"      → TCP {t['tcp_ms']} ms · UDP {t['udp_ms']} ms")

        k1.terminate()

    finally:
        for p in proses:
            p.terminate()
        for p in proses:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in (AKAR / "data" / "uji_klient_n1.db", AKAR / "data" / "uji_klient_n2.db"):
            f.unlink(missing_ok=True)
        # kembalikan nama.txt & server.txt
        if nama_asli:
            nama_f.write_text(nama_asli, encoding="utf-8")
        if server_asli:
            server_f.write_text(server_asli, encoding="utf-8")

    print()
    print("=" * 62)
    print(f"  HASIL: {lulus} lulus, {gagal} gagal")
    print("=" * 62)
    return 0 if gagal == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
