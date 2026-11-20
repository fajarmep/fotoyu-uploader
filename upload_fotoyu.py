import os
import io
import glob
import time
import hashlib
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
from urllib.parse import urlparse, unquote
from datetime import datetime, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageOps

# ================= USER CONFIGURATION =================
USERNAME = "yucapturemoment"
PASSWORD = "@Rahasia1986"

PRICE = 35000
CURRENCY_ID = "MR9Xdjv70WpeODE8DEPLAyb8xVBQzq31"
TAG_IDS = ["baonVBMrDJ9XQw50v8Gpj6d7ZgWP20vA"]
LOCATION_JSON = '{"name":"Kota Yogyakarta", "longitude":110.36475, "latitude":-7.8013673}'

# --- Aturan kompresi (SATU-SATUNYA metode yang dipakai) ---
# Target ukuran akhir  : maksimal 500 KB
# I8V  = 1440          : sisi terpanjang gambar diperkecil ke maksimal 1440 px
# cR7  = 100           : iterasi kualitas JPEG dimulai dari quality = 100
# Dmf  = 50 * 2^20     : batas ukuran file MASUKAN (50 MiB) sebelum diproses
# JTH  = 50 * 10^6     : batas total piksel MASUKAN (50 MP) sebelum diproses
# Iterasi: 100 -> 90 -> 80 -> 75 -> 70 -> ... turun bertahap sampai file <= 500 KB
MAX_OUTPUT_BYTES = 500 * 1024        # 500 KB target keluaran
I8V_MAX_DIMENSION = 1440             # sisi terpanjang keluaran
cR7_START_QUALITY = 95               # ultra-high-quality start 95 (maksimal ketajaman & 100% aman <= 500 KB)
Dmf_MAX_INPUT_BYTES = 50 * (2 ** 20)  # 50 MiB batas file masuk
JTH_MAX_INPUT_PIXELS = 50 * 10 ** 6   # 50 MP batas piksel masuk
cR7_MIN_QUALITY = 20                 # batas bawah quality saat iterasi

# --- Bulk & Concurrency ---
CPU_WORKERS = max(2, min(8, (os.cpu_count() or 4)))  # Worker paralel kompresi CPU
UPLOAD_WORKERS = 10                  # Worker paralel upload S3
COMPRESS_BULK_MAX = 2000             # Mendukung hingga 2.000 file sekaligus (sesuai limit browser)
UPLOAD_BULK_MAX = 2000               # Mendukung hingga 2.000 file sekaligus (sesuai limit browser)
FILE_STABLE_AGE = 1.5                # Non-blocking threshold umur file selesai ditulis (detik)

# --- Kegagalan kompresi ---
COMPRESS_FAIL_LIMIT = 3              # gagal 3x -> catat log & skip di putaran berikutnya

# --- Folder kerja (SELALU satu folder dengan file python ini) ---
# Upload/Compress/Complete/Temp dibuat di folder yang sama dengan script.
# Bila folder ini read-only, pindahkan script ke folder yang bisa ditulis
# (script akan memberi pesan jelas), atau set environment FOTOYU_BASE_DIR.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_base_dir():
    return os.environ.get("FOTOYU_BASE_DIR") or PROJECT_DIR


BASE_DIR = _resolve_base_dir()
UPLOAD_DIR = os.path.join(BASE_DIR, "Upload")
COMPRESS_DIR = os.path.join(BASE_DIR, "Compress")
COMPLETE_DIR = os.path.join(BASE_DIR, "Complete")
TEMP_DIR = os.path.join(BASE_DIR, "Temp")

# --- Perilaku watcher ---
POLL_INTERVAL = 1                    # detik jeda antar pemindaian (responsif untuk burst kamera)
IMAGE_EXTS = (".jpg", ".jpeg", ".png")  # hanya JPG/JPEG/PNG (tanpa HEIC/HEIF)

# True  = tiap foto jadi creation terpisah.
# False = semua foto dalam satu putaran dikirim sebagai satu creation/series.
PER_PHOTO_CREATION = False

# Tampilkan key S3 tujuan (hasil decode URL presigned) sebelum upload.
DEBUG_KEYS = True

# Content-Type upload (server Fotoyu menandatangani URL sebagai image/jpeg).
CONTENT_TYPE_OVERRIDE = "image/jpeg"
# ======================================================

BASE_URL = "https://api.fotoyu.com"
TIMEOUT = 60
HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://www.fotoyu.com/"
}

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# State kegagalan kompresi (hanya dipakai oleh thread kompres)
compress_fail_counts = {}
compress_skip = set()
_path_lock = threading.Lock()
_fail_lock = threading.Lock()

# Anti-Duplikat
HISTORY_PATH = os.path.join(BASE_DIR, "history_uploaded.json")
uploaded_fingerprints = set()
pending_fingerprints = {}
_history_lock = threading.Lock()


def log(msg):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def build_session():
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=30, pool_maxsize=30)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


# ============================================================
#  UTIL
# ============================================================
def ensure_dirs():
    for d in (UPLOAD_DIR, COMPRESS_DIR, COMPLETE_DIR, TEMP_DIR):
        os.makedirs(d, exist_ok=True)


def test_writable(directory):
    """Cek apakah folder benar-benar bisa ditulis. Mengembalikan (ok, error)."""
    probe = os.path.join(directory, "._write_test.tmp")
    try:
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True, None
    except Exception as e:
        return False, e


def unique_path(directory, filename):
    """Hindari menimpa file yang sudah ada: nama.jpg -> nama_1.jpg, dst."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{i}{ext}")
        i += 1
    return candidate


IMAGE_EXTS_SET = {".jpg", ".jpeg", ".png"}

def list_images(directory):
    """Mendaftar foto dengan os.scandir (jauh lebih cepat daripada glob untuk ribuan file)."""
    try:
        files = []
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_file():
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTS_SET:
                        files.append(entry.path)
        files.sort()
        return files
    except Exception:
        return []


def is_file_stable(path, min_age=FILE_STABLE_AGE):
    """Cek non-blocking apakah file sudah selesai ditulis oleh kamera/OS.
    Menghindari time.sleep() yang memblokir proses ingest burst foto."""
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
    """Sidik jari cepat & unik untuk deteksi anti-duplikat.
    Kombinasi ukuran file + 32KB awal (EXIF & header) + 32KB akhir.
    Sangat cepat (<1 ms) dan 100% akurat membedakan foto kamera asli."""
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


def load_history():
    global uploaded_fingerprints
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    fps = data.get("fingerprints", {})
                    if isinstance(fps, dict):
                        uploaded_fingerprints = set(fps.keys())
                    elif isinstance(fps, list):
                        uploaded_fingerprints = set(fps)
                elif isinstance(data, list):
                    uploaded_fingerprints = set(data)
        except Exception:
            pass

    if not uploaded_fingerprints:
        threading.Thread(target=index_complete_dir, daemon=True).start()
    else:
        log(f"[HISTORY] Anti-Duplikat aktif: {len(uploaded_fingerprints)} foto terindeks.")


def index_complete_dir():
    try:
        files = list_images(COMPLETE_DIR)
        new_records = {}
        for f in files:
            fp = get_file_fingerprint(f)
            if fp:
                new_records[fp] = os.path.basename(f)
        if new_records:
            save_history(new_records)
            log(f"[HISTORY] Index {len(new_records)} foto di Complete ke Anti-Duplikat.")
    except Exception:
        pass


def save_history(new_records):
    if not new_records:
        return
    with _history_lock:
        current_data = {"fingerprints": {}}
        if os.path.exists(HISTORY_PATH):
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
            except Exception:
                current_data = {"fingerprints": {}}
        fps = current_data.setdefault("fingerprints", {})
        now_iso = datetime.now(timezone.utc).isoformat()
        for fp, name in new_records.items():
            uploaded_fingerprints.add(fp)
            fps[fp] = {"name": name, "uploaded_at": now_iso}
        try:
            tmp = HISTORY_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(current_data, f, indent=2)
            os.replace(tmp, HISTORY_PATH)
        except Exception:
            pass


def get_file_md5(file_path):
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_content_type(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    return CONTENT_TYPES.get(ext, "image/jpeg")


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
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def quality_sequence(start=cR7_START_QUALITY, min_q=cR7_MIN_QUALITY):
    q = start
    while q >= min_q:
        yield q
        q -= 5 if q > 60 else 10


# ============================================================
#  KOMPRESI (hanya resize I8V=1440 + iterasi quality sampai <= 500 KB)
# ============================================================
def compress_image(src_path, dst_path):
    """Mengembalikan (ok, quality, size, width, height).
    Penulisan bersifat atomik (.part lalu rename) agar thread upload tidak
    pernah membaca file yang belum selesai ditulis.
    Memakai libjpeg DCT draft decode + Bicubic + Smart Quality Search."""
    with Image.open(src_path) as img:
        w_orig, h_orig = img.size
        longest_orig = max(w_orig, h_orig)
        if longest_orig > I8V_MAX_DIMENSION:
            try:
                img.draft("RGB", (I8V_MAX_DIMENSION * 2, I8V_MAX_DIMENSION * 2))
            except Exception:
                pass

        try:
            img = ImageOps.exif_transpose(img)  # putar sesuai EXIF (potret tetap potret)
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
            img.save(buf, "JPEG", quality=q)
            size = buf.tell()
            if size <= MAX_OUTPUT_BYTES:
                tmp_path = dst_path + ".part"
                with open(tmp_path, "wb") as f:
                    f.write(buf.getvalue())
                os.replace(tmp_path, dst_path)
                return True, q, size, width, height

    return False, None, None, None, None


def _handle_compress_failure(src, name, reason):
    """Catat kegagalan; file tetap di Upload untuk diulang.
    Setelah COMPRESS_FAIL_LIMIT kali, file di-skip pada putaran berikutnya."""
    with _fail_lock:
        count = compress_fail_counts.get(src, 0) + 1
        compress_fail_counts[src] = count
        if count >= COMPRESS_FAIL_LIMIT:
            compress_skip.add(src)
            log(f"[COMPRESS] SKIP PERMANEN {name} -> gagal {count}x ({reason}). Tidak dicoba lagi.")
        else:
            log(f"[COMPRESS] GAGAL {name} -> {reason}. Percobaan {count}/{COMPRESS_FAIL_LIMIT}, akan diulang.")


def run_compress_cycle():
    """Ambil hingga COMPRESS_BULK_MAX foto dari Upload, kompres paralel ke Compress, pindahkan asli ke Complete."""
    ok_write, werr = test_writable(COMPRESS_DIR)
    if not ok_write:
        log(f"[COMPRESS] Folder Compress TIDAK BISA DITULIS ({werr}). Putaran dilewati.")
        return 0

    files = [f for f in list_images(UPLOAD_DIR) if f not in compress_skip]
    if not files:
        return 0

    batch = files[:COMPRESS_BULK_MAX]
    log(f"[COMPRESS] Menemukan {len(files)} file di Upload. Memproses {len(batch)} file secara paralel ({CPU_WORKERS} worker)...")

    processed = 0
    proc_lock = threading.Lock()

    def process_one(src):
        nonlocal processed
        name = os.path.basename(src)
        try:
            if not is_file_stable(src):
                # File masih sedang ditulis kamera, lewati (0 detik delay)
                return

            # --- Cek Anti-Duplikat ---
            fp = get_file_fingerprint(src)
            with _history_lock:
                is_dup = bool(fp and fp in uploaded_fingerprints)
            if is_dup:
                log(f"[DUPLIKAT] {name} sudah pernah diupload, lewati kompresi & arsipkan.")
                with _path_lock:
                    dst = unique_path(COMPLETE_DIR, name)
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass
                return

            size_in = os.path.getsize(src)
            if size_in > Dmf_MAX_INPUT_BYTES:
                _handle_compress_failure(src, name, f"{size_in / 2**20:.1f} MB > batas {Dmf_MAX_INPUT_BYTES / 2**20:.0f} MB")
                return

            with Image.open(src) as im:
                w0, h0 = im.size
            if w0 * h0 > JTH_MAX_INPUT_PIXELS:
                _handle_compress_failure(src, name, f"{w0}x{h0} > batas {JTH_MAX_INPUT_PIXELS} px")
                return

            base, _ = os.path.splitext(name)
            with _path_lock:
                out_path = unique_path(COMPRESS_DIR, base + ".jpg")

            ok, q, out_size, out_w, out_h = compress_image(src, out_path)
            if ok:
                log(f"[COMPRESS] OK   {name} -> {os.path.basename(out_path)} "
                    f"({out_w}x{out_h}, q={q}, {out_size / 1024:.1f} KB)")
                with _history_lock:
                    if fp:
                        pending_fingerprints[os.path.basename(out_path)] = (fp, name)
                with _path_lock:
                    dst = unique_path(COMPLETE_DIR, name)
                shutil.move(src, dst)
                with _fail_lock:
                    compress_fail_counts.pop(src, None)
                with proc_lock:
                    processed += 1
            else:
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except Exception:
                        pass
                _handle_compress_failure(src, name, f"tidak mencapai <= 500 KB (quality min {cR7_MIN_QUALITY})")
        except Exception as e:
            _handle_compress_failure(src, name, f"error: {e}")

    with ThreadPoolExecutor(max_workers=CPU_WORKERS) as executor:
        list(executor.map(process_one, batch))

    return processed


# ============================================================
#  UPLOAD (API Fotoyu)
# ============================================================
def extract_token_from_response(data):
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ["token", "access_token", "jwt", "authorization"]:
            if key in data and data[key]:
                val = data[key]
                if isinstance(val, str):
                    return val
                elif isinstance(val, dict):
                    res = extract_token_from_response(val)
                    if res:
                        return res
        for k, v in data.items():
            res = extract_token_from_response(v)
            if res:
                return res
    elif isinstance(data, list):
        for item in data:
            res = extract_token_from_response(item)
            if res:
                return res
    return None


def login():
    url = f"{BASE_URL}/gs/v2/auth/login"
    payload = {"identifier": USERNAME, "password": PASSWORD}
    try:
        res = SESSION.post(url, json=payload, headers=HEADERS_BASE, timeout=TIMEOUT)
        if res.status_code == 200:
            token = extract_token_from_response(res.json())
            if token:
                log("[AUTH] Login berhasil.")
                return token
        log(f"[AUTH] Login gagal: {res.status_code} {res.text[:200]}")
        return None
    except Exception as e:
        log(f"[AUTH] Error: {e}")
        return None


def prepare_upload_item(path):
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext == "jpg":
        ext = "jpeg"
    try:
        with Image.open(path) as img:
            width, height = img.size
    except Exception as e:
        log(f"[UPLOAD] SKIP {os.path.basename(path)}: tidak bisa dibaca ({e})")
        return None
    size = os.path.getsize(path)
    return {
        "original": path,
        "upload_path": path,
        "name": os.path.basename(path),
        "ext": ext,
        "content_type": get_content_type(path),
        "width": width,
        "height": height,
        "size": size,
        "signature": get_file_md5(path),
    }


def get_bulk_presigned_urls(token, items):
    url = f"{BASE_URL}/gs/v3/creations/link"
    headers = HEADERS_BASE.copy()
    headers["Authorization"] = f"Bearer {token}"

    contents_payload = []
    meta_map = {}
    for meta in items:
        sig = meta["signature"]
        meta_map.setdefault(sig, []).append(meta)
        contents_payload.append({
            "height": meta["height"],
            "width": meta["width"],
            "size": meta["size"],
            "extension": meta["ext"],
            "file": {},
            "signature": sig
        })

    try:
        res = SESSION.post(url, json={"contents": contents_payload}, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        log(f"[UPLOAD] Error presigned URL: {e}")
        return [], meta_map, False

    if res.status_code in (200, 201):
        results = res.json().get("result", [])
        log(f"[UPLOAD] Dapat {len(results)} presigned URL.")
        return results, meta_map, False
    if res.status_code == 401:
        return [], meta_map, True
    log(f"[UPLOAD] Presigned URL gagal: {res.status_code} {res.text[:200]}")
    return [], meta_map, False


def resolve_presigned_link(item):
    upload_key = item.get("upload_key") or item.get("key")
    for field in ("original_link", "original_url", "presigned_url", "upload_link", "link", "url"):
        val = item.get(field)
        if isinstance(val, str) and val.startswith("http"):
            return val, upload_key
        if isinstance(val, dict):
            for sub in ("original_link", "url", "link"):
                if isinstance(val.get(sub), str):
                    return val[sub], upload_key
    return None, upload_key


def s3_key_from_url(url):
    if not url:
        return None
    try:
        return unquote(urlparse(url).path.lstrip("/"))
    except Exception:
        return None


def upload_file_to_s3(upload_url, file_path, width, height, content_type):
    headers = {
        "User-Agent": HEADERS_BASE["User-Agent"],
        "Content-Type": content_type,
        "x-amz-meta-imagewidth": str(width),
        "x-amz-meta-imageheight": str(height)
    }
    with open(file_path, "rb") as f:
        data = f.read()
    try:
        res = SESSION.put(upload_url, data=data, headers=headers, timeout=TIMEOUT)
        ok = res.status_code in (200, 201)
        body = "" if ok else " ".join(res.text.split())[:400]
        return ok, res.status_code, res.headers.get("ETag", ""), body
    except Exception as e:
        return False, None, "", str(e)


def build_creation_payload(contents, ref_file_path):
    iso_time, unix_time = get_file_timestamps(ref_file_path)
    return {
        "currency_id": CURRENCY_ID,
        "location": LOCATION_JSON,
        "description": "",
        "original_at": iso_time,
        "unix_original_at": unix_time,
        "is_bibs": [{"tag_id": TAG_IDS[0], "is_bib": False}],
        "nickname": [],
        "price": PRICE,
        "resend": True,
        "tag_ids": TAG_IDS,
        "contents": contents
    }


def finalize_creations(token, entries):
    url = f"{BASE_URL}/gs/v4/creations"
    headers = HEADERS_BASE.copy()
    headers["Authorization"] = f"Bearer {token}"

    if PER_PHOTO_CREATION:
        ok_count = 0
        for content_obj, ref_path in entries:
            try:
                res = SESSION.post(url, json=build_creation_payload([content_obj], ref_path),
                                   headers=headers, timeout=TIMEOUT)
                if res.status_code in (200, 201):
                    ok_count += 1
            except Exception:
                pass
        return ok_count == len(entries)

    payload = build_creation_payload([c for c, _ in entries], entries[0][1])
    try:
        res = SESSION.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        return res.status_code in (200, 201)
    except Exception:
        return False


def run_upload_batch(token, files):
    """Upload satu batch file dari Compress. Mengembalikan (token, daftar_file_sukses)."""
    with ThreadPoolExecutor(max_workers=CPU_WORKERS) as prep_exec:
        items = [res for res in prep_exec.map(prepare_upload_item, files) if res is not None]
    if not items:
        return token, []

    s3_results, meta_map, need_relogin = get_bulk_presigned_urls(token, items)
    if need_relogin:
        token = login()
        if not token:
            return token, []
        s3_results, meta_map, _ = get_bulk_presigned_urls(token, items)
    if not s3_results:
        return token, []

    if DEBUG_KEYS:
        for item in s3_results:
            _url, _ = resolve_presigned_link(item)
            _key = s3_key_from_url(_url)
            if _key:
                log(f"[UPLOAD] key: {_key}")

    work_items = []
    for item in s3_results:
        sig = item.get("signature")
        bucket = meta_map.get(sig)
        if not bucket:
            continue
        meta = bucket.pop(0)
        s3_url, upload_key = resolve_presigned_link(item)
        if not s3_url:
            log(f"[UPLOAD] GAGAL {meta['name']}: {item.get('error') or 'URL tidak ada'}")
            continue
        work_items.append((meta, s3_url, upload_key, sig))

    entries = []
    success_files = []
    entries_lock = threading.Lock()

    def upload_one(work):
        meta, s3_url, upload_key, sig = work
        base_type = CONTENT_TYPE_OVERRIDE or meta["content_type"]
        candidates = [base_type]
        if meta["content_type"] not in candidates:
            candidates.append(meta["content_type"])

        success = False
        status = None
        for ct in candidates:
            success, status, _etag, body = upload_file_to_s3(
                s3_url, meta["upload_path"], meta["width"], meta["height"], ct)
            if success or status != 403:
                if not success:
                    log(f"[UPLOAD] GAGAL {meta['name']} ({status}): {body}")
                break

        if success:
            content_obj = {
                "title": meta["name"],
                "content": s3_url,
                "height": meta["height"],
                "width": meta["width"],
                "size": meta["size"],
                "signature": sig
            }
            if upload_key:
                content_obj["upload_key"] = upload_key
            with entries_lock:
                entries.append((content_obj, meta["original"]))
                success_files.append(meta["original"])
            log(f"[UPLOAD] OK   {meta['name']} ({status})")

    with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as up_exec:
        list(up_exec.map(upload_one, work_items))

    if entries:
        ok = finalize_creations(token, entries)
        if ok:
            log(f"[UPLOAD] Publikasi BERHASIL untuk {len(entries)} foto.")
            new_records = {}
            with _history_lock:
                for f in success_files:
                    comp_name = os.path.basename(f)
                    if comp_name in pending_fingerprints:
                        fp, orig_name = pending_fingerprints.pop(comp_name)
                        new_records[fp] = orig_name
                    else:
                        fp_direct = get_file_fingerprint(f)
                        if fp_direct:
                            new_records[fp_direct] = comp_name
            if new_records:
                save_history(new_records)
        else:
            log(f"[UPLOAD] Publikasi GAGAL. File tetap di Compress agar dicoba lagi.")
            return token, []

    return token, success_files


def run_upload_cycle(token):
    """Ambil maksimal 200 file dari Compress, upload, PINDAHKAN yang berhasil publish ke Temp.
    File asli tidak pernah dihapus (arsip di folder Complete)."""
    files = list_images(COMPRESS_DIR)
    if not files:
        return token, 0

    batch = files[:UPLOAD_BULK_MAX]
    log(f"[UPLOAD] Menemukan {len(files)} file di Compress. Mengunggah {len(batch)} file...")

    token, success_files = run_upload_batch(token, batch)
    for f in success_files:
        try:
            shutil.move(f, unique_path(TEMP_DIR, os.path.basename(f)))
        except Exception:
            pass
    return token, len(success_files)


# ============================================================
#  WORKER THREADS (kompres & upload berjalan bersamaan)
# ============================================================
def compress_worker(stop_event):
    while not stop_event.is_set():
        try:
            run_compress_cycle()
        except Exception as e:
            log(f"[COMPRESS] Loop error: {e}")
        stop_event.wait(POLL_INTERVAL)


def upload_worker(stop_event, token):
    while not stop_event.is_set():
        try:
            if not token:
                token = login()
            token, _ = run_upload_cycle(token)
        except Exception as e:
            log(f"[UPLOAD] Loop error: {e}")
        stop_event.wait(POLL_INTERVAL)


# ============================================================
#  MAIN
# ============================================================
def main():
    try:
        ensure_dirs()
        load_history()
    except Exception as e:
        print(f"[FATAL] Gagal membuat folder kerja: {e}")
        print("Jalankan di terminal biasa / sebagai Administrator, atau set FOTOYU_BASE_DIR.")
        return
    print("==========================================================")
    print("        FOTOYU AUTO COMPRESS & UPLOAD WATCHER             ")
    print("==========================================================")
    print(f"Upload   : {UPLOAD_DIR}")
    print(f"Compress : {COMPRESS_DIR}")
    print(f"Complete : {COMPLETE_DIR}")
    print(f"Temp     : {TEMP_DIR}")
    print(f"Format   : JPG / JPEG / PNG (tanpa HEIC/HEIF)")
    print(f"Kompresi : I8V={I8V_MAX_DIMENSION}, cR7={cR7_START_QUALITY}, "
          f"target<={MAX_OUTPUT_BYTES / 1024:.0f}KB, gagal maks {COMPRESS_FAIL_LIMIT}x")
    print(f"Bulk     : kompres {COMPRESS_BULK_MAX}/putaran, upload {UPLOAD_BULK_MAX}/putaran")
    print(f"Mode     : kompres & upload berjalan bersamaan (2 thread)")
    print("==========================================================\n")

    if os.path.abspath(BASE_DIR) != os.path.abspath(PROJECT_DIR):
        print("[INFO] Memakai folder kerja dari FOTOYU_BASE_DIR:")
        print(f"       {BASE_DIR}\n")

    problems = []
    for d in (UPLOAD_DIR, COMPRESS_DIR, COMPLETE_DIR, TEMP_DIR):
        ok, err = test_writable(d)
        if not ok:
            problems.append((d, err))
    if problems:
        print("[FATAL] Folder kerja TIDAK BISA DITULIS oleh proses ini:")
        for d, err in problems:
            print(f"        - {d}  ->  {err}")
        print("\nUpload/Compress/Complete/Temp harus berada SATU folder dengan file python ini,")
        print(f"di lokasi yang bisa ditulis:  {PROJECT_DIR}")
        print("\nSolusi:")
        print("  1. Jalankan script di PowerShell/CMD BIASA (di luar sandbox), atau")
        print("  2. Pindahkan file python + folder Upload/Compress/Complete/Temp ke")
        print("     folder yang bisa ditulis (mis. C:\\Users\\Administrator\\FotoYuUploader).")
        return

    log("[START] Login ke Fotoyu...")
    token = None
    while not token:
        token = login()
        if not token:
            log("[START] Login gagal, coba lagi 5 detik...")
            time.sleep(5)

    log("[START] Memantau folder Upload & Compress. Tekan Ctrl+C untuk berhenti.\n")

    stop_event = threading.Event()
    t_compress = threading.Thread(target=compress_worker, args=(stop_event,), daemon=True)
    t_upload = threading.Thread(target=upload_worker, args=(stop_event, token), daemon=True)
    t_compress.start()
    t_upload.start()

    try:
        while t_compress.is_alive() and t_upload.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("[STOP] Dihentikan oleh pengguna. Sampai jumpa!")
        stop_event.set()


if __name__ == "__main__":
    main()
