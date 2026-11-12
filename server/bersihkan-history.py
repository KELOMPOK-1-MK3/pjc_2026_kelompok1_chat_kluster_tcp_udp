#!/usr/bin/env python3
"""Bersihkan history chat dari database kluster.

Yang dihapus: isi tabel pesan, perintah, kejadian, dan ukur.
Yang DIPERTAHANKAN: skema tabel, daftar laptop, dan tabel node.

Kenapa tidak hapus file .db-nya: server akan membuat ulang file kosong
dengan skema yang benar, tapi daftar laptop (yang dibuat saat anggota
klien menyambung) ikut hilang. Kalau Yogi mau tes lagi, dia harus
menyambung ulang 6 laptop. Bersihin isinya lebih aman.

Jalankan saat server MATI.
"""
from __future__ import annotations

import glob
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

TABEL_HAPUS = ["pesan", "perintah", "kejadian", "ukur"]
TABEL_JAGA = ["node", "laptop"]


def main() -> None:
    db_file = DATA / "kluster.db"
    if db_file.exists():
        db_file.unlink()
        print(f"hapus: {db_file.name}")

    for f in sorted(glob.glob(str(DATA / "*.db"))):
        db = sqlite3.connect(f)
        db.row_factory = sqlite3.Row
        sebelum = {}
        for t in TABEL_HAPUS:
            try:
                sebelum[t] = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error:
                sebelum[t] = 0
        for t in TABEL_HAPUS:
            db.execute(f'DELETE FROM "{t}"')
        # reset auto-increment supaya id pesan mulai dari 1 lagi
        try:
            db.execute("DELETE FROM sqlite_sequence")
        except sqlite3.Error:
            pass
        db.commit()

        sisa_laptop = db.execute("SELECT COUNT(*) FROM laptop").fetchone()[0]
        db.close()
        print(f"{Path(f).name}: " + ", ".join(
            f"{t}={sebelum[t]}" for t in TABEL_HAPUS) +
            f" → dihapus | laptop dipertahankan: {sisa_laptop}")


if __name__ == "__main__":
    main()
