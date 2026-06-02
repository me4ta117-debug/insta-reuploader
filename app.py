import os
import sys
import uuid
import random
import threading
import subprocess
import json
from pathlib import Path
from tkinter import Tk, ttk, filedialog, messagebox, StringVar, BooleanVar, IntVar, DoubleVar
from tkinter.scrolledtext import ScrolledText

import yt_dlp

# === Пути ===
APP_DIR = Path.home() / "InstaReuploader"
APP_DIR.mkdir(exist_ok=True)
CONFIG_FILE = APP_DIR / "config.json"
TEMP_DIR = APP_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)


def get_ffmpeg_path() -> str:
    """Ищет ffmpeg.exe сначала внутри собранного приложения, потом рядом с exe, потом в PATH."""
    # 1) PyInstaller bundle (--onefile) — файлы лежат в sys._MEIPASS
    if getattr(sys, "frozen", False):
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        bundled = bundle_dir / "ffmpeg.exe"
        if bundled.exists():
            return str(bundled)
        # 2) Рядом с exe (на случай ручной подмены)
        next_to_exe = Path(sys.executable).parent / "ffmpeg.exe"
        if next_to_exe.exists():
            return str(next_to_exe)
    else:
        # 3) В режиме разработки — рядом со скриптом
        local = Path(__file__).parent / "ffmpeg.exe"
        if local.exists():
            return str(local)

    # 4) Фоллбэк — системный PATH
    return "ffmpeg"


FFMPEG = get_ffmpeg_path()


# ---------- Логика обработки ----------

def download_video(url: str, output_path: Path, cookies_file: Path | None, log):
    log(f"⏬ Скачиваю: {url}")
    ydl_opts = {
        "outtmpl": str(output_path),
        "format": "mp4/bestvideo+bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "ffmpeg_location": FFMPEG,
    }
    if cookies_file and cookies_file.exists():
        ydl_opts["cookiefile"] = str(cookies_file)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return output_path


def banner_overlay_xy(position: str, margin: int) -> tuple[str, str]:
    pos = {
        "top":          ("(W-w)/2",        f"{margin}"),
        "bottom":       ("(W-w)/2",        f"H-h-{margin}"),
        "top-left":     (f"{margin}",      f"{margin}"),
        "top-right":    (f"W-w-{margin}",  f"{margin}"),
        "bottom-left":  (f"{margin}",      f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}",  f"H-h-{margin}"),
        "center":       ("(W-w)/2",        "(H-h)/2"),
    }
    return pos.get(position, pos["bottom"])


def modify_video(
    input_path: Path,
    output_path: Path,
    *,
    hflip: bool,
    banner_path: Path | None,
    banner_position: str,
    banner_width_pct: int,
    banner_opacity: float,
    banner_margin: int,
    log,
):
    brightness = round(random.uniform(-0.05, 0.08), 3)
    contrast   = round(random.uniform(0.95, 1.10), 3)
    saturation = round(random.uniform(0.90, 1.15), 3)
    gamma      = round(random.uniform(0.95, 1.05), 3)
    speed      = round(random.uniform(0.97, 1.03), 3)
    crop_px    = random.randint(4, 12)

    log(f"🎨 Параметры: brightness={brightness}, contrast={contrast}, "
        f"saturation={saturation}, gamma={gamma}, speed={speed}, "
        f"crop={crop_px}px, hflip={hflip}")

    filters_main = [
        f"eq=brightness={brightness}:contrast={contrast}:"
        f"saturation={saturation}:gamma={gamma}",
        f"crop=iw-{crop_px*2}:ih-{crop_px*2}:{crop_px}:{crop_px}",
        f"setpts={1/speed:.4f}*PTS",
    ]
    if hflip:
        filters_main.append("hflip")

    cmd = [FFMPEG, "-y", "-i", str(input_path)]
    use_banner = banner_path and banner_path.exists()

    if use_banner:
        cmd += ["-i", str(banner_path)]
        x, y = banner_overlay_xy(banner_position, banner_margin)
        filter_complex = (
            f"[0:v]{','.join(filters_main)}[v0];"
            f"[1:v]scale=iw*{banner_width_pct/100}:-1,"
            f"format=rgba,colorchannelmixer=aa={banner_opacity}[bnr];"
            f"[v0][bnr]overlay={x}:{y}:format=auto[v]"
        )
        cmd += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "0:a?"]
    else:
        cmd += ["-vf", ",".join(filters_main)]

    cmd += [
        "-af", f"atempo={speed}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    log("⚙️ Перекодирую через ffmpeg...")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW
    subprocess.run(cmd, check=True, capture_output=True, creationflags=creationflags)
    return output_path


# ---------- GUI ----------

class App:
    def __init__(self, root: Tk):
        self.root = root
        root.title("Instagram Reuploader")
        root.geometry("720x680")
        root.minsize(640, 580)

        self.cfg = self._load_config()

        self.url_var       = StringVar()
        self.output_dir    = StringVar(value=self.cfg.get("output_dir", str(Path.home() / "Downloads")))
        self.cookies_path  = StringVar(value=self.cfg.get("cookies_path", ""))
        self.banner_path   = StringVar(value=self.cfg.get("banner_path", ""))
        self.hflip_var     = BooleanVar(value=self.cfg.get("hflip", False))
        self.banner_on     = BooleanVar(value=self.cfg.get("banner_on", True))
        self.banner_pos    = StringVar(value=self.cfg.get("banner_pos", "bottom"))
        self.banner_width  = IntVar(value=self.cfg.get("banner_width", 90))
        self.banner_op     = DoubleVar(value=self.cfg.get("banner_opacity", 0.85))
        self.banner_margin = IntVar(value=self.cfg.get("banner_margin", 20))

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        url_frame = ttk.LabelFrame(self.root, text="Ссылка на видео")
        url_frame.pack(fill="x", **pad)
        ttk.Entry(url_frame, textvariable=self.url_var).pack(fill="x", padx=8, pady=8)

        paths_frame = ttk.LabelFrame(self.root, text="Файлы")
        paths_frame.pack(fill="x", **pad)
        self._path_row(paths_frame, 0, "Папка для сохранения:", self.output_dir, self._pick_dir)
        self._path_row(paths_frame, 1, "Cookies (cookies.txt):",  self.cookies_path, self._pick_cookies)
        self._path_row(paths_frame, 2, "Баннер (PNG):",           self.banner_path,  self._pick_banner)
        paths_frame.columnconfigure(1, weight=1)

        opts_frame = ttk.LabelFrame(self.root, text="Опции обработки")
        opts_frame.pack(fill="x", **pad)
        ttk.Checkbutton(opts_frame, text="Зеркальное отражение (hflip)",
                        variable=self.hflip_var).grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Checkbutton(opts_frame, text="Накладывать баннер",
                        variable=self.banner_on).grid(row=0, column=1, sticky="w", padx=8, pady=4)

        ttk.Label(opts_frame, text="Позиция баннера:").grid(row=1, column=0, sticky="w", padx=8)
        ttk.Combobox(opts_frame, textvariable=self.banner_pos, state="readonly",
                     values=["top", "bottom", "top-left", "top-right",
                             "bottom-left", "bottom-right", "center"]
                     ).grid(row=1, column=1, sticky="ew", padx=8, pady=2)

        ttk.Label(opts_frame, text="Ширина баннера (%):").grid(row=2, column=0, sticky="w", padx=8)
        ttk.Spinbox(opts_frame, from_=10, to=100, textvariable=self.banner_width, width=8
                    ).grid(row=2, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(opts_frame, text="Прозрачность (0.0–1.0):").grid(row=3, column=0, sticky="w", padx=8)
        ttk.Spinbox(opts_frame, from_=0.0, to=1.0, increment=0.05,
                    textvariable=self.banner_op, width=8
                    ).grid(row=3, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(opts_frame, text="Отступ от края (px):").grid(row=4, column=0, sticky="w", padx=8)
        ttk.Spinbox(opts_frame, from_=0, to=200, textvariable=self.banner_margin, width=8
                    ).grid(row=4, column=1, sticky="w", padx=8, pady=2)
        opts_frame.columnconfigure(1, weight=1)

        self.go_btn = ttk.Button(self.root, text="🚀 Обработать", command=self._on_go)
        self.go_btn.pack(fill="x", padx=8, pady=8)

        log_frame = ttk.LabelFrame(self.root, text="Лог")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_widget = ScrolledText(log_frame, height=10, state="disabled", wrap="word")
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

        self.log(f"ffmpeg: {FFMPEG}")

    def _path_row(self, parent, row, label, var, picker):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=4)
        ttk.Button(parent, text="...", width=4, command=picker).grid(row=row, column=2, padx=4)

    def _pick_dir(self):
        path = filedialog.askdirectory(initialdir=self.output_dir.get() or str(Path.home()))
        if path:
            self.output_dir.set(path)

    def _pick_cookies(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if path:
            self.cookies_path.set(path)

    def _pick_banner(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All", "*.*")])
        if path:
            self.banner_path.set(path)

    def log(self, msg: str):
        def _append():
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", msg + "\n")
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")
        self.root.after(0, _append)

    def _on_go(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Ой", "Вставь ссылку на видео.")
            return
        if "instagram.com" not in url:
            if not messagebox.askyesno("Не Instagram",
                                       "Ссылка не похожа на Instagram. Всё равно попробовать?"):
                return
        out_dir = Path(self.output_dir.get())
        if not out_dir.exists():
            messagebox.showerror("Ошибка", f"Папка не найдена: {out_dir}")
            return

        self._save_config()
        self.go_btn.configure(state="disabled", text="Обрабатываю...")
        threading.Thread(target=self._worker, args=(url, out_dir), daemon=True).start()

    def _worker(self, url: str, out_dir: Path):
        uid = uuid.uuid4().hex[:8]
        raw_path = TEMP_DIR / f"raw_{uid}.mp4"
        final_path = out_dir / f"reup_{uid}.mp4"
        try:
            cookies = Path(self.cookies_path.get()) if self.cookies_path.get() else None
            download_video(url, raw_path, cookies, self.log)

            banner = Path(self.banner_path.get()) if (self.banner_on.get() and self.banner_path.get()) else None
            modify_video(
                raw_path, final_path,
                hflip=self.hflip_var.get(),
                banner_path=banner,
                banner_position=self.banner_pos.get(),
                banner_width_pct=self.banner_width.get(),
                banner_opacity=float(self.banner_op.get()),
                banner_margin=self.banner_margin.get(),
                log=self.log,
            )
            self.log(f"✅ Готово: {final_path}")
            self.root.after(0, lambda: messagebox.showinfo("Готово", f"Сохранено:\n{final_path}"))
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
            self.log(f"❌ ffmpeg ошибка:\n{err[-500:]}")
            self.root.after(0, lambda: messagebox.showerror("Ошибка ffmpeg", err[-500:]))
        except Exception as e:
            self.log(f"❌ Ошибка: {e}")
            self.root.after(0, lambda: messagebox.showerror("Ошибка", str(e)))
        finally:
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)
            self.root.after(0, lambda: self.go_btn.configure(state="normal", text="🚀 Обработать"))

    def _load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_config(self):
        cfg = {
            "output_dir":     self.output_dir.get(),
            "cookies_path":   self.cookies_path.get(),
            "banner_path":    self.banner_path.get(),
            "hflip":          self.hflip_var.get(),
            "banner_on":      self.banner_on.get(),
            "banner_pos":     self.banner_pos.get(),
            "banner_width":   self.banner_width.get(),
            "banner_opacity": float(self.banner_op.get()),
            "banner_margin":  self.banner_margin.get(),
        }
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
