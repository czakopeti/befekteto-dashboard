@echo off
title Befektető Dashboard – Automatikus Frissítés
color 0A
cls
echo.
echo  ╔════════════════════════════════════════════════════╗
echo  ║     BEFEKTETŐ DASHBOARD – Automatikus Frissítés    ║
echo  ║     Kézi adatbevitel: NEM szükséges               ║
echo  ╚════════════════════════════════════════════════════╝
echo.

REM Könyvtár beállítás
cd /d "%~dp0"

REM Python ellenőrzés
python --version >nul 2>&1
if errorlevel 1 (
  echo  [HIBA] Python nincs telepítve!
  echo  Töltsd le: https://python.org/downloads
  echo  Telepítéskor pipáld be: "Add Python to PATH"
  pause
  exit /b 1
)

echo  [1/3] Csomagok ellenőrzése...
pip install yfinance requests pandas openpyxl --quiet --disable-pip-version-check
echo  [2/3] Adatok letöltése (SPX, VIX, EPS, FRED, CBOE, AAII)...
echo  [2/3] Ez kb. 30-60 másodpercet vesz igénybe...
echo.

python auto_update.py

if errorlevel 1 (
  echo.
  echo  [HIBA] A script hibával futott le. Ellenőrizd:
  echo  - Internet kapcsolat OK?
  echo  - FRED API kulcs beállítva az auto_update.py-ban?
  pause
  exit /b 1
)

echo.
echo  ════════════════════════════════════════════════════
echo  Következő frissítés: jövő pénteken
echo  ════════════════════════════════════════════════════
echo.
timeout /t 3 >nul
