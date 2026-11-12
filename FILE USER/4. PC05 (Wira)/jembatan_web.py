"""Jembatan web untuk klien kluster.

Kenapa file ini ada:
  Browser TIDAK bisa membuka socket TCP mentah. Jadi halaman web tidak
  mungkin bicara langsung ke server kluster. Solusinya: client.py tetap
  memegang sambungan TCP/UDP seperti biasa, lalu file ini menyalakan
  HTTP kecil di localhost sebagai jembatan. Browser hanya bicara ke
  localhost — tidak butuh akses jaringan apa pun, dan tetap jalan walau
  laptop anggota dipakai tanpa izin firewall.

Kenapa tanpa pustaka luar:
  Semua modul di sini bawaan Python (http.server, json, threading).
  Anggota kelompok tidak perlu pip install apa pun, dan tetap jalan
  offline — syarat yang sama seperti seluruh proyek ini.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

HALAMAN = r"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kluster — Pengguna</title>
<style>
  /* ===================================================================
     TEMA — satu sumber warna, dipakai konsisten.

     Dibanding versi pertama, palet digeser dari biru generik ke campuran
     slate gelap + aksen teal. Alasannya bukan selera: biru dipakai untuk
     TCP, jadi memakai biru juga untuk tombol, judul, dan sorotan membuat
     arti "jalur TCP" hilang di lautan biru. Teal dipakai khusus untuk
     aksi (kirim, pilih), ungu khusus pesan pribadi, biru khusus TCP,
     amber khusus UDP.
     =================================================================== */
  :root{
    /* ---- MODE GELAP (bawaan) ---- */
    --bg:#080c11;
    --bg-2:#0d131b;
    --panel:#111a24;
    --panel-2:#17222e;
    --panel-3:#1e2b3a;
    --garis:rgba(148,178,210,.13);
    --garis-2:rgba(148,178,210,.24);
    --tinta:#eaf2fb;
    --tinta-2:#a3b6cc;
    --tinta-3:#7a8fa6;

    --aksi:#14b8a6;          /* teal — aksi utama */
    --aksi-2:#0d9488;
    --tcp:#3b82f6;           /* biru — jalur TCP */
    --udp:#f59e0b;           /* amber — jalur UDP */
    --pribadi:#a855f7;       /* ungu — pesan pribadi */
    --hidup:#22c55e;
    --mati:#ef4444;

    --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
    --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
    --r:14px;
    --bayang:0 1px 2px rgba(0,0,0,.4),0 12px 32px -8px rgba(0,0,0,.55);

    /* Warna khusus latar animasi. Dipisah supaya mode terang tidak
       memakai warna yang sama — garis putih di atas putih akan hilang. */
    --jaring:rgba(120,190,255,.34);
    --jaring-titik:rgba(150,205,255,.70);

    /* Permukaan bubble dipisah dari --panel supaya tiap tema bisa
       menentukan sendiri. Di mode terang, gradient teal tipis di atas
       putih nyaris tak berwarna — jadi terang memakai warna solid. */
    --aku-latar:#0f766e;
    --aku-teks:#f0fffc;
    --aku-garis:rgba(13,148,136,.75);
    --masuk-latar:#182230;
    --masuk-garis:rgba(148,178,210,.16);
    --pendar-1:rgba(20,184,166,.13);
    --pendar-2:rgba(168,85,247,.11);
  }

  /* ---- MODE TERANG ----
     Bukan sekadar membalik warna: teks dan garis perlu digelapkan, dan
     aksen dinaikkan saturasinya karena di atas latar terang warna pucat
     kehilangan kontras. */
  html[data-tema="terang"]{
    --bg:#f4f7fb;
    --bg-2:#e9eff7;
    --panel:#ffffff;
    --panel-2:#f3f6fb;
    --panel-3:#e6edf6;
    --garis:rgba(22,45,80,.11);
    --garis-2:rgba(22,45,80,.20);
    --tinta:#111c2b;
    /* Terukur: #3c4f66 di atas putih = 8.9:1 ; #56698a = 5.3:1.
       Versi sebelumnya (#647a93) hanya 4.0:1 — gagal ambang 4,5. */
    --tinta-2:#31425a;
    --tinta-3:#526484;

    --aksi:#0d9488;
    --aksi-2:#0f766e;
    --tcp:#2563eb;
    --udp:#b45309;
    --pribadi:#7c3aed;
    --hidup:#15803d;
    --mati:#dc2626;

    --bayang:0 1px 2px rgba(16,35,70,.07),0 10px 24px -12px rgba(16,35,70,.22);
    --jaring:rgba(37,99,235,.16);
    --jaring-titik:rgba(37,99,235,.34);

    /* Di mode terang, bubble sendiri memakai teal PEKAT dengan teks
       putih — bukan teal pucat. Teal pucat di atas putih gagal kontras
       dan tombol Kirim terlihat seperti nonaktif. */
    --aku-latar:#0d7a6b;
    --aku-teks:#ffffff;
    --aku-garis:#0b6b5e;
    --masuk-latar:#ffffff;
    --masuk-garis:#c9d6e4;
    --pendar-1:rgba(20,184,166,.16);
    --pendar-2:rgba(124,58,237,.10);
  }

  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;background:var(--bg);color:var(--tinta);font:14.5px/1.55 var(--ui);
    display:flex;flex-direction:column;overflow:hidden;
    -webkit-font-smoothing:antialiased;
  }
  /* Latar: dua lapisan.
       1. <canvas id="latar"> — jaringan node yang bergerak pelan.
       2. Pendar radial di atasnya, supaya sudut layar tidak datar.

     Kenapa kanvas dan bukan CSS animasi: yang diinginkan adalah "jaringan
     kluster", yaitu titik-titik yang saling tersambung dan mengirim
     denyut. Itu butuh perhitungan antar-titik; CSS tidak bisa
     menyambungkan elemen tanpa SVG atau garis manual. Satu kanvas dengan
     40 titik jauh lebih murah daripada 40 div + 100 elemen garis. */
  #latar{
    position:fixed;inset:0;width:100%;height:100%;z-index:0;
    pointer-events:none;display:block;
  }
  body::before{
    content:"";position:fixed;inset:0;pointer-events:none;z-index:1;
    background:
      radial-gradient(760px 460px at 10% -10%, var(--pendar-1), transparent 72%),
      radial-gradient(720px 420px at 92% 112%, var(--pendar-2), transparent 72%);
  }
  /* Semua isi di atas kanvas latar. */
  header,main,footer{position:relative;z-index:2}

  /* ------------------------------------------------------------ header */
  header{
    flex:0 0 auto;display:flex;align-items:center;gap:13px;
    padding:13px 20px;border-bottom:1px solid var(--garis);
    background:color-mix(in srgb, var(--panel) 88%, transparent);
    backdrop-filter:blur(14px);
  }
  .lambang{
    width:38px;height:38px;border-radius:11px;flex:0 0 auto;
    display:grid;place-items:center;font-weight:700;font-size:15px;
    background:linear-gradient(145deg,var(--aksi),#0f766e);
    color:#032b28;letter-spacing:-.5px;
    box-shadow:0 4px 14px -4px rgba(20,184,166,.65);
  }
  .identitas{min-width:0}
  .identitas h1{margin:0;font-size:15px;font-weight:660;letter-spacing:-.2px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .identitas .sub{font-size:11.5px;color:var(--tinta-3);font-family:var(--mono);
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .kanan{margin-left:auto;display:flex;align-items:center;gap:8px;flex:0 0 auto}

  .tema{
    width:34px;height:34px;border-radius:10px;display:grid;place-items:center;
    border:1px solid var(--garis-2);background:var(--panel-2);
    color:var(--tinta-2);font-size:15px;cursor:pointer;line-height:1;
    transition:background .15s,color .15s,transform .15s;
  }
  .tema:hover{background:var(--panel-3);color:var(--tinta);transform:rotate(15deg)}

  .status{
    display:inline-flex;align-items:center;gap:7px;padding:5px 11px;
    border-radius:20px;font-size:11.5px;font-weight:600;
    border:1px solid var(--garis-2);background:var(--panel-2);
    color:var(--tinta-2);
  }
  .status .titik{width:7px;height:7px;border-radius:50%;background:var(--mati)}
  .status.hidup{color:#86efac;border-color:rgba(34,197,94,.32);
    background:transparent}
  /* Terukur di mode terang: hijau #15803d di atas hijau pucat hanya
     2,2:1. Dibuat solid dengan teks putih → 5,1:1. */
  html[data-tema="terang"] .status.hidup{
    background:#15803d;border-color:#15803d;color:#ffffff;font-weight:700}
  html[data-tema="terang"] .status.hidup .titik{background:#bbf7d0}
  .status.hidup .titik{background:var(--hidup);
    box-shadow:0 0 0 0 rgba(34,197,94,.6);animation:denyut 2.2s ease-out infinite}
  @keyframes denyut{
    0%{box-shadow:0 0 0 0 rgba(34,197,94,.55)}
    70%{box-shadow:0 0 0 7px rgba(34,197,94,0)}
    100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}
  }

  /* ------------------------------------------------------------- tata */
  main{flex:1 1 auto;display:grid;grid-template-columns:1fr 264px;min-height:0}
  @media(max-width:820px){
    main{grid-template-columns:1fr}
    aside{display:none}
  }

  /* ------------------------------------------------------- ruang pesan */
  .ruang{position:relative;min-height:0;overflow:hidden}
  /* Kolom percakapan dibatasi ~880px dan ditengahkan. Di layar 1920px,
     tanpa batas ini bubble kiri dan kanan terpisah 1.500px dan mata harus
     melompat jauh untuk mengikuti percakapan. */
  #pesan{
    height:100%;overflow-y:auto;overscroll-behavior:contain;
    /* max-width + margin auto pada flex child bekerja, tapi hanya kalau
       induknya bukan flex. Di sini .ruang bukan flex, jadi aman. */
    max-width:900px;margin:0 auto;width:100%;
    padding:20px 26px 6px;display:flex;flex-direction:column;gap:3px;
    scrollbar-width:thin;scrollbar-color:var(--panel-3) transparent;
    scroll-behavior:smooth;
  }
  #pesan::-webkit-scrollbar{width:9px}
  #pesan::-webkit-scrollbar-thumb{
    background:var(--panel-3);border-radius:9px;
    border:2px solid var(--bg);
  }
  #pesan::-webkit-scrollbar-thumb:hover{background:#2b3a4d}
  /* Penyangga: mendorong bubble ke bawah selama isinya belum memenuhi
     tinggi panel. Lebih andal daripada justify-content:flex-end, yang
     memotong pesan TERLAMA saat isinya melebihi tinggi. */
  #pesan::before{content:"";margin-top:auto}

  /* Tombol "ke pesan terbaru": hanya muncul saat pengguna menggulir ke
     atas. Tanpa ini, pesan baru masuk tanpa terlihat dan pengguna tidak
     tahu harus menggulir ke mana. */
  .ke-bawah{
    position:absolute;right:22px;bottom:16px;z-index:5;
    display:none;align-items:center;gap:6px;padding:7px 13px;
    border-radius:20px;border:1px solid var(--garis-2);
    background:var(--panel-3);color:var(--tinta);font:inherit;
    font-size:11.5px;font-weight:650;cursor:pointer;
    box-shadow:var(--bayang);transition:filter .15s,transform .15s;
  }
  .ke-bawah:hover{filter:brightness(1.15);transform:translateY(-1px)}
  .ke-bawah.tampil{display:inline-flex;animation:masuk .18s ease both}
  .ke-bawah .n{
    background:var(--aksi);color:#02231f;border-radius:10px;
    padding:0 6px;font-size:10.5px;font-weight:800;
  }

  .kosong-chat{
    position:absolute;inset:0;display:flex;flex-direction:column;
    align-items:center;justify-content:center;gap:9px;text-align:center;
    color:var(--tinta-3);pointer-events:none;padding:24px;
  }
  .kosong-chat .ikon{
    width:54px;height:54px;border-radius:16px;display:grid;place-items:center;
    background:var(--panel-2);border:1px solid var(--garis);
    font-size:24px;margin-bottom:3px;
  }
  .kosong-chat b{color:var(--tinta-2);font-size:13.5px;font-weight:600}
  .kosong-chat span{font-size:12px;max-width:300px;line-height:1.6}

  /* ----------------------------------------------------------- bubble */
  .bubble{
    max-width:min(68%,560px);padding:9px 13px;border-radius:var(--r);
    background:var(--masuk-latar);border:1px solid var(--masuk-garis);
    align-self:flex-start;position:relative;
    box-shadow:var(--bayang);
    /* Animasi masuk: sedikit naik + pudar. 180ms cukup terasa tapi tidak
       memperlambat saat banyak pesan datang bersamaan. */
    animation:masuk .22s cubic-bezier(.22,1,.36,1) both;
  }
  /* Masuk dari sisi bubble: pesan sendiri naik dari kanan, pesan orang
     lain dari kiri. Arah masuk mengikutkan siapa pengirimnya, jadi mata
     bisa menebak arah percakapan walau animasinya cuma 220 ms. */
  @keyframes masuk{
    from{opacity:0;transform:translateY(10px) translateX(-8px) scale(.97)}
    to{opacity:1;transform:none}
  }
  @keyframes masukAku{
    from{opacity:0;transform:translateY(10px) translateX(8px) scale(.97)}
    to{opacity:1;transform:none}
  }
  .bubble.aku{animation-name:masukAku}

  /* Kilau singkat di tepi bubble saat baru tiba: menandai "ini yang baru"
     tanpa perlu angka notifikasi. */
  @keyframes kilau{
    0%{box-shadow:0 0 0 0 rgba(20,184,166,.42)}
    100%{box-shadow:0 0 0 12px rgba(20,184,166,0)}
  }
  .bubble.sorot{animation:masuk .22s cubic-bezier(.22,1,.36,1) both,
                          kilau .9s ease-out .1s both}
  .bubble.aku{
    align-self:flex-end;
    /* Warna badan adalah pembeda utama, bukan cuma posisi — pesan panjang
       membuat perbedaan posisi sulit dibaca sekilas. */
    background:var(--aku-latar);
    border-color:var(--aku-garis);
    border-bottom-right-radius:5px;
  }
  .bubble.masuk{ border-bottom-left-radius:5px }

  /* Pesan pribadi: garis ungu di sisi kiri + latar keunguan. Warna saja
     tidak cukup, jadi bentuknya juga dibedakan (garis tepi). */
  .bubble.pribadi{
    border-left:3px solid var(--pribadi);
    background:linear-gradient(160deg,rgba(168,85,247,.17),rgba(168,85,247,.07));
  }
  .bubble.aku.pribadi{
    background:linear-gradient(160deg,#7c3aed,#6d28d9);
    border-color:#6d28d9;border-left:3px solid #a855f7;
  }

  .kepala{
    display:flex;align-items:center;gap:6px;flex-wrap:wrap;
    font-size:11px;color:var(--tinta-3);margin-bottom:3px;
  }
  /* Nama pengirim memakai bentuk yang SAMA untuk pesan sendiri dan orang
     lain — hanya warnanya yang beda. Dulu "Kamu" ditulis sebagai teks biasa
     sementara nama lain sebagai chip, jadi dua gaya berbeda di posisi yang
     sama dan terbaca seperti dua komponen tak berhubungan. */
  .kepala .dari{
    font-weight:700;color:var(--tinta);font-size:11px;
    background:rgba(148,178,210,.11);border:1px solid var(--garis);
    padding:1px 6px;border-radius:5px;letter-spacing:.1px;
  }
  .kepala .dari.diriku{color:#7fe3d6;background:rgba(20,184,166,.16);
    border-color:rgba(20,184,166,.32)}
  .kepala .jam{font-family:var(--mono);font-size:11px;margin-left:auto;
    color:var(--tinta-2);opacity:.9}

  .lencana{
    font-size:9.5px;font-weight:800;padding:1.5px 5.5px;border-radius:5px;
    text-transform:uppercase;letter-spacing:.5px;font-family:var(--mono);
  }
  .lencana.tcp{background:rgba(59,130,246,.17);color:#93c5fd;
    border:1px solid rgba(59,130,246,.28)}
  html[data-tema="terang"] .lencana.tcp{background:rgba(37,99,235,.12);
    color:#1d4ed8;border-color:rgba(37,99,235,.30)}
  /* Di dalam bubble teal, lencana biru menghilang. Pakai putih transparan
     supaya jalur tetap terbaca tanpa keluar dari palet. */
  .bubble.aku .lencana.tcp,
  .bubble.aku .lencana.udp{
    background:rgba(255,255,255,.20);border-color:rgba(255,255,255,.36);
    color:#ffffff;
  }
  .lencana.udp{background:rgba(245,158,11,.17);color:#fcd34d;
    border:1px solid rgba(245,158,11,.30)}
  html[data-tema="terang"] .lencana.udp{background:rgba(180,83,9,.13);
    color:#92400e;border-color:rgba(180,83,9,.32)}
  .lencana.pribadi{background:rgba(168,85,247,.20);color:#ddb2ff;
    border:1px solid rgba(168,85,247,.34)}

  .isi{white-space:pre-wrap;word-break:break-word;font-size:14px;
    line-height:1.55}
  .bubble.aku .isi{color:var(--aku-teks)}
  /* Jam dan label di atas bubble teal harus PUTIH PENUH. Sebelumnya
     78% opasitas → kontras ~3:1 pada teks 11px, gagal ambang keterbacaan. */
  .bubble.aku .kepala{color:#ffffff}
  .bubble.aku .kepala .jam{color:#ffffff;opacity:1}
  .bubble.aku .kepala .dari{background:rgba(255,255,255,.22);
    border-color:rgba(255,255,255,.34);color:#ffffff}

  /* Tanda "tidak dijamin" untuk UDP: titik-titik samar. Memberi isyarat
     visual bahwa pesan ini boleh hilang, tanpa perlu kalimat panjang. */
  .bubble.tanpa-jaminan .isi::after{
    content:"⋯";margin-left:5px;color:var(--udp);opacity:.75;font-weight:700;
  }

  .sistem-pesan{
    align-self:center;font-size:11.5px;color:var(--tinta-2);font-weight:600;
    background:var(--panel-2);padding:4px 12px;border-radius:20px;
    border:1px solid var(--garis-2);text-align:center;max-width:86%;
    animation:masuk .22s ease both;
  }
  .sistem-pesan.buruk{color:#fca5a5;border-color:rgba(239,68,68,.34);
    background:rgba(239,68,68,.08)}

  /* --------------------------------------------------------- sidebar */
  aside{
    border-left:1px solid var(--garis-2);
    background:color-mix(in srgb, var(--panel) 86%, transparent);
    backdrop-filter:blur(10px);padding:15px 13px;overflow-y:auto;
    display:flex;flex-direction:column;gap:16px;
  }
  aside::-webkit-scrollbar{width:7px}
  aside::-webkit-scrollbar-thumb{background:var(--panel-3);border-radius:7px}

  .judul-sisi{
    display:flex;align-items:center;gap:7px;font-size:10px;
    text-transform:uppercase;letter-spacing:.9px;color:var(--tinta-3);
    font-weight:700;margin-bottom:7px;
  }
  .judul-sisi .jml{
    margin-left:auto;font-family:var(--mono);font-size:10px;
    background:var(--panel-2);border:1px solid var(--garis);
    padding:0 5px;border-radius:5px;letter-spacing:0;
  }

  .orang{
    display:flex;align-items:center;gap:9px;padding:8px 9px;
    border-radius:10px;font-size:13px;cursor:pointer;
    border:1px solid transparent;
    transition:background .14s,border-color .14s,transform .14s;
  }
  .orang:hover{background:var(--panel-2);transform:translateX(1px)}
  .orang .dot{
    width:7px;height:7px;border-radius:50%;background:var(--hidup);flex:0 0 auto;
    box-shadow:0 0 7px rgba(34,197,94,.55);
  }
  .orang .nm{font-family:var(--mono);font-size:12.5px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .orang .ket{margin-left:auto;font-size:10px;color:var(--tinta-2);
    font-weight:700;letter-spacing:.4px;text-transform:uppercase;flex:0 0 auto;
    border:1px solid var(--garis-2);border-radius:4px;padding:0 4px}
  .orang.pergi{opacity:.62}
  .orang.pergi .dot{background:var(--tinta-3);box-shadow:none}
  .orang.pergi .ket{color:var(--tinta-3)}
  /* Terpilih: bukan cuma warna — ada garis tepi kiri dan tanda panah. */
  .orang.terpilih{
    background:rgba(168,85,247,.15);border-color:rgba(168,85,247,.34);
    box-shadow:inset 3px 0 0 var(--pribadi);
  }
  .orang.terpilih .ket{color:#ddb2ff}
  aside .kosong{font-size:12px;color:var(--tinta-3);font-style:italic;
    padding:9px 4px;line-height:1.5}

  /* ------------------------------------------------------------- kirim */
  footer{
    flex:0 0 auto;border-top:1px solid var(--garis);
    background:color-mix(in srgb, var(--panel) 92%, transparent);
    backdrop-filter:blur(14px);
    padding:11px 20px 13px;
  }
  .jalur-baris{display:flex;align-items:center;gap:6px;margin-bottom:9px;
    flex-wrap:wrap}
  .jalur{
    display:inline-flex;background:var(--panel-2);border:1px solid var(--garis);
    border-radius:9px;padding:2px;gap:2px;
  }
  .jalur button{
    font:inherit;font-size:11px;font-weight:650;padding:4px 12px;
    border:0;border-radius:7px;background:transparent;color:var(--tinta-3);
    cursor:pointer;transition:background .15s,color .15s;
    display:inline-flex;align-items:center;gap:5px;
  }
  .jalur button:hover{color:var(--tinta-2)}
  .jalur button.on[data-jalur=TCP]{background:rgba(59,130,246,.20);
    color:#bfdbfe;box-shadow:inset 0 0 0 1px rgba(59,130,246,.34)}
  .jalur button.on[data-jalur=UDP]{background:rgba(245,158,11,.20);
    color:#fde68a;box-shadow:inset 0 0 0 1px rgba(245,158,11,.34)}

  /* ---- MODE TERANG: pilihan aktif dibuat SOLID ----
     Diukur: teks biru (#1e40af) di atas biru pucat (#e0ecff) hanya 2,0:1.
     Solusi paling andal bukan menggelapkan teks, tapi membalik: latar
     solid, teks putih. Hasilnya 8:1 lebih dan keadaan aktif langsung
     terlihat tanpa harus membaca. */
  html[data-tema="terang"] .jalur{background:#e8eef7;border-color:#cdd8e6}
  html[data-tema="terang"] .jalur button.on[data-jalur=TCP]{
    background:#1d4ed8;color:#ffffff;font-weight:750;
    box-shadow:0 2px 6px -2px rgba(29,78,216,.6)}
  html[data-tema="terang"] .jalur button.on[data-jalur=UDP]{
    background:#b45309;color:#ffffff;font-weight:750;
    box-shadow:0 2px 6px -2px rgba(180,83,9,.6)}
  html[data-tema="terang"] .jalur button:not(.on){color:#41536b}
  html[data-tema="terang"] .jalur button:not(.on):hover{color:#1b2b40}
  .jalur button.on[data-jalur=UDP]{background:rgba(245,158,11,.20);
    color:#fcd34d;box-shadow:inset 0 0 0 1px rgba(245,158,11,.34)}

  .tujuan{
    display:none;align-items:center;gap:6px;font-size:11px;font-weight:650;
    padding:4px 10px;border-radius:9px;color:#ddb2ff;
    background:rgba(168,85,247,.15);border:1px solid rgba(168,85,247,.32);
  }
  .tujuan.tampil{display:inline-flex;animation:masuk .18s ease both}
  .tujuan b{font-family:var(--mono);font-size:11px}
  .tujuan .x{cursor:pointer;opacity:.7;font-size:13px;line-height:1;
    padding:0 1px}
  .tujuan .x:hover{opacity:1}

  .baris{display:flex;gap:9px;align-items:flex-end}
  textarea{
    flex:1;background:var(--panel-2);color:var(--tinta);
    border:1px solid var(--garis-2);border-radius:12px;padding:11px 14px;
    box-shadow:inset 0 1px 2px rgba(16,35,70,.04);
    font:inherit;font-size:14px;line-height:1.45;resize:none;height:46px;
    outline:none;transition:border-color .15s,box-shadow .15s,background .15s;
    max-height:130px;
  }
  textarea::placeholder{color:var(--tinta-3);opacity:1}
  textarea:disabled{opacity:.55;cursor:not-allowed}
  textarea:focus{
    border-color:rgba(20,184,166,.55);background:var(--panel-3);
    box-shadow:0 0 0 3px rgba(20,184,166,.13);
  }
  .kirim{
    height:46px;padding:0 20px;border-radius:12px;border:0;flex:0 0 auto;
    /* Warna AKSI solid, bukan gradient pucat. Di mode terang, gradient
       tipis membuat tombol terbaca seperti nonaktif walau siap dikirim. */
    background:var(--aksi);
    color:#ffffff;font:inherit;font-weight:750;font-size:13.5px;
    cursor:pointer;display:inline-flex;align-items:center;gap:7px;
    transition:transform .12s,filter .15s,opacity .15s;
    box-shadow:0 6px 18px -8px rgba(20,184,166,.8);
  }
  .kirim:hover:not(:disabled){filter:brightness(1.09);transform:translateY(-1px)}
  .kirim:active:not(:disabled){transform:translateY(0) scale(.99)}
  .kirim:disabled{opacity:1;background:var(--panel-3);color:var(--tinta-3);
    cursor:default;box-shadow:none}
  .kirim svg{width:15px;height:15px}

  .ket{font-size:11px;color:var(--tinta-2);margin-top:7px;
    display:flex;gap:14px;flex-wrap:wrap}
  .ket b{color:var(--tinta-2);font-weight:650}

  /* --------------------------------------------------------- responsif */
  @media(max-width:640px){
    header{padding:10px 13px;gap:9px}
    .lambang{width:33px;height:33px;font-size:13px}
    #pesan{padding:14px 13px 4px}
    .bubble{max-width:88%}
    footer{padding:9px 13px 11px}
    .ket{display:none}
  }
  /* Hormati setelan sistem: pengguna yang minta gerak dikurangi tidak
     boleh dipaksa melihat animasi masuk tiap pesan. */
  @media(prefers-reduced-motion:reduce){
    *,*::before,*::after{animation:none!important;transition:none!important}
    #pesan{scroll-behavior:auto}
  }

/* ------------------------------------------------------------------
   PERPINDAHAN TEMA — lingkaran melebar dari tombol.

   Ditiru dari komponen "Animated Theme Toggler", tapi versi aslinya
   React + framer-motion. Di sini tidak perlu keduanya: View Transitions
   API sudah ada di browser, dan animasinya cuma satu clip-path.

   Kenapa lingkaran dari tombol, bukan cross-fade biasa:
   cross-fade membuat kedua tema saling tembus di seluruh layar sekaligus —
   teks jadi dobel dan susah dibaca selama 700 ms. Lingkaran menyapu, jadi
   di setiap titik hanya ada satu tema yang terlihat.

   Catatan: bawaannya View Transitions memberi cross-fade pada ::view-
   transition-old/new. Itu harus DIMATIKAN dulu, kalau tidak hasilnya
   cross-fade + lingkaran sekaligus = kabur.
   ------------------------------------------------------------------ */
@supports (view-transition-name: root) {
  ::view-transition-old(root),
  ::view-transition-new(root) {
    animation: none;
    mix-blend-mode: normal;
  }
  /* Tema lama tetap diam di bawah; tema baru yang menyapu di atasnya. */
  ::view-transition-old(root) { z-index: 1 }
  ::view-transition-new(root) { z-index: 2 }
}

/* Dipakai sesaat saat tema diganti oleh View Transition: matikan
   transisi CSS supaya tidak bertumpuk dengan animasi lingkaran. */
html.tanpa-transisi *, html.tanpa-transisi *::before,
html.tanpa-transisi *::after{
  transition:none !important;
}

/* Ikon tema: berputar + mengecil saat bertukar. */
#ikon-tema{
  display:inline-block;
  transition:transform .34s cubic-bezier(.34,1.56,.64,1), opacity .18s;
}
#ikon-tema.tukar{ transform:rotate(-90deg) scale(.4); opacity:0 }
</style>
</head>
<body>
<canvas id="latar"></canvas>

<header>
  <div class="lambang" id="lambang">◎</div>
  <div class="identitas">
    <h1 id="nama">menyambung…</h1>
    <div class="sub" id="info">—</div>
  </div>
  <div class="kanan">
    <button class="tema" id="tombol-tema" title="Ganti mode terang / gelap">
      <span id="ikon-tema">☀</span>
    </button>
    <span class="status" id="status"><span class="titik"></span><span id="status-teks">menyambung</span></span>
  </div>
</header>

<main>
  <div class="ruang">
    <div id="pesan"></div>
    <button class="ke-bawah" id="ke-bawah">
      <span>pesan baru</span><span class="n" id="jml-baru">0</span>
    </button>
    <div class="kosong-chat" id="kosong-chat">
      <div class="ikon">💬</div>
      <b>Belum ada pesan</b>
      <span>Ketik di bawah untuk mengirim ke semua laptop.
            Klik nama di panel kanan untuk mengirim pribadi.</span>
    </div>
  </div>

  <aside>
    <div>
      <div class="judul-sisi">Laptop lain <span class="jml" id="jml">0</span></div>
      <div id="daftar-orang"><div class="kosong">belum ada laptop lain</div></div>
    </div>
  </aside>
</main>

<footer>
  <div class="jalur-baris">
    <div class="jalur">
      <button data-jalur="TCP" class="on" title="Dijamin sampai, urutan terjaga">
        <span>TCP</span><span style="opacity:.65">· dijamin</span>
      </button>
      <button data-jalur="UDP" title="Cepat, tapi boleh hilang">
        <span>UDP</span><span style="opacity:.65">· cepat</span>
      </button>
    </div>
    <span class="tujuan" id="tujuan">
      <span>pribadi ke</span><b id="tujuan-nama">—</b>
      <span class="x" id="batal-tujuan" title="Batal (kirim ke semua)">×</span>
    </span>
  </div>
  <div class="baris">
    <textarea id="tulis" rows="1"
      placeholder="Tulis pesan…  (Enter kirim · Shift+Enter baris baru)"></textarea>
    <button class="kirim" id="kirim" disabled>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4Z"/>
      </svg>
      Kirim
    </button>
  </div>
  <div class="ket">
    <span id="ket-tujuan">Terkirim ke <b>semua laptop</b></span>
  </div>
</footer>

<script>
const el = (id) => document.getElementById(id);
let jalur = "TCP";
let tujuan = "semua";
let namaKu = "";
let nomorTerakhir = 0;
let adaPesan = false;
let onlineSebelumnya = null;

/* ------------------------------------------------------------- kirim */
async function kirim() {
  const isi = el("tulis").value.trim();
  if (!isi) return;
  el("kirim").disabled = true;
  try {
    const r = await fetch(`/api/kirim?isi=${encodeURIComponent(isi)}` +
                          `&tujuan=${encodeURIComponent(tujuan)}&jalur=${jalur}`);
    const d = await r.json();
    if (!d.ok) tambahSistem(d.pesan || "gagal mengirim", true);
    else el("tulis").value = "";
  } catch (e) {
    tambahSistem("jembatan lokal tidak menjawab", true);
  }
  el("kirim").disabled = !el("tulis").value.trim();
  tinggiOtomatis();
  el("tulis").focus();
  muat();
}

/* ------------------------------------------------------------ render */
/* Satu baris untuk SEMUA gangguan, bukan chip baru tiap percobaan.
   Sebelumnya tiap kegagalan menambah satu chip; karena UI mencoba tiap
   0,9 detik, layar penuh tulisan "Jembatan lokal tidak menjawab" dalam
   beberapa detik dan menutupi percakapan. */
let galatAktif = null;

function tambahSistem(teks, buruk) {
  if (buruk) {
    if (galatAktif) { galatAktif.textContent = teks; return; }
    galatAktif = document.createElement("div");
    galatAktif.className = "sistem-pesan buruk";
    galatAktif.textContent = teks;
    el("pesan").appendChild(galatAktif);
    gulirBawah();
    return;
  }
  const d = document.createElement("div");
  d.className = "sistem-pesan";
  const jam = new Date().toTimeString().slice(0, 8);
  d.textContent = `${teks} · ${jam}`;
  el("pesan").appendChild(d);
  gulirBawah();
}

function bersihkanGalat() {
  if (galatAktif) { galatAktif.remove(); galatAktif = null; }
}

const dekatBawah = () => {
  const p = el("pesan");
  return p.scrollHeight - p.scrollTop - p.clientHeight < 120;
};

let belumDibaca = 0;

function gulirBawah(paksa) {
  const p = el("pesan");
  if (paksa || dekatBawah()) {
    p.scrollTop = p.scrollHeight;
    belumDibaca = 0;
    el("ke-bawah").classList.remove("tampil");
  }
}

el("ke-bawah").onclick = () => gulirBawah(true);

/* Saat pengguna menggulir jauh dari dasar, pesan baru tidak memaksa
   lompatan (itu menyebalkan kalau sedang membaca ke atas). Yang muncul
   adalah tombol berisi jumlah pesan yang belum dilihat. */
el("pesan").addEventListener("scroll", () => {
  if (dekatBawah() && belumDibaca) {
    belumDibaca = 0;
    el("ke-bawah").classList.remove("tampil");
  }
});

function tambahPesan(p, diam) {
  const aku = (p.dari === namaKu);
  const b = document.createElement("div");
  b.className = "bubble" + (aku ? " aku" : " masuk") +
                (p.ke && p.ke !== "semua" ? " pribadi" : "") +
                (p.protokol === "UDP" ? " tanpa-jaminan" : "");
  /* Pesan lama (saat memuat riwayat) tidak dianimasikan — kalau semua
     ikut beranimasi, membuka halaman terlihat seperti ledakan. */
  if (diam) b.style.animation = "none";

  const kepala = document.createElement("div");
  kepala.className = "kepala";

  const dari = document.createElement("span");
  dari.className = "dari" + (aku ? " diriku" : "");
  dari.textContent = aku ? "Kamu" : p.dari;
  kepala.appendChild(dari);

  const lencana = document.createElement("span");
  lencana.className = "lencana " + (p.protokol === "UDP" ? "udp" : "tcp");
  lencana.textContent = p.protokol === "UDP" ? "UDP" : "TCP";
  kepala.appendChild(lencana);

  if (p.ke && p.ke !== "semua") {
    const pl = document.createElement("span");
    pl.className = "lencana pribadi";
    pl.textContent = "pribadi";
    kepala.appendChild(pl);
  }

  const jam = document.createElement("span");
  jam.className = "jam";
  jam.textContent = (p.waktu || "").slice(11, 19);
  kepala.appendChild(jam);

  const isi = document.createElement("div");
  isi.className = "isi";
  isi.textContent = p.isi;

  b.appendChild(kepala);
  b.appendChild(isi);
  el("pesan").appendChild(b);
  if (!diam) {
    b.classList.add("sorot");
    /* Lepas kelas setelah animasi selesai supaya tidak menumpuk di
       ratusan bubble — tiap kelas animasi yang tertinggal menahan
       compositor layer dan membuat scroll berat. */
    setTimeout(() => b.classList.remove("sorot"), 1100);
  }
  return b;
}

/* Tampilkan tulisan "Belum ada pesan" HANYA kalau tidak ada bubble.
   Perhatikan arahnya: tampil=1 berarti tampilkan. Versi sebelumnya
   menggunakan nilai terbalik sehingga teks kosong tetap muncul walau
   percakapan sudah berjalan. */
function tampilkanKosong(tampil) {
  el("kosong-chat").style.display = tampil ? "flex" : "none";
}

function gambarOrang(daftar, pernah) {
  const w = el("daftar-orang");
  const aktif = new Set(daftar.filter((n) => n !== namaKu));
  const semua = [...aktif];
  for (const n of (pernah || [])) if (n !== namaKu && !aktif.has(n)) semua.push(n);
  /* Urut abjad. Server mengirim sesuai urutan kamus internal yang berubah
     tiap kali ada yang masuk-keluar, jadi tanpa ini daftar melompat-lompat. */
  semua.sort((a, b) => a.localeCompare(b));

  /* Tulis "aktif/total". Dulu hanya jumlah aktif, padahal daftarnya juga
     memuat laptop yang sudah keluar — angka 2 di samping empat baris
     terbaca seperti salah hitung. */
  el("jml").textContent = semua.length === aktif.size
    ? String(aktif.size)
    : `${aktif.size}/${semua.length}`;

  if (!semua.length) {
    w.innerHTML = '<div class="kosong">belum ada laptop lain</div>';
    return;
  }
  w.innerHTML = "";
  for (const n of semua) {
    const d = document.createElement("div");
    d.className = "orang" + (aktif.has(n) ? "" : " pergi") +
                  (tujuan === n ? " terpilih" : "");
    /* Label "AKTIF" TIDAK diulang di setiap baris: titik hijau sudah
       menyatakan hal yang sama. Yang perlu teks hanya kondisi "keluar",
       karena itu pengecualian. */
    d.innerHTML = '<span class="dot"></span><span class="nm"></span>' +
                  '<span class="ket"></span>';
    d.querySelector(".nm").textContent = n;
    d.querySelector(".ket").textContent = aktif.has(n) ? "" : "keluar";
    d.onclick = () => {
      tujuan = (tujuan === n) ? "semua" : n;
      perbaruiTujuan();
      w.querySelectorAll(".orang").forEach((x) => {
        x.classList.toggle("terpilih",
          x.querySelector(".nm").textContent === tujuan);
      });
      el("tulis").focus();
    };
    w.appendChild(d);
  }
}

function perbaruiTujuan() {
  const pribadi = tujuan !== "semua";
  el("tujuan").classList.toggle("tampil", pribadi);
  if (pribadi) el("tujuan-nama").textContent = tujuan;
  el("tulis").placeholder = pribadi
    ? `Pesan pribadi ke ${tujuan}…`
    : "Tulis pesan…  (Enter kirim · Shift+Enter baris baru)";
  /* Caption harus jujur soal jaminan. Menulis "terkirim" untuk jalur UDP
     itu keliru — UDP tidak menjamin sampai, dan itu justru inti yang
     diuji dosen saat demo. */
  const ke = pribadi ? `ke <b>${tujuan}</b> saja` : "ke <b>semua laptop</b>";
  el("ket-tujuan").innerHTML = (jalur === "UDP")
    ? `Dikirim lewat UDP ${ke} · <b>tidak dijamin sampai</b>`
    : `Dikirim lewat TCP ${ke}`;
}

/* ------------------------------------------------------------- muat */
async function muat() {
  try {
    const r = await fetch("/api/keadaan", { cache: "no-store" });
    const d = await r.json();
    namaKu = d.nama;

    el("nama").textContent = d.nama;
    el("info").textContent = `node ${d.node} · ${d.host}:${d.port}`;
    el("lambang").textContent = (d.nama || "?").replace(/[^A-Za-z0-9]/g, "")
      .slice(0, 2).toUpperCase() || "◎";

    bersihkanGalat();
    el("tulis").disabled = false;
    el("tulis").placeholder = (tujuan === "semua")
      ? "Tulis pesan…  (Enter kirim · Shift+Enter baris baru)"
      : `Pesan pribadi ke ${tujuan}…`;
    el("status").classList.toggle("hidup", d.hidup);
    el("status-teks").textContent = d.hidup ? "tersambung" : "terputus";

    gambarOrang(d.online, d.pernah);

    /* Klien sendiri yang menentukan mana yang belum digambar, memakai
       `nomor`. Server tidak dipercaya untuk itu: dashboard memanggil tiap
       0,9 detik dan beberapa permintaan bisa balapan. */
    /* Urutan render WAJIB urut nomor. Server mengirim riwayat dalam urutan
       penyimpanan, dan pesan yang datang dari dua laptop bisa tersimpan
       tidak berurutan. Tanpa sort, bubble "Kamu" bisa terselip di antara
       pesan orang lain — dan itu yang membuat percakapan terlihat
       mengambang dan tidak nyambung. */
    const baru = (d.semua || [])
      .filter((p) => p.nomor > nomorTerakhir)
      .sort((a, b) => a.nomor - b.nomor);
    const diam = !adaPesan && baru.length > 4;   /* muat riwayat awal */
    for (const p of baru) {
      tambahPesan(p, diam);
      nomorTerakhir = p.nomor;
      adaPesan = true;
    }
    /* Status kosong ditentukan oleh ADA-TIDAKNYA bubble, bukan oleh
       kedatangan pesan pada permintaan ini. Memakai `baru.length` membuat
       tulisan "Belum ada pesan" tetap terlihat padahal percakapan sudah
       jalan — karena permintaan berikutnya tidak membawa pesan baru. */
    /* Pesan sistem saat laptop lain bergabung / keluar.

       Ini bukan hiasan: tugasnya membuktikan perilaku kluster (anggota
       datang dan pergi), dan itu justru inti mata kuliahnya. Efek
       sampingnya percakapan tidak lagi terasa kosong saat baru dibuka. */
    const onlineKini = new Set((d.online || []).filter((n) => n !== namaKu));
    /* Beri jeda supaya tidak membanjiri percakapan.

       Laptop yang koneksinya tidak stabil bisa keluar-masuk beberapa kali
       per detik, dan tanpa penjagaan ini ruang chat penuh tulisan
       "X bergabung / X keluar" sampai pesan asli terdorong keluar layar.
       Perubahan yang terjadi dalam 4 detik pertama tidak dicatat. */
    const sekarang = Date.now();
    if (onlineSebelumnya && sekarang - (window.__akhirEvent || 0) > 1500) {
      const masuk = [...onlineKini].filter((n) => !onlineSebelumnya.has(n));
      const pergi = [...onlineSebelumnya].filter((n) => !onlineKini.has(n));
      /* Pesan sistem dicatat SETELAH pesan yang datang pada permintaan
         yang sama. Dulu urutannya terbalik: laptop yang bergabung lalu
         langsung mengirim pesan tampak mengirim DULU sebelum bergabung.
         Di sini event ditempel sesudahnya — pada satu putaran polling
         tidak ada informasi waktu yang lebih presisi. */
      for (const n of masuk) tambahSistem(`${n} bergabung`);
      for (const n of pergi) tambahSistem(`${n} keluar`);
      if (masuk.length || pergi.length) window.__akhirEvent = sekarang;
    }
    onlineSebelumnya = onlineKini;

    tampilkanKosong(document.querySelectorAll(".bubble").length === 0 ? 1 : 0);
    if (baru.length) {
      if (diam || dekatBawah()) {
        gulirBawah(true);
      } else {
        belumDibaca += baru.length;
        el("jml-baru").textContent = belumDibaca;
        el("ke-bawah").classList.add("tampil");
      }
    }
  } catch (e) {
    /* Jembatan lokal tidak menjawab = client.py sudah mati (jendela
       terminalnya ditutup, atau Ctrl+C). Data terakhir TIDAK boleh tetap
       tampil: daftar laptop dan status "tersambung" yang sudah basi
       membuat pengguna mengira sistem masih jalan. */
    el("status").classList.remove("hidup");
    el("status-teks").textContent = "jembatan mati";
    el("daftar-orang").innerHTML =
      '<div class="kosong">tidak tersambung</div>';
    el("jml").textContent = "0";
    el("kirim").disabled = true;
    el("tulis").disabled = true;
    el("tulis").placeholder = "jembatan mati — jalankan ulang client.py";
    tambahSistem(
      "Program client.py berhenti. Jalankan ulang di terminal " +
      "(python3 client.py --server … --web), lalu muat ulang halaman ini.",
      true);
  }
}

/* --------------------------------------------------------- kendali */
document.querySelectorAll(".jalur button").forEach((b) => {
  b.onclick = () => {
    jalur = b.dataset.jalur;
    document.querySelectorAll(".jalur button")
      .forEach((x) => x.classList.toggle("on", x === b));
    perbaruiTujuan();
  };
});

el("batal-tujuan").onclick = () => {
  tujuan = "semua";
  perbaruiTujuan();
  document.querySelectorAll(".orang").forEach((x) =>
    x.classList.remove("terpilih"));
};

el("kirim").onclick = kirim;

/* Textarea tumbuh mengikuti isi, maksimal 130px (lalu bergulir sendiri).
   Tanpa ini, pesan tiga baris membuat kolom tetap satu baris dan teks
   terpotong di tengah. */
function tinggiOtomatis() {
  const t = el("tulis");
  t.style.height = "auto";
  t.style.height = Math.min(130, t.scrollHeight) + "px";
}

el("tulis").addEventListener("input", () => {
  el("kirim").disabled = !el("tulis").value.trim();
  tinggiOtomatis();
});
el("tulis").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); kirim(); }
});


/* ====================================================================
   LATAR — DUA ANIMASI BERBEDA, satu per mode.

   Kenapa dua, bukan satu yang diwarnai ulang:
     Di mode gelap, cahaya di atas hitam terbaca sebagai "menyala" tanpa
     usaha. Di mode terang, efek yang sama justru hilang: titik biru tipis
     di atas putih hanya jadi noda. Jadi tiap mode butuh BAHASA VISUAL
     sendiri, bukan sekadar ganti warna.

     GELAP  — "Jaringan Kluster": node gelap yang saling menyambung, denyut
              cahaya berjalan di kabel. Terbaca sebagai ruang gelap
              bertitik cahaya.

     TERANG — "Aliran Konstelasi": garis lengkung besar yang mengalir
              seperti pita, plus butiran yang mengorbit. Dipilih karena
              di atas latar terang, BENTUK lebih terbaca daripada cahaya —
              jadi mediumnya garis tebal ber-gradient, bukan titik.

   Keduanya:
     - digerakkan setInterval (bukan requestAnimationFrame, yang mati saat
       tab tidak fokus — persis saat presentasi),
     - berhenti sendiri saat prefers-reduced-motion,
     - berbagi satu kanvas, ditukar saat tema berganti.
   ==================================================================== */
(function latar() {
  const c = document.getElementById("latar");
  if (!c) return;
  const g = c.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  let timer = null;
  let mode = null;            /* "gelap" | "terang" */
  let t0 = 0;

  /* ---------------------------------------------------------- utilitas */
  function ukur() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = c.clientWidth; H = c.clientHeight;
    c.width = Math.round(W * dpr);
    c.height = Math.round(H * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  const acak = (a, b) => a + Math.random() * (b - a);
  const cssVar = (n) => getComputedStyle(document.documentElement)
    .getPropertyValue(n).trim();

  /* ==================================================================
     MODE GELAP — jaringan node + denyut
     ================================================================== */
  const gelap = {
    titik: [], denyut: [], jarak: 138,

    siapkan() {
      this.titik = [];
      this.denyut = [];
      const n = Math.round(Math.min(52, (W * H) / 26000));
      for (let i = 0; i < n; i++) {
        this.titik.push({
          x: Math.random() * W, y: Math.random() * H,
          vx: acak(-0.09, 0.09), vy: acak(-0.09, 0.09),
          r: acak(1, 2.4),
        });
      }
    },

    gambar(dt) {
      const warnaGaris = cssVar("--jaring") || "rgba(120,190,255,.34)";
      const warnaTitik = cssVar("--jaring-titik") || "rgba(150,205,255,.7)";
      g.clearRect(0, 0, W, H);

      for (const p of this.titik) {
        p.x += p.vx * (dt / 16);
        p.y += p.vy * (dt / 16);
        if (p.x < -30) p.x = W + 30;
        if (p.x > W + 30) p.x = -30;
        if (p.y < -30) p.y = H + 30;
        if (p.y > H + 30) p.y = -30;
      }

      g.lineWidth = 1;
      for (let i = 0; i < this.titik.length; i++) {
        for (let j = i + 1; j < this.titik.length; j++) {
          const dx = this.titik[i].x - this.titik[j].x;
          const dy = this.titik[i].y - this.titik[j].y;
          const d2 = dx * dx + dy * dy;
          if (d2 > this.jarak * this.jarak) continue;
          const a = 1 - Math.sqrt(d2) / this.jarak;
          g.globalAlpha = a * a;
          g.strokeStyle = warnaGaris;
          g.beginPath();
          g.moveTo(this.titik[i].x, this.titik[i].y);
          g.lineTo(this.titik[j].x, this.titik[j].y);
          g.stroke();
        }
      }

      g.globalAlpha = 1;
      g.fillStyle = warnaTitik;
      for (const p of this.titik) {
        g.beginPath(); g.arc(p.x, p.y, p.r, 0, 6.283); g.fill();
      }

      if (Math.random() < 0.01 && this.denyut.length < 8 && this.titik.length > 1) {
        const a = Math.floor(Math.random() * this.titik.length);
        let b = Math.floor(Math.random() * this.titik.length);
        if (a !== b) this.denyut.push({ a, b, t: 0, laju: acak(0.005, 0.013) });
      }
      for (let k = this.denyut.length - 1; k >= 0; k--) {
        const dn = this.denyut[k];
        dn.t += dn.laju * (dt / 16);
        const A = this.titik[dn.a], B = this.titik[dn.b];
        if (dn.t >= 1 || !A || !B) { this.denyut.splice(k, 1); continue; }
        const x = A.x + (B.x - A.x) * dn.t;
        const y = A.y + (B.y - A.y) * dn.t;
        g.globalAlpha = 0.9; g.fillStyle = warnaTitik;
        g.beginPath(); g.arc(x, y, 2.2, 0, 6.283); g.fill();
        g.globalAlpha = 0.2;
        g.beginPath(); g.arc(x, y, 7, 0, 6.283); g.fill();
      }
      g.globalAlpha = 1;
    },
  };

  /* ==================================================================
     MODE TERANG — pita aliran + butiran mengorbit

     Di atas latar terang, titik-titik cahaya tidak terbaca. Yang terbaca
     adalah BENTUK: garis tebal ber-gradient yang melengkung pelan, seperti
     pita kaca berwarna. Ditambah butiran yang bergerak di sepanjang pita
     itu supaya ada arah, bukan cuma gambar diam yang bergoyang.
     ================================================================== */
  const terang = {
    pita: [],

    siapkan() {
      this.pita = [];
      const n = 4;
      for (let i = 0; i < n; i++) {
        this.pita.push({
          /* fasa berbeda supaya pita tidak bergerak serempak */
          fasa: (Math.PI * 2 * i) / n + acak(-0.3, 0.3),
          ampx: acak(0.16, 0.30),      /* amplitudo horizontal */
          ampy: acak(0.10, 0.20),
          laju: acak(0.00016, 0.00030),/* sangat lambat: 1 putaran ~ 1 jam */
          tebal: acak(70, 150),
          butir: Array.from({ length: 7 }, () => ({
            s: Math.random(),          /* posisi sepanjang pita 0..1 */
            v: acak(0.00006, 0.00015),
            r: acak(1.6, 3.6),
          })),
        });
      }
    },

    gambar(dt, waktu) {
      g.clearRect(0, 0, W, H);
      /* Sapuan dasar: sangat tipis, tapi memberi "kertas berwarna" supaya
         pita punya sesuatu untuk ditumpangi. Tanpa ini pita melayang di
         atas putih kosong dan tepinya terlihat bergerigi. */
      const dasar = g.createLinearGradient(0, 0, W, H);
      dasar.addColorStop(0, "rgba(13,148,136,0.045)");
      dasar.addColorStop(0.5, "rgba(255,255,255,0)");
      dasar.addColorStop(1, "rgba(124,58,237,0.040)");
      g.fillStyle = dasar;
      g.fillRect(0, 0, W, H);

      const t = waktu;
      const grad = [
        ["rgba(13,148,136,0.52)", "rgba(13,148,136,0.02)"],
        ["rgba(37,99,235,0.46)", "rgba(37,99,235,0.02)"],
        ["rgba(124,58,237,0.40)", "rgba(124,58,237,0.02)"],
        ["rgba(20,184,166,0.36)", "rgba(20,184,166,0.02)"],
      ];

      this.pita.forEach((p, idx) => {
        const geser = t * p.laju + p.fasa;
        const [c1, c2] = grad[idx % grad.length];

        /* Pita = kurva Bezier lebar dengan gradient sepanjang badannya.
           Digambar 3 lapis dengan ketebalan menurun supaya tepinya
           melunak — tanpa ini garisnya terlihat seperti coretan. */
        const buatJalur = () => {
          const y0 = H * (0.18 + idx * 0.22) + Math.sin(geser) * H * p.ampy;
          const y1 = H * (0.30 + idx * 0.22) + Math.cos(geser * 0.8) * H * p.ampy;
          const y2 = H * (0.55 + idx * 0.18) + Math.sin(geser * 1.2 + 1) * H * p.ampy;
          const y3 = H * (0.72 + idx * 0.12) + Math.cos(geser + 2) * H * p.ampy;
          g.beginPath();
          g.moveTo(-W * 0.1, y0);
          g.bezierCurveTo(
            W * 0.30 + Math.sin(geser) * W * p.ampx, y1,
            W * 0.62 + Math.cos(geser * 0.7) * W * p.ampx, y2,
            W * 1.1, y3
          );
        };

        for (const [lipat, alfa] of [[1.6, 0.35], [1.0, 0.7], [0.45, 1]]) {
          buatJalur();
          g.lineWidth = p.tebal * lipat * 0.34;
          g.lineCap = "round";
          g.globalAlpha = alfa * 0.62;
          const lg = g.createLinearGradient(0, 0, W, H);
          lg.addColorStop(0, c1);
          lg.addColorStop(1, c2);
          g.strokeStyle = lg;
          g.stroke();
        }

        /* Butiran bergerak di sepanjang kurva yang sama. Posisinya
           dihitung ulang dari persamaan tadi, jadi selalu menempel pada
           pitanya — bukan mengambang lepas. */
        g.globalAlpha = 0.55;
        g.fillStyle = c1.replace(/0\.\d+\)$/, "0.85)");
        for (const b of p.butir) {
          b.s += b.v * (dt / 16);
          if (b.s > 1) b.s -= 1;
          const s = b.s;
          /* perkiraan titik pada kurva Bezier kubik */
          const y = H * 0.45
            + Math.sin(geser + s * 4) * H * p.ampy * 1.1
            + (s - 0.5) * H * 0.5;
          const x = s * W * 1.15 - W * 0.075;
          g.beginPath();
          g.arc(x, y, b.r, 0, 6.283);
          g.fill();
        }
      });
      g.globalAlpha = 1;
    },
  };

  /* ------------------------------------------------------------ kendali */
  function hentikan() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  function mulai() {
    hentikan();
    const tema = document.documentElement.dataset.tema === "terang"
      ? "terang" : "gelap";
    ukur();
    mode = tema;
    const mesin = tema === "terang" ? terang : gelap;
    mesin.siapkan();

    const kurangi = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (kurangi) {
      /* Satu bingkai statis. Pengguna yang minta gerak dikurangi tidak
         boleh dipaksa melihat animasi berjalan terus. */
      mesin.gambar(16, 1200);
      return;
    }

    let akhir = 0;
    timer = setInterval(() => {
      const sekarang = performance.now();
      const dt = Math.min(64, sekarang - (akhir || sekarang));
      akhir = sekarang;
      mesin.gambar(dt, sekarang);
    }, 40);
  }

  window.__latarMulai = mulai;
  window.addEventListener("resize", () => {
    ukur();
    const mesin = mode === "terang" ? terang : gelap;
    mesin.siapkan();
  });

  mulai();
})();

/* ------------------------------------------------------- tema terang / gelap */
(function tema() {
  const akar = document.documentElement;
  const tombol = document.getElementById("tombol-tema");
  const ikon = document.getElementById("ikon-tema");

  let tanpaTransisi = false;

  function pakai(nama) {
    /* Saat dipanggil dari dalam startViewTransition, kita sudah punya
       animasi sendiri. Transisi CSS bawaan harus dimatikan sebentar,
       kalau tidak warna ikut "mengejar" dan hasilnya terlihat lambat
       dibanding lingkarannya. */
    if (tanpaTransisi) akar.classList.add("tanpa-transisi");
    akar.dataset.tema = nama;
    if (tanpaTransisi) {
      void akar.offsetWidth;              /* paksa hitung ulang gaya */
      akar.classList.remove("tanpa-transisi");
    }
    /* Ikon menunjukkan tindakan BERIKUTNYA, bukan keadaan sekarang:
       saat gelap tampil matahari (untuk pindah ke terang). */
    ikon.textContent = nama === "terang" ? "☾" : "☀";
    try { localStorage.setItem("kluster-tema", nama); } catch (e) {}
    /* Latar DIGANTI, bukan diwarnai ulang: tiap mode punya mesin
       animasinya sendiri (lihat blok latar di atas). */
    if (window.__latarMulai) window.__latarMulai();
  }

  let tersimpan = null;
  try { tersimpan = localStorage.getItem("kluster-tema"); } catch (e) {}
  /* Bawaan: ikuti setelan sistem operasi. */
  const sukaTerang = window.matchMedia("(prefers-color-scheme: light)").matches;
  pakai(tersimpan || (sukaTerang ? "terang" : "gelap"));

  /* ---------------------------------------------------------------
     Ganti tema dengan lingkaran melebar dari tombol.

     Kalau browser tidak punya View Transitions (Safari lama, Firefox
     lama), jatuh ke ganti langsung — lebih baik tanpa animasi daripada
     tombol yang tidak bereaksi.
     --------------------------------------------------------------- */
  let sedangBeralih = false;

  tombol.onclick = () => {
    if (sedangBeralih) return;                 /* cegah klik beruntun */
    const tujuan = akar.dataset.tema === "terang" ? "gelap" : "terang";

    const kotak = tombol.getBoundingClientRect();
    const cx = kotak.left + kotak.width / 2;
    const cy = kotak.top + kotak.height / 2;
    /* Jari-jari harus cukup untuk menutup sudut terjauh layar, bukan
       hanya jarak ke tepi terdekat — kalau kurang, sudut layar tidak
       ikut tersapu dan tema lama tertinggal di pojok. */
    const jari = Math.hypot(
      Math.max(cx, window.innerWidth - cx),
      Math.max(cy, window.innerHeight - cy)
    );

    /* Ikon berputar dulu; teks ikon ditukar di tengah animasi supaya
       pergantiannya tidak terlihat. */
    const kurangi = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!kurangi) {
      ikon.classList.add("tukar");
      setTimeout(() => ikon.classList.remove("tukar"), 340);
    }

    if (!document.startViewTransition || kurangi) {
      pakai(tujuan);
      return;
    }

    sedangBeralih = true;
    tanpaTransisi = false;
    const transisi = document.startViewTransition(() => { pakai(tujuan); });

    transisi.ready.then(() => {
      akar.animate(
        { clipPath: [
            `circle(0px at ${cx}px ${cy}px)`,
            `circle(${jari}px at ${cx}px ${cy}px)`,
        ] },
        { duration: 700, easing: "ease-in-out",
          pseudoElement: "::view-transition-new(root)" }
      );
    }).catch(() => {});

    transisi.finished.catch(() => {}).finally(() => { sedangBeralih = false; });
  };
})();

perbaruiTujuan();
muat();
setInterval(muat, 900);
</script>
</body>
</html>
"""


class Jembatan:
    """HTTP kecil di localhost yang menyambungkan browser ke client.py.

    Semua keadaan disimpan di sini dan dibaca dari Pengguna lewat
    referensi langsung — tidak ada salinan data, jadi tidak ada
    kemungkinan tampilan berbeda dari kenyataan.
    """

    def __init__(self, pengguna, port: int = 0) -> None:
        self.p = pengguna
        self.port = port
        self.id_terakhir = 0
        self.server_http = None

    def pasang(self, port: int = 0) -> int:
        """Nyalakan HTTP di thread latar. Balikkan port yang dipakai."""
        self.port = port
        jembatan = self

        class Penangan(BaseHTTPRequestHandler):
            # Log bawaan mencetak satu baris tiap permintaan; dashboard
            # memanggil tiap 0,9 detik sehingga terminal penuh sampah.
            def log_message(self, *a) -> None:
                pass

            def _json(self, data) -> None:
                isi = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(isi)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(isi)

            def do_GET(self) -> None:
                u = urlparse(self.path)
                if u.path in ("/", "/index.html"):
                    isi = HALAMAN.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(isi)))
                    self.end_headers()
                    self.wfile.write(isi)
                    return

                if u.path == "/api/keadaan":
                    self._json(jembatan.keadaan())
                    return

                if u.path == "/api/kirim":
                    q = parse_qs(u.query)
                    isi = unquote(q.get("isi", [""])[0])[:2000]
                    tujuan = q.get("tujuan", ["semua"])[0][:40] or "semua"
                    jalur = (q.get("jalur", ["TCP"])[0] or "TCP").upper()
                    hasil = jembatan.kirim(isi, tujuan, jalur)
                    self._json(hasil)
                    return

                self.send_error(404)

        httpd = HTTPServer(("127.0.0.1", self.port), Penangan)
        self.port = httpd.server_address[1]
        self.server_http = httpd
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return self.port

    def keadaan(self) -> dict:
        p = self.p
        # Ambil salinan id SEBELUM apa pun diubah. `baru` dihitung dari
        # salinan ini, bukan dari self.id_terakhir yang mungkin sudah
        # dinaikkan oleh permintaan lain (dashboard memanggil tiap 0,9
        # detik, dan permintaan bisa datang bersamaan).
        ambang = self.id_terakhir
        baru = [x for x in p.riwayat if x.nomor > ambang]
        if p.riwayat:
            self.id_terakhir = max(x.nomor for x in p.riwayat)

        return {
            "nama": p.nama,
            "node": p.node,
            "host": p.host,
            "port": p.port,
            # `hidup` = benar-benar tersambung ke server, BUKAN sekadar
            # program masih jalan. Dulu memakai p.jalan, sehingga UI
            # menampilkan "tersambung" padahal server sudah mati.
            "hidup": bool(p.sock) and not p.sambung_terputus,
            "protokol_udp": p.sock_udp is not None,
            "online": list(p.online),
            # Pengirim yang PERNAH mengirim tapi mungkin sudah keluar.
            # Tanpa ini, orang yang baru saja mengirim pesan lalu menutup
            # programnya langsung hilang dari sidebar — padahal namanya
            # masih ada di percakapan dan pesannya mungkin perlu dibalas.
            "pernah": sorted({x.dari for x in p.riwayat if x.dari != p.nama}),
            "idTerakhir": self.id_terakhir,
            # Riwayat LENGKAP (bukan cuma yang baru). Dashboard memakainya
            # saat halaman dimuat ulang: tanpa ini, me-refresh browser
            # mengosongkan percakapan padahal riwayatnya masih ada.
            "semua": [{"nomor": x.nomor, "dari": x.dari, "ke": x.ke,
                       "isi": x.isi, "waktu": x.waktu,
                       "protokol": x.protokol} for x in p.riwayat[-120:]],
            "baru": [{"dari": x.dari, "ke": x.ke, "isi": x.isi,
                      "waktu": x.waktu, "protokol": x.protokol}
                     for x in baru],
        }

    def kirim(self, isi: str, tujuan: str, jalur: str) -> dict:
        if not isi.strip():
            return {"ok": False, "pesan": "pesannya masih kosong"}
        if jalur == "UDP":
            # Pesan UDP di protokol hanya mendukung siaran; tujuan pribadi
            # lewat UDP belum ada di server, jadi ditolak dengan jelas
            # daripada diam-diam dikirim ke semua orang.
            if tujuan != "semua":
                return {"ok": False,
                        "pesan": "pesan pribadi hanya lewat jalur TCP"}
            berhasil = self.p.kirim_udp(
                {"tipe": "pesan-udp", "dari": self.p.nama, "isi": isi})
            if berhasil:
                # Catat SEKALI dengan `dari` = nama sendiri, supaya tampil
                # sebagai bubble "Kamu" di sisi kanan. Pantulan dari server
                # nanti otomatis diabaikan oleh dedup di catat_riwayat.
                from datetime import datetime
                self.p.catat_riwayat(
                    self.p.nama, "semua", isi,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "UDP")
            return ({"ok": True} if berhasil
                    else {"ok": False, "pesan": "jalur UDP belum siap"})

        berhasil = self.p.kirim({"tipe": "pesan", "ke": tujuan, "isi": isi})
        if not berhasil:
            return {"ok": False, "pesan": "sambungan TCP terputus"}
        # Pesan yang SAYA kirim tidak pernah dipantulkan balik oleh server
        # (pengirim sengaja tidak menerima gemanya sendiri). Kalau tidak
        # dicatat di sini, UI tidak akan pernah menampilkan pesan sendiri —
        # pengguna mengira pesannya gagal padahal sudah sampai.
        from datetime import datetime
        self.p.catat_riwayat(self.p.nama, tujuan, isi,
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "TCP")
        return {"ok": True}
