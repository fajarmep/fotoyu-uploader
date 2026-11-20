#!/bin/bash
# ============================================================
#   FotoYu Uploader - SETUP & BUILD (macOS Portable)
#   Klik dua kali file ini di macOS (setelah: chmod +x)
#   Hasil: folder "Portable MacOS" berisi "FotoYu Uploader.dmg"
# ============================================================
cd "$(dirname "$0")"

pause_exit() { [ -t 0 ] && read -n 1 -s -r -p "Tekan tombol apa saja untuk keluar..."; echo; return 0; }
fail() { echo "[ERROR] $1"; pause_exit; exit 1; }

echo "============================================================"
echo "  FotoYu Uploader - SETUP & BUILD (macOS Portable)"
echo "============================================================"

command -v python3 >/dev/null 2>&1 || fail "python3 tidak ditemukan. Install Python 3 dari https://www.python.org (versi python.org sudah termasuk tkinter)."

# --- Tentukan arsitektur target ---
# x86_64 = kompatibel Intel native + Apple Silicon (via Rosetta 2).
# (universal2 tidak bisa karena Pillow hanya menyediakan wheel per-arsitektur.)
NATIVE_ARCH="$(uname -m)"
RUN=""
if [ "$NATIVE_ARCH" = "arm64" ]; then
  if /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
    RUN="/usr/bin/arch -x86_64"
    echo "[i] Rosetta terdeteksi -> build x86_64 (jalan di Intel & Apple Silicon)."
  else
    echo "[i] Rosetta tidak ada -> build native arm64 (hanya Apple Silicon)."
  fi
else
  echo "[i] Build native x86_64."
fi

if [ ! -x ".build_venv/bin/python" ]; then
  echo "[1/6] Membuat virtual environment..."
  $RUN python3 -m venv .build_venv || fail "gagal membuat venv."
else
  echo "[1/6] Virtual environment sudah ada."
fi
VPY=".build_venv/bin/python"

echo "[2/6] Memasang dependensi (pillow, requests, pywebview, pyinstaller)..."
$RUN "$VPY" -m pip install --upgrade pip
$RUN "$VPY" -m pip install pillow requests pywebview pyinstaller || fail "gagal install dependensi."

echo "[3/6] Menyiapkan ikon (.icns)..."
SRC_ICON=""
if [ -f icon.png ]; then SRC_ICON="icon.png"; elif [ -f app.png ]; then SRC_ICON="app.png"; fi
if [ -n "$SRC_ICON" ]; then
  ICONSET=".app.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for s in 16 32 64 128 256 512; do
    sips -z $s $s "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
    d=$((s*2))
    sips -z $d $d "$SRC_ICON" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$ICONSET" -o app.icns >/dev/null 2>&1
  rm -rf "$ICONSET"
fi

echo "[4/6] Membangun aplikasi (PyInstaller, tanpa UPX)..."
OPTS=""
if [ -f app.icns ]; then OPTS="--icon app.icns --add-data app.icns:."; fi
if [ -f icon.png ]; then OPTS="$OPTS --add-data icon.png:."; fi
$RUN "$VPY" -m PyInstaller --noconfirm --clean --windowed --onedir --noupx --name "FotoYu Uploader" $OPTS fotoyu_gui.py || fail "gagal build PyInstaller."

APPEXE="dist/FotoYu Uploader.app/Contents/MacOS/FotoYu Uploader"
if [ -f "$APPEXE" ]; then
  echo "      Arsitektur hasil build: $(lipo -archs "$APPEXE" 2>/dev/null)"
fi

echo "[5/6] Menyusun 'Portable MacOS' dan membuat DMG..."
rm -rf "Portable MacOS"; mkdir -p "Portable MacOS"
STAGE=".dmg_stage"; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "dist/FotoYu Uploader.app" "$STAGE/"
cp installer_macos.command "$STAGE/Install FotoYu Uploader.command"
chmod +x "$STAGE/Install FotoYu Uploader.command"
if [ -f "Cara Pakai.txt" ]; then cp "Cara Pakai.txt" "$STAGE/Cara Pakai.txt"; fi
hdiutil create -volname "FotoYu Uploader" -srcfolder "$STAGE" -ov -format UDZO "Portable MacOS/FotoYu Uploader.dmg" || fail "gagal membuat DMG."
rm -rf "$STAGE"

echo "[6/6] Selesai!"
echo "Hasil: $(pwd)/Portable MacOS/FotoYu Uploader.dmg"
if [ -t 0 ]; then
  open "Portable MacOS"
  read -n 1 -s -r -p "Tekan tombol apa saja untuk keluar..."
fi
