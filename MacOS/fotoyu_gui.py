import webview
import webbrowser
from PIL import ImageOps
from concurrent.futures import ThreadPoolExecutor, as_completed
"""
FotoYu Uploader - Auto Compress & Upload (GUI Modern)
=====================================================
Memantau folder Upload -> kompres otomatis (<= 500KB, sisi terpanjang 1440px,
iterasi quality) -> upload ke Fotoyu -> file yang berhasil diupload DIHAPUS dari
folder Compress. File asli tetap diamankan di folder Complete.

Fitur Utama:
- Ultra-fast multi-threaded compression & S3 upload (hingga 2.000 file sekaligus).
- Smart anti-duplicate fingerprinting (mencegah kompres/upload ulang foto yang sama).
- Modern CLI Console Log (tampilan terminal informatif, rapi, bisa disembunyikan/tampilkan).
- Mode Upload berbasis Combobox dropdown (Semua/Ganjil/Genap) untuk Windows & macOS.
- Statistik Portofolio FotoYu (Jumlah Foto, Akurasi 5+, Akurasi 5-, Konfirm, Terjual per tanggal).

Tampilan memakai widget ttk bawaan sehingga menyesuaikan OS (Windows/macOS)
secara otomatis. Butuh: pip install pillow requests (tkinter bawaan Python).
"""

import os
import io
import re
import sys
import glob
import json
import time
import queue
import base64
import shutil
import hashlib
import platform
import threading
import subprocess
import csv
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, unquote
from datetime import datetime, timezone, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageOps

# ============================================================
#  OS / TAMPILAN
# ============================================================
OS_NAME = platform.system()
IS_WIN = OS_NAME == "Windows"
IS_MAC = OS_NAME == "Darwin"

if IS_WIN:
    FONT_NAME = "Segoe UI"
    FONT_CLI_NAME = "Consolas"
elif IS_MAC:
    FONT_NAME = "Helvetica Neue"
    FONT_CLI_NAME = "Menlo"
else:
    FONT_NAME = "DejaVu Sans"
    FONT_CLI_NAME = "Courier"

FONT = (FONT_NAME, 10)
FONT_BOLD = (FONT_NAME, 10, "bold")
FONT_TITLE = (FONT_NAME, 13, "bold")
FONT_BIG = (FONT_NAME, 18, "bold")
FONT_SM = (FONT_NAME, 9)
FONT_CLI = (FONT_CLI_NAME, 9)

C_OK = "#16a34a"
C_FAIL = "#dc2626"
C_SKIP = "#d97706"

# ============================================================
#  PATH & KONFIGURASI
# ============================================================
if getattr(sys, "frozen", False):
    _exe = os.path.abspath(sys.executable)
    if ".app/contents/macos" in _exe.replace("\\", "/").lower():
        _d = _exe
        for _ in range(4):
            _d = os.path.dirname(_d)
        BASE_DIR = _d
    else:
        BASE_DIR = os.path.dirname(_exe)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _is_dir_writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        t = os.path.join(path, ".writetest")
        with open(t, "w") as f:
            f.write("1")
        os.remove(t)
        return True
    except Exception:
        return False

# Jika folder aplikasi dipasang di direktori sistem yang read-only (misal /Applications di macOS),
# gunakan folder Documents user sebagai basis agar aplikasi tetap bisa berjalan lancar tanpa crash.
if not _is_dir_writable(BASE_DIR):
    BASE_DIR = os.path.join(os.path.expanduser("~"), "Documents", "FotoYu Uploader")
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
    except Exception:
        pass

UPLOAD_DIR = ""
COMPRESS_DIR = os.path.join(BASE_DIR, "Compress")
COMPLETE_DIR = ""
def find_data_file(filename):
    candidates = [
        os.path.join(BASE_DIR, filename),
        os.path.join(os.path.dirname(BASE_DIR), filename),
        os.path.join(os.getcwd(), filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return os.path.join(BASE_DIR, filename)

CONFIG_PATH = find_data_file("config.json")
PRESETS_PATH = find_data_file("presets.json")
HISTORY_PATH = find_data_file("history_uploaded.json")

IMAGE_EXTS = (".jpg", ".jpeg", ".png")
IMAGE_EXTS_SET = {".jpg", ".jpeg", ".png"}


def get_icon_path():
    """Mencari path icon (icon.png / app.ico / app.png) baik saat skrip Python maupun PyInstaller frozen."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        for name in ("icon.png", "app.ico", "app.png"):
            candidates.append(os.path.join(sys._MEIPASS, name))
    for name in ("icon.png", "app.ico", "app.png"):
        candidates.append(os.path.join(BASE_DIR, name))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("icon.png", "app.ico", "app.png"):
        candidates.append(os.path.join(script_dir, name))
    parent_dir = os.path.dirname(script_dir)
    for name in ("icon.png", "app.ico", "app.png"):
        candidates.append(os.path.join(parent_dir, name))

    for p in candidates:
        if os.path.exists(p):
            return p
    return None

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "price": "",
    "location_name": "",
    "longitude": "",
    "latitude": "",
    "description": "",
    "mode": "all",
    "upload_dir": "",
    "complete_dir": "",
    "currency_id": "MR9Xdjv70WpeODE8DEPLAyb8xVBQzq31",
    "tag_id": "ezgM1YO8Lo3nVGbPeRkx50lbAdXQRvNK",
}

# --- Aturan kompresi (I8V=1440, cR7=95 ultra-high-quality start, target <= 500 KB) ---
MAX_OUTPUT_BYTES = 500 * 1024
I8V_MAX_DIMENSION = 1440
cR7_START_QUALITY = 95
cR7_MIN_QUALITY = 20
Dmf_MAX_INPUT_BYTES = 50 * (2 ** 20)
JTH_MAX_INPUT_PIXELS = 50 * 10 ** 6

# --- Concurrency & Burst Shutter Handling ---
CPU_WORKERS = max(2, min(8, (os.cpu_count() or 4)))
UPLOAD_WORKERS = 10
COMPRESS_BULK_MAX = 2000
UPLOAD_BULK_MAX = 2000
CREATION_CHUNK = 50
COMPRESS_FAIL_LIMIT = 3
UPLOAD_FAIL_LIMIT = 3
POLL_INTERVAL = 1
FILE_STABLE_AGE = 1.5

BASE_URL = "https://api.fotoyu.com"
TIMEOUT = 60
HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://www.fotoyu.com/",
}


# ============================================================
#  UTIL
# ============================================================
def ensure_dirs():
    """Hanya membuat satu folder kerja internal aplikasi yaitu folder Compress."""
    try:
        os.makedirs(COMPRESS_DIR, exist_ok=True)
    except Exception:
        pass


def format_idr(amount):
    """Format angka rupiah rapi: Rp 35.000."""
    try:
        n = int(round(float(amount)))
        return f"Rp {n:,}".replace(",", ".")
    except Exception:
        return "Rp 0"


def format_eta(seconds):
    """Format estimasi waktu selesai secara ringkas (mis. 1j 12m, 3m 20d, 45d)."""
    try:
        sec = int(max(0, round(seconds)))
        if sec >= 3600:
            h = sec // 3600
            m = (sec % 3600) // 60
            return f"{h}j {m}m"
        if sec >= 60:
            m = sec // 60
            s = sec % 60
            return f"{m}m {s}d"
        return f"{sec}d"
    except Exception:
        return "-"


def format_bytes_human(num_bytes):
    """Format ukuran byte ke KB / MB / GB yang mudah dibaca."""
    try:
        v = float(num_bytes)
        if v >= 1024 ** 3:
            return f"{v / (1024 ** 3):.2f} GB"
        if v >= 1024 ** 2:
            return f"{v / (1024 ** 2):.1f} MB"
        if v >= 1024:
            return f"{v / 1024:.1f} KB"
        return f"{int(v)} B"
    except Exception:
        return "-"


def parse_storage_data(raw):
    """Parse defensif hasil endpoint /gs/v1/creators/storage menjadi dict info storage terstruktur."""
    if not isinstance(raw, dict):
        return {"used": None, "limit": None, "pct": 0.0, "text": "", "used_str": "", "limit_str": ""}
    res = raw.get("result", raw)
    if isinstance(res, dict) and isinstance(res.get("data"), dict):
        res = res["data"]
    if not isinstance(res, dict):
        return {"used": None, "limit": None, "pct": 0.0, "text": "", "used_str": "", "limit_str": ""}

    def pick_num(keys):
        for k in keys:
            val = res.get(k)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val)
                except ValueError:
                    pass
        return None

    used = pick_num(("used_storage", "used", "storage_used", "used_size", "used_bytes", "current_usage", "usage"))
    limit = pick_num(("limit_storage", "limit", "max_storage", "storage_limit", "total", "total_storage", "quota", "max_size", "limit_bytes", "capacity"))
    bonus = pick_num(("bonus_storage", "bonus", "extra_storage"))
    if limit is not None and bonus is not None and bonus > 0:
        limit += bonus
    pct = pick_num(("percentage", "used_percentage", "percent"))

    unit = str(res.get("unit") or "").upper()
    def fmt_val(v):
        if v is None:
            return ""
        if unit in ("GB", "MB", "TB"):
            return f"{v:.2f} {unit}"
        if v > 10000:
            return format_bytes_human(v)
        return f"{v:.2f} GB"

    used_str = fmt_val(used)
    limit_str = fmt_val(limit)
    calc_pct = 0.0

    if used is not None and limit is not None and limit > 0:
        if pct is None:
            calc_pct = (used / limit) * 100.0
        else:
            calc_pct = float(pct)
        calc_pct = min(100.0, max(0.0, calc_pct))
        txt = f"{used_str} / {limit_str} ({calc_pct:.1f}%)"
    elif used is not None:
        txt = f"{used_str} terpakai"
    elif limit is not None:
        txt = f"Kuota: {limit_str}"
    else:
        txt = ""

    return {
        "used": used,
        "limit": limit,
        "pct": calc_pct,
        "used_str": used_str,
        "limit_str": limit_str,
        "text": txt,
    }


def parse_storage_summary(raw):
    """Kompatibilitas mundur: mengembalikan teks string ringkas storage."""
    return parse_storage_data(raw).get("text", "")


def parse_fee_summary(raw, price):
    """Parse defensif hasil /payment/v2/transactions/fee -> (net_amount, fee_amount)."""
    if not isinstance(raw, dict):
        return None, None
    res = raw.get("result", raw)
    if isinstance(res, dict) and isinstance(res.get("data"), dict):
        res = res["data"]
    if not isinstance(res, dict):
        return None, None

    net = None
    fee = None

    # 1. Parse dari struktur real FotoYu API (/payment/v2/transactions/fee)
    p_dict = res.get("price")
    if isinstance(p_dict, dict):
        for ch in ("web", "google", "apple"):
            c = p_dict.get(ch)
            if isinstance(c, dict):
                nc = c.get("net_creator")
                if isinstance(nc, dict) and isinstance(nc.get("nominal"), (int, float)):
                    net = float(nc["nominal"])
                    break
                elif isinstance(c.get("nominal"), (int, float)) and net is None:
                    net = float(c["nominal"])

    f_dict = res.get("fotoyu_fee") or res.get("platform_fee") or res.get("service_fee")
    if isinstance(f_dict, dict):
        for ch in ("web", "google", "apple"):
            c = f_dict.get(ch)
            if isinstance(c, dict):
                nom = c.get("nominal")
                if isinstance(nom, (int, float)):
                    fee = float(nom)
                    break

    # 2. Fallback parser untuk format flat
    def pick_num(keys):
        for k in keys:
            val = res.get(k)
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                try:
                    return float(val)
                except ValueError:
                    pass
        return None

    if net is None:
        net = pick_num(("net_amount", "creator_amount", "creator_earning", "earning", "net_price", "net", "received", "income", "subtotal", "amount_received"))
    if fee is None:
        fee = pick_num(("total_fee", "fee", "platform_fee", "admin_fee", "service_fee", "fee_amount", "deduction", "fotoyu_fee"))
    pct = pick_num(("fee_percentage", "percentage", "rate"))

    if net is None and fee is not None and price > 0:
        net = max(0.0, float(price) - fee)
    elif fee is None and net is not None and price > 0:
        fee = max(0.0, float(price) - net)
    elif net is None and fee is None and pct is not None and price > 0:
        fee = float(price) * (pct / 100.0 if pct > 1 else pct)
        net = max(0.0, float(price) - fee)
    elif net is None and fee is None and price > 0:
        # Fallback default jika endpoint fee offline/gagal: fee FotoYu standar 15.8%
        fee = round(float(price) * 0.158)
        net = max(0.0, float(price) - fee)

    if net is None and fee is None:
        return None, None
    return net, fee


def extract_fototree_name(item):
    """Mengekstrak nama Fototree dari objek creation atau group secara defensif."""
    if not isinstance(item, dict):
        return ""
    if item.get("fototree"):
        return str(item["fototree"]).strip()
    if item.get("tree_name"):
        return str(item["tree_name"]).strip()
    tags = item.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict):
                tname = t.get("name") or t.get("tree_name")
                if tname:
                    return str(tname).strip()
            elif isinstance(t, str) and t.strip():
                return t.strip()
    nicknames = item.get("tag_nicknames")
    if isinstance(nicknames, list):
        for nick in nicknames:
            if nick and str(nick).strip():
                return str(nick).strip()
    loc = item.get("location")
    if isinstance(loc, dict) and loc.get("name"):
        return str(loc["name"]).strip()
    elif isinstance(loc, str) and loc.strip():
        try:
            loc_data = json.loads(loc)
            if isinstance(loc_data, dict) and loc_data.get("name"):
                return str(loc_data["name"]).strip()
        except Exception:
            pass
        return loc.strip()
    return ""


def get_robofind_label(item):
    """Tentukan status AI Robofind (Selesai / Proses) dari objek creation."""
    if not isinstance(item, dict):
        return "-"
    done = item.get("is_robofind_done")
    status = str(item.get("robofind_status") or "").lower().strip()
    if done is True or status in ("processed", "done", "completed", "success"):
        return "Selesai"
    if done is False or status in ("pending", "processing", "queued", "in_progress", "waiting"):
        return "Proses"
    return "Selesai" if (item.get("face_count", 0) or item.get("found_count", 0)) else "-"


def open_folder(path):
    try:
        if IS_WIN:
            os.startfile(path)  # noqa
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def safe_move_file(src, dest, max_retries=5, delay=0.2):
    """Memindahkan file dengan retry dan otomatis membuat folder tujuan jika belum ada."""
    dest_dir = os.path.dirname(dest)
    if dest_dir and not os.path.isdir(dest_dir):
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception:
            pass
    for attempt in range(max_retries):
        try:
            if dest_dir and not os.path.isdir(dest_dir):
                os.makedirs(dest_dir, exist_ok=True)
            shutil.move(src, dest)
            return True
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(delay)
    return False


def unique_path(directory, filename):
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{i}{ext}")
        i += 1
    return candidate


def list_images(directory):
    try:
        files = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file():
                    if entry.name.startswith("."):
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTS_SET:
                        files.append(entry.path)
        files.sort()
        return files
    except Exception:
        return []


def is_file_stable(path, min_age=FILE_STABLE_AGE):
    try:
        stat = os.stat(path)
        if stat.st_size == 0:
            return False
        if (time.time() - stat.st_mtime) < min_age:
            return False
        with open(path, "rb") as f:
            if stat.st_size > 1024:
                f.seek(-1024, os.SEEK_END)
                _ = f.read(1024)
        return True
    except (OSError, PermissionError):
        return False


def get_file_fingerprint(path):
    try:
        stat = os.stat(path)
        size = stat.st_size
        if size == 0:
            return None
        h = hashlib.md5()
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(32768))
            if size > 65536:
                f.seek(-32768, os.SEEK_END)
                h.update(f.read(32768))
        return f"{size}_{h.hexdigest()}"
    except Exception:
        return None


def get_file_md5(file_path):
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_timestamps(file_path):
    try:
        mtime = os.path.getmtime(file_path)
    except Exception:
        mtime = time.time()
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"), int(mtime)


def flatten_alpha(img):
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img if img.mode == "RGB" else img.convert("RGB")


def quality_sequence(start=cR7_START_QUALITY, min_q=cR7_MIN_QUALITY):
    q = start
    while q >= min_q:
        yield q
        q -= 5 if q > 60 else 10


def trailing_number(filename):
    m = re.findall(r"\d+", os.path.splitext(filename)[0])
    return int(m[-1]) if m else None


def mode_match(filename, index, mode):
    if mode == "all":
        return True
    n = trailing_number(filename)
    if n is None:
        n = index
    return (n % 2 == 1) if mode == "odd" else (n % 2 == 0)


def extract_token(data):
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("token", "access_token", "jwt", "authorization"):
            if data.get(key):
                val = data[key]
                if isinstance(val, str):
                    return val
                r = extract_token(val)
                if r:
                    return r
        for v in data.values():
            r = extract_token(v)
            if r:
                return r
    elif isinstance(data, list):
        for item in data:
            r = extract_token(item)
            if r:
                return r
    return None


def parse_trees(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        tag_id = it.get("tag_id") or it.get("id")
        loc = it.get("location") or {}
        if name and tag_id:
            out.append({
                "name": name,
                "tag_id": tag_id,
                "location_name": loc.get("name") or name,
                "longitude": loc.get("longitude"),
                "latitude": loc.get("latitude"),
            })
    return out


def build_session():
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=0.8,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST", "PUT"]))
    ad = HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=30)
    s.mount("https://", ad)
    s.mount("http://", ad)
    return s


# ============================================================
#  KOMPRESI
# ============================================================
def compress_image(src_path, dst_path):
    try:
        with Image.open(src_path) as img:
            w_orig, h_orig = img.size
            if w_orig * h_orig > JTH_MAX_INPUT_PIXELS:
                return False, None, None, None, None
            longest_orig = max(w_orig, h_orig)
            if longest_orig > I8V_MAX_DIMENSION:
                try:
                    img.draft("RGB", (I8V_MAX_DIMENSION * 2, I8V_MAX_DIMENSION * 2))
                except Exception:
                    pass

            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            img = flatten_alpha(img)
            width, height = img.size
            longest = max(width, height)
            if longest > I8V_MAX_DIMENSION:
                scale = I8V_MAX_DIMENSION / float(longest)
                new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
                img = img.resize(new_size, Image.Resampling.BICUBIC)
                width, height = img.size

            for q in quality_sequence():
                buf = io.BytesIO()
                try:
                    img.save(buf, "JPEG", quality=q)
                    size = buf.tell()
                    if size <= MAX_OUTPUT_BYTES:
                        tmp = dst_path + ".part"
                        with open(tmp, "wb") as f:
                            f.write(buf.getvalue())
                        os.replace(tmp, dst_path)
                        return True, q, size, width, height
                finally:
                    buf.close()
    except Exception:
        pass
    return False, None, None, None, None


# ============================================================
#  API FOTOYU
# ============================================================
class FotoyuApi:
    def __init__(self):
        self.session = build_session()
        self.token = None
        self._lock = threading.Lock()

    def login(self, username, password):
        try:
            with self.session.post(f"{BASE_URL}/gs/v2/auth/login",
                                  json={"identifier": username, "password": password},
                                  headers=HEADERS_BASE, timeout=30) as r:
                if r.status_code == 200:
                    return extract_token(r.json())
        except Exception:
            pass
        return None

    def set_token(self, token):
        with self._lock:
            self.token = token

    def get_token(self):
        with self._lock:
            return self.token

    def tree_search(self, query):
        token = self.get_token()
        if not token:
            return None
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(f"{BASE_URL}/tree/v1/trees/search",
                                 params={"page": 1, "limit": 20, "name": query, "is_upload": True},
                                 headers=headers, timeout=20) as r:
                if r.status_code == 401:
                    self.set_token(None)
                    return None
                if r.status_code == 200:
                    data = r.json()
                    res = data.get("result", data)
                    items = res.get("data") if isinstance(res, dict) else res
                    return items or []
        except Exception:
            pass
        return []

    def presigned_urls(self, items):
        token = self.get_token()
        if not token:
            return [], {}, True
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        payload = []
        meta_map = {}
        for meta in items:
            sig = meta["signature"]
            meta_map.setdefault(sig, []).append(meta)
            payload.append({"height": meta["height"], "width": meta["width"],
                            "size": meta["size"], "extension": meta["ext"],
                            "file": {}, "signature": sig})
        try:
            with self.session.post(f"{BASE_URL}/gs/v3/creations/link",
                                  json={"contents": payload}, headers=headers, timeout=TIMEOUT) as r:
                if r.status_code in (200, 201):
                    return r.json().get("result", []), meta_map, False
                if r.status_code == 401:
                    self.set_token(None)
                    return [], meta_map, True
        except Exception:
            pass
        return [], meta_map, False

    def upload_s3(self, url, path, width, height, content_type):
        headers = {
            "User-Agent": HEADERS_BASE["User-Agent"],
            "Content-Type": content_type,
            "x-amz-meta-imagewidth": str(width),
            "x-amz-meta-imageheight": str(height),
        }
        with open(path, "rb") as f:
            data = f.read()
        try:
            with self.session.put(url, data=data, headers=headers, timeout=TIMEOUT) as r:
                return r.status_code in (200, 201), r.status_code
        except Exception:
            return False, None

    def finalize(self, contents, ref_path, price, location_str, description, currency_id, tag_id):
        token = self.get_token()
        if not token:
            return False
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        iso_time, unix_time = get_file_timestamps(ref_path)
        body = {
            "currency_id": currency_id,
            "location": location_str,
            "description": description,
            "original_at": iso_time,
            "unix_original_at": unix_time,
            "is_bibs": [{"tag_id": tag_id, "is_bib": False}],
            "nickname": [],
            "price": price,
            "resend": True,
            "tag_ids": [tag_id],
            "contents": contents,
        }
        self.last_finalize_error = ""
        try:
            with self.session.post(f"{BASE_URL}/gs/v4/creations", json=body,
                                  headers=headers, timeout=TIMEOUT) as r:
                if r.status_code == 401:
                    self.set_token(None)
                if r.status_code in (200, 201):
                    return True
                try:
                    msg = r.json().get("message") or r.text[:120]
                except Exception:
                    msg = r.text[:120]
                self.last_finalize_error = f"HTTP {r.status_code}: {msg}"
                return False
        except Exception as e:
            self.last_finalize_error = str(e)
            return False

    # --- Metode Statistik Akun FotoYu ---
    def get_groups(self, limit=50, filter_type="all"):
        """Mengambil daftar folder tanggal pembuatan."""
        token = self.get_token()
        if not token:
            return None
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(
                f"{BASE_URL}/gs/v4/creations/groups",
                params={"limit": limit, "filter": filter_type, "sort_by": "created_at", "sort_dir": "desc", "content_type": "all"},
                headers=headers, timeout=25
            ) as r:
                if r.status_code == 401:
                    self.set_token(None)
                    return None
                if r.status_code == 200:
                    data = r.json()
                    return data.get("result", {}).get("group_data", [])
        except Exception:
            pass
        return []

    def get_date_counts(self, date_str):
        """Mengambil total foto, terjual, dan status untuk tanggal tertentu."""
        token = self.get_token()
        if not token:
            return {}
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(
                f"{BASE_URL}/gs/v1/creations/counts",
                params={"date": date_str, "filter": "all,available"},
                headers=headers, timeout=20
            ) as r:
                if r.status_code == 200:
                    return r.json().get("result", {})
        except Exception:
            pass
        return {}

    def get_creations_page(self, date_str, page=1, limit=100, filter_type="available"):
        """Mengambil rincian foto dan metrik wajah pada tanggal tertentu (paginated)."""
        token = self.get_token()
        if not token:
            return None
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(
                f"{BASE_URL}/gs/v5/creations",
                params={
                    "page": page,
                    "limit": limit,
                    "filter": filter_type,
                    "sort_by": "created_at",
                    "sort_dir": "desc",
                    "date": date_str
                },
                headers=headers, timeout=30
            ) as r:
                if r.status_code == 401:
                    self.set_token(None)
                    return None
                if r.status_code == 200:
                    data = r.json()
                    return data.get("result", {}).get("data", [])
        except Exception:
            pass
        return []

    def get_sold_creations(self, limit=None):
        """Mengambil SEMUA daftar foto yang telah terjual tanpa batasan 100 foto (auto-pagination lengkap)."""
        token = self.get_token()
        if not token:
            return []
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        all_sold = []
        page = 1
        per_page = 100
        while True:
            try:
                with self.session.get(
                    f"{BASE_URL}/gs/v5/creations",
                    params={"page": page, "limit": per_page, "filter": "sold", "sort_by": "purchased_at", "sort_dir": "desc"},
                    headers=headers, timeout=30
                ) as r:
                    if r.status_code == 200:
                        d = r.json().get("result", {}).get("data", []) or []
                        if not d:
                            break
                        all_sold.extend(d)
                        if len(d) < per_page or (limit and len(all_sold) >= limit):
                            break
                        page += 1
                    else:
                        break
            except Exception:
                break
        return all_sold

    def get_creation_details(self, content_id):
        """Mengambil detail lengkap satu creation (price, tags, fototree, location)."""
        token = self.get_token()
        if not token or not content_id:
            return {}
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(
                f"{BASE_URL}/gs/v4/creations/details",
                params={"content_id": content_id},
                headers=headers, timeout=15
            ) as r:
                if r.status_code == 200:
                    data = r.json()
                    return data.get("result", {})
        except Exception:
            pass
        return {}

    def get_storage(self):
        """Mengambil info kuota penyimpanan kreator (/gs/v1/creators/storage)."""
        token = self.get_token()
        if not token:
            return {}
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(f"{BASE_URL}/gs/v1/creators/storage", headers=headers, timeout=15) as r:
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return {}

    def get_transaction_fee(self, price, tag_id=None):
        """Mengambil simulasi potongan fee & pendapatan bersih (/payment/v2/transactions/fee)."""
        token = self.get_token()
        if not token:
            return {}
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        params = {"price": int(price)}
        if tag_id:
            params["tag_id"] = tag_id
        try:
            with self.session.get(f"{BASE_URL}/payment/v2/transactions/fee", params=params, headers=headers, timeout=12) as r:
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        return {}

    def check_server_health(self):
        """Cek koneksi & status maintenance server FotoYu -> ('ok'|'maintenance'|'offline', msg)."""
        try:
            with self.session.get(f"{BASE_URL}/notification/v1/maintenance", headers=HEADERS_BASE, timeout=10) as r:
                if r.status_code == 503:
                    return "maintenance", "Server FotoYu sedang maintenance (503)."
                if r.status_code == 200:
                    try:
                        data = r.json()
                        res = data.get("result", data)
                        if isinstance(res, dict):
                            is_maint = (
                                res.get("is_maintenance") is True
                                or res.get("maintenance") is True
                                or res.get("active") is True
                                or str(res.get("status", "")).lower() == "maintenance"
                            )
                            if is_maint:
                                msg = res.get("message") or res.get("description") or "Server FotoYu sedang maintenance."
                                return "maintenance", str(msg)
                    except Exception:
                        pass
                return "ok", ""
        except (requests.ConnectionError, requests.Timeout):
            return "offline", "Koneksi internet terputus."
        except Exception:
            return "ok", ""


def resolve_link(item):
    key = item.get("upload_key") or item.get("key")
    for field in ("original_link", "original_url", "presigned_url", "upload_link", "link", "url"):
        v = item.get(field)
        if isinstance(v, str) and v.startswith("http"):
            return v, key
        if isinstance(v, dict):
            for sub in ("original_link", "url", "link"):
                if isinstance(v.get(sub), str):
                    return v[sub], key
    return None, key


def center_popup(child, parent=None, default_w=None, default_h=None):
    """
    Posisikan child window persis di tengah parent window (pixel-perfect center).
    Mendukung popup yang lebih besar maupun lebih kecil dari parent, serta mode compact.
    """
    try:
        child.update_idletasks()
        sw = child.winfo_screenwidth()
        sh = child.winfo_screenheight()
        if parent:
            parent.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_x()
            py = parent.winfo_y()
        else:
            pw, ph = sw, sh
            px, py = 0, 0

        ww = min(default_w or child.winfo_reqwidth(), max(300, sw - 40))
        wh = min(default_h or child.winfo_reqheight(), max(240, sh - 80))

        x = px + (pw - ww) // 2
        y = py + (ph - wh) // 2

        x = max(10, min(x, sw - ww - 10))
        y = max(30, min(y, sh - wh - 50))

        child.geometry(f"{ww}x{wh}+{x}+{y}")
    except Exception:
        pass


def bring_to_front(win, parent=None):
    if parent:
        try:
            win.transient(parent)
        except Exception:
            pass
    try:
        win.lift()
    except Exception:
        pass
    try:
        win.focus_force()
    except Exception:
        pass

def format_display_date(d_str):
    """
    Mengubah format tanggal YYYY-MM-DD menjadi DD-MM-YYYY saat tampil di program.
    Jika sudah berformat DD-MM-YYYY atau bukan tanggal, kembalikan string aslinya.
    """
    if not d_str or not isinstance(d_str, str):
        return str(d_str or "")
    s = d_str.strip()[:10]
    parts = s.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return s



HTML_CONTENT = r'''<!DOCTYPE html>
<html lang="id" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FotoYu Uploader</title>
<style>
/* ==========================================================================
   COLOR SCHEMES & CSS VARIABLES
   Light: Inspired by light.png (#DCE7F3, #F3F8FC, #C1D8F0, #4663AC, #141E33)
   Dark:  Inspired by dark.jpg (#141518, #2B2D33, #50545E, #9AA0AC, #E5E7EE)
   ========================================================================== */
:root, [data-theme="light"] {
  --bg-app: #DCE7F3;
  --bg-surface: #F3F8FC;
  --card-bg: #FFFFFF;
  --card-border: #C1D8F0;
  --card-shadow: 0 1px 4px rgba(70, 99, 172, 0.08);
  --primary: #4663AC;
  --primary-hover: #3A5496;
  --primary-active: #2E4378;
  --text-main: #141E33;
  --text-muted: #5A6B82;
  --text-subtle: #7E8D9F;
  --input-bg: #F8FAFD;
  --input-border: #C1D8F0;
  --input-focus: #4663AC;
  --btn-secondary-bg: #EAF1FA;
  --btn-secondary-border: #C1D8F0;
  --btn-secondary-text: #27407D;
  --table-header-bg: #E8F0F9;
  --table-stripe: #F6FAFD;
  --table-hover: #E2ECF8;
  --border-subtle: #D2DEEB;
  --zoom-backdrop: rgba(200, 217, 237, 0.94);
  --status-bar-bg: #FFFFFF;
  --status-bar-border: #C1D8F0;
  --badge-bg: #E1EDFA;
  --badge-text: #27407D;
  --success: #2E7D32;
  --danger: #D32F2F;
  --warning: #ED6C02;
  --info: #0288D1;
  --matrix-bg: #F8FAFD;
  --matrix-border: #D2DEEB;
}

[data-theme="dark"] {
  --bg-app: #141518;
  --bg-surface: #1E2024;
  --card-bg: #23252B;
  --card-border: #2B2D33;
  --card-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
  --primary: #4A72B8;
  --primary-hover: #5C84CB;
  --primary-active: #3C609E;
  --text-main: #E5E7EE;
  --text-muted: #9AA0AC;
  --text-subtle: #6D7380;
  --input-bg: #1A1C20;
  --input-border: #3A3D45;
  --input-focus: #5C84CB;
  --btn-secondary-bg: #2B2D33;
  --btn-secondary-border: #3A3D45;
  --btn-secondary-text: #E5E7EE;
  --table-header-bg: #1A1C20;
  --table-stripe: #202228;
  --table-hover: #2B2E36;
  --border-subtle: #2B2D33;
  --zoom-backdrop: rgba(20, 21, 24, 0.95);
  --status-bar-bg: #1E2024;
  --status-bar-border: #2B2D33;
  --badge-bg: #2B2D33;
  --badge-text: #9AA0AC;
  --success: #4CAF50;
  --danger: #F44336;
  --warning: #FFA726;
  --info: #29B6F6;
  --matrix-bg: #1A1C20;
  --matrix-border: #2B2D33;
}

/* ==========================================================================
   GLOBAL RESET & TYPOGRAPHY (All font sizes strictly 12px)
   ========================================================================== */
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 12px;
}

html, body {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  height: 100vh;
  box-sizing: border-box;
  overflow: hidden;
  background-color: var(--bg-app);
  color: var(--text-main);
  user-select: none;
  display: flex;
  flex-direction: column;
}

/* ==========================================================================
   FORM INPUTS & BUTTONS - UNIFORM 30px HEIGHT
   ========================================================================== */
input[type="text"],
input[type="password"],
input[type="number"],
input[type="date"],
select {
  height: 30px;
  line-height: 30px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--input-border);
  background-color: var(--input-bg);
  color: var(--text-main);
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  width: 100%;
}

input:focus, select:focus {
  border-color: var(--input-focus);
  box-shadow: 0 0 0 2px rgba(70, 99, 172, 0.15);
}

input::placeholder {
  color: var(--text-subtle);
  opacity: 0.8;
}

input[type="password"]::-ms-reveal,
input[type="password"]::-ms-clear {
  display: none !important;
  width: 0 !important;
  height: 0 !important;
  pointer-events: none !important;
}

.mode-inline-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  margin-bottom: 8px;
}
.mode-label {
  font-weight: 600;
  color: var(--text-main);
  white-space: nowrap;
  font-size: 12px;
}
.mode-select {
  flex: 1;
  height: 30px !important;
  line-height: 30px;
}

.btn {
  height: 30px;
  line-height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  transition: background-color 0.15s, border-color 0.15s, opacity 0.15s;
  white-space: nowrap;
}

.btn:active {
  transform: translateY(1px);
}

.btn-primary {
  background-color: var(--primary);
  color: #FFFFFF;
}
.btn-primary:hover {
  background-color: var(--primary-hover);
}

.btn-secondary {
  background-color: var(--btn-secondary-bg);
  border-color: var(--btn-secondary-border);
  color: var(--btn-secondary-text);
}
.btn-secondary:hover {
  filter: brightness(0.96);
}

.btn-danger-outline {
  background-color: transparent;
  border-color: var(--danger);
  color: var(--danger);
}
.btn-danger-outline:hover {
  background-color: var(--danger);
  color: #FFFFFF;
}

.btn-start {
  height: 34px;
  line-height: normal;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  background-color: #2E7D32;
  color: #FFFFFF;
  width: 100%;
  border-radius: 6px;
}
.btn-start:hover {
  background-color: #256628;
}
.btn-start.running {
  background-color: #D32F2F;
}
.btn-start.running:hover {
  background-color: #B71C1C;
}
#btnSaveConfig {
  height: 34px !important;
  line-height: normal;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  width: 100%;
  border-radius: 6px;
}
.btn:disabled, .btn[disabled] {
  opacity: 0.45;
  cursor: not-allowed !important;
  pointer-events: none;
}
.btn-start:disabled, .btn-start[disabled] {
  background-color: #6c757d !important;
  opacity: 0.55;
  cursor: not-allowed !important;
  pointer-events: none;
}

/* ==========================================================================
   TOP NAVBAR (NO APP ICON / NO APP NAME)
   ========================================================================== */
.navbar {
  height: 38px;
  min-height: 38px;
  background-color: var(--bg-surface);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
}

.nav-tabs {
  display: flex;
  gap: 8px;
}

.nav-tab {
  height: 28px;
  line-height: normal;
  padding: 0 14px;
  border-radius: 6px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: all 0.15s ease;
  vertical-align: middle;
}

.nav-tab:hover {
  background-color: var(--btn-secondary-bg);
  color: var(--text-main);
}

.nav-tab.active {
  background-color: var(--primary);
  color: #FFFFFF;
}

.btn-theme {
  background-color: var(--btn-secondary-bg);
  border-color: var(--btn-secondary-border);
  color: var(--text-main);
  padding: 0 12px;
}

/* ==========================================================================
   VIEW CONTAINER & PANES
   ========================================================================== */
.view-container {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.view-pane {
  display: none;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

.view-pane.active {
  display: flex;
}

/* ==========================================================================
   TAB 1: UPLOADER VIEW
   ========================================================================== */
.uploader-grid {
  display: flex;
  gap: 12px;
  padding: 10px 14px;
  flex: 1;
  min-height: 0;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

.card {
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  box-shadow: var(--card-shadow);
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.card-half {
  flex: 1 1 0;
  min-width: 0;
  max-width: 100%;
}

.card-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-subtle);
  background-color: var(--bg-surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-header h3 {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-main);
}

.card-body {
  padding: 10px 12px;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  overflow-y: auto;
  gap: 7px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 3px;
  position: relative;
}

.label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.label-row label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-main);
}

.hint-fee {
  font-size: 11px;
  font-weight: 600;
  color: var(--success);
}

.row-2col {
  display: flex;
  gap: 8px;
}

.row-2col > * {
  flex: 1;
}

.input-with-btn {
  display: flex;
  gap: 6px;
}

.input-with-btn input {
  flex: 1;
}

.pwd-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
}

.pwd-wrapper input {
  padding-right: 36px;
}

.btn-pwd {
  position: absolute;
  right: 4px;
  width: 28px;
  height: 24px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  border-radius: 4px;
}
.btn-pwd:hover {
  background-color: var(--btn-secondary-bg);
}

/* Autocomplete Dropdown */
.autocomplete-container {
  position: relative;
}

.dropdown-menu {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  max-height: 150px;
  overflow-y: auto;
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  z-index: 1000;
  display: none;
}

.dropdown-item {
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.dropdown-item:last-child {
  border-bottom: none;
}
.dropdown-item:hover, .dropdown-item.active {
  background-color: var(--table-hover);
}
.dropdown-item strong {
  font-size: 12px;
}
.dropdown-item .loc-sub {
  font-size: 11px;
  color: var(--text-muted);
}

/* Operational Controls */
.queue-box {
  background-color: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.queue-status-text {
  font-weight: 600;
  color: var(--primary);
  font-size: 12px;
  text-align: center;
}

.pb-container {
  height: 10px;
  background-color: var(--input-border);
  border-radius: 5px;
  overflow: hidden;
}

.pb-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #38ef7d);
  transition: width 0.2s ease;
}

.pb-footer {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-muted);
}

.stat-progress-box {
  margin: 10px 0 6px 0;
  padding: 8px 12px;
  background-color: var(--input-bg);
  border: 1px solid var(--card-border);
  border-radius: 6px;
}
.stat-pb-header {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 5px;
  font-weight: 500;
}
.stat-pb-container {
  height: 8px;
  background-color: var(--input-border);
  border-radius: 4px;
  overflow: hidden;
}
.stat-pb-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--primary), #38ef7d);
  transition: width 0.2s ease;
}

/* 2x2 Matrix Counter */
.matrix-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.counter-box {
  background-color: var(--matrix-bg);
  border: 1px solid var(--matrix-border);
  border-radius: 6px;
  padding: 8px;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.counter-box .val {
  font-size: 15px;
  font-weight: 700;
}

.counter-box .lbl {
  font-size: 11px;
  color: var(--text-muted);
}

.text-success { color: var(--success); }
.text-danger { color: var(--danger); }
.text-warning { color: var(--warning); }
.text-info { color: var(--info); }

/* ==========================================================================
   LOG DRAWER
   ========================================================================== */
.log-toggle-bar {
  height: 28px;
  min-height: 28px;
  background-color: var(--bg-surface);
  border-top: 1px solid var(--border-subtle);
  padding: 0 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-muted);
  box-sizing: border-box;
}
.log-toggle-bar:hover {
  background-color: var(--btn-secondary-bg);
  color: var(--text-main);
}

.bar-left-content {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bar-icon-box {
  width: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 13px;
  line-height: 1;
}

.log-drawer {
  display: none;
  height: 180px;
  background-color: var(--card-bg);
  border-top: 1px solid var(--border-subtle);
  flex-direction: column;
}

.log-drawer-header {
  height: 32px;
  min-height: 32px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: var(--bg-surface);
  border-bottom: 1px solid var(--border-subtle);
}

.log-filters {
  display: flex;
  gap: 6px;
}

.filter-btn {
  height: 24px;
  line-height: 24px;
  padding: 0 8px;
  font-size: 11px;
  border-radius: 4px;
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}
.filter-btn.active {
  background-color: var(--primary);
  color: #FFFFFF;
  border-color: var(--primary);
}

.log-table-wrap {
  flex: 1;
  overflow-y: auto;
}

/* ==========================================================================
   TAB 2: STATISTIK VIEW
   ========================================================================== */
.stat-container {
  padding: 10px 14px;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
}

.stat-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: nowrap !important;
  gap: 8px;
  width: 100%;
  box-sizing: border-box;
}

.stat-filter-row {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  font-size: 11px;
}

.stat-action-row {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.date-input-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 115px;
  height: 30px;
}

.date-text-input {
  width: 100% !important;
  height: 30px !important;
  line-height: 28px !important;
  padding: 0 24px 0 8px !important;
  border-radius: 6px;
  border: 1px solid var(--input-border);
  background-color: var(--input-bg);
  color: var(--text-main);
  font-size: 11px !important;
  text-align: center;
  box-sizing: border-box;
}

.date-native-hidden {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
  pointer-events: none;
}

.date-calendar-icon {
  position: absolute;
  right: 6px;
  cursor: pointer;
  font-size: 11px;
  user-select: none;
  opacity: 0.7;
  transition: opacity 0.15s;
}

.date-calendar-icon:hover {
  opacity: 1;
}

.stat-action-btn {
  height: 30px !important;
  min-height: 30px !important;
  max-height: 30px !important;
  line-height: 1 !important;
  padding: 0 10px !important;
  font-size: 11px !important;
  box-sizing: border-box !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 5px !important;
  white-space: nowrap !important;
  border-width: 1px !important;
  border-style: solid !important;
  vertical-align: middle !important;
  overflow: hidden !important;
}

.stat-action-btn .btn-icon {
  font-size: 11px !important;
  line-height: 1 !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
}

.badge-storage {
  height: 30px !important;
  line-height: 28px !important;
  padding: 0 10px !important;
  border-radius: 6px !important;
  background-color: var(--badge-bg);
  color: var(--badge-text);
  font-weight: 500;
  border: 1px solid var(--border-subtle);
  display: inline-flex !important;
  align-items: center !important;
  font-size: 11px !important;
  white-space: nowrap !important;
  box-sizing: border-box !important;
}

/* 8 KPI Cards Grid (2x4) */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}

.kpi-card {
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  padding: 8px 10px;
  box-shadow: var(--card-shadow);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kpi-title {
  font-size: 11px;
  color: var(--text-muted);
}

.kpi-val {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-main);
}

/* Sub-tabs inside Statistik */
.subtabs-bar {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 6px;
}

.subtab-btn {
  height: 28px;
  line-height: 28px;
  padding: 0 12px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-weight: 500;
}
.subtab-btn:hover {
  background-color: var(--btn-secondary-bg);
}
.subtab-btn.active {
  background-color: var(--btn-secondary-bg);
  border-color: var(--btn-secondary-border);
  color: var(--primary);
  font-weight: 600;
}

.subtab-content {
  display: none;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}
.subtab-content.active {
  display: flex;
}

/* ==========================================================================
   TABLES & SEPARATING COLUMN BORDERS (All th 1 line only)
   ========================================================================== */
.table-wrap {
  flex: 1;
  min-height: 180px;
  overflow: auto;
  border: 1px solid var(--card-border);
  border-radius: 6px;
  background-color: var(--card-bg);
}

table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

th, td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border-subtle);
  border-right: 1px solid var(--border-subtle);
  font-size: 12px;
  vertical-align: middle;
  text-align: center !important;
}

th:last-child, td:last-child {
  border-right: none;
}

th {
  background-color: var(--table-header-bg);
  color: var(--text-main);
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  position: sticky;
  top: 0;
  z-index: 10;
  text-align: center !important;
  cursor: pointer;
  user-select: none;
}

th:hover {
  background-color: var(--table-hover) !important;
}

tbody tr:nth-child(even) {
  background-color: var(--table-stripe);
}

tbody tr:hover {
  background-color: var(--table-hover);
  cursor: pointer;
}

.th-center, .td-center, .th-right, .td-right, .th-left, .td-left {
  text-align: center !important;
}

.thumb-preview {
  width: 38px;
  height: 38px;
  object-fit: cover;
  border-radius: 4px;
  border: 1px solid var(--border-subtle);
  cursor: pointer;
}

/* Leaderboard Bar Chart */
.chart-container {
  padding: 12px;
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}

.chart-bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chart-label {
  width: 160px;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-bar-track {
  flex: 1;
  height: 16px;
  background-color: var(--input-bg);
  border-radius: 4px;
  overflow: hidden;
}

.chart-bar-value {
  height: 100%;
  background: var(--primary);
  border-radius: 4px;
  transition: width 0.3s ease;
}

.chart-count {
  width: 50px;
  text-align: right;
  font-size: 11px;
  font-weight: 600;
}

/* ==========================================================================
   STATUS BAR
   ========================================================================== */
.status-bar {
  height: 28px;
  min-height: 28px;
  background-color: var(--status-bar-bg);
  border-top: 1px solid var(--status-bar-border);
  display: flex;
  align-items: center;
  padding: 0 14px;
  gap: 8px;
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}
.status-dot.green { background-color: #2E7D32; box-shadow: 0 0 6px #2E7D32; }
.status-dot.yellow { background-color: #ED6C02; box-shadow: 0 0 6px #ED6C02; }
.status-dot.red { background-color: #D32F2F; box-shadow: 0 0 6px #D32F2F; }
.status-dot.blue { background-color: #0288D1; box-shadow: 0 0 6px #0288D1; }

.status-text {
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ==========================================================================
   MODALS: Rincian Foto & Zoom Photo
   ========================================================================== */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: rgba(0, 0, 0, 0.45);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}
.modal-overlay.active {
  display: flex;
}

.modal-card {
  width: 920px;
  max-width: 95vw;
  height: 520px;
  max-height: 90vh;
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-sm {
  width: 380px !important;
  max-width: 90vw !important;
  height: auto !important;
  max-height: 85vh !important;
}

.modal-md {
  width: 480px !important;
  max-width: 92vw !important;
  height: auto !important;
  max-height: 85vh !important;
}

.modal-header {
  padding: 10px 14px;
  background-color: var(--bg-surface);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.modal-header h3 {
  font-size: 13px;
  font-weight: 600;
}

.modal-close-btn {
  background: transparent;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: var(--text-muted);
}
.modal-close-btn:hover {
  color: var(--danger);
}

.modal-body {
  padding: 10px 14px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}

.modal-summary-bar {
  display: flex;
  gap: 12px;
  align-items: center;
}

.modal-badge {
  padding: 4px 10px;
  border-radius: 4px;
  background-color: var(--badge-bg);
  color: var(--badge-text);
  font-weight: 600;
}

/* Zoom Modal */
.zoom-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--zoom-backdrop);
  backdrop-filter: blur(4px);
  display: none;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 10000;
  padding: 20px;
}
.zoom-overlay.active {
  display: flex;
}

.zoom-img-wrap {
  position: relative;
  max-width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.zoom-img {
  max-width: 90vw;
  max-height: 75vh;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.zoom-caption {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-main);
  background-color: var(--card-bg);
  padding: 4px 12px;
  border-radius: 12px;
  border: 1px solid var(--border-subtle);
}

.zoom-close {
  position: absolute;
  top: 15px;
  right: 20px;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background-color: var(--card-bg);
  border: 1px solid var(--card-border);
  color: var(--text-main);
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}
</style>
</head>
<body>

<!-- TOP NAVBAR -->
<div class="navbar">
  <div class="nav-tabs">
    <button id="navTabUploader" class="nav-tab active" onclick="switchNavTab('uploader')"><span style="display:inline-flex;align-items:center;">🚀</span><span>Uploader</span></button>
    <button id="navTabStatistik" class="nav-tab" onclick="switchNavTab('statistik')"><span style="display:inline-flex;align-items:center;">📊</span><span>Statistik</span></button>
  </div>
  <div>
    <button id="btnThemeToggle" class="btn btn-theme" onclick="toggleTheme()">🌙 Mode Gelap</button>
  </div>
</div>

<!-- MAIN VIEW CONTAINER -->
<div class="view-container">

  <!-- ========================================================================
       VIEW 1: UPLOADER
       ======================================================================== -->
  <div id="viewUploader" class="view-pane active">
    <div class="uploader-grid">

      <!-- LEFT CARD: KONFIGURASI AKUN & UPLOAD -->
      <div class="card card-half">
        <div class="card-header">
          <h3>Konfigurasi Akun & Upload</h3>
        </div>
        <div class="card-body">
          <!-- Presets Row -->
          <div class="input-with-btn">
            <select id="presetSelect" onchange="onPresetSelected()">
              <option value="">-- Pilih Preset --</option>
            </select>
            <button class="btn btn-secondary" onclick="openPresetModal()">+ Preset</button>
            <button class="btn btn-danger-outline" onclick="deletePreset()">Hapus</button>
          </div>

          <!-- Username & Password -->
          <div class="row-2col">
            <div class="form-group">
              <div class="label-row"><label>Username</label></div>
              <input type="text" id="cfgUsername" placeholder="Username FotoYu" oninput="onCredsChanged(); checkFormValidity()">
            </div>
            <div class="form-group">
              <div class="label-row"><label>Password</label></div>
              <div class="pwd-wrapper">
                <input type="password" id="cfgPassword" placeholder="Password FotoYu" oninput="onCredsChanged(); checkFormValidity()">
                <button type="button" class="btn-pwd" id="btnTogglePwd" onclick="togglePasswordVisibility()">👁️</button>
              </div>
            </div>
          </div>

          <!-- Harga Satuan & Fototree -->
          <div class="row-2col">
            <div class="form-group">
              <div class="label-row"><label>Harga Satuan (Rp)</label></div>
              <input type="number" id="cfgPrice" placeholder="Harga Satuan (Rp)" oninput="checkFormValidity()">
            </div>
            <div class="form-group autocomplete-container">
              <div class="label-row"><label>Fototree</label></div>
              <input type="text" id="cfgFototree" placeholder="Cari Fototree..." oninput="onFototreeInput(); checkFormValidity()" onfocus="onFototreeFocus()" autocomplete="off">
              <div id="treeDropdown" class="dropdown-menu"></div>
              <input type="hidden" id="cfgTagId">
              <input type="hidden" id="cfgLat">
              <input type="hidden" id="cfgLon">
            </div>
          </div>

          <!-- Lat & Lon Coordinates -->
          <div class="row-2col">
            <div class="form-group">
              <div class="label-row"><label>Latitude</label></div>
              <input type="text" id="dispLat" placeholder="Latitude (0.0)" onchange="onManualCoordChange()">
            </div>
            <div class="form-group">
              <div class="label-row"><label>Longitude</label></div>
              <input type="text" id="dispLon" placeholder="Longitude (0.0)" onchange="onManualCoordChange()">
            </div>
          </div>

          <!-- Deskripsi Foto -->
          <div class="form-group">
            <div class="label-row"><label>Deskripsi Foto (Opsional)</label></div>
            <input type="text" id="cfgDesc" placeholder="Deskripsi foto (opsional)">
          </div>

          <!-- Source Directory -->
          <div class="form-group">
            <div class="label-row"><label>Folder Source</label></div>
            <div class="input-with-btn">
              <input type="text" id="cfgUploadDir" placeholder="Folder Source (foto asli)" oninput="onFolderInputChanged('source')" onchange="onFolderInputChanged('source')">
              <button class="btn btn-secondary" onclick="browseSourceFolder()">Pilih</button>
              <button class="btn btn-secondary" id="btnOpenSource" onclick="openSourceFolder()" disabled>Buka</button>
            </div>
          </div>

          <!-- Completed Directory -->
          <div class="form-group">
            <div class="label-row"><label>Folder Completed</label></div>
            <div class="input-with-btn">
              <input type="text" id="cfgCompleteDir" placeholder="Folder Completed" oninput="onFolderInputChanged('complete')" onchange="onFolderInputChanged('complete')">
              <button class="btn btn-secondary" onclick="browseCompleteFolder()">Pilih</button>
              <button class="btn btn-secondary" id="btnOpenComplete" onclick="openCompleteFolder()" disabled>Buka</button>
            </div>
          </div>

          <!-- Save Konfigurasi Button -->
          <button class="btn btn-primary" id="btnSaveConfig" onclick="saveConfiguration()" style="width: 100%; margin-top: 2px;">💾 Simpan Konfigurasi</button>
        </div>
      </div>

      <!-- RIGHT CARD: KONTROL OPERASIONAL -->
      <div class="card card-half">
        <div class="card-header">
          <h3>Kontrol Operasional</h3>
        </div>
        <div class="card-body">
          <!-- Mode Upload (Sejajar dengan Preset) -->
          <div class="mode-inline-row">
            <span class="mode-label">Mode Upload:</span>
            <select id="cfgMode" class="mode-select">
              <option value="all">Semua (All)</option>
              <option value="odd">Foto Angka Ganjil</option>
              <option value="even">Foto Angka Genap</option>
            </select>
          </div>

          <!-- Queue Box & Progress -->
          <div class="queue-box">
            <div id="queueText" class="queue-status-text">Antrean: 0 Source  |  0 Siap Upload</div>
            <div class="pb-container">
              <div id="pbFill" class="pb-fill" style="width: 0%;"></div>
            </div>
            <div class="pb-footer">
              <span id="pbLabel">Menunggu proses...</span>
              <span id="pbPct">0%</span>
            </div>
          </div>

          <!-- 2x2 Matrix Counter -->
          <div class="matrix-grid">
            <div class="counter-box">
              <span class="val text-success" id="cntCompOk">0</span>
              <span class="lbl">Kompres Sukses</span>
            </div>
            <div class="counter-box">
              <span class="val text-danger" id="cntCompFail">0</span>
              <span class="lbl">Kompres Gagal</span>
            </div>
            <div class="counter-box">
              <span class="val text-success" id="cntUpOk">0</span>
              <span class="lbl">Upload Sukses</span>
            </div>
            <div class="counter-box">
              <span class="val text-danger" id="cntUpFail">0</span>
              <span class="lbl">Upload Gagal</span>
            </div>
          </div>

          <!-- Reset, Retry & Anti-Duplikat Buttons -->
          <div style="display: flex; gap: 6px;">
            <button class="btn btn-secondary" style="flex: 1;" onclick="resetCounters()">🔄 Reset Counter</button>
            <button class="btn btn-secondary" style="flex: 1;" onclick="retryFailed()">🔁 Ulangi Gagal</button>
            <button class="btn btn-secondary" style="flex: 1.1;" onclick="openDuplicateModal()">🛡️ Anti-Duplikat</button>
          </div>

          <!-- Big Mulai / Berhenti Button -->
          <button id="btnStartStop" class="btn btn-start" onclick="toggleStartStop()" disabled>🚀 Mulai Proses</button>
        </div>
      </div>

    </div>

    <!-- LOG DRAWER (EXPANDABLE) -->
    <div class="log-toggle-bar" onclick="toggleLog()">
      <div class="bar-left-content">
        <span class="bar-icon-box">📋</span>
        <span id="logSummaryText">Log Aktivitas (OK: 0 | Gagal: 0 | Info: 0 | Skip: 0)</span>
      </div>
      <span id="logToggleArrow">▲ Buka Log</span>
    </div>
    <div id="logDrawer" class="log-drawer">
      <div class="log-drawer-header">
        <div class="log-filters">
          <button class="filter-btn active" data-filter="all" onclick="filterLogs('all')">Semua</button>
          <button class="filter-btn" data-filter="ok" onclick="filterLogs('ok')">OK</button>
          <button class="filter-btn" data-filter="fail" onclick="filterLogs('fail')">Gagal</button>
          <button class="filter-btn" data-filter="info" onclick="filterLogs('info')">Info</button>
          <button class="filter-btn" data-filter="skip" onclick="filterLogs('skip')">Skip</button>
        </div>
        <button class="btn btn-secondary" style="height: 24px; line-height: 24px; padding: 0 8px; font-size: 11px;" onclick="clearLogs()">Bersihkan</button>
      </div>
      <div class="log-table-wrap">
        <table id="tableLogs">
          <thead>
            <tr>
              <th style="width: 70px;">Waktu</th>
              <th style="width: 80px;">Aksi</th>
              <th style="width: 200px;">Nama File</th>
              <th>Keterangan</th>
              <th style="width: 70px;" class="th-center">Status</th>
            </tr>
          </thead>
          <tbody id="tbodyLogs">
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ========================================================================
       VIEW 2: STATISTIK
       ======================================================================== -->
  <div id="viewStatistik" class="view-pane">
    <div class="stat-container">

      <!-- Top Filter & Action Bar (1 Baris Sejajar: Filter Tanggal, Reset, Penyimpanan, Muat Ulang & Update All Data) -->
      <div class="stat-header-bar">
        <div class="stat-filter-row">
          <span>Rentang Tanggal:</span>
          <div class="date-input-wrap">
            <input type="text" id="statDateStartDisp" class="date-text-input" placeholder="DD/MM/YYYY" maxlength="10" oninput="formatDateInput(this)" onchange="applyStatFilterFromDisplay()">
            <input type="date" id="statDateStartNative" class="date-native-hidden" onchange="syncNativeToDisp('start')">
            <span class="date-calendar-icon" onclick="openNativeDatePicker('start')" title="Pilih Tanggal">📅</span>
          </div>
          <span>s/d</span>
          <div class="date-input-wrap">
            <input type="text" id="statDateEndDisp" class="date-text-input" placeholder="DD/MM/YYYY" maxlength="10" oninput="formatDateInput(this)" onchange="applyStatFilterFromDisplay()">
            <input type="date" id="statDateEndNative" class="date-native-hidden" onchange="syncNativeToDisp('end')">
            <span class="date-calendar-icon" onclick="openNativeDatePicker('end')" title="Pilih Tanggal">📅</span>
          </div>
          <button class="btn btn-secondary stat-action-btn" onclick="resetStatFilter()">Reset Filter</button>
        </div>
        <div class="stat-action-row">
          <span class="badge-storage" id="statStorageBadge">Penyimpanan: -</span>
          <button class="btn btn-secondary stat-action-btn" onclick="loadStatistics(false)" title="Muat data baru secara cepat (incremental)"><span class="btn-icon">🔄</span> Muat Ulang</button>
          <button class="btn btn-primary stat-action-btn" onclick="openModalConfirmUpdateAll()" title="Pindai ulang seluruh riwayat foto & timpa cache"><span class="btn-icon">⚡</span> Update All Data</button>
        </div>
      </div>

      <!-- 8 KPI Cards (2x4) -->
      <div class="kpi-grid">
        <div class="kpi-card">
          <span class="kpi-title">Total Foto</span>
          <span class="kpi-val" id="statTotalPhotos">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">Deteksi Wajah</span>
          <span class="kpi-val" id="statTotalFaces">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">AK+ Cocok</span>
          <span class="kpi-val" id="statTotalAkPlus">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">AK- Rendah</span>
          <span class="kpi-val" id="statTotalAkMin">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">Terkonfirmasi</span>
          <span class="kpi-val" id="statTotalKonfirm">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">Terjual</span>
          <span class="kpi-val text-success" id="statTotalSold">0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">Estimasi Omset</span>
          <span class="kpi-val text-success" id="statTotalRev">Rp 0</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-title">Rasio Konversi</span>
          <span class="kpi-val text-info" id="statConvRate">0.0%</span>
        </div>
      </div>

      <!-- STATISTIK PROGRESS BAR -->
      <div id="statProgressBox" class="stat-progress-box" style="display: none;">
        <div class="stat-pb-header">
          <span id="statPbLabel">Memuat data statistik...</span>
          <span id="statPbPct">0%</span>
        </div>
        <div class="stat-pb-container">
          <div id="statPbFill" class="stat-pb-fill" style="width: 0%;"></div>
        </div>
      </div>

      <!-- Sub-tabs Bar -->
      <div class="subtabs-bar">
        <button id="subtabBtnDates" class="subtab-btn active" onclick="switchStatSubTab('dates')">📅 Penjualan per Tanggal</button>
        <button id="subtabBtnLeaderboard" class="subtab-btn" onclick="switchStatSubTab('leaderboard')">🏆 Leaderboard</button>
        <button id="subtabBtnSold" class="subtab-btn" onclick="switchStatSubTab('sold')">🖼️ Semua Foto Terjual</button>
      </div>

      <!-- Sub-tab 1: Penjualan per Tanggal -->
      <div id="subtabDates" class="subtab-content active">
        <div class="table-wrap">
          <table id="tableDates">
            <thead>
              <tr>
                <th style="width: 110px;">Tanggal</th>
                <th style="width: 60px;" class="th-right">Foto</th>
                <th style="width: 65px;" class="th-right">Wajah</th>
                <th style="width: 65px;" class="th-right">AK+</th>
                <th style="width: 65px;" class="th-right">AK-</th>
                <th style="width: 75px;" class="th-right">Konfirm</th>
                <th style="width: 65px;" class="th-right">Terjual</th>
                <th style="width: 120px;" class="th-right">Estimasi Omset</th>
                <th style="width: 110px;">Keterangan</th>
                <th style="width: 75px;" class="th-center">Aksi</th>
              </tr>
            </thead>
            <tbody id="tbodyDates">
            </tbody>
          </table>
        </div>
      </div>

      <!-- Sub-tab 2: Leaderboard (No icons inside tabs/headers, clean full-width table) -->
      <div id="subtabLeaderboard" class="subtab-content">
        <div style="display: flex; gap: 8px; margin-bottom: 8px;">
          <button id="btnLbTree" class="btn btn-secondary active" onclick="setLeaderboardMode('tree')">Berdasarkan Fototree</button>
          <button id="btnLbDate" class="btn btn-secondary" onclick="setLeaderboardMode('date')">Berdasarkan Tanggal</button>
        </div>

        <!-- Visual Bar Chart -->
        <div id="lbChart" class="chart-container"></div>

        <!-- Leaderboard Table (Full Width) -->
        <div class="table-wrap">
          <table id="tableLeaderboard">
            <thead>
              <tr>
                <th style="width: 50px;" class="th-center">No</th>
                <th id="thLbName">Nama Fototree</th>
                <th style="width: 120px;" class="th-right">Terjual (Foto)</th>
                <th style="width: 160px;" class="th-right">Estimasi Omset</th>
              </tr>
            </thead>
            <tbody id="tbodyLeaderboard">
            </tbody>
          </table>
        </div>
      </div>

      <!-- Sub-tab 3: Semua Foto Terjual -->
      <div id="subtabSold" class="subtab-content">
        <div class="table-wrap">
          <table id="tableSold">
            <thead>
              <tr>
                <th>Judul Foto</th>
                <th style="width: 150px;">Fototree</th>
                <th style="width: 105px;">Tanggal</th>
                <th style="width: 100px;">Harga</th>
                <th style="width: 105px;">Net Kreator</th>
                <th style="width: 75px;">Preview</th>
              </tr>
            </thead>
            <tbody id="tbodySold">
            </tbody>
          </table>
        </div>
      </div>

    </div>
  </div>

</div>

<!-- BOTTOM STATUS BAR -->
<div class="status-bar">
  <div class="bar-left-content">
    <span class="bar-icon-box"><span id="statusDot" class="status-dot green"></span></span>
    <span id="statusText" class="status-text">Siap. Isi konfigurasi & pilih folder lalu klik Mulai Proses.</span>
  </div>
</div>

<!-- ========================================================================
     MODAL 1: RINCIAN FOTO POPUP
     ======================================================================== -->
<div id="modalPhotoDetail" class="modal-overlay">
  <div class="modal-card">
    <div class="modal-header">
      <h3>Rincian Foto - Tanggal: <span id="modalDateTitle">-</span></h3>
      <button class="modal-close-btn" onclick="closePhotoDetailModal()">✕</button>
    </div>
    <div class="modal-body">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <div class="modal-summary-bar">
          <span class="modal-badge" id="modalBadgeTotal">Total: 0 Foto</span>
          <span class="modal-badge" style="background-color: var(--success); color: #fff;" id="modalBadgeSold">Terjual: 0 Foto</span>
          <span class="modal-badge" id="modalBadgeEstRev">Estimasi Omset: Rp 0</span>
        </div>
        <div style="display: flex; gap: 6px;">
          <button class="filter-btn active" data-mfilter="all" onclick="filterModalPhotos('all')">Semua</button>
          <button class="filter-btn" data-mfilter="sold" onclick="filterModalPhotos('sold')">Terjual</button>
          <button class="filter-btn" data-mfilter="available" onclick="filterModalPhotos('available')">Tersedia</button>
        </div>
      </div>

      <div class="table-wrap" style="flex: 1;">
        <table>
          <thead>
            <tr>
              <th style="width: 130px;">ID / Judul</th>
              <th>Fototree</th>
              <th style="width: 95px;">Resolusi</th>
              <th style="width: 70px;">Ukuran</th>
              <th style="width: 50px;">Wajah</th>
              <th style="width: 75px;">RoboFind</th>
              <th style="width: 75px;">Status</th>
              <th style="width: 85px;">Harga</th>
              <th style="width: 95px;">Net Kreator</th>
              <th style="width: 70px;">Preview</th>
            </tr>
          </thead>
          <tbody id="tbodyModalPhotos">
          </tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ========================================================================
     MODAL 2: ZOOM PHOTO PREVIEW
     ======================================================================== -->
<div id="modalZoom" class="zoom-overlay" onclick="closeZoomModal(event)">
  <button class="zoom-close" onclick="closeZoomModal(event, true)">✕</button>
  <div class="zoom-img-wrap" onclick="event.stopPropagation()">
    <img id="zoomImg" class="zoom-img" src="" alt="Preview Foto">
    <div id="zoomCaption" class="zoom-caption">Memuat foto...</div>
  </div>
</div>

<!-- ========================================================================
     MODAL 3: INPUT NAMA PRESET
     ======================================================================== -->
<div id="modalPresetName" class="modal-overlay">
  <div class="modal-card modal-sm">
    <div class="modal-header">
      <h3>Simpan Preset Event</h3>
      <button class="modal-close-btn" onclick="closePresetModal()">✕</button>
    </div>
    <div class="modal-body" style="gap: 12px; padding: 14px;">
      <div class="form-group">
        <div class="label-row"><label>Nama Preset</label></div>
        <input type="text" id="inputPresetName" placeholder="Contoh: Malioboro Pagi" onkeydown="if(event.key==='Enter') confirmSavePreset()">
      </div>
      <div style="display: flex; justify-content: flex-end; gap: 8px;">
        <button class="btn btn-secondary" onclick="closePresetModal()">Batal</button>
        <button class="btn btn-primary" onclick="confirmSavePreset()">Simpan</button>
      </div>
    </div>
  </div>
</div>

<!-- ========================================================================
     MODAL 4: KELOLA RIWAYAT ANTI-DUPLIKAT
     ======================================================================== -->
<div id="modalDuplicates" class="modal-overlay">
  <div class="modal-card modal-md">
    <div class="modal-header">
      <h3>Kelola Riwayat Anti-Duplikat</h3>
      <button class="modal-close-btn" onclick="closeDuplicateModal()">✕</button>
    </div>
    <div class="modal-body" style="gap: 12px; padding: 14px;">
      <div style="font-size: 13px; font-weight: 600; color: var(--primary);" id="dupCountText">
        Total foto terindeks dalam Anti-Duplikat: 0 Foto
      </div>
      <div style="color: var(--text-muted); font-size: 11px; line-height: 1.5;">
        Riwayat Anti-Duplikat mencegah file foto yang sama diunggah dua kali.<br>
        Bersihkan riwayat hanya jika Anda ingin mengunggah ulang foto tersebut.
      </div>
      <div style="display: flex; gap: 8px; margin-top: 4px;">
        <button class="btn btn-secondary" onclick="reindexDuplicates()">🔄 Indeks Ulang Completed</button>
        <button class="btn btn-danger-outline" onclick="clearDuplicates()">🗑️ Bersihkan Riwayat</button>
      </div>
      <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
        <button class="btn btn-secondary" onclick="closeDuplicateModal()">Tutup</button>
      </div>
    </div>
  </div>
</div>

<!-- ========================================================================
     MODAL 5: KONFIRMASI SIMPAN KONFIGURASI
     ======================================================================== -->
<div id="modalSaveSuccess" class="modal-overlay">
  <div class="modal-card modal-sm">
    <div class="modal-header">
      <h3>Informasi Simpan</h3>
      <button class="modal-close-btn" onclick="closeSaveSuccessModal()">✕</button>
    </div>
    <div class="modal-body" style="gap: 12px; padding: 16px; text-align: center;">
      <div style="font-size: 32px;">💾</div>
      <div style="font-size: 13px; font-weight: 600; color: var(--text-main);">Konfigurasi Berhasil Disimpan</div>
      <div style="color: var(--text-muted); font-size: 11px; line-height: 1.4;">
        Semua isian konfigurasi akun, harga, lokasi, dan folder destinasi telah berhasil disimpan ke berkas <b>config.json</b>.
      </div>
      <div style="display: flex; justify-content: center; margin-top: 8px;">
        <button class="btn btn-primary" style="min-width: 90px;" onclick="closeSaveSuccessModal()">OK</button>
      </div>
    </div>
  </div>
</div>

<!-- ========================================================================
     MODAL 6: KONFIRMASI UPDATE ALL DATA
     ======================================================================== -->
<div id="modalConfirmUpdateAll" class="modal-overlay">
  <div class="modal-card" style="max-width: 440px;">
    <div class="modal-header">
      <h3 style="color: var(--primary);">⚡ Konfirmasi Update All Data</h3>
      <button class="modal-close-btn" onclick="closeModalConfirmUpdateAll()">✕</button>
    </div>
    <div class="modal-body" style="gap: 12px; padding: 16px; font-size: 12px; line-height: 1.6; color: var(--text-main);">
      <p style="margin: 0;">
        <strong>Update All Data</strong> akan memindai ulang seluruh riwayat foto dari FotoYu, memperbarui perubahan foto-foto lama (seperti perubahan harga, fototree, tag), dan menyimpannya kembali ke cache lokal.
      </p>
      <p style="margin: 0; color: var(--text-muted); font-size: 11px;">
        Proses ini mungkin membutuhkan waktu beberapa saat tergantung jumlah total foto Anda. Lanjutkan proses?
      </p>
    </div>
    <div class="modal-footer" style="padding: 12px 16px; display: flex; justify-content: flex-end; gap: 8px; border-top: 1px solid var(--border-subtle);">
      <button class="btn btn-secondary" onclick="closeModalConfirmUpdateAll()">Batal</button>
      <button class="btn btn-primary" onclick="proceedUpdateAllData()">Ya, Update Semua</button>
    </div>
  </div>
</div>

<!-- ========================================================================
     JAVASCRIPT APPLICATION LOGIC
     ======================================================================== -->
<script>
// Global State
let currentConfig = {};
let currentPresets = {};
let currentStatistics = null;
let currentModalItems = [];
let currentLogFilter = 'all';
let currentModalFilter = 'all';
let currentLbMode = 'tree';
let isRunning = false;
let isLogOpen = false;
let searchTimer = null;
let priceFeeTimer = null;
let allLogs = [];

// Formatters
function formatIdr(num) {
  if (num === null || num === undefined || isNaN(num)) return "Rp 0";
  return "Rp " + Math.round(num).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// --------------------------------------------------------------------------
// THEME SWITCHER
// --------------------------------------------------------------------------
let currentTheme = 'light';

function setTheme(t) {
  currentTheme = (t === 'dark') ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', currentTheme);
  document.body.setAttribute('data-theme', currentTheme);
  try {
    localStorage.setItem('fotoyu_theme', currentTheme);
  } catch (e) {}

  const btn = document.getElementById('btnThemeToggle');
  if (btn) {
    if (currentTheme === 'dark') {
      btn.innerHTML = '☀️ Mode Terang';
      btn.title = 'Beralih ke Mode Terang';
    } else {
      btn.innerHTML = '🌙 Mode Gelap';
      btn.title = 'Beralih ke Mode Gelap';
    }
  }

  if (window.pywebview && window.pywebview.api && window.pywebview.api.save_theme) {
    window.pywebview.api.save_theme(currentTheme).catch(() => {});
  }
}

function toggleTheme() {
  setTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

// Initialize theme immediately and safely
(function initTheme() {
  let saved = 'light';
  try {
    saved = localStorage.getItem('fotoyu_theme') || 'light';
  } catch (e) {
    saved = 'light';
  }
  setTheme(saved);
})();

// --------------------------------------------------------------------------
// NAVIGATION TABS
// --------------------------------------------------------------------------
function switchNavTab(tab) {
  document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.view-pane').forEach(p => p.classList.remove('active'));

  if (tab === 'uploader') {
    document.getElementById('navTabUploader').classList.add('active');
    document.getElementById('viewUploader').classList.add('active');
  } else if (tab === 'statistik') {
    document.getElementById('navTabStatistik').classList.add('active');
    document.getElementById('viewStatistik').classList.add('active');
    if (!currentStatistics) {
      loadStatistics();
    }
  }
}

// --------------------------------------------------------------------------
// PASSWORD VISIBILITY TOGGLE
// --------------------------------------------------------------------------
function togglePasswordVisibility() {
  const inp = document.getElementById('cfgPassword');
  const btn = document.getElementById('btnTogglePwd');
  if (inp.type === 'password') {
    inp.type = 'text';
    btn.textContent = '🙈';
  } else {
    inp.type = 'password';
    btn.textContent = '👁️';
  }
}

// --------------------------------------------------------------------------
// FORM BINDINGS & REALTIME FEE
// --------------------------------------------------------------------------
function onPriceInput() {
  // Net Kreator hint removed as requested
}

function updateFeeHint(price) {
  // Net Kreator hint removed as requested
}

function onCredsChanged() {
  // auto trigger queue or state if needed
}

function onManualCoordChange() {
  const lat = document.getElementById('dispLat').value;
  const lon = document.getElementById('dispLon').value;
  document.getElementById('cfgLat').value = lat;
  document.getElementById('cfgLon').value = lon;
}

// --------------------------------------------------------------------------
// FOTOTREE AUTOCOMPLETE (Clean without '(-)')
// --------------------------------------------------------------------------
function onFototreeInput() {
  clearTimeout(searchTimer);
  const q = document.getElementById('cfgFototree').value.trim();
  const dropdown = document.getElementById('treeDropdown');
  if (!q) {
    dropdown.style.display = 'none';
    dropdown.innerHTML = '';
    return;
  }
  searchTimer = setTimeout(() => {
    performTreeSearch(q);
  }, 350);
}

function onFototreeFocus() {
  const q = document.getElementById('cfgFototree').value.trim();
  if (q) performTreeSearch(q);
}

function performTreeSearch(q) {
  const dropdown = document.getElementById('treeDropdown');
  const u = document.getElementById('cfgUsername').value;
  const p = document.getElementById('cfgPassword').value;

  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.search_fototree(q, u, p).then(results => {
      renderTreeDropdown(results);
    }).catch(err => {
      dropdown.style.display = 'none';
    });
  }
}

function renderTreeDropdown(results) {
  const dropdown = document.getElementById('treeDropdown');
  dropdown.innerHTML = '';
  if (!results || results.length === 0) {
    dropdown.style.display = 'none';
    return;
  }

  results.forEach(item => {
    const div = document.createElement('div');
    div.className = 'dropdown-item';
    // Remove '(-)' if location is empty or '-'
    const hasLoc = item.location && item.location.trim() !== '' && item.location.trim() !== '-';
    const locHtml = hasLoc ? ` <span class="loc-sub">(${escapeHtml(item.location)})</span>` : '';
    div.innerHTML = `<div><strong>${escapeHtml(item.name)}</strong>${locHtml}</div>`;
    div.onclick = () => {
      document.getElementById('cfgFototree').value = item.name;
      const tagId = item.tag_id || item.id || "";
      document.getElementById('cfgTagId').value = tagId;
      if (currentConfig) {
        currentConfig.tag_id = tagId;
        currentConfig.fototree = item.name;
        currentConfig.location_name = item.name;
      }
      document.getElementById('cfgLat').value = item.lat || "0.0";
      document.getElementById('cfgLon').value = item.lon || "0.0";
      document.getElementById('dispLat').value = item.lat || "0.0";
      document.getElementById('dispLon').value = item.lon || "0.0";
      dropdown.style.display = 'none';
      checkFormValidity();
    };
    dropdown.appendChild(div);
  });
  dropdown.style.display = 'block';
}

document.addEventListener('click', (e) => {
  const container = document.querySelector('.autocomplete-container');
  if (container && !container.contains(e.target)) {
    const dropdown = document.getElementById('treeDropdown');
    if (dropdown) dropdown.style.display = 'none';
  }
});

// --------------------------------------------------------------------------
// DIRECTORY BROWSING & ACTIONS
// --------------------------------------------------------------------------
function browseSourceFolder() {
  if (window.pywebview && window.pywebview.api) {
    const cur = document.getElementById('cfgUploadDir').value;
    window.pywebview.api.pick_source_dir(cur).then(dir => {
      if (dir) {
        document.getElementById('cfgUploadDir').value = dir;
        // Realtime queue update immediately on change!
        window.pywebview.api.refresh_queue().then(res => {
          if (res && res.text) document.getElementById('queueText').textContent = res.text;
        });
        checkFormValidity();
      }
    });
  }
}

function browseCompleteFolder() {
  if (window.pywebview && window.pywebview.api) {
    const cur = document.getElementById('cfgCompleteDir').value;
    window.pywebview.api.pick_complete_dir(cur).then(dir => {
      if (dir) {
        document.getElementById('cfgCompleteDir').value = dir;
        checkFormValidity();
      }
    });
  }
}

let savedSourcePath = "";
let savedCompletePath = "";

function updateFolderOpenButtons() {
  const src = (document.getElementById('cfgUploadDir').value || '').trim();
  const comp = (document.getElementById('cfgCompleteDir').value || '').trim();

  const btnOpenSrc = document.getElementById('btnOpenSource');
  if (btnOpenSrc) {
    const isSrcSaved = Boolean(src && savedSourcePath && src === savedSourcePath);
    btnOpenSrc.disabled = !isSrcSaved;
    btnOpenSrc.title = isSrcSaved ? "Buka folder di File Explorer" : "Kolom harus terisi dan disimpan / hasil load preset";
  }

  const btnOpenComp = document.getElementById('btnOpenComplete');
  if (btnOpenComp) {
    const isCompSaved = Boolean(comp && savedCompletePath && comp === savedCompletePath);
    btnOpenComp.disabled = !isCompSaved;
    btnOpenComp.title = isCompSaved ? "Buka folder di File Explorer" : "Kolom harus terisi dan disimpan / hasil load preset";
  }
}

function openSourceFolder() {
  const p = (document.getElementById('cfgUploadDir').value || '').trim();
  if (window.pywebview && window.pywebview.api && p) {
    window.pywebview.api.open_folder(p);
  }
}

function openCompleteFolder() {
  const p = (document.getElementById('cfgCompleteDir').value || '').trim();
  if (window.pywebview && window.pywebview.api && p) {
    window.pywebview.api.open_folder(p);
  }
}

function onFolderInputChanged(type) {
  if (type === 'source' && window.pywebview && window.pywebview.api) {
    window.pywebview.api.refresh_queue().then(res => {
      if (res && res.text) document.getElementById('queueText').textContent = res.text;
    });
  }
  checkFormValidity();
}

// --------------------------------------------------------------------------
// FORM VALIDITY & CONFIG SAVE
// --------------------------------------------------------------------------
function checkFormValidity() {
  const u = (document.getElementById('cfgUsername').value || '').trim();
  const p = (document.getElementById('cfgPassword').value || '');
  const price = parseInt(document.getElementById('cfgPrice').value || '0', 10);
  const tree = (document.getElementById('cfgFototree').value || '').trim();
  const src = (document.getElementById('cfgUploadDir').value || '').trim();
  const comp = (document.getElementById('cfgCompleteDir').value || '').trim();

  updateFolderOpenButtons();

  // Check required fields for Mulai button
  const isValid = Boolean(u && p && price > 0 && tree && src && comp);
  const btnStart = document.getElementById('btnStartStop');
  if (btnStart && !isRunning) {
    btnStart.disabled = !isValid;
    btnStart.title = isValid ? "Klik untuk memulai proses kompresi & upload" : "Lengkapi semua isian form untuk memulai";
  }
}

function openSaveSuccessModal() {
  const modal = document.getElementById('modalSaveSuccess');
  if (modal) modal.classList.add('active');
}

function closeSaveSuccessModal() {
  const modal = document.getElementById('modalSaveSuccess');
  if (modal) modal.classList.remove('active');
}

function saveConfiguration() {
  const data = getFormData();
  const btn = document.getElementById('btnSaveConfig');
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.save_config(data).then(ok => {
      if (ok) {
        savedSourcePath = (document.getElementById('cfgUploadDir').value || '').trim();
        savedCompletePath = (document.getElementById('cfgCompleteDir').value || '').trim();
        updateFolderOpenButtons();
        openSaveSuccessModal();
        if (btn) {
          const oldText = btn.textContent;
          btn.textContent = "✔️ Konfigurasi Tersimpan!";
          setTimeout(() => { btn.textContent = oldText; }, 1600);
        }
      }
    });
  }
}

// --------------------------------------------------------------------------
// PRESETS MANAGEMENT (Modal Custom Sesuai Color Scheme)
// --------------------------------------------------------------------------
function renderPresets(presets, selName = "") {
  currentPresets = presets || {};
  const sel = document.getElementById('presetSelect');
  sel.innerHTML = '<option value="">-- Pilih Preset --</option>';
  Object.keys(currentPresets).forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    if (name === selName) opt.selected = true;
    sel.appendChild(opt);
  });
}

function onPresetSelected() {
  const sel = document.getElementById('presetSelect');
  const name = sel.value;
  if (!name || !currentPresets[name]) return;
  const p = currentPresets[name];
  if (currentConfig) {
    currentConfig.active_preset = name;
  }
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_active_preset) {
    window.pywebview.api.set_active_preset(name);
  }
  if (p.username) document.getElementById('cfgUsername').value = p.username;
  if (p.password) document.getElementById('cfgPassword').value = p.password;
  if (p.price) {
    document.getElementById('cfgPrice').value = p.price;
  }
  if (p.fototree || p.location_name) document.getElementById('cfgFototree').value = p.fototree || p.location_name;
  if (p.tag_id) {
    document.getElementById('cfgTagId').value = p.tag_id;
    if (currentConfig) currentConfig.tag_id = p.tag_id;
  }
  const lat = p.lat || p.latitude || "0.0";
  const lon = p.lon || p.longitude || "0.0";
  document.getElementById('cfgLat').value = lat;
  document.getElementById('dispLat').value = lat;
  document.getElementById('cfgLon').value = lon;
  document.getElementById('dispLon').value = lon;

  if (p.desc || p.description) document.getElementById('cfgDesc').value = p.desc || p.description;
  if (p.mode) document.getElementById('cfgMode').value = p.mode;

  const upDir = p.upload_dir || p.source_dir || "";
  document.getElementById('cfgUploadDir').value = upDir;
  savedSourcePath = upDir.trim();

  const compDir = p.complete_dir || "";
  document.getElementById('cfgCompleteDir').value = compDir;
  savedCompletePath = compDir.trim();

  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.refresh_queue();
  }
  updateFolderOpenButtons();
  checkFormValidity();
}

function getFormData() {
  const sel = document.getElementById('presetSelect');
  const activePreset = sel ? sel.value : "";
  const tagId = document.getElementById('cfgTagId').value.trim() || (currentConfig && currentConfig.tag_id) || "";
  return {
    active_preset: activePreset,
    username: document.getElementById('cfgUsername').value.trim(),
    password: document.getElementById('cfgPassword').value,
    price: document.getElementById('cfgPrice').value.trim(),
    fototree: document.getElementById('cfgFototree').value.trim(),
    lat: document.getElementById('cfgLat').value.trim() || document.getElementById('dispLat').value.trim(),
    lon: document.getElementById('cfgLon').value.trim() || document.getElementById('dispLon').value.trim(),
    desc: document.getElementById('cfgDesc').value.trim(),
    mode: document.getElementById('cfgMode').value,
    source_dir: document.getElementById('cfgUploadDir').value.trim(),
    upload_dir: document.getElementById('cfgUploadDir').value.trim(),
    complete_dir: document.getElementById('cfgCompleteDir').value.trim(),
    tag_id: tagId,
    currency_id: (currentConfig && currentConfig.currency_id) ? currentConfig.currency_id : "MR9Xdjv70WpeODE8DEPLAyb8xVBQzq31"
  };
}

function openPresetModal() {
  const modal = document.getElementById('modalPresetName');
  const input = document.getElementById('inputPresetName');
  if (input) input.value = '';
  if (modal) modal.classList.add('active');
  setTimeout(() => { if (input) input.focus(); }, 100);
}

function closePresetModal() {
  const modal = document.getElementById('modalPresetName');
  if (modal) modal.classList.remove('active');
}

function confirmSavePreset() {
  const input = document.getElementById('inputPresetName');
  const name = input ? input.value.trim() : '';
  if (!name) {
    alert("Masukkan nama preset terlebih dahulu.");
    return;
  }
  const cfg = getFormData();
  cfg.active_preset = name;
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.save_preset(name, cfg).then(presets => {
      renderPresets(presets, name);
      closePresetModal();
      if (window.pywebview.api.set_active_preset) {
        window.pywebview.api.set_active_preset(name);
      }
    });
  }
}

function deletePreset() {
  const sel = document.getElementById('presetSelect');
  const name = sel.value;
  if (!name) {
    alert("Pilih preset yang ingin dihapus terlebih dahulu.");
    return;
  }
  if (confirm(`Yakin ingin menghapus preset '${name}'?`)) {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.delete_preset(name).then(presets => {
        renderPresets(presets);
      });
    }
  }
}

// --------------------------------------------------------------------------
// ANTI-DUPLIKAT MODAL
// --------------------------------------------------------------------------
function openDuplicateModal() {
  const modal = document.getElementById('modalDuplicates');
  if (modal) modal.classList.add('active');
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.get_duplicate_count().then(count => {
      document.getElementById('dupCountText').textContent = `Total foto terindeks dalam Anti-Duplikat: ${count || 0} Foto`;
    });
  }
}

function closeDuplicateModal() {
  const modal = document.getElementById('modalDuplicates');
  if (modal) modal.classList.remove('active');
}

function reindexDuplicates() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.reindex_duplicates().then(count => {
      document.getElementById('dupCountText').textContent = `Total foto terindeks dalam Anti-Duplikat: ${count || 0} Foto`;
    });
  }
}

function clearDuplicates() {
  if (confirm("Yakin ingin menghapus seluruh riwayat Anti-Duplikat?\nFoto yang sama akan bisa dikompres dan diunggah ulang.")) {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.clear_duplicates().then(count => {
        document.getElementById('dupCountText').textContent = `Total foto terindeks dalam Anti-Duplikat: ${count || 0} Foto`;
      });
    }
  }
}

// --------------------------------------------------------------------------
// PROCESS CONTROL (Mulai / Berhenti)
// --------------------------------------------------------------------------
function toggleStartStop() {
  const btn = document.getElementById('btnStartStop');
  if (!isRunning) {
    const data = getFormData();
    if (!data.username || !data.password) {
      alert("Username dan password tidak boleh kosong!");
      return;
    }
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.start_upload(data).then(ok => {
        if (ok) {
          isRunning = true;
          btn.disabled = false;
          btn.textContent = "🛑 Berhenti";
          btn.classList.add("running");
        }
      });
    }
  } else {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.stop_upload().then(() => {
        isRunning = false;
        btn.textContent = "🚀 Mulai Proses";
        btn.classList.remove("running");
        checkFormValidity();
      });
    }
  }
}

function resetCounters() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.reset_counters();
  }
}

function retryFailed() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.retry_failed();
  }
}

// --------------------------------------------------------------------------
// LOG DRAWER & FILTERING
// --------------------------------------------------------------------------
function toggleLog() {
  isLogOpen = !isLogOpen;
  const drawer = document.getElementById('logDrawer');
  const arrow = document.getElementById('logToggleArrow');
  if (isLogOpen) {
    drawer.style.display = 'flex';
    arrow.textContent = "▼ Tutup Log";
  } else {
    drawer.style.display = 'none';
    arrow.textContent = "▲ Buka Log";
  }
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.toggle_log(isLogOpen);
  }
}

function filterLogs(status) {
  currentLogFilter = status;
  document.querySelectorAll('.log-filters .filter-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === status);
  });
  renderLogs();
}

function clearLogs() {
  allLogs = [];
  document.getElementById('tbodyLogs').innerHTML = '';
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.clear_logs();
  }
}

function renderLogs() {
  const tbody = document.getElementById('tbodyLogs');
  tbody.innerHTML = '';
  const filtered = (currentLogFilter === 'all')
    ? allLogs
    : allLogs.filter(l => (l.status || '').toLowerCase() === currentLogFilter.toLowerCase());

  filtered.slice(-200).forEach(l => {
    const tr = document.createElement('tr');
    let colorClass = "text-info";
    if (l.status === 'ok') colorClass = "text-success";
    else if (l.status === 'fail') colorClass = "text-danger";
    else if (l.status === 'skip') colorClass = "text-warning";

    tr.innerHTML = `
      <td>${escapeHtml(l.stamp)}</td>
      <td><strong>${escapeHtml(l.action)}</strong></td>
      <td title="${escapeHtml(l.filename)}">${escapeHtml(l.filename || '-')}</td>
      <td title="${escapeHtml(l.info)}">${escapeHtml(l.info || '')}</td>
      <td class="td-center ${colorClass}"><strong>${escapeHtml((l.status || 'INFO').toUpperCase())}</strong></td>
    `;
    tbody.appendChild(tr);
  });
  const wrap = document.querySelector('.log-table-wrap');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

// --------------------------------------------------------------------------
// PYWEBVIEW CALLBACKS (Called from Python backend)
// --------------------------------------------------------------------------
window.onStatusUpdate = function(data) {
  const textEl = document.getElementById('statusText');
  const dotEl = document.getElementById('statusDot');
  if (textEl) textEl.textContent = data.text || "";
  if (dotEl) {
    dotEl.className = 'status-dot ' + (data.color || 'green');
  }
  if (data.text && (data.text.includes("Dihentikan") || data.text.includes("Selesai"))) {
    isRunning = false;
    const btn = document.getElementById('btnStartStop');
    if (btn) {
      btn.textContent = "🚀 Mulai Proses";
      btn.classList.remove("running");
      checkFormValidity();
    }
  }
};

window.onQueueUpdate = function(data) {
  const qEl = document.getElementById('queueText');
  if (qEl && data && data.text) {
    qEl.textContent = data.text;
  }
};

window.onProgressUpdate = function(pct) {
  const p = Math.max(0, Math.min(100, parseFloat(pct) || 0));
  const fill = document.getElementById('pbFill');
  const pctEl = document.getElementById('pbPct');
  const lblEl = document.getElementById('pbLabel');
  if (fill) fill.style.width = p + "%";
  if (pctEl) pctEl.textContent = Math.round(p) + "%";
  if (lblEl) lblEl.textContent = (p >= 100) ? "Selesai" : (p > 0 ? "Memproses..." : "Menunggu proses...");
};

window.onCounterUpdate = function(c) {
  if (!c) return;
  document.getElementById('cntCompOk').textContent = c.c_ok || 0;
  document.getElementById('cntCompFail').textContent = c.c_fail || 0;
  document.getElementById('cntUpOk').textContent = c.u_ok || 0;
  document.getElementById('cntUpFail').textContent = c.u_fail || 0;
};

window.onLogRow = function(row, counts) {
  if (!row) return;
  allLogs.push(row);
  if (allLogs.length > 2000) allLogs.shift();
  if (counts) {
    document.getElementById('logSummaryText').textContent =
      `Log Aktivitas (OK: ${counts.ok || 0} | Gagal: ${counts.fail || 0} | Info: ${counts.info || 0} | Skip: ${counts.skip || 0})`;
  }
  if (currentLogFilter === 'all' || (row.status || '').toLowerCase() === currentLogFilter.toLowerCase()) {
    const tbody = document.getElementById('tbodyLogs');
    const tr = document.createElement('tr');
    let colorClass = "text-info";
    if (row.status === 'ok') colorClass = "text-success";
    else if (row.status === 'fail') colorClass = "text-danger";
    else if (row.status === 'skip') colorClass = "text-warning";

    tr.innerHTML = `
      <td>${escapeHtml(row.stamp)}</td>
      <td><strong>${escapeHtml(row.action)}</strong></td>
      <td title="${escapeHtml(row.filename)}">${escapeHtml(row.filename || '-')}</td>
      <td title="${escapeHtml(row.info)}">${escapeHtml(row.info || '')}</td>
      <td class="td-center ${colorClass}"><strong>${escapeHtml((row.status || 'INFO').toUpperCase())}</strong></td>
    `;
    tbody.appendChild(tr);
    const wrap = document.querySelector('.log-table-wrap');
    if (wrap) wrap.scrollTop = wrap.scrollHeight;
  }
};

// --------------------------------------------------------------------------
// STATISTIK MODULE
// --------------------------------------------------------------------------
function switchStatSubTab(tab) {
  document.querySelectorAll('.subtabs-bar .subtab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.subtab-content').forEach(c => c.classList.remove('active'));

  if (tab === 'dates') {
    document.getElementById('subtabBtnDates').classList.add('active');
    document.getElementById('subtabDates').classList.add('active');
  } else if (tab === 'leaderboard') {
    document.getElementById('subtabBtnLeaderboard').classList.add('active');
    document.getElementById('subtabLeaderboard').classList.add('active');
    renderLeaderboard();
  } else if (tab === 'sold') {
    document.getElementById('subtabBtnSold').classList.add('active');
    document.getElementById('subtabSold').classList.add('active');
    renderSoldTable();
  }
}

let statProgressTimeout = null;

function setStatProgress(pct, label, show = true) {
  const box = document.getElementById('statProgressBox');
  const fill = document.getElementById('statPbFill');
  const pctEl = document.getElementById('statPbPct');
  const lblEl = document.getElementById('statPbLabel');
  if (!box || !fill || !pctEl || !lblEl) return;
  if (!show) {
    box.style.display = 'none';
    return;
  }
  if (statProgressTimeout) {
    clearTimeout(statProgressTimeout);
    statProgressTimeout = null;
  }
  box.style.display = 'block';
  const val = Math.min(100, Math.max(0, Math.round(pct)));
  fill.style.width = val + '%';
  pctEl.textContent = val + '%';
  if (label) lblEl.textContent = label;
}

window.onStatProgress = function(pct, label) {
  setStatProgress(pct, label, true);
  if (pct >= 100) {
    statProgressTimeout = setTimeout(() => {
      setStatProgress(0, "", false);
    }, 3500);
  }
};

function openModalConfirmUpdateAll() {
  document.getElementById('modalConfirmUpdateAll').classList.add('active');
}

function closeModalConfirmUpdateAll() {
  document.getElementById('modalConfirmUpdateAll').classList.remove('active');
}

function proceedUpdateAllData() {
  closeModalConfirmUpdateAll();
  loadStatistics(true);
}

function loadStatistics(force = false) {
  const u = (document.getElementById('cfgUsername').value || '').trim();
  const p = (document.getElementById('cfgPassword').value || '');
  const tbody = document.getElementById('tbodyDates');
  if (tbody && (!currentStatistics || !currentStatistics.dates || currentStatistics.dates.length === 0)) {
    tbody.innerHTML = '<tr><td colspan="10" class="td-center" style="padding: 24px;">Memuat data statistik dari FotoYu... Mohon tunggu.</td></tr>';
  }
  setStatProgress(5, force ? "Memulai Update All Data dari FotoYu..." : "Membaca cache statistik / memeriksa update...", true);
  if (window.pywebview && window.pywebview.api) {
    window.onStatusUpdate({ text: force ? "Memperbarui seluruh data statistik FotoYu..." : "Memuat data statistik dari FotoYu...", color: "yellow" });
    window.pywebview.api.fetch_statistics(force, u, p).then(data => {
      if (data) {
        currentStatistics = data;
        applyStatFilter();
        setStatProgress(100, "Data statistik berhasil dimuat.", true);
        statProgressTimeout = setTimeout(() => {
          setStatProgress(0, "", false);
        }, 3500);
        window.onStatusUpdate({ text: "Data statistik berhasil diperbarui.", color: "green" });
      } else {
        if (tbody && (!currentStatistics || !currentStatistics.dates)) {
          tbody.innerHTML = '<tr><td colspan="10" class="td-center" style="padding: 24px; color: var(--danger);">Gagal memuat statistik. Pastikan username & password benar dan koneksi stabil.</td></tr>';
        }
        setStatProgress(100, "Gagal memuat statistik. Cek koneksi/login.", true);
        statProgressTimeout = setTimeout(() => {
          setStatProgress(0, "", false);
        }, 4000);
        window.onStatusUpdate({ text: "Gagal memuat statistik. Cek koneksi/login.", color: "red" });
      }
    }).catch(err => {
      if (tbody && (!currentStatistics || !currentStatistics.dates)) {
        tbody.innerHTML = `<tr><td colspan="10" class="td-center" style="padding: 24px; color: var(--danger);">Error memuat statistik: ${escapeHtml(err)}</td></tr>`;
      }
      setStatProgress(100, "Error memuat statistik: " + err, true);
      statProgressTimeout = setTimeout(() => {
        setStatProgress(0, "", false);
      }, 4000);
      window.onStatusUpdate({ text: "Error memuat statistik: " + err, color: "red" });
    });
  }
}

function formatDateInput(input) {
  let v = input.value.replace(/[^\d]/g, '');
  if (v.length > 8) v = v.slice(0, 8);
  let formatted = '';
  if (v.length > 0) formatted += v.slice(0, 2);
  if (v.length > 2) formatted += '/' + v.slice(2, 4);
  if (v.length > 4) formatted += '/' + v.slice(4, 8);
  input.value = formatted;
  if (formatted.length === 10) {
    applyStatFilterFromDisplay();
  }
}

function openNativeDatePicker(type) {
  const nativeEl = document.getElementById(type === 'start' ? 'statDateStartNative' : 'statDateEndNative');
  if (nativeEl) {
    if (typeof nativeEl.showPicker === 'function') {
      try { nativeEl.showPicker(); } catch (e) { nativeEl.click(); }
    } else {
      nativeEl.click();
    }
  }
}

function syncNativeToDisp(type) {
  const nativeEl = document.getElementById(type === 'start' ? 'statDateStartNative' : 'statDateEndNative');
  const dispEl = document.getElementById(type === 'start' ? 'statDateStartDisp' : 'statDateEndDisp');
  if (!nativeEl || !dispEl) return;
  const val = nativeEl.value; // YYYY-MM-DD
  if (val && val.length === 10) {
    const parts = val.split('-');
    dispEl.value = `${parts[2]}/${parts[1]}/${parts[0]}`;
  } else {
    dispEl.value = '';
  }
  applyStatFilterFromDisplay();
}

function parseDispToIso(dispVal) {
  if (!dispVal) return '';
  const parts = dispVal.split('/');
  if (parts.length === 3 && parts[0].length === 2 && parts[1].length === 2 && parts[2].length === 4) {
    return `${parts[2]}-${parts[1]}-${parts[0]}`;
  }
  return '';
}

function applyStatFilterFromDisplay() {
  const sEl = document.getElementById('statDateStartDisp');
  const eEl = document.getElementById('statDateEndDisp');
  const dispStart = sEl ? sEl.value.trim() : '';
  const dispEnd = eEl ? eEl.value.trim() : '';
  const isoStart = parseDispToIso(dispStart);
  const isoEnd = parseDispToIso(dispEnd);
  applyStatFilter(isoStart, isoEnd);
}

function applyStatFilter(dStart, dEnd) {
  if (!currentStatistics) return;
  if (dStart === undefined) {
    const sEl = document.getElementById('statDateStartDisp');
    dStart = sEl ? parseDispToIso(sEl.value.trim()) : '';
  }
  if (dEnd === undefined) {
    const eEl = document.getElementById('statDateEndDisp');
    dEnd = eEl ? parseDispToIso(eEl.value.trim()) : '';
  }

  let filteredDates = currentStatistics.dates || [];
  if (dStart) {
    filteredDates = filteredDates.filter(r => r.raw_date >= dStart);
  }
  if (dEnd) {
    filteredDates = filteredDates.filter(r => r.raw_date <= dEnd);
  }

  // Recalculate 8 KPI summary cards dynamically
  let tPhotos = 0, tFaces = 0, tAkPlus = 0, tAkMin = 0, tKonfirm = 0, tSold = 0, tRev = 0;
  filteredDates.forEach(r => {
    tPhotos += (r.photos || 0);
    tFaces += (r.faces || 0);
    tAkPlus += (r.ak_plus || 0);
    tAkMin += (r.ak_min || 0);
    tKonfirm += (r.konfirm || 0);
    tSold += (r.sold || 0);
    tRev += (r.est_rev || 0);
  });
  const convPct = (tSold / Math.max(1, tPhotos)) * 100;

  document.getElementById('statTotalPhotos').textContent = tPhotos.toLocaleString();
  document.getElementById('statTotalFaces').textContent = tFaces.toLocaleString();
  document.getElementById('statTotalAkPlus').textContent = tAkPlus.toLocaleString();
  document.getElementById('statTotalAkMin').textContent = tAkMin.toLocaleString();
  document.getElementById('statTotalKonfirm').textContent = tKonfirm.toLocaleString();
  document.getElementById('statTotalSold').textContent = tSold.toLocaleString();
  document.getElementById('statTotalRev').textContent = formatIdr(tRev);
  document.getElementById('statConvRate').textContent = convPct.toFixed(1) + "%";

  const storageTxt = (currentStatistics.summary && currentStatistics.summary.storage_text) ? currentStatistics.summary.storage_text : "-";
  document.getElementById('statStorageBadge').textContent = `Penyimpanan: ${storageTxt}`;

  // Render Table Dates
  const tbody = document.getElementById('tbodyDates');
  tbody.innerHTML = '';
  filteredDates.forEach(r => {
    const tr = document.createElement('tr');
    tr.title = "Dobel klik untuk melihat rincian foto";
    tr.ondblclick = () => openPhotoDetailModal(r.raw_date, r.date);

    tr.innerHTML = `
      <td><strong>${escapeHtml(r.date)}</strong></td>
      <td class="td-right">${r.photos || 0}</td>
      <td class="td-right">${r.faces || 0}</td>
      <td class="td-right">${r.ak_plus || 0}</td>
      <td class="td-right">${r.ak_min || 0}</td>
      <td class="td-right">${r.konfirm || 0}</td>
      <td class="td-right text-success"><strong>${r.sold || 0}</strong></td>
      <td class="td-right text-success">${formatIdr(r.est_rev || 0)}</td>
      <td>${escapeHtml(r.desc || '-')}</td>
      <td class="td-center">
        <button class="btn btn-secondary" style="height: 22px; line-height: 22px; padding: 0 6px; font-size: 11px;" onclick="openPhotoDetailModal('${r.raw_date}', '${r.date}')">Rincian</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  renderLeaderboard();
  renderSoldTable();
}

function resetStatFilter() {
  const sDisp = document.getElementById('statDateStartDisp');
  const eDisp = document.getElementById('statDateEndDisp');
  const sNat = document.getElementById('statDateStartNative');
  const eNat = document.getElementById('statDateEndNative');
  if (sDisp) sDisp.value = '';
  if (eDisp) eDisp.value = '';
  if (sNat) sNat.value = '';
  if (eNat) eNat.value = '';
  applyStatFilter('', '');
}

// --------------------------------------------------------------------------
// LEADERBOARD (No Icons Inside Tabs, Full Width Table)
// --------------------------------------------------------------------------
function setLeaderboardMode(mode) {
  currentLbMode = mode;
  document.getElementById('btnLbTree').classList.toggle('active', mode === 'tree');
  document.getElementById('btnLbDate').classList.toggle('active', mode === 'date');
  document.getElementById('thLbName').textContent = (mode === 'tree') ? "Nama Fototree" : "Tanggal";
  renderLeaderboard();
}

function renderLeaderboard() {
  if (!currentStatistics || !currentStatistics.leaderboard) return;
  const list = (currentLbMode === 'tree')
    ? (currentStatistics.leaderboard.by_tree || [])
    : (currentStatistics.leaderboard.by_date || []);

  const chartEl = document.getElementById('lbChart');
  const tbody = document.getElementById('tbodyLeaderboard');
  chartEl.innerHTML = '';
  tbody.innerHTML = '';

  const maxSold = list.length > 0 ? Math.max(...list.map(x => x.sold || 0), 1) : 1;

  list.slice(0, 10).forEach((item, idx) => {
    // Bar row
    const pct = Math.round(((item.sold || 0) / maxSold) * 100);
    const rowDiv = document.createElement('div');
    rowDiv.className = 'chart-bar-row';
    rowDiv.innerHTML = `
      <span class="chart-label" title="${escapeHtml(item.name)}">${idx + 1}. ${escapeHtml(item.name)}</span>
      <div class="chart-bar-track">
        <div class="chart-bar-value" style="width: ${pct}%"></div>
      </div>
      <span class="chart-count">${item.sold || 0} Foto</span>
    `;
    chartEl.appendChild(rowDiv);

    // Table row
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="td-center">${idx + 1}</td>
      <td><strong>${escapeHtml(item.name)}</strong></td>
      <td class="td-right text-success"><strong>${item.sold || 0}</strong></td>
      <td class="td-right text-success">${formatIdr(item.est_rev || 0)}</td>
    `;
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------------------------
// SEMUA FOTO TERJUAL TABLE
// --------------------------------------------------------------------------
function renderSoldTable() {
  if (!currentStatistics || !currentStatistics.sold_photos) return;
  const tbody = document.getElementById('tbodySold');
  tbody.innerHTML = '';

  if (currentStatistics.sold_photos.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="td-center" style="padding: 24px;">Belum ada foto terjual.</td></tr>';
    return;
  }

  currentStatistics.sold_photos.forEach(item => {
    const tr = document.createElement('tr');
    const thumbUrl = item.thumbnail_url || item.image_url || "";
    const cid = item.content_id || "";
    const title = item.title || cid || "Foto";
    const dateStr = (item.original_at || item.created_at || "").slice(0, 10);
    const tree = item.fototree || "-";
    const price = item.price || 0;
    const net = item.net_price || 0;

    tr.innerHTML = `
      <td title="${escapeHtml(title)}">${escapeHtml(title)}</td>
      <td>${escapeHtml(tree)}</td>
      <td>${escapeHtml(dateStr)}</td>
      <td>${formatIdr(price)}</td>
      <td class="text-success"><strong>${formatIdr(net)}</strong></td>
      <td>
        <button class="btn btn-secondary" style="height: 22px; line-height: 22px; padding: 0 8px; font-size: 11px;" onclick="openZoomModal('${cid}', '${escapeHtml(title)}', '${thumbUrl}')">Lihat</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------------------------
// MODAL: RINCIAN FOTO
// --------------------------------------------------------------------------
function openPhotoDetailModal(rawDate, dispDate) {
  const modal = document.getElementById('modalPhotoDetail');
  document.getElementById('modalDateTitle').textContent = dispDate || rawDate;
  modal.classList.add('active');

  const tbody = document.getElementById('tbodyModalPhotos');
  tbody.innerHTML = '<tr><td colspan="10" class="td-center" style="padding: 20px;">Memuat rincian foto...</td></tr>';

  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.get_photo_details(rawDate).then(res => {
      if (res) {
        currentModalItems = res.items || [];
        // Header statistics: User specified "Terjual: x Foto" (capital F)
        document.getElementById('modalBadgeTotal').textContent = `Total: ${res.stats.total_photos || 0} Foto`;
        document.getElementById('modalBadgeSold').textContent = `Terjual: ${res.stats.sold_count || 0} Foto`;
        document.getElementById('modalBadgeEstRev').textContent = `Estimasi Omset: ${formatIdr(res.stats.est_rev || 0)}`;
        filterModalPhotos('all');
      }
    });
  }
}

function closePhotoDetailModal() {
  document.getElementById('modalPhotoDetail').classList.remove('active');
}

function filterModalPhotos(filter) {
  currentModalFilter = filter;
  document.querySelectorAll('.modal-body .filter-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mfilter === filter);
  });

  const tbody = document.getElementById('tbodyModalPhotos');
  tbody.innerHTML = '';

  let filtered = currentModalItems;
  if (filter === 'sold') {
    filtered = currentModalItems.filter(x => x.purchase_status === 'sold');
  } else if (filter === 'available') {
    filtered = currentModalItems.filter(x => x.purchase_status !== 'sold');
  }

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="td-center" style="padding: 20px;">Tidak ada data foto pada filter ini.</td></tr>';
    return;
  }

  filtered.forEach(it => {
    const tr = document.createElement('tr');
    const isSold = (it.purchase_status === 'sold');
    const statusText = isSold ? '<span class="text-success"><strong>Terjual</strong></span>' : '<span class="text-muted">Tersedia</span>';
    const cid = it.content_id || "";
    const title = it.title || cid || "Foto";

    tr.innerHTML = `
      <td title="${escapeHtml(title)}">${escapeHtml(title)}</td>
      <td>${escapeHtml(it.fototree || '-')}</td>
      <td>${escapeHtml(it.dim_str || '-')}</td>
      <td>${escapeHtml(it.size_str || '-')}</td>
      <td>${it.face_count || 0}</td>
      <td>${escapeHtml(it.robofind_status || 'OK')}</td>
      <td>${statusText}</td>
      <td>${formatIdr(it.price || 0)}</td>
      <td class="text-success"><strong>${formatIdr(it.net_price || 0)}</strong></td>
      <td>
        <button class="btn btn-secondary" style="height: 22px; line-height: 22px; padding: 0 8px; font-size: 11px;" onclick="openZoomModal('${cid}', '${escapeHtml(title)}')">Lihat</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------------------------
// MODAL: ZOOM PREVIEW (Orientation & Scheme Preserved)
// --------------------------------------------------------------------------
function openZoomModal(contentId, title, fallbackUrl = "") {
  const modal = document.getElementById('modalZoom');
  const img = document.getElementById('zoomImg');
  const cap = document.getElementById('zoomCaption');

  img.src = "";
  cap.textContent = `Memuat pratinjau: ${title || contentId}...`;
  modal.classList.add('active');

  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.get_image_preview_info(contentId, title).then(info => {
      if (info && info.preview_url) {
        img.src = info.preview_url;
        cap.textContent = `${info.title} (${info.dim_str} | ${info.size_str})`;
      } else if (fallbackUrl) {
        img.src = fallbackUrl;
        cap.textContent = title;
      } else {
        cap.textContent = `Gagal memuat pratinjau untuk: ${title || contentId}`;
      }
    }).catch(() => {
      if (fallbackUrl) {
        img.src = fallbackUrl;
        cap.textContent = title;
      } else {
        cap.textContent = `Tidak dapat menampilkan pratinjau: ${title || contentId}`;
      }
    });
  } else if (fallbackUrl) {
    img.src = fallbackUrl;
    cap.textContent = title;
  }
}

function closeZoomModal(event, force = false) {
  if (force || event.target.id === 'modalZoom') {
    document.getElementById('modalZoom').classList.remove('active');
  }
}

// --------------------------------------------------------------------------
// INITIAL LOAD FROM BACKEND
// --------------------------------------------------------------------------
let isInitialStateLoaded = false;
let initRetryCount = 0;

function applyInitialData(data) {
  if (!data) return;
  isInitialStateLoaded = true;
  currentConfig = data.config || {};
  currentPresets = data.presets || {};
  const activePreset = currentConfig.active_preset || "";
  renderPresets(currentPresets, activePreset);

  if (activePreset && currentPresets[activePreset]) {
    document.getElementById('presetSelect').value = activePreset;
    onPresetSelected();
  } else {
    // Fill inputs from config
    if (currentConfig.username) document.getElementById('cfgUsername').value = currentConfig.username;
    if (currentConfig.password) document.getElementById('cfgPassword').value = currentConfig.password;
    if (currentConfig.price) {
      document.getElementById('cfgPrice').value = currentConfig.price;
    }
    if (currentConfig.fototree || currentConfig.location_name) {
      document.getElementById('cfgFototree').value = currentConfig.fototree || currentConfig.location_name;
    }
    if (currentConfig.tag_id) {
      document.getElementById('cfgTagId').value = currentConfig.tag_id;
    }
    const lat = currentConfig.lat || currentConfig.latitude || "0.0";
    const lon = currentConfig.lon || currentConfig.longitude || "0.0";
    document.getElementById('cfgLat').value = lat;
    document.getElementById('dispLat').value = lat;
    document.getElementById('cfgLon').value = lon;
    document.getElementById('dispLon').value = lon;

    if (currentConfig.desc || currentConfig.description) {
      document.getElementById('cfgDesc').value = currentConfig.desc || currentConfig.description;
    }
    if (currentConfig.mode) document.getElementById('cfgMode').value = currentConfig.mode;
    const upDir = currentConfig.upload_dir || currentConfig.source_dir || "";
    if (upDir) {
      document.getElementById('cfgUploadDir').value = upDir;
      savedSourcePath = upDir.trim();
    }
    const compDir = currentConfig.complete_dir || "";
    if (compDir) {
      document.getElementById('cfgCompleteDir').value = compDir;
      savedCompletePath = compDir.trim();
    }
    updateFolderOpenButtons();
    checkFormValidity();
  }

  if (data.counters) window.onCounterUpdate(data.counters);
  if (data.queue_text) document.getElementById('queueText').textContent = data.queue_text;
  if (data.status) window.onStatusUpdate(data.status);
  if (currentConfig.theme) setTheme(currentConfig.theme);
}

/*__INITIAL_DATA_PLACEHOLDER__*/
if (typeof window.__INITIAL_DATA__ !== 'undefined' && window.__INITIAL_DATA__) {
  applyInitialData(window.__INITIAL_DATA__);
}

function tryLoadInitialState() {
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.get_initial_data().then(data => {
      applyInitialData(data);
    }).catch(() => {});
  } else if (initRetryCount < 60) {
    initRetryCount++;
    setTimeout(tryLoadInitialState, 100);
  }
}

// Background queue polling from JS side (3-second interval, purely non-blocking)
setInterval(() => {
  if (window.pywebview && window.pywebview.api && !isRunning) {
    window.pywebview.api.refresh_queue().then(res => {
      if (res && res.text) {
        const qEl = document.getElementById('queueText');
        if (qEl) qEl.textContent = res.text;
      }
    }).catch(() => {});
  }
}, 3000);

window.addEventListener('pywebviewready', tryLoadInitialState);
window.addEventListener('DOMContentLoaded', tryLoadInitialState);
setTimeout(tryLoadInitialState, 50);
setTimeout(tryLoadInitialState, 250);
setTimeout(tryLoadInitialState, 800);

// ==========================================================================
// UNIVERSAL TABLE HEADER SORTING (Click th to Sort Ascending/Descending)
// ==========================================================================
function getCellSortValue(cell) {
  const text = cell.textContent.trim();
  if (!text) return { type: 'string', val: '' };

  // 1. Currency: e.g. "Rp 35.000", "Rp 0"
  if (/^Rp\s*[\d.,]+/i.test(text)) {
    const num = parseFloat(text.replace(/[^\d]/g, '')) || 0;
    return { type: 'number', val: num };
  }

  // 2. Percentage: e.g. "12.5%"
  if (/^[\d.,]+%$/.test(text)) {
    const num = parseFloat(text.replace('%', '').replace(',', '.')) || 0;
    return { type: 'number', val: num };
  }

  // 3. Integer or float with optional units (KB, MB, px, Foto)
  if (/^[\d.,]+(\s*(KB|MB|px|Foto))?$/i.test(text)) {
    const num = parseFloat(text.replace(/[^\d.]/g, '')) || 0;
    return { type: 'number', val: num };
  }

  // 4. Dates: check DD/MM/YYYY or DD-MM-YYYY
  const dmyMatch = text.match(/^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})/);
  if (dmyMatch) {
    const dVal = new Date(parseInt(dmyMatch[3], 10), parseInt(dmyMatch[2], 10) - 1, parseInt(dmyMatch[1], 10)).getTime();
    return { type: 'date', val: dVal };
  }
  // Try Date.parse for ISO or other standard dates
  const parsedDate = Date.parse(text);
  if (!isNaN(parsedDate) && text.length >= 8) {
    return { type: 'date', val: parsedDate };
  }

  return { type: 'string', val: text.toLowerCase() };
}

document.addEventListener('click', function(e) {
  const th = e.target.closest('th');
  if (!th) return;
  const table = th.closest('table');
  if (!table) return;
  const tbody = table.querySelector('tbody');
  if (!tbody) return;

  const thText = th.textContent.replace(/[▲▼]/g, '').trim().toLowerCase();
  if (thText === 'aksi') return;

  const colIdx = Array.from(th.parentNode.children).indexOf(th);
  if (colIdx < 0) return;

  const rows = Array.from(tbody.querySelectorAll('tr'));
  if (rows.length === 0) return;
  if (rows.length === 1 && rows[0].querySelector('td[colspan]')) return;

  const isAsc = th.dataset.sortDir !== 'asc';
  th.dataset.sortDir = isAsc ? 'asc' : 'desc';

  table.querySelectorAll('th').forEach(otherTh => {
    if (otherTh !== th) {
      delete otherTh.dataset.sortDir;
      const arrow = otherTh.querySelector('.sort-arrow');
      if (arrow) arrow.remove();
    }
  });

  let arrow = th.querySelector('.sort-arrow');
  if (!arrow) {
    arrow = document.createElement('span');
    arrow.className = 'sort-arrow';
    arrow.style.marginLeft = '4px';
    arrow.style.fontSize = '9px';
    arrow.style.opacity = '0.85';
    th.appendChild(arrow);
  }
  arrow.textContent = isAsc ? '▲' : '▼';

  rows.sort((rowA, rowB) => {
    const cellA = rowA.children[colIdx];
    const cellB = rowB.children[colIdx];
    if (!cellA || !cellB) return 0;

    const valA = getCellSortValue(cellA);
    const valB = getCellSortValue(cellB);

    if (valA.type === 'number' && valB.type === 'number') {
      return isAsc ? (valA.val - valB.val) : (valB.val - valA.val);
    }
    if (valA.type === 'date' && valB.type === 'date') {
      return isAsc ? (valA.val - valB.val) : (valB.val - valA.val);
    }
    return isAsc
      ? String(valA.val).localeCompare(String(valB.val), undefined, { numeric: true, sensitivity: 'base' })
      : String(valB.val).localeCompare(String(valA.val), undefined, { numeric: true, sensitivity: 'base' });
  });

  rows.forEach(r => tbody.appendChild(r));
});

</script>
</body>
</html>
'''


FIXED_WINDOW_WIDTH = 1016 if IS_WIN else 1000

def get_screen_bounds():
    sw, sh = 1366, 768
    try:
        if hasattr(webview, "screens") and webview.screens:
            s0 = webview.screens[0]
            sw, sh = s0.width, s0.height
    except Exception:
        pass
    if IS_WIN:
        try:
            import ctypes
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            pass
    return sw, sh


def _extract_preview_url(item):
    """Ambil URL preview/thumbnail foto dari objek creation API Fotoyu."""
    if not isinstance(item, dict):
        return ""
    for field in ("image_url", "preview_url", "url", "file_url", "original_url", "thumbnail_url", "thumbnail", "src"):
        v = item.get(field)
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, dict):
            for sub in ("url", "link", "src"):
                sv = v.get(sub)
                if isinstance(sv, str) and sv.startswith("http"):
                    return sv
    return ""


class App:
    def __init__(self):
        self._window = None
        self._api_bridge = None
        ensure_dirs()

        self.api = FotoyuApi()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.workers = []
        self.compress_fail_counts = {}
        self.compress_skip = set()
        self.upload_fail_counts = {}
        self.upload_skip = set()
        self._login_lock = threading.Lock()
        self._path_lock = threading.Lock()
        self._fail_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self.history_lock = threading.Lock()
        self.uploaded_fingerprints = set()
        self.pending_fingerprints = {}
        self._running = False
        self._compress_busy = False
        self._upload_busy = False
        self._last_status_text = "Siap. Isi konfigurasi & pilih folder lalu klik Mulai Proses."
        self._run_seq = 0
        self._authed_creds = (None, None)
        self._presets = {}
        self._last_health_check = 0.0
        self._session_up_start = None
        self._session_up_done = 0
        self._session_comp_start = None
        self._session_comp_done = 0
        self.tag_id = DEFAULT_CONFIG["tag_id"]
        self._loc_payload_name = ""
        self._fee_cache = {}
        self.counters = {"c_ok": 0, "c_fail": 0, "u_ok": 0, "u_fail": 0}
        self.log_counts = {"ok": 0, "fail": 0, "info": 0, "skip": 0}
        self.logs = []
        self.cfg = dict(DEFAULT_CONFIG)
        self.creations_by_date = {}
        self.sold_creations_all = []
        self.log_visible = False

        self._load_presets()
        self._load_config()
        self._load_history()

    def eval_js(self, script):
        """Eksekusi JavaScript di context webview secara thread-safe."""
        if self._window:
            try:
                self._window.evaluate_js(script)
            except Exception:
                pass

    def update_progress(self, pct):
        self.eval_js(f"window.onProgressUpdate && window.onProgressUpdate({float(pct)})")

    def update_stat_progress(self, pct, label):
        lbl_json = json.dumps(str(label))
        self.eval_js(f"window.onStatProgress && window.onStatProgress({float(pct)}, {lbl_json})")

    def _status_dot_color(self, text):
        if not text:
            return "green"
        t = text.lower()
        if any(k in t for k in ("error", "gagal", "invalid", "ditolak", "putus", "timeout", "berhenti")):
            return "red"
        if any(k in t for k in ("maintenance", "menunggu", "sisa", "memuat", "proses", "unggah", "kompres")):
            return "yellow"
        if any(k in t for k in ("selesai", "berhasil")):
            return "blue"
        return "green"

    def set_status(self, text, dot_color=None):
        if not dot_color:
            dot_color = self._status_dot_color(text)
        self._last_status_text = text
        self.eval_js(f"window.onStatusUpdate && window.onStatusUpdate({json.dumps({'text': text, 'color': dot_color})})")

    def bump(self, key, n=1):
        with self._fail_lock:
            self.counters[key] = self.counters.get(key, 0) + n
        self.eval_js(f"window.onCounterUpdate && window.onCounterUpdate({json.dumps(self.counters)})")

    def log_row(self, action, filename, info, status="info"):
        stamp = time.strftime("%H:%M:%S")
        row = {"stamp": stamp, "action": action, "filename": filename, "info": info, "status": status}
        with self._log_lock:
            self.logs.append(row)
            if len(self.logs) > 2000:
                self.logs.pop(0)
            s_key = status.lower()
            if s_key in self.log_counts:
                self.log_counts[s_key] += 1
        self.eval_js(f"window.onLogRow && window.onLogRow({json.dumps(row)}, {json.dumps(self.log_counts)})")

    def toggle_log(self, show):
        self.log_visible = bool(show)
        sw, sh = get_screen_bounds()
        max_allowed_h = max(520, sh - 60)
        target_h = min(840 if show else 680, max_allowed_h)
        if self._window:
            try:
                self._window.resize(FIXED_WINDOW_WIDTH, target_h)
            except Exception:
                pass
        return self.log_visible

    def _get_upload_dir(self):
        d = self.cfg.get("upload_dir") or self.cfg.get("source_dir")
        return d if d and os.path.isdir(d) else UPLOAD_DIR

    def _get_complete_dir(self):
        d = self.cfg.get("complete_dir")
        return d if d and os.path.isdir(d) else COMPLETE_DIR

    def _get_target_complete_dir(self):
        """Mengembalikan path subfolder tanggal hari ini (DD-MM-YYYY) di dalam root completed dir.
        Folder dibuat otomatis secara thread-safe jika belum ada."""
        root_dir = self._get_complete_dir()
        if not root_dir or not os.path.isdir(root_dir):
            return root_dir or ""
        today_str = datetime.now().strftime("%d-%m-%Y")
        target_dir = os.path.join(root_dir, today_str)
        try:
            os.makedirs(target_dir, exist_ok=True)
            return target_dir
        except Exception:
            return root_dir

    def _update_queue_display(self):
        up_dir = self._get_upload_dir()
        c_src = len(list_images(up_dir)) if os.path.isdir(up_dir) else 0
        c_rdy = len(list_images(COMPRESS_DIR)) if os.path.isdir(COMPRESS_DIR) else 0
        q_text = f"Antrean: {c_src} Source  |  {c_rdy} Siap Upload"
        return {"text": q_text, "source": c_src, "ready": c_rdy}

    def _has_source_files(self):
        up_dir = self._get_upload_dir()
        if not up_dir or not os.path.isdir(up_dir):
            return False
        return bool(list_images(up_dir))

    def _has_compress_files(self):
        return bool(list_images(COMPRESS_DIR))

    def _has_pending_files(self):
        return self._has_source_files() or self._has_compress_files()

    def _maybe_set_monitoring_status(self):
        if not self._running or self.stop_event.is_set() or self.pause_event.is_set():
            return
        if self._compress_busy or self._upload_busy:
            return
        if not self._has_pending_files():
            self.set_status("Semua file selesai diproses. Memantau folder...")

    def _load_presets(self):
        global PRESETS_PATH
        PRESETS_PATH = find_data_file("presets.json")
        if os.path.exists(PRESETS_PATH):
            try:
                with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                    self._presets = json.load(f) or {}
            except Exception:
                self._presets = {}
        else:
            self._presets = {}

    def _save_presets_file(self):
        targets = {
            PRESETS_PATH,
            os.path.join(BASE_DIR, "presets.json"),
            os.path.join(os.path.dirname(BASE_DIR), "presets.json"),
            os.path.join(os.getcwd(), "presets.json")
        }
        for target in targets:
            try:
                if os.path.exists(target) or target == PRESETS_PATH:
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(self._presets, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def _config_dict(self):
        return dict(self.cfg)

    def _apply_config_values(self, data):
        if not isinstance(data, dict):
            return
        for k, v in data.items():
            self.cfg[k] = v
        if "tag_id" in data and data["tag_id"]:
            self.tag_id = data["tag_id"]
            self.cfg["tag_id"] = data["tag_id"]
        elif self.cfg.get("tag_id"):
            self.tag_id = self.cfg["tag_id"]
        if "active_preset" in data:
            self.cfg["active_preset"] = data["active_preset"]
        if "currency_id" in data and data["currency_id"]:
            self.currency_id = data["currency_id"]
        elif self.cfg.get("currency_id"):
            self.currency_id = self.cfg["currency_id"]
        if "source_dir" in data:
            self.cfg["upload_dir"] = data["source_dir"]
        elif "upload_dir" in data:
            self.cfg["source_dir"] = data["upload_dir"]

        if "fototree" in data:
            self.cfg["location_name"] = data["fototree"]
            self._loc_payload_name = data["fototree"]
        elif "location_name" in data:
            self.cfg["fototree"] = data["location_name"]
            self._loc_payload_name = data["location_name"]

        if "lon" in data:
            self.cfg["longitude"] = data["lon"]
        elif "longitude" in data:
            self.cfg["lon"] = data["longitude"]

        if "lat" in data:
            self.cfg["latitude"] = data["lat"]
        elif "latitude" in data:
            self.cfg["lat"] = data["latitude"]

    def _load_config(self):
        global CONFIG_PATH
        CONFIG_PATH = find_data_file("config.json")
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._apply_config_values(data)
            except Exception:
                pass

    def _save_config_silent(self):
        targets = {
            CONFIG_PATH,
            os.path.join(BASE_DIR, "config.json"),
            os.path.join(os.path.dirname(BASE_DIR), "config.json"),
            os.path.join(os.getcwd(), "config.json")
        }
        for target in targets:
            try:
                if os.path.exists(target) or target == CONFIG_PATH:
                    with open(target, "w", encoding="utf-8") as f:
                        json.dump(self.cfg, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def _load_history(self):
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    records = json.load(f)
                with self.history_lock:
                    self.uploaded_fingerprints = set(records.keys())
            except Exception:
                self.uploaded_fingerprints = set()
        else:
            self.uploaded_fingerprints = set()
        self._index_complete_dir()

    def _index_complete_dir(self):
        comp_dir = self._get_complete_dir()
        if not comp_dir or not os.path.isdir(comp_dir):
            return
        files = []
        for root, dirs, filenames in os.walk(comp_dir):
            for fn in filenames:
                if not fn.startswith(".") and os.path.splitext(fn)[1].lower() in IMAGE_EXTS_SET:
                    files.append(os.path.join(root, fn))
        new_records = {}
        for f in files:
            fp = get_file_fingerprint(f)
            if fp and fp not in self.uploaded_fingerprints:
                new_records[fp] = os.path.basename(f)
        if new_records:
            self._save_history(new_records)

    def _save_history(self, new_records):
        with self.history_lock:
            self.uploaded_fingerprints.update(new_records.keys())
            try:
                existing = {}
                if os.path.exists(HISTORY_PATH):
                    try:
                        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                            existing = json.load(f) or {}
                    except Exception:
                        existing = {}
                existing.update(new_records)
                with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

    def _ensure_token(self, username=None, password=None):
        u = (username or self.cfg.get("username", "")).strip()
        p = password if password is not None else self.cfg.get("password", "")
        if not u or not p:
            return False
        with self._login_lock:
            if self._authed_creds != (None, None) and self._authed_creds != (u, p):
                self.api.set_token(None)

            if self.api.get_token():
                return True

            token = self.api.login(u, p)
            if token:
                self.api.set_token(token)
                self._authed_creds = (u, p)
                self._cached_username = u
                self._cached_password = p
                self.log_row("AUTH", "", "Login berhasil.", "ok")
                return True
            self.log_row("AUTH", "", "Login gagal. Cek username/password.", "fail")
            return False

    def get_net_creator_earning(self, price, tag_id=None):
        try:
            p = int(float(price or 0))
        except (ValueError, TypeError):
            p = 0
        if p <= 0:
            return 0
        tag = tag_id or self.tag_id or DEFAULT_CONFIG["tag_id"]
        cache_key = (p, str(tag or ""))
        if cache_key in self._fee_cache:
            return self._fee_cache[cache_key]
        if self.api.get_token():
            try:
                raw = self.api.get_transaction_fee(p, tag)
                net, _ = parse_fee_summary(raw, p)
                if net is not None and net > 0:
                    self._fee_cache[cache_key] = net
                    return net
            except Exception:
                pass
        fee = round(p * 0.158)
        net = max(0, int(p - fee))
        self._fee_cache[cache_key] = net
        return net

    def on_reset_counters(self):
        with self._fail_lock:
            self.counters = {"c_ok": 0, "c_fail": 0, "u_ok": 0, "u_fail": 0}
        self.bump("c_ok", 0)
        self.log_row("COUNTER", "", "Counter berhasil direset ke 0", "info")

    def on_retry_failed(self):
        with self._fail_lock:
            n_comp = len(self.compress_skip)
            n_up = len(self.upload_skip)
            self.compress_skip.clear()
            self.upload_skip.clear()
            self.compress_fail_counts.clear()
            self.upload_fail_counts.clear()
        self.log_row("RETRY", "", f"Mereset {n_comp} file kompres & {n_up} file upload yang gagal/skip", "info")
        self.set_status("Jatah gagal direset. Mengulangi file...")

    def on_start(self):
        if self._running:
            return True
        u = self.cfg.get("username", "").strip()
        p = self.cfg.get("password", "")
        if not u or not p:
            self.set_status("Username dan password tidak boleh kosong.", "red")
            return False

        up_dir = self._get_upload_dir()
        comp_dir = self._get_complete_dir()
        if not os.path.isdir(up_dir):
            self.set_status(f"Folder Source tidak ditemukan: {up_dir}", "red")
            return False
        if not os.path.isdir(comp_dir):
            self.set_status(f"Folder Completed tidak ditemukan: {comp_dir}", "red")
            return False

        self._cached_username = u
        self._cached_password = p
        self._cached_price = self.cfg.get("price", "")
        self._cached_loc = self.cfg.get("fototree", "") or self.cfg.get("location_name", "")
        self._cached_lon = self.cfg.get("lon", "0.0") or self.cfg.get("longitude", "0.0")
        self._cached_lat = self.cfg.get("lat", "0.0") or self.cfg.get("latitude", "0.0")
        self._cached_mode = self.cfg.get("mode", "all")
        self._cached_desc = self.cfg.get("desc", "") or self.cfg.get("description", "")
        self._cached_upload_dir = up_dir
        self._cached_complete_dir = comp_dir
        self._loc_payload_name = self._cached_loc

        # Pastikan tag_id aktif termuat
        self.tag_id = self.cfg.get("tag_id") or getattr(self, "tag_id", None) or DEFAULT_CONFIG["tag_id"]
        self.currency_id = self.cfg.get("currency_id") or getattr(self, "currency_id", None) or DEFAULT_CONFIG["currency_id"]

        self.stop_event.clear()
        self.pause_event.clear()
        self._running = True
        self._run_seq += 1
        my_seq = self._run_seq

        self.set_status("Memulai proses kompresi dan unggah...", "yellow")
        self.log_row("START", "", f"Mulai proses (Mode: {self._cached_mode})", "info")

        w_comp = threading.Thread(target=self._compress_worker, args=(my_seq,), daemon=True)
        w_up = threading.Thread(target=self._upload_worker, args=(my_seq,), daemon=True)
        self.workers = [w_comp, w_up]
        w_comp.start()
        w_up.start()
        return True

    def on_stop(self):
        self._running = False
        self.stop_event.set()
        self.pause_event.clear()
        self.set_status("Dihentikan oleh pengguna.", "red")
        self.log_row("STOP", "", "Proses dihentikan oleh pengguna", "info")

    def fetch_statistics_data(self, default_price=35000, u="", p="", force=False):
        if not u:
            u = self.cfg.get("username", "")
        if not p:
            p = self.cfg.get("password", "")

        self.update_stat_progress(5, "Memeriksa cache statistik lokal...")
        cache_file = os.path.join(BASE_DIR, "stats_cache.json")
        cached_sold_dict = {}
        cached_creations = {}
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    old_c = json.load(f)
                    if isinstance(old_c, dict):
                        for s in old_c.get("sold_photos", []):
                            if s.get("content_id"):
                                cached_sold_dict[s["content_id"]] = s
                        cached_creations = old_c.get("_creations_by_date", {})
                        if not force and not u and not p and "summary" in old_c:
                            self.update_stat_progress(50, "Membaca data dari cache lokal...")
                            self.creations_by_date = cached_creations
                            self.sold_creations_all = old_c.get("sold_photos", [])
                            self.update_stat_progress(100, "Selesai memuat cache.")
                            return old_c
            except Exception:
                pass

        if not self.api.get_token():
            self.update_stat_progress(10, "Menghubungkan ke FotoYu API...")
            if not self._ensure_token(u, p):
                if os.path.exists(cache_file):
                    try:
                        with open(cache_file, "r", encoding="utf-8") as f:
                            c_fallback = json.load(f)
                            if c_fallback and "summary" in c_fallback:
                                self.update_stat_progress(100, "Memuat data dari cache (offline/fallback)")
                                return c_fallback
                    except Exception:
                        pass
                self.update_stat_progress(100, "Gagal login ke FotoYu")
                return None

        self.update_stat_progress(15, "Mengambil metadata grup tanggal...")
        groups = self.api.get_groups(limit=50, filter_type="all") or []

        def fetch_storage():
            try:
                s = self.api.get_storage()
                d = parse_storage_data(s)
                if d.get("text"):
                    return d
            except Exception:
                pass
            for cand_file in ("fotoyustorage.json", os.path.join(os.path.dirname(__file__), "..", "fotoyustorage.json")):
                if os.path.isfile(cand_file):
                    try:
                        with open(cand_file, "r", encoding="utf-8") as f_local:
                            local_d = json.load(f_local)
                            for req in local_d.get("requests", []):
                                if "creators/storage" in req.get("url", ""):
                                    t_auth = req.get("headers", {}).get("authorization")
                                    if t_auth and not self.api.get_token():
                                        self.api.set_token(t_auth.replace("Bearer ", ""))
                                        s = self.api.get_storage()
                                        d = parse_storage_data(s)
                                        if d.get("text"):
                                            return d
                    except Exception:
                        pass
            return {}

        self.update_stat_progress(25, "Mengambil data kuota penyimpanan & foto terjual...")
        with ThreadPoolExecutor(max_workers=2) as meta_ex:
            f_stor = meta_ex.submit(fetch_storage)
            f_sold = meta_ex.submit(self.api.get_sold_creations)
            storage_data = f_stor.result() or {}
            raw_sold_items = f_sold.result() or []

        self.update_stat_progress(35, f"Memperkaya metadata {len(raw_sold_items)} foto terjual...")

        def enrich_sold(it):
            cid = it.get("content_id")
            if not force and cid and cid in cached_sold_dict:
                cached_item = cached_sold_dict[cid]
                if cached_item.get("net_price") is not None:
                    return cached_item
            if cid:
                try:
                    det = self.api.get_creation_details(cid)
                    if det:
                        if "price" in det and det["price"]:
                            it["price"] = det["price"]
                        if "location" in det and det["location"]:
                            it["location"] = det["location"]
                        if "tags" in det and det["tags"]:
                            it["tags"] = det["tags"]
                        if "tag_nicknames" in det and det["tag_nicknames"]:
                            it["tag_nicknames"] = det["tag_nicknames"]
                        if "resolution" in det and det["resolution"]:
                            it["resolution"] = det["resolution"]
                        if "size" in det and det["size"]:
                            it["size"] = det["size"]
                except Exception:
                    pass
            tree_name = extract_fototree_name(it)
            if tree_name:
                it["fototree"] = tree_name
            price_val = it.get("price") or default_price
            it["price"] = price_val
            tag_id = None
            if isinstance(it.get("tags"), list) and it["tags"]:
                tag_id = it["tags"][0].get("id")
            it["net_price"] = self.get_net_creator_earning(price_val, tag_id)
            return it

        sold_items = []
        if raw_sold_items:
            with ThreadPoolExecutor(max_workers=5) as ex_sold:
                sold_items = list(ex_sold.map(enrich_sold, raw_sold_items))

        sold_by_date = {}
        for item in sold_items:
            d_str = (item.get("original_at") or item.get("created_at") or "")[:10]
            if d_str:
                sold_by_date.setdefault(d_str, []).append(item)

        valid_groups = [g for g in groups if (g.get("date", "")[:10])]
        total_groups = len(valid_groups)
        self.update_stat_progress(45, f"Menyinkronkan {total_groups} kelompok tanggal...")

        def fetch_one_group(g):
            date_str = g.get("date", "")[:10]
            count_photos = g.get("count", 0)
            disp_date = format_display_date(date_str)

            # Smart cache reuse if photo count matches and not force
            if not force and disp_date in cached_creations and len(cached_creations[disp_date]) == count_photos:
                date_creations = cached_creations[disp_date]
                ak_plus = sum(it.get("found_count", 0) for it in date_creations)
                ak_min = sum(it.get("found_count_low", 0) for it in date_creations)
                konfirm = sum(it.get("confirmed_count", 0) for it in date_creations)
                faces = sum(it.get("face_count", 0) for it in date_creations)
                c_counts = self.api.get_date_counts(date_str)
                sold_count = c_counts.get("total_sold", 0)
                tree_name = date_creations[0].get("fototree", "") if date_creations else ""
                date_price = date_creations[0].get("price", default_price) if date_creations else default_price
            else:
                c_counts = self.api.get_date_counts(date_str)
                sold_count = c_counts.get("total_sold", 0)

                date_creations = []
                ak_plus = ak_min = konfirm = faces = 0
                page = 1
                while True:
                    items = self.api.get_creations_page(date_str, page=page, limit=100, filter_type="available")
                    if not items:
                        break
                    date_creations.extend(items)
                    for it in items:
                        ak_plus += it.get("found_count", 0)
                        ak_min += it.get("found_count_low", 0)
                        konfirm += it.get("confirmed_count", 0)
                        faces += it.get("face_count", 0)
                        if it.get("purchase_status") == "sold":
                            sold_count += 1
                    if len(items) < 100:
                        break
                    page += 1

                tree_name = ""
                date_price = default_price
                if date_creations:
                    cid0 = date_creations[0].get("content_id")
                    if cid0:
                        try:
                            det0 = self.api.get_creation_details(cid0)
                            if det0:
                                tree_name = extract_fototree_name(det0)
                                if "price" in det0 and det0["price"]:
                                    date_price = det0["price"]
                        except Exception:
                            pass

                for it in date_creations:
                    if not it.get("fototree") and tree_name:
                        it["fototree"] = tree_name
                    if "price" not in it:
                        it["price"] = date_price

            if date_str in sold_by_date:
                existing_ids = {c.get("content_id") for c in date_creations if c.get("content_id")}
                for s_item in sold_by_date[date_str]:
                    cid = s_item.get("content_id")
                    if not cid or cid not in existing_ids:
                        date_creations.append(s_item)
                        ak_plus += s_item.get("found_count", 0)
                        ak_min += s_item.get("found_count_low", 0)
                        konfirm += s_item.get("confirmed_count", 0)
                        faces += s_item.get("face_count", 0)
                sold_count = max(sold_count, len(sold_by_date[date_str]))

            est_rev = 0.0
            date_sold_items = sold_by_date.get(date_str, [])
            for s in date_sold_items:
                est_rev += s.get("net_price", self.get_net_creator_earning(s.get("price", date_price)))
            if sold_count > len(date_sold_items):
                est_rev += (sold_count - len(date_sold_items)) * self.get_net_creator_earning(date_price)

            actual_photos = max(count_photos, len(date_creations))

            return (date_str, disp_date, date_creations, {
                "raw_date": date_str,
                "date": disp_date,
                "photos": actual_photos,
                "faces": faces,
                "ak_plus": ak_plus,
                "ak_min": ak_min,
                "konfirm": konfirm,
                "sold": sold_count,
                "est_rev": est_rev,
                "desc": "Tersedia" if actual_photos > sold_count else "Terjual Semua",
            })

        results = []
        if valid_groups:
            done_groups = 0
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(fetch_one_group, g): g for g in valid_groups}
                for fut in as_completed(futures):
                    res = fut.result()
                    if res is not None:
                        results.append(res)
                    done_groups += 1
                    pct = 45 + int((done_groups / max(1, total_groups)) * 45)
                    g_info = futures[fut].get("date", "")[:10]
                    self.update_stat_progress(pct, f"Menyinkronkan tanggal ({done_groups}/{total_groups}): {g_info}")

        processed_dates = set()
        all_rows = []
        for date_str, disp_date, date_creations, row_info in results:
            processed_dates.add(date_str)
            self.creations_by_date[disp_date] = date_creations
            self.creations_by_date[date_str] = date_creations
            all_rows.append(row_info)

        for d_str, s_list in sold_by_date.items():
            if d_str not in processed_dates:
                processed_dates.add(d_str)
                s_ak_plus = sum(it.get("found_count", 0) for it in s_list)
                s_ak_min = sum(it.get("found_count_low", 0) for it in s_list)
                s_konfirm = sum(it.get("confirmed_count", 0) for it in s_list)
                s_faces = sum(it.get("face_count", 0) for it in s_list)
                disp_d_str = format_display_date(d_str)
                self.creations_by_date[disp_d_str] = s_list
                self.creations_by_date[d_str] = s_list
                s_est_rev = sum(it.get("net_price", self.get_net_creator_earning(it.get("price", default_price))) for it in s_list)
                all_rows.append({
                    "raw_date": d_str,
                    "date": disp_d_str,
                    "photos": len(s_list),
                    "faces": s_faces,
                    "ak_plus": s_ak_plus,
                    "ak_min": s_ak_min,
                    "konfirm": s_konfirm,
                    "sold": len(s_list),
                    "est_rev": s_est_rev,
                    "desc": "Terjual (Completed)",
                })

        all_rows.sort(key=lambda r: r.get("raw_date", ""), reverse=True)
        self.sold_creations_all = list(sold_items)

        # Leaderboard:
        tree_stats = {}
        for s in self.sold_creations_all:
            t_name = s.get("fototree") or extract_fototree_name(s) or "Lainnya"
            if not t_name or t_name == "-":
                t_name = "Tanpa Fototree"
            if t_name not in tree_stats:
                tree_stats[t_name] = {"sold": 0, "est_rev": 0.0}
            tree_stats[t_name]["sold"] += 1
            tree_stats[t_name]["est_rev"] += s.get("net_price", 0.0)

        lb_tree = [
            {"name": k, "sold": v["sold"], "est_rev": v["est_rev"]}
            for k, v in tree_stats.items()
        ]
        lb_tree.sort(key=lambda x: (x["sold"], x["est_rev"]), reverse=True)

        date_stats = {}
        for s in self.sold_creations_all:
            d_val = (s.get("original_at") or s.get("created_at") or "")[:10]
            disp_d = format_display_date(d_val) if d_val else "Tanpa Tanggal"
            if disp_d not in date_stats:
                date_stats[disp_d] = {"sold": 0, "est_rev": 0.0}
            date_stats[disp_d]["sold"] += 1
            date_stats[disp_d]["est_rev"] += s.get("net_price", 0.0)

        lb_date = [
            {"name": k, "sold": v["sold"], "est_rev": v["est_rev"]}
            for k, v in date_stats.items()
        ]
        lb_date.sort(key=lambda x: (x["sold"], x["est_rev"]), reverse=True)

        tot_sold = sum(r["sold"] for r in all_rows)
        tot_photos = sum(r["photos"] for r in all_rows)
        tot_faces = sum(r.get("faces", 0) for r in all_rows)
        tot_ak_plus = sum(r.get("ak_plus", 0) for r in all_rows)
        tot_ak_min = sum(r.get("ak_min", 0) for r in all_rows)
        tot_konfirm = sum(r.get("konfirm", 0) for r in all_rows)
        tot_rev = sum(r["est_rev"] for r in all_rows)
        conv_pct = (tot_sold / max(1, tot_photos)) * 100.0

        stats_payload = {
            "summary": {
                "total_photos": tot_photos,
                "total_faces": tot_faces,
                "total_ak_plus": tot_ak_plus,
                "total_ak_min": tot_ak_min,
                "total_konfirm": tot_konfirm,
                "total_sold": tot_sold,
                "total_revenue": tot_rev,
                "conversion_rate": f"{conv_pct:.1f}%",
                "storage_text": storage_data.get("text") or storage_data.get("used_str") or "-"
            },
            "dates": all_rows,
            "sold_photos": self.sold_creations_all,
            "leaderboard": {
                "by_tree": lb_tree[:10],
                "by_date": lb_date[:10]
            },
            "_creations_by_date": self.creations_by_date
        }

        self.update_stat_progress(95, "Menyimpan data cache...")
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(stats_payload, f, ensure_ascii=False)
        except Exception:
            pass

        self.update_stat_progress(100, "Sinkronisasi statistik selesai.")
        return stats_payload

    def get_photo_details_data(self, date_str):
        creations = self.creations_by_date.get(date_str)
        if not creations:
            disp_date = format_display_date(date_str)
            creations = self.creations_by_date.get(disp_date, [])

        sold_count = sum(1 for c in creations if c.get("purchase_status") == "sold")
        est_rev = sum(c.get("net_price", 0.0) for c in creations if c.get("purchase_status") == "sold")

        formatted_items = []
        for it in creations:
            cid = it.get("content_id")
            title = it.get("title") or cid or "Foto"
            tree_name = it.get("fototree") or extract_fototree_name(it) or "-"
            res = it.get("resolution") if isinstance(it.get("resolution"), dict) else {}
            dim_str = f"{res.get('width', 0)}×{res.get('height', 0)} px" if (res and res.get('width')) else "-"
            fsize = it.get("size") or 0
            size_str = f"{fsize/1024:.0f} KB" if fsize else "-"

            formatted_items.append({
                "content_id": cid,
                "title": title,
                "fototree": tree_name,
                "found_count": it.get("found_count", 0),
                "found_count_low": it.get("found_count_low", 0),
                "confirmed_count": it.get("confirmed_count", 0),
                "face_count": it.get("face_count", 0),
                "robofind_status": get_robofind_label(it) or "OK",
                "purchase_status": it.get("purchase_status", "available"),
                "price": it.get("price", 0),
                "net_price": it.get("net_price", 0),
                "dim_str": dim_str,
                "size_str": size_str,
                "raw_item": it
            })

        return {
            "date": date_str,
            "items": formatted_items,
            "stats": {
                "total_photos": len(creations),
                "sold_count": sold_count,
                "est_rev": est_rev
            }
        }

    def get_image_preview_info(self, content_id, title):
        cand_dirs = [
            self._get_target_complete_dir(),
            self._get_complete_dir(),
            COMPLETE_DIR,
            os.path.join(os.path.dirname(BASE_DIR), "Complete"),
            self._get_upload_dir(),
            COMPRESS_DIR,
            os.path.join(os.path.dirname(BASE_DIR), "Compress"),
            UPLOAD_DIR,
            os.path.join(os.path.dirname(BASE_DIR), "Upload"),
            os.path.join(os.path.dirname(BASE_DIR), "Source"),
        ]
        local_path = ""
        for d in cand_dirs:
            if d and os.path.isdir(d):
                cand = os.path.join(d, title)
                if os.path.isfile(cand):
                    local_path = cand
                    break

        rw, rh = 0, 0
        fsize = 0
        data_uri = ""
        original_url = ""

        # Check API creation details first to get exact FotoYu resolution & size
        if content_id:
            try:
                det = self.api.get_creation_details(content_id)
                if det:
                    url = _extract_preview_url(det)
                    if url:
                        original_url = url
                    res = det.get("resolution") if isinstance(det.get("resolution"), dict) else {}
                    if res and res.get("width"):
                        rw = res.get("width", 0)
                        rh = res.get("height", 0)
                    if det.get("size"):
                        fsize = det.get("size", 0)
            except Exception:
                pass

        if local_path and os.path.isfile(local_path):
            try:
                if not fsize:
                    fsize = os.path.getsize(local_path)
                with Image.open(local_path) as im:
                    try:
                        im = ImageOps.exif_transpose(im)
                    except Exception:
                        pass
                    if not (rw and rh):
                        rw, rh = im.size
                    im_thumb = im.copy()
                    im_thumb.thumbnail((500, 500), Image.Resampling.LANCZOS)
                    buf = io.BytesIO()
                    im_thumb.convert("RGB").save(buf, format="JPEG", quality=85)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    data_uri = f"data:image/jpeg;base64,{b64}"
            except Exception:
                pass

        if not data_uri and original_url:
            data_uri = original_url

        dim_str = f"{rw}×{rh} px" if (rw and rh) else "-"
        if fsize:
            if fsize >= 1024 * 1024:
                size_str = f"{fsize / (1024 * 1024):.2f} MB"
            else:
                size_str = f"{fsize / 1024:.0f} KB"
        else:
            size_str = "-"

        return {
            "title": title or content_id or "Foto",
            "dim_str": dim_str,
            "size_str": size_str,
            "preview_url": data_uri,
            "original_url": original_url or data_uri
        }

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_event.is_set():
                return
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.15)
            time.sleep(0.1)

    # ---------------- Worker: Kompres ----------------
    def _compress_worker(self, my_seq=None):
        while not self.stop_event.is_set() and (my_seq is None or self._run_seq == my_seq):
            try:
                self._compress_cycle()
                self._maybe_set_monitoring_status()
            except Exception as e:
                self.log_row("KOMPRES", "", f"Error: {e}", "fail")
            self._interruptible_sleep(POLL_INTERVAL)

    def _compress_cycle(self):
        up_dir = self._get_upload_dir()
        comp_dir = self._get_target_complete_dir()
        if not up_dir or not os.path.isdir(up_dir) or not comp_dir:
            return
        mode = getattr(self, "_cached_mode", "all")
        all_files = list_images(up_dir)
        if not all_files:
            return
        files = [f for f in all_files if f not in self.compress_skip]
        files = [f for i, f in enumerate(files, 1) if mode_match(os.path.basename(f), i, mode)]
        if not files:
            return
        batch = files[:COMPRESS_BULK_MAX]
        total = len(batch)
        done_count = 0
        counter_lock = threading.Lock()
        if self._session_comp_start is None:
            self._session_comp_start = time.time()
        batch_start = time.time()

        def process_one(src):
            nonlocal done_count
            if self.stop_event.is_set():
                return
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.15)
            name = os.path.basename(src)
            try:
                if not is_file_stable(src):
                    return

                # --- Cek Anti-Duplikat ---
                fp = get_file_fingerprint(src)
                with self.history_lock:
                    is_dup = bool(fp and fp in self.uploaded_fingerprints)
                if is_dup:
                    self.log_row("DUPLIKAT", name, "Sudah pernah diupload, lewati kompresi & pindah ke Completed", "skip")
                    with self._path_lock:
                        t_dir = self._get_target_complete_dir() or comp_dir
                        dest = unique_path(t_dir, name)
                    safe_move_file(src, dest)
                    return

                size_in = os.path.getsize(src)
                if size_in > Dmf_MAX_INPUT_BYTES:
                    self._compress_fail(src, name, "melebihi 50 MB")
                    return
                with Image.open(src) as im:
                    w0, h0 = im.size
                if w0 * h0 > JTH_MAX_INPUT_PIXELS:
                    self._compress_fail(src, name, "melebihi 50 MP")
                    return
                base, _ = os.path.splitext(name)
                with self._path_lock:
                    out_path = unique_path(COMPRESS_DIR, base + ".jpg")
                ok, q, size, w, h = compress_image(src, out_path)
                if ok:
                    self.log_row("KOMPRES", name, f"{w}x{h}, q={q}, {size/1024:.0f} KB", "ok")
                    comp_name = os.path.basename(out_path)
                    with self.history_lock:
                        if fp:
                            self.pending_fingerprints[comp_name] = (fp, name)
                    with self._path_lock:
                        t_dir = self._get_target_complete_dir() or comp_dir
                        dest = unique_path(t_dir, name)
                    safe_move_file(src, dest)
                    with self._fail_lock:
                        self.compress_fail_counts.pop(src, None)
                    self.bump("c_ok")
                else:
                    if os.path.exists(out_path):
                        try:
                            os.remove(out_path)
                        except Exception:
                            pass
                    self._compress_fail(src, name, "tidak mencapai <= 500 KB")
            except Exception as e:
                self._compress_fail(src, name, f"error: {e}")
            finally:
                with counter_lock:
                    done_count += 1
                    curr = done_count
                    self._session_comp_done += 1
                if not self.stop_event.is_set() and not self.pause_event.is_set():
                    elapsed = max(0.1, time.time() - batch_start)
                    rate_pm = (curr / elapsed) * 60.0
                    rem = max(0, total - curr)
                    eta_s = (rem / (curr / elapsed)) if curr > 0 else 0
                    status_msg = f"Kompres: {name} ({curr}/{total})  |  Sisa: {rem}  |  ~{rate_pm:.0f} foto/mnt"
                    if rem > 0 and elapsed >= 1.0:
                        status_msg += f"  |  ETA: {format_eta(eta_s)}"
                    self.set_status(status_msg)
                pct = (curr / max(1, total)) * 100
                self.update_progress(pct)

        self._compress_busy = True
        try:
            with ThreadPoolExecutor(max_workers=CPU_WORKERS) as executor:
                list(executor.map(process_one, batch))
            self.update_progress(100)
        finally:
            self._compress_busy = False

    def _compress_fail(self, src, name, reason):
        with self._fail_lock:
            n = self.compress_fail_counts.get(src, 0) + 1
            self.compress_fail_counts[src] = n
            if n >= COMPRESS_FAIL_LIMIT:
                self.compress_skip.add(src)
                self.log_row("KOMPRES", name, f"SKIP PERMANEN (gagal {n}x: {reason})", "skip")
            else:
                self.log_row("KOMPRES", name, f"GAGAL ({reason}) - {n}/{COMPRESS_FAIL_LIMIT}", "fail")
        self.bump("c_fail")

    # ---------------- Worker: Upload ----------------
    def _wait_if_server_unavailable(self):
        """Cek koneksi/maintenance berkala; bila offline/maintenance, tunggu pulih tanpa membakar jatah gagal."""
        now = time.time()
        if (now - self._last_health_check) < 20.0:
            return False
        self._last_health_check = now
        state, msg = self.api.check_server_health()
        if state in ("offline", "maintenance"):
            wait_msg = msg or "Koneksi terputus / server maintenance."
            self.set_status(f"{wait_msg} Menunggu pulih...")
            self.log_row("INFO", "", f"{wait_msg} Menunggu koneksi pulih...", "skip")
            self._last_health_check = 0.0
            self._interruptible_sleep(5)
            return True
        return False

    def _upload_worker(self, my_seq=None):
        first = True
        while not self.stop_event.is_set() and (my_seq is None or self._run_seq == my_seq):
            try:
                if self._ensure_token():
                    if first:
                        first = False
                        if not self.stop_event.is_set() and not self.pause_event.is_set():
                            if not self._compress_busy and not self._upload_busy and not self._has_source_files():
                                self.set_status("Memantau folder...")
                    self._upload_cycle()
                    self._maybe_set_monitoring_status()
                else:
                    if first:
                        first = False
                        if not self.stop_event.is_set() and not self.pause_event.is_set():
                            self.set_status("Login gagal. Cek username/password.")
            except Exception as e:
                self.log_row("UPLOAD", "", f"Error: {e}", "fail")
            self._interruptible_sleep(POLL_INTERVAL)

    def _upload_cycle(self):
        files = list_images(COMPRESS_DIR)
        if not files:
            return
        files = [f for f in files if f not in self.upload_skip]
        if not files:
            return
        if self._wait_if_server_unavailable():
            return
        batch = files[:UPLOAD_BULK_MAX]
        batch_start = time.time()
        self._upload_busy = True
        try:
            for start in range(0, len(batch), CREATION_CHUNK):
                if self.stop_event.is_set():
                    return
                chunk = batch[start:start + CREATION_CHUNK]
                self._upload_chunk(chunk, start, len(batch), batch_start=batch_start)
        finally:
            self._upload_busy = False

    def _upload_chunk(self, files, offset, grand_total, batch_start=None):
        if self.stop_event.is_set():
            return
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.15)
        if batch_start is None:
            batch_start = time.time()

        def prep_item(f):
            try:
                with Image.open(f) as im:
                    w, h = im.size
                ext = os.path.splitext(f)[1].lstrip(".").lower()
                if ext == "jpg":
                    ext = "jpeg"
                return {
                    "original": f,
                    "upload_path": f,
                    "name": os.path.basename(f),
                    "ext": ext,
                    "width": w,
                    "height": h,
                    "size": os.path.getsize(f),
                    "signature": get_file_md5(f)
                }
            except Exception as e:
                self.log_row("UPLOAD", os.path.basename(f), f"SKIP: {e}", "skip")
                return None

        with ThreadPoolExecutor(max_workers=CPU_WORKERS) as prep_exec:
            items = [res for res in prep_exec.map(prep_item, files) if res is not None]

        if not items:
            return

        results, meta_map, relogin = self.api.presigned_urls(items)
        if relogin and self._ensure_token():
            results, meta_map, _ = self.api.presigned_urls(items)
        if not results:
            self._last_health_check = 0.0
            if not self._wait_if_server_unavailable():
                self.log_row("UPLOAD", "", "Gagal mendapatkan presigned URL.", "fail")
            return

        def _record_upload_fail(f_path, fname, reason):
            with self._fail_lock:
                n_up = self.upload_fail_counts.get(f_path, 0) + 1
                self.upload_fail_counts[f_path] = n_up
                if n_up >= UPLOAD_FAIL_LIMIT:
                    self.upload_skip.add(f_path)
                    self.log_row("UPLOAD", fname, f"SKIP PERMANEN (gagal {n_up}x: {reason})", "skip")

        work_items = []
        for item in results:
            sig = item.get("signature")
            bucket = meta_map.get(sig)
            if not bucket:
                continue
            meta = bucket.pop(0)
            url, upload_key = resolve_link(item)
            if not url:
                reason = item.get("error") or "URL kosong"
                self.log_row("UPLOAD", meta["name"], reason, "fail")
                self.bump("u_fail")
                _record_upload_fail(meta["upload_path"], meta["name"], reason)
                continue
            work_items.append((meta, url, upload_key, sig))

        entries = []
        success = []
        entries_lock = threading.Lock()
        uploaded_count = 0

        def upload_single(work):
            nonlocal uploaded_count
            if self.stop_event.is_set():
                return
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.15)

            meta, url, upload_key, sig = work
            ok, code = self.api.upload_s3(url, meta["upload_path"], meta["width"],
                                          meta["height"], "image/jpeg")
            with entries_lock:
                uploaded_count += 1
                curr = offset + uploaded_count
                if ok:
                    obj = {
                        "title": meta["name"],
                        "content": url,
                        "height": meta["height"],
                        "width": meta["width"],
                        "size": meta["size"],
                        "signature": sig
                    }
                    if upload_key:
                        obj["upload_key"] = upload_key
                    entries.append((obj, meta["original"]))
                    success.append(meta["original"])
                    self.log_row("UPLOAD", meta["name"], f"S3 OK ({code})", "ok")
                else:
                    self.log_row("UPLOAD", meta["name"], f"S3 GAGAL ({code})", "fail")
                    self.bump("u_fail")
                    # Jangan membakar jatah gagal permanen jika koneksi benar-benar putus (code is None)
                    if code is not None:
                        _record_upload_fail(meta["upload_path"], meta["name"], f"upload S3 ({code})")

            if not self.stop_event.is_set() and not self.pause_event.is_set():
                elapsed = max(0.1, time.time() - batch_start)
                rate_pm = (curr / elapsed) * 60.0
                rem = max(0, grand_total - curr)
                eta_s = (rem / (curr / elapsed)) if curr > 0 else 0
                status_msg = f"Upload: {meta['name']} ({curr}/{grand_total})  |  Sisa: {rem}  |  ~{rate_pm:.0f} foto/mnt"
                if rem > 0 and elapsed >= 1.0:
                    status_msg += f"  |  ETA: {format_eta(eta_s)}"
                self.set_status(status_msg)
            pct = (curr / max(1, grand_total)) * 100
            self.update_progress(pct)

        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as up_exec:
            list(up_exec.map(upload_single, work_items))

        if not entries:
            return

        loc_name = self._loc_payload_name or getattr(self, "_cached_loc", "")
        try:
            lon = float(getattr(self, "_cached_lon", 0) or 0)
            lat = float(getattr(self, "_cached_lat", 0) or 0)
        except Exception:
            lon = lat = 0.0
        location_str = json.dumps({
            "name": loc_name,
            "longitude": lon,
            "latitude": lat,
        })
        try:
            price = int(float(getattr(self, "_cached_price", 0) or 0))
        except Exception:
            price = 0
        desc = getattr(self, "_cached_desc", "")
        ok = self.api.finalize([c for c, _ in entries], entries[0][1], price,
                               location_str, desc,
                               self.cfg.get("currency_id"), self.tag_id)
        if ok:
            new_records = {}
            with self.history_lock:
                for f in success:
                    comp_name = os.path.basename(f)
                    if comp_name in self.pending_fingerprints:
                        fp, orig_name = self.pending_fingerprints.pop(comp_name)
                        new_records[fp] = orig_name
                    else:
                        fp_direct = get_file_fingerprint(f)
                        if fp_direct:
                            new_records[fp_direct] = comp_name
            if new_records:
                self._save_history(new_records)

            for f in success:
                with self._fail_lock:
                    self.upload_fail_counts.pop(f, None)
                try:
                    os.remove(f)
                except Exception:
                    pass
            self.bump("u_ok", len(success))
            self.log_row("PUBLISH", "", f"BERHASIL {len(success)} foto diunggah ke Fotoyu", "ok")
        else:
            err_detail = getattr(self.api, "last_finalize_error", "") or "publikasi ditolak server"
            self.log_row("PUBLISH", "", f"GAGAL publikasi creation ({err_detail}), file tetap di Compress", "fail")
            self.bump("u_fail", len(success))
            for f in success:
                _record_upload_fail(f, os.path.basename(f), f"finalize ({err_detail})")





class AppApi:
    def __init__(self, app):
        self._app = app
        self._window = None

    def get_initial_data(self):
        up_dir = self._app._get_upload_dir()
        c_src = len(list_images(up_dir)) if os.path.isdir(up_dir) else 0
        c_rdy = len(list_images(COMPRESS_DIR)) if os.path.isdir(COMPRESS_DIR) else 0
        q_text = f"Antrean: {c_src} Source  |  {c_rdy} Siap Upload"
        return {
            "config": self._app.cfg,
            "presets": self._app._presets,
            "counters": self._app.counters,
            "log_counts": self._app.log_counts,
            "queue_text": q_text,
            "status": {
                "text": self._app._last_status_text,
                "color": self._app._status_dot_color(self._app._last_status_text)
            }
        }

    def refresh_queue(self):
        return self._app._update_queue_display()

    def save_config(self, cfg_data):
        self._app._apply_config_values(cfg_data)
        self._app._save_config_silent()
        self._app.log_row("SAVE", "", f"Konfigurasi & folder destinasi disimpan: {os.path.basename(CONFIG_PATH)}", "ok")
        self._app.set_status("Konfigurasi berhasil disimpan ke disk.", "green")
        return True

    def get_duplicate_count(self):
        return len(self._app.uploaded_fingerprints)

    def reindex_duplicates(self):
        self._app._index_complete_dir()
        c = len(self._app.uploaded_fingerprints)
        self._app.log_row("HISTORY", "", f"Indeks ulang selesai. Total terindeks: {c} foto", "info")
        return c

    def clear_duplicates(self):
        with self._app.history_lock:
            self._app.uploaded_fingerprints.clear()
            if os.path.exists(HISTORY_PATH):
                try:
                    os.remove(HISTORY_PATH)
                except Exception:
                    pass
        self._app.log_row("HISTORY", "", "Riwayat Anti-Duplikat dibersihkan.", "skip")
        self._app.set_status("Riwayat Anti-Duplikat dibersihkan.", "yellow")
        return 0

    def save_preset(self, name, cfg_data):
        if not name:
            return self._app._presets
        self._app._presets[name] = cfg_data
        self._app._save_presets_file()
        self._app.cfg["active_preset"] = name
        self._app._save_config_silent()
        return self._app._presets

    def set_active_preset(self, name):
        self._app.cfg["active_preset"] = name or ""
        self._app._save_config_silent()
        return True

    def delete_preset(self, name):
        if name in self._app._presets:
            del self._app._presets[name]
            self._app._save_presets_file()
            if self._app.cfg.get("active_preset") == name:
                self._app.cfg["active_preset"] = ""
                self._app._save_config_silent()
        return self._app._presets

    def pick_source_dir(self, current_dir=""):
        dlg_type = getattr(webview.FileDialog, "FOLDER", webview.FOLDER_DIALOG) if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        res = self._window.create_file_dialog(dlg_type, directory=current_dir or "")
        if res and len(res) > 0:
            return res[0]
        return ""

    def pick_complete_dir(self, current_dir=""):
        dlg_type = getattr(webview.FileDialog, "FOLDER", webview.FOLDER_DIALOG) if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
        res = self._window.create_file_dialog(dlg_type, directory=current_dir or "")
        if res and len(res) > 0:
            return res[0]
        return ""

    def open_folder(self, name):
        if name in ("source", "upload"):
            p = self._app._get_upload_dir()
        elif name == "complete":
            sub = self._app._get_target_complete_dir()
            p = sub if (sub and os.path.isdir(sub)) else self._app._get_complete_dir()
        elif name == "compress":
            p = COMPRESS_DIR
        else:
            p = name
        open_folder(p)

    def search_fototree(self, query, username="", password=""):
        if not query or len(query.strip()) < 1:
            return []
        u = username or self._app.cfg.get("username", "")
        p = password or self._app.cfg.get("password", "")
        if not self._app.api.get_token():
            if u and p:
                self._app._ensure_token(u, p)
        res = self._app.api.tree_search(query)
        if not res and u and p:
            self._app._ensure_token(u, p)
            res = self._app.api.tree_search(query)
        trees = parse_trees(res)
        out = []
        for t in trees:
            out.append({
                "id": t.get("tag_id") or t.get("id"),
                "tag_id": t.get("tag_id") or t.get("id"),
                "name": t.get("name"),
                "location": t.get("location_name") or t.get("location") or "",
                "lat": t.get("latitude", 0.0),
                "lon": t.get("longitude", 0.0)
            })
        return out

    def calculate_fee(self, price, tag_id=None):
        try:
            price_int = int(float(price or 0))
        except Exception:
            price_int = 0
        if price_int <= 0:
            return {"net_earning": 0, "formatted_net": ""}
        net = self._app.get_net_creator_earning(price_int, tag_id)
        return {
            "net_earning": net,
            "formatted_net": format_idr(net)
        }

    def start_upload(self, form_data):
        self._app._apply_config_values(form_data)
        self._app._save_config_silent()
        return self._app.on_start()

    def stop_upload(self):
        self._app.on_stop()
        return True

    def reset_counters(self):
        self._app.on_reset_counters()
        return True

    def retry_failed(self):
        self._app.on_retry_failed()
        return True

    def clear_logs(self):
        with self._app._log_lock:
            self._app.logs.clear()
            self._app.log_counts = {"ok": 0, "fail": 0, "info": 0, "skip": 0}
        return True

    def toggle_log(self, show):
        return self._app.toggle_log(show)

    def save_theme(self, theme):
        self._app.cfg["theme"] = theme
        self._app._save_config_silent()
        return True

    def fetch_statistics(self, force=False, username="", password=""):
        return self._app.fetch_statistics_data(u=username, p=password, force=force)

    def get_photo_details(self, date_str):
        return self._app.get_photo_details_data(date_str)

    def get_image_preview_info(self, content_id, title):
        return self._app.get_image_preview_info(content_id, title)


def main():
    if "--test" in sys.argv or "--cli" in sys.argv:
        print("Testing backend headless...")
        app = App()
        app._load_config()
        print("App initialized successfully. Config:", app.cfg)
        sys.exit(0)

    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("fotoyu.uploader.app")
        except Exception:
            pass

    app = App()
    api = AppApi(app)
    app._api_bridge = api

    initial_payload = {
        "config": app.cfg,
        "presets": app._presets,
        "counters": app.counters,
        "log_counts": app.log_counts,
        "status": {
            "text": app._last_status_text,
            "color": app._status_dot_color(app._last_status_text)
        }
    }
    rendered_html = HTML_CONTENT.replace(
        "/*__INITIAL_DATA_PLACEHOLDER__*/",
        f"window.__INITIAL_DATA__ = {json.dumps(initial_payload, ensure_ascii=False)};"
    )

    sw, sh = get_screen_bounds()
    max_allowed_h = max(520, sh - 60)
    init_h = min(680, max_allowed_h)

    window = webview.create_window(
        title="FotoYu Uploader",
        html=rendered_html,
        js_api=api,
        width=FIXED_WINDOW_WIDTH,
        height=init_h,
        resizable=False
    )
    app._window = window
    api._window = window

    try:
        window.events.closed += app.on_stop
    except Exception:
        pass

    webview.start(debug=False)


if __name__ == "__main__":
    main()
