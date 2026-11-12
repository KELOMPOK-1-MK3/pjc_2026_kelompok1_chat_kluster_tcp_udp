@echo off
chcp 65001 >nul
title Kluster - PC03 (Sidiq)
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   Python belum terpasang di laptop ini.
  echo   Unduh dari https://www.python.org/downloads/
  echo   Saat memasang, CENTANG "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

echo  Pilih cara pakai:
echo    1  Antarmuka web di browser  (disarankan)
echo    2  Terminal teks
echo.
set /p pilih="  Pilihan [1]: "
if "%pilih%"=="2" (
  python client.py
) else (
  python client.py --web
)
if errorlevel 1 (
  echo.
  echo   Program berhenti. Baca CARA-PAKAI.txt di folder ini.
  echo.
  pause
)
