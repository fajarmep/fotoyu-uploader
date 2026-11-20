@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Publish / Rebuild DMG via GitHub Actions

echo ============================================================
echo   FotoYu Uploader - Publish / Rebuild DMG (macOS) via GitHub
echo ============================================================
echo.

REM ---------- 1. cek git ----------
where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] "git" tidak ditemukan. Install Git for Windows: https://git-scm.com/download/win
  pause
  exit /b 1
)

REM ---------- 2. cek / pasang gh ----------
where gh >nul 2>nul
if errorlevel 1 (
  echo [1/8] GitHub CLI ^(gh^) belum ada - memasang via winget...
  winget install --id GitHub.cli -e --accept-package-agreements --accept-source-agreements
  set "PATH=%PATH%;%ProgramFiles%\GitHub CLI"
)
where gh >nul 2>nul
if errorlevel 1 (
  echo [ERROR] "gh" masih belum terpasang. Install manual dari https://cli.github.com lalu jalankan lagi.
  pause
  exit /b 1
)

REM ---------- 3. login ----------
gh auth status >nul 2>nul
if errorlevel 1 (
  echo [2/8] Login ke GitHub ^(cukup SEKALI^)...
  gh auth login --hostname github.com --git-protocol https --scopes repo,workflow --web
)
gh auth status >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Belum login ke GitHub.
  pause
  exit /b 1
)

for /f "usebackq delims=" %%u in (`gh api user --jq .login`) do set "GHUSER=%%u"
echo      Login sebagai: !GHUSER!

set "REPO=fotoyu-uploader"
set "REPOin="
set /p "REPOin= [3/8] Nama repo GitHub [Enter = fotoyu-uploader]: "
if not "!REPOin!"=="" set "REPO=!REPOin!"
set "FULL=!GHUSER!/!REPO!"

REM ---------- 4. bersihkan + push isi folder terbaru ----------
echo [4/8] Menyiapkan repo publik: https://github.com/!FULL!
echo      Membersihkan riwayat git lokal ^(agar tidak ada file besar ikut^)...
if exist ".git" (
  attrib -h -s /s /d ".git\*" >nul 2>nul
  rmdir /s /q ".git"
)
git init >nul
git add -A
git -c user.email="bot@local" -c user.name="bot" commit -m "build %RANDOM%" >nul 2>nul
git branch -M main

gh repo view "!FULL!" >nul 2>nul
if errorlevel 1 (
  gh repo create "!FULL!" --public --source=. --remote=origin --push
) else (
  git remote remove origin >nul 2>nul
  git remote add origin "https://github.com/!FULL!.git"
  git push -u origin main --force
)
gh repo edit "!FULL!" --default-branch main >nul 2>nul

REM ---------- 5. jalankan workflow (rebuild dari kode terbaru) ----------
echo [5/8] Menjalankan build di GitHub (runner macOS)...
set "TRIGGERED="
for /l %%i in (1,1,12) do (
  if not defined TRIGGERED (
    gh workflow run "build.yml" --repo "!FULL!" >nul 2>nul
    if !errorlevel! equ 0 (
      set "TRIGGERED=1"
    ) else (
      echo      Menunggu workflow terdaftar... ^(%%i/12^)
      ping -n 7 127.0.0.1 >nul
    )
  )
)
if not defined TRIGGERED (
  echo [ERROR] Tidak bisa menjalankan workflow. Cek https://github.com/!FULL!/actions
  pause
  exit /b 1
)

echo      Menunggu workflow mulai...
ping -n 16 127.0.0.1 >nul
set "RUNID="
for /f "usebackq delims=" %%r in (`gh run list --repo "!FULL!" --workflow "build.yml" --event workflow_dispatch --limit 1 --json databaseId --jq ".[0].databaseId"`) do set "RUNID=%%r"
if "!RUNID!"=="" (
  echo [ERROR] Workflow run tidak ditemukan.
  pause
  exit /b 1
)
echo      Menunggu build selesai (biasanya 5-15 menit)...
echo      URL run: https://github.com/!FULL!/actions/runs/!RUNID!
gh run watch "!RUNID!" --repo "!FULL!" --exit-status
set "RC=!errorlevel!"
set "CONC="
for /f "usebackq delims=" %%c in (`gh run view "!RUNID!" --repo "!FULL!" --json conclusion --jq .conclusion`) do set "CONC=%%c"
if not "!RC!"=="0" (
  echo.
  echo [ERROR] Build tidak sukses. Status akhir: !CONC!
  echo         Log lengkap : https://github.com/!FULL!/actions/runs/!RUNID!
  if "!CONC!"=="cancelled" (
    echo         "cancelled" = run DIBATALKAN ^(bukan error program^).
    echo         Kemungkinan: dibatalkan manual, atau repo/commit berubah saat run berjalan.
  )
  echo         Coba lagi   : gh run rerun !RUNID! --repo !FULL!
  echo         Atau jalankan ulang Publish_DMG.bat.
  pause
  exit /b 1
)

REM ---------- 6. unduh DMG ----------
echo [6/8] Mengunduh hasil DMG...
rmdir /s /q "_dl" >nul 2>nul
gh run download "!RUNID!" --repo "!FULL!" -n "Portable-MacOS" -D "_dl"
set "DMG="
for /r "_dl" %%f in (*.dmg) do set "DMG=%%f"
if not defined DMG (
  echo [ERROR] DMG tidak ditemukan di artifact.
  pause
  exit /b 1
)
copy /y "!DMG!" "FotoYu Uploader.dmg" >nul

REM ---------- 7/8. release publik (link download, versi bertanggal) ----------
for /f "usebackq delims=" %%t in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy.MM.dd-HHmmss'"`) do set "TAG=v%%t"
echo [7/8] Membuat Release versi !TAG! ...
gh release create "!TAG!" "FotoYu Uploader.dmg" --repo "!FULL!" --title "FotoYu Uploader !TAG!" --notes "DMG (build otomatis)." --clobber

set "ASSETURL="
for /f "usebackq delims=" %%u in (`gh release view "!TAG!" --repo "!FULL!" --json assets --jq ".assets[0].url"`) do set "ASSETURL=%%u"

echo.
echo ============================================================
echo  SELESAI.
echo.
echo  FILE DMG LOKAL (paling mudah - langsung di PC ini):
echo    %~dp0FotoYu Uploader.dmg
if exist "%~dp0FotoYu Uploader.dmg" (echo    [OK] file ada) else (echo    [X] file TIDAK ada)
echo.
echo  LINK DOWNLOAD (GitHub):
if defined ASSETURL (
  echo    !ASSETURL!
) else (
  echo    https://github.com/!FULL!/releases/tag/!TAG!
)
echo    Halaman semua versi: https://github.com/!FULL!/releases
echo ============================================================
echo.
pause
