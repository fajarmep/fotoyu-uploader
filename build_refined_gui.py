# build_refined_gui.py
# Generator for refined, pixel-perfect pywebview GUI based on user specifications
import os
import re

print("Starting generation of refined GUI...")

# 1. Base backend code from tkinter.bak
with open("Windows/fotoyu_gui.py.tkinter.bak", "r", encoding="utf-8") as f:
    orig = f.read()

boundary_marker = "def format_display_date(d_str):"
idx = orig.find(boundary_marker)
assert idx != -1, "Marker format_display_date not found!"

end_fdd = orig.find("\ndef setup_tree_grid_lines", idx)
assert end_fdd != -1, "Marker setup_tree_grid_lines not found!"

fdd_code = orig[idx:end_fdd].strip()
base_code = orig[:idx].strip() + "\n\n" + fdd_code + "\n\n"

# Clean up tkinter imports from base_code
base_code = re.sub(r'import tkinter[^\n]*\n', '', base_code)
base_code = re.sub(r'from tkinter[^\n]*\n', '', base_code)
base_code = re.sub(r'try:\s+from PIL import ImageTk\s+except Exception:\s+ImageTk = None\n', '', base_code)

header_imports = """import webview
import webbrowser
from PIL import ImageOps
from concurrent.futures import ThreadPoolExecutor, as_completed
"""
base_code = header_imports + base_code

base_code = base_code.replace(
    '"tag_id": "baonVBMrDJ9XQw50v8Gpj6d7ZgWP20vA"',
    '"tag_id": "ezgM1YO8Lo3nVGbPeRkx50lbAdXQRvNK"'
)

old_smf = '''def safe_move_file(src, dest, max_retries=3, delay=0.15):
    """Memindahkan file dengan retry untuk mencegah kendala temporary file-lock pada OS."""
    for attempt in range(max_retries):
        try:
            shutil.move(src, dest)
            return True
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(delay)
    return False'''

new_smf = '''def safe_move_file(src, dest, max_retries=5, delay=0.2):
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
    return False'''

if old_smf in base_code:
    base_code = base_code.replace(old_smf, new_smf)

# Cleaned up dead replacement

old_paths = '''CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PRESETS_PATH = os.path.join(BASE_DIR, "presets.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history_uploaded.json")'''

new_paths = '''def find_data_file(filename):
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
HISTORY_PATH = find_data_file("history_uploaded.json")'''

if old_paths in base_code:
    base_code = base_code.replace(old_paths, new_paths)

old_sold_code = '''    def get_sold_creations(self, limit=200):
        """Mengambil daftar foto yang telah terjual."""
        token = self.get_token()
        if not token:
            return []
        headers = dict(HEADERS_BASE)
        headers["Authorization"] = f"Bearer {token}"
        try:
            with self.session.get(
                f"{BASE_URL}/gs/v5/creations",
                params={"page": 1, "limit": limit, "filter": "sold", "sort_by": "purchased_at", "sort_dir": "desc"},
                headers=headers, timeout=25
            ) as r:
                if r.status_code == 200:
                    return r.json().get("result", {}).get("data", [])
        except Exception:
            pass
        return []'''

new_sold_code = '''    def get_sold_creations(self, limit=None):
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
        return all_sold'''

if old_sold_code in base_code:
    base_code = base_code.replace(old_sold_code, new_sold_code)

# Extract worker methods from tkinter.bak
start_w = orig.find("    def _interruptible_sleep(self, seconds):")
end_w = orig.find("    # ---------------- Buka folder ----------------", start_w)
worker_methods = orig[start_w:end_w]

# Patch _compress_cycle to move photos into today's subfolder (DD-MM-YYYY)
old_cc_dir = '''    def _compress_cycle(self):
        up_dir = self._get_upload_dir()
        comp_dir = self._get_complete_dir()'''

new_cc_dir = '''    def _compress_cycle(self):
        up_dir = self._get_upload_dir()
        comp_dir = self._get_target_complete_dir()'''

if old_cc_dir in worker_methods:
    worker_methods = worker_methods.replace(old_cc_dir, new_cc_dir)

# Also ensure process_one moves files to target subfolder
old_mv1 = '''                    with self._path_lock:
                        dest = unique_path(comp_dir, name)
                    safe_move_file(src, dest)
                    return'''

new_mv1 = '''                    with self._path_lock:
                        t_dir = self._get_target_complete_dir() or comp_dir
                        dest = unique_path(t_dir, name)
                    safe_move_file(src, dest)
                    return'''

if old_mv1 in worker_methods:
    worker_methods = worker_methods.replace(old_mv1, new_mv1)

old_mv2 = '''                    with self._path_lock:
                        dest = unique_path(comp_dir, name)
                    safe_move_file(src, dest)
                    with self._fail_lock:'''

new_mv2 = '''                    with self._path_lock:
                        t_dir = self._get_target_complete_dir() or comp_dir
                        dest = unique_path(t_dir, name)
                    safe_move_file(src, dest)
                    with self._fail_lock:'''

if old_mv2 in worker_methods:
    worker_methods = worker_methods.replace(old_mv2, new_mv2)

# Clean up Tkinter calls from worker_methods
worker_methods = re.sub(
    r'self\.post\(lambda p=pct: self\.pb\.configure\(value=p\)\)',
    'self.update_progress(pct)',
    worker_methods
)
worker_methods = re.sub(
    r'self\.post\(lambda: self\.pb\.configure\(value=100\)\)',
    'self.update_progress(100)',
    worker_methods
)
worker_methods = re.sub(r'self\.post\([^\n]+\)\n', '', worker_methods)

# App code
app_code = r'''
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

'''

app_code += worker_methods + "\n"

# AppApi code
api_code = r'''
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
'''

with open("app_ui.html", "r", encoding="utf-8") as f:
    html_content = f.read()

full_script = base_code + "\n\nHTML_CONTENT = r'''" + html_content + "'''\n\n" + app_code + "\n\n" + api_code

with open("Windows/fotoyu_gui.py", "w", encoding="utf-8") as f:
    f.write(full_script)

with open("MacOS/fotoyu_gui.py", "w", encoding="utf-8") as f:
    f.write(full_script)

print("Finished generating Windows/fotoyu_gui.py and MacOS/fotoyu_gui.py successfully!")
