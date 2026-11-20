#!/bin/bash
# ============================================================
#   Installer FotoYu Uploader (dijalankan dari dalam DMG)
#   - Cek prasyarat (macOS, arsitektur aplikasi)
#   - Memilih lokasi yang BISA ditulis
#   - Membuat folder "Portable MacOS" + menyalin aplikasi
#   - Membuat shortcut di Desktop
#   (DMG hanya berisi aplikasi terkompilasi - TANPA file sumber .py)
# ============================================================
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/FotoYu Uploader.app"
APPEXE="$APP/Contents/MacOS/FotoYu Uploader"

pause_exit() {
  read -n 1 -s -r -p "Tekan tombol apa saja untuk keluar..."
  echo
}

echo "============================================================"
echo "  FotoYu Uploader - Pemasangan (macOS)"
echo "============================================================"
echo

# ---------- 1. Info sistem ----------
MVER="$(sw_vers -productVersion 2>/dev/null)"
ARCH_SYS="$(uname -m)"
echo "  macOS          : ${MVER:-?}"
echo "  Arsitektur PC  : $ARCH_SYS"

# ---------- 2. Cek aplikasi ----------
if [ ! -d "$APP" ]; then
  echo "[ERROR] 'FotoYu Uploader.app' tidak ditemukan di DMG ini."
  echo "        DMG rusak / belum di-build dengan benar."
  pause_exit; exit 1
fi
chmod +x "$APPEXE" 2>/dev/null
APP_ARCH="$(lipo -archs "$APPEXE" 2>/dev/null)"
echo "  Aplikasi        : ada (${APP_ARCH:-unknown})"

if [ -n "$APP_ARCH" ] && ! echo "$APP_ARCH" | grep -q "$ARCH_SYS"; then
  echo
  echo "[PERINGATAN] Arsitektur aplikasi ($APP_ARCH) tidak memuat '$ARCH_SYS'."
  echo "             Aplikasi Intel di Apple Silicon butuh Rosetta 2 (macOS akan menawarkan)."
  echo "             Bila gagal, minta DMG versi UNIVERSAL."
fi
echo

# ---------- 3. Pilih lokasi (validasi bisa ditulis) ----------
while true; do
  DEST_ROOT="$(osascript -e 'try
POSIX path of (choose folder with prompt "Pilih folder yang BISA DITULIS untuk memasang FotoYu Uploader (contoh: Documents)")
end try' 2>/dev/null)"
  if [ -z "$DEST_ROOT" ]; then
    echo "Pemasangan dibatalkan."
    pause_exit; exit 0
  fi
  DEST_ROOT="${DEST_ROOT%/}"
  DEST="$DEST_ROOT/Portable MacOS"
  if mkdir -p "$DEST" 2>/dev/null && touch "$DEST/.wtest" 2>/dev/null; then
    rm -f "$DEST/.wtest"
    break
  fi
  echo "[ERROR] Tidak bisa menulis ke: $DEST_ROOT"
  echo "        Folder dilindungi sistem. Pilih lokasi lain (Documents/Home Anda atau /Users/Shared)."
  osascript -e 'display alert "Lokasi tidak bisa ditulis" message "Folder tersebut dilindungi sistem. Silakan pilih lokasi lain, misalnya folder Documents di Home Anda." as warning' >/dev/null 2>&1
done

# ---------- 4. Salin aplikasi ----------
rm -rf "$DEST/FotoYu Uploader.app"
if ! cp -R "$APP" "$DEST/"; then
  echo "[ERROR] Gagal menyalin aplikasi ke: $DEST"
  pause_exit; exit 1
fi
xattr -dr com.apple.quarantine "$DEST/FotoYu Uploader.app" 2>/dev/null
# folder kerja internal kompresi (Folder Sumber & Arsip dipilih pengguna di aplikasi)
mkdir -p "$DEST/Compress"

# ---------- 5. Shortcut Desktop ----------
ln -sfn "$DEST/FotoYu Uploader.app" "$HOME/Desktop/FotoYu Uploader" 2>/dev/null

# ---------- 6. Laporan ----------
echo
if [ -d "$DEST/FotoYu Uploader.app" ]; then
  echo "Selesai! Terpasang di : $DEST"
  echo "Shortcut             : Desktop/FotoYu Uploader"
  open "$DEST"
else
  echo "[ERROR] Pemasangan gagal. Folder tujuan tidak ditemukan: $DEST"
fi
pause_exit
