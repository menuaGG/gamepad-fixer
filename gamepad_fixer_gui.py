#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gamepad_fixer_gui.py - Gamepad Fixer pencereli uygulama (menu'lu).

Ozellikler:
  - Baslat (F5) / Durdur (F6) ile sanal kol koprusunu kontrol etme
  - Menuden filtre ayarlari (olu bolge, dairesellik, hiz vb.) - kaydedilir
  - Canli log + durum gostergesi (fiziksel/sanal kol, titreşim)
  - Test Ekrani: canli dugme/tetik paneli, dairesellik testi (Avg Error),
    titresim dugmeleri (1 sn / sonsuz) ve titresim gucu kaydiricisi
  - Pil gostergesi (BT HID), 'Windows ile otomatik baslat' secenegi
  - Koyu/acik tema (Gorunum menusu veya Ctrl+T) - rahat okunur arayuz
  - Araclar > Titresim Testi (A: XInput, B: BT HID, C: uctan uca)

Gizli test modlari:
  --cli [fixer args]   : konsol modunda duzelticiyi calistirir
  --clilog <dosya>     : ciktilari dosyaya yazar (penceresiz exe testi)
  --smoke-gui          : pencereyi acar, ~2.5 sn sonra kapatir
  --start-min          : pencereyi simge durumunda acar + kopruyu baslatir
                         (Windows ile otomatik baslatma bunu kullanir)

EXE uretimi:
  python -m PyInstaller --onefile --windowed --name GamepadFixer
      --collect-all vgamepad --hidden-import hid
      --icon assets/gamepad.ico --add-data "assets;assets" gamepad_fixer_gui.py
"""

import contextlib
import ctypes
import gc
import json
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, scrolledtext, ttk

BASEDIR = os.path.dirname(os.path.abspath(__file__))
if BASEDIR not in sys.path:
    sys.path.insert(0, BASEDIR)

APP_TITLE = "Gamepad Fixer"
APP_VERSION = "1.9"
CREATE_NO_WINDOW = 0x08000000

CONFIG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "GamepadFixer", "config.json")

LOG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "GamepadFixer", "app.log")

DEFAULTS = {
    "deadzone": 0.12,
    "circularity": 1.0,
    "crosstalk": 0.0,
    "smooth": 0.03,
    "rate": 120,
    "slot": -1,
    "invert_y": False,
    "panel_flip_x": False,
    "panel_flip_y": True,
    "no_rumble": False,
    "rumble_scale": 100,
    "theme": "dark",
    "panel_mode": "detayli",
}

# Tema paletleri: "dark" (varsayilan) ve "light" (acik).
# Yumusak tonlar secildi; amac uzun kullanimda gozu yormamak.
THEMES = {
    "dark": {
        "label": "Koyu",
        "bg": "#1c1e23",
        "panel": "#25272e",
        "panel2": "#2b2e36",
        "fg": "#e7e9ee",
        "muted": "#9aa3af",
        "accent": "#5aa2ff",
        "border": "#3b3f49",
        "entry_bg": "#15171c",
        "log_bg": "#14161b",
        "log_fg": "#d3d9e0",
        "sel_bg": "#2e4a72",
        "canvas_bg": "#191b21",
        "grid": "#31353e",
        "axis": "#4a505c",
        "trail": "#7c8492",
        "dot": "#5aa2ff",
        "btn_face": "#2b2e36",
        "btn_fg": "#e7e9ee",
        "btn_press": "#35c15c",
        "btn_press_fg": "#ffffff",
        "ok": "#57d178",
        "err": "#ff7b72",
        "menu_bg": "#25272e",
        "menu_fg": "#e7e9ee",
        "card": "#22242b",
        "side_bg": "#16171c",
        "hover": "#2c2f37",
        "divider": "#33363f",
        "green": "#3ecf6a",
    },
    "light": {
        "label": "Açık",
        "bg": "#f2f4f8",
        "panel": "#ffffff",
        "panel2": "#e9edf2",
        "fg": "#23282f",
        "muted": "#5d6673",
        "accent": "#0b63ce",
        "border": "#d3d9e1",
        "entry_bg": "#ffffff",
        "log_bg": "#ffffff",
        "log_fg": "#2b3138",
        "sel_bg": "#cfe2ff",
        "canvas_bg": "#fbfcfe",
        "grid": "#d9dee5",
        "axis": "#aab2bd",
        "trail": "#98a2ae",
        "dot": "#1f6feb",
        "btn_face": "#e9edf2",
        "btn_fg": "#23282f",
        "btn_press": "#2fbe5e",
        "btn_press_fg": "#ffffff",
        "ok": "#1a7f37",
        "err": "#c62828",
        "menu_bg": "#ffffff",
        "menu_fg": "#23282f",
        "card": "#ffffff",
        "side_bg": "#e9edf3",
        "hover": "#dde3ec",
        "divider": "#d7dce4",
        "green": "#2fbe5e",
    },
}

# Tik (tk) widget'larin tema rolleri: kind -> {widget secenegi: palet anahtari}
THEME_KINDS = {
    "card": {"bg": "card"},
    "card_bord": {"bg": "card", "highlightbackground": "divider"},
    "card_lbl": {"bg": "card", "fg": "fg"},
    "card_muted": {"bg": "card", "fg": "muted"},
    "card_dot": {"bg": "card", "fg": "green"},
    "card_cv": {"bg": "card"},
    "divider_line": {"bg": "divider"},
    "side": {"bg": "side_bg"},
}


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", BASEDIR)
    return os.path.join(base, rel)


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in DEFAULTS:
            if k in data:
                cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except OSError:
        return False


def pid_alive(pid):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW)
        return str(pid) in out.stdout
    except Exception:
        return False


def another_fixer_pid():
    """Baska bir duzeltici canliysa PID'sini dondurur (yoksa None)."""
    p = os.path.join(tempfile.gettempdir(), "gamepad_fixer.pid")
    try:
        with open(p, "r") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if pid == os.getpid():
        return None
    return pid if pid_alive(pid) else None


class LineWriter:
    """print ciktisini satirlara bolup kuyruga koyar."""

    def __init__(self, q):
        self._q = q
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._q.put(line)

    def flush(self):
        if self._buf:
            self._q.put(self._buf)
            self._buf = ""


class FixerApp:
    def __init__(self):
        self.cfg = load_config()
        self._queue = queue.Queue()
        self._thread = None
        self._stop_event = None
        self._running = False
        self._last_exit = 0
        self._rumble_thread = None
        self._test_handles = {}
        self._log_lines = []

        # Test Ekrani durumu (bkz. _build_test_tab / _test_tick)
        self._vslot = None            # sanal kol yuvasi (calisirsa)
        self._pslot = None            # fiziksel kol yuvasi
        self._gf_mod = None
        self._circ = {"on": False,
                      "L": {"buckets": {}, "ver": 0},
                      "R": {"buckets": {}, "ver": 0}}
        self._btn_widgets = {}
        self._btn_last = {}
        self._trig_widgets = {}
        self._trig_last = {}
        self._axis_vars = {}
        self._stick_cvs = {}
        self._stick_items = {}
        self._tick_n = 0
        self._flip_x = bool(self.cfg.get("panel_flip_x", False))
        self._flip_y = bool(self.cfg.get("panel_flip_y", False))
        self._vibinf_on = False
        self._vib_after = None
        self._vib_last = None
        self._tick_err_logged = False
        self._dz_cal = None            # olu bolge testi olcum durumu
        self.btn_dztest = None         # olu bolge testi dugmesi
        self._live_settings = {
            "rumble_scale": int(self.cfg.get("rumble_scale", 100))}
        self._scale_save_job = None
        self._auto_mode = False
        self._batt_next = None
        self._batt_busy = False
        self._batt_t = 0.0
        self._batt_raw_logged = False
        self._autostart_was = self._autostart_enabled()
        self._theme_choice = str(self.cfg.get("theme", "dark")).lower()
        if self._theme_choice not in ("dark", "light", "system"):
            self._theme_choice = "dark"
        self._theme_name = self._resolve_theme(self._theme_choice)
        self._colors = dict(THEMES[self._theme_name])
        self._panel_mode = str(self.cfg.get("panel_mode", "detayli")).lower()
        if self._panel_mode not in ("sade", "detayli"):
            self._panel_mode = "detayli"
        self._menus = []
        self._menu_ds = {}
        self._theme_widgets = []
        self._menubtn_pairs = []
        self._nav = {}
        self._nav_hover_job = {}
        self._pages = {}
        self._page = "panel"
        self._batt_level = None
        self._batt_charging = False

        # Kalici dosya logu: %LOCALAPPDATA%\GamepadFixer\app.log
        # (Sorun teshisi icin calisan uygulamanin ciktisi buraya da yazilir.)
        self._file_log = None
        try:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            self._file_log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
        except OSError:
            self._file_log = None
        self._log_file("---- uygulama acildi (v%s) ----" % APP_VERSION)
        self._log_file("panel X yonu: %s (kalici ayar)"
                       % ("ters" if self._flip_x else "duz"))
        self._log_file("panel Y yonu: %s (kalici ayar)"
                       % ("cevrilmis" if self._flip_y else "duz"))
        self._log_file("otomatik baslatma: %s"
                       % ("acik" if self._autostart_was else "kapali"))
        self._log_file("tema: %s (%s)" % (self._theme_name, self._theme_choice))
        self._log_file("panel duzeni: %s" % self._panel_mode)

        self.root = tk.Tk()
        self.root.title("%s v%s" % (APP_TITLE, APP_VERSION))
        self.root.geometry("1140x720")
        self.root.minsize(1060, 620)
        self.root.configure(bg=self._colors["bg"])
        try:
            self.root.iconbitmap(resource_path(os.path.join("assets", "gamepad.ico")))
        except Exception:
            pass

        self._setup_styles()
        self._build_menu()
        self._build_widgets()
        self._apply_theme()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_error
        self.root.after(100, self._poll)
        self.root.after(150, self._test_tick)
        self.root.after(5000, self._system_theme_poll)
        self._intro()

    # ------------------------------------------------------------------ arayuz
    def _build_menu(self):
        self._menus = []
        self._menu_ds = {}

        m_app = tk.Menu(self.root, tearoff=0)
        m_app.add_command(label="Başlat", accelerator="F5", command=self._start)
        m_app.add_command(label="Durdur", accelerator="F6", command=self._stop)
        m_app.add_separator()
        m_app.add_command(label="Çıkış", command=self._on_close)
        self._menus.append(m_app)
        self._menu_ds["app"] = m_app

        m_tools = tk.Menu(self.root, tearoff=0)
        m_tools.add_command(label="Titreşim Testi", command=self._rumble_test)
        m_tools.add_command(label="Ölü Bölge Testi",
                            command=self._dz_test_from_menu)
        self._menus.append(m_tools)
        self._menu_ds["tools"] = m_tools

        m_view = tk.Menu(self.root, tearoff=0)
        self._theme_var = tk.StringVar(value=self._theme_choice)
        m_theme = tk.Menu(m_view, tearoff=0)
        m_theme.add_radiobutton(label="☾  Koyu Tema (Varsayılan)",
                                variable=self._theme_var, value="dark",
                                command=lambda: self._set_theme("dark"))
        m_theme.add_radiobutton(label="☀  Açık Tema",
                                variable=self._theme_var, value="light",
                                command=lambda: self._set_theme("light"))
        m_theme.add_radiobutton(label="▣  Sistem Varsayılanı",
                                variable=self._theme_var, value="system",
                                command=lambda: self._set_theme("system"))
        m_view.add_cascade(label="Tema", menu=m_theme)
        self._menus.append(m_theme)

        m_panel = tk.Menu(m_view, tearoff=0)
        self._panel_var = tk.StringVar(value=self._panel_mode)
        m_panel.add_radiobutton(label="Sade", variable=self._panel_var,
                                value="sade",
                                command=lambda: self._set_panel_mode("sade"))
        m_panel.add_radiobutton(label="Detaylı", variable=self._panel_var,
                                value="detayli",
                                command=lambda: self._set_panel_mode("detayli"))
        m_view.add_cascade(label="Panel Düzeni", menu=m_panel)
        self._menus.append(m_panel)

        m_view.add_separator()
        m_view.add_command(label="Temayı değiştir (Ctrl+T)",
                           command=self._theme_toggle)
        self._menus.append(m_view)
        self._menu_ds["view"] = m_view

        m_set = tk.Menu(self.root, tearoff=0)
        m_set.add_command(label="Filtre Ayarları...", command=self._open_settings)
        m_set.add_command(label="Varsayılanlara Dön", command=self._reset_settings)
        self._menus.append(m_set)
        self._menu_ds["set"] = m_set

        m_help = tk.Menu(self.root, tearoff=0)
        m_help.add_command(label="Nasıl Kullanılır?", command=self._help)
        m_help.add_command(label="Hakkında", command=self._about)
        self._menus.append(m_help)
        self._menu_ds["help"] = m_help

    def _build_widgets(self):
        c = self._colors
        self._theme_widgets = []
        self._menubtn_pairs = []

        # --- ust serit: kart gorunumlu menu dugmeleri ---
        topbar = tk.Frame(self.root, bg=c["bg"])
        topbar.pack(fill="x", padx=10, pady=(8, 0))

        def menubtn(text, menu):
            f = tk.Frame(topbar, bg=c["card"], cursor="hand2",
                         highlightthickness=1, highlightbackground=c["divider"])
            lbl = tk.Label(f, text=text, bg=c["card"], fg=c["fg"],
                           font=("Segoe UI", 10), padx=12, pady=6,
                           cursor="hand2")
            lbl.pack()
            self._menubtn_pairs.append((f, lbl))

            def enter(_e, f=f, lbl=lbl):
                f.configure(bg=self._colors["hover"])
                lbl.configure(bg=self._colors["hover"])

            def leave(_e, f=f, lbl=lbl):
                f.configure(bg=self._colors["card"])
                lbl.configure(bg=self._colors["card"])

            for w in (f, lbl):
                w.bind("<Button-1>",
                       lambda e, m=menu, ww=f: self._popup_menu(m, ww))
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
            return f

        menubtn("✎  Düzeltici", self._menu_ds["app"]).pack(side="left", padx=(0, 6))
        menubtn("⚒  Araçlar", self._menu_ds["tools"]).pack(side="left", padx=(0, 6))
        menubtn("◐  Görünüm", self._menu_ds["view"]).pack(side="left", padx=(0, 6))
        menubtn("⚙  Ayarlar", self._menu_ds["set"]).pack(side="left", padx=(0, 6))
        menubtn("❓  Yardım", self._menu_ds["help"]).pack(side="left")

        # --- aksiyon seridi: Baslat / Durdur + otomatik baslat ---
        act = tk.Frame(self.root, bg=c["bg"])
        act.pack(fill="x", padx=10, pady=(8, 0))
        self.btn_start = ttk.Button(act, text="▶  Başlat (F5)",
                                    style="Accent.TButton", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(act, text="■  Durdur (F6)", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        self._autostart_var = tk.BooleanVar(value=self._autostart_was)
        ttk.Checkbutton(act, text="Windows ile otomatik başlat",
                        variable=self._autostart_var,
                        command=self._autostart_toggle).pack(side="right")

        # --- durum degiskenleri ---
        self.var_status = tk.StringVar(value="Durum: Hazır")
        self.var_phys = tk.StringVar(value="Fiziksel kol: -")
        self.var_virt = tk.StringVar(value="Sanal kol: -")
        self.var_rumble = tk.StringVar(value="Titreşim: -")
        self.var_batt = tk.StringVar(value="Pil: -")
        self.var_live = tk.StringVar(value="Canlı: -")
        self.var_phys_s = tk.StringVar(value="-")
        self.var_virt_s = tk.StringVar(value="-")
        self.var_rumble_s = tk.StringVar(value="-")
        self.var_batt_s = tk.StringVar(value="-")
        self._mirror(self.var_phys, self.var_phys_s, "Fiziksel kol: ")
        self._mirror(self.var_virt, self.var_virt_s, "Sanal kol: ")
        self._mirror(self.var_rumble, self.var_rumble_s, "Titreşim: ")
        self._mirror(self.var_batt, self.var_batt_s, "Pil: ")

        # --- durum kartlari (Panel Duzeni: Sade iken gizlenir) ---
        self._stats_row = tk.Frame(self.root, bg=c["card"], highlightthickness=1,
                                   highlightbackground=c["divider"])
        self._stats_row.pack(fill="x", padx=10, pady=(8, 0))
        self._reg(self._stats_row, "card_bord")
        for col in (0, 2, 4, 6, 8):
            self._stats_row.grid_columnconfigure(col, weight=1)
        self._vib_bars_cvs = []
        self._batt_cvs = []

        def stat_card(title, col):
            box = tk.Frame(self._stats_row, bg=c["card"])
            box.grid(row=0, column=col, sticky="nsew", padx=12, pady=7)
            tl = tk.Label(box, text=title, bg=c["card"], fg=c["muted"],
                          font=("Segoe UI", 9))
            tl.pack(anchor="w")
            self._reg(box, "card")
            self._reg(tl, "card_muted")
            row = tk.Frame(box, bg=c["card"])
            row.pack(anchor="w", pady=(3, 0))
            self._reg(row, "card")
            return row

        def vdiv(col):
            d = tk.Frame(self._stats_row, width=1, bg=c["divider"])
            d.grid(row=0, column=col, sticky="ns", pady=6)
            self._reg(d, "divider_line")

        r = stat_card("Durum", 0)
        dot = tk.Label(r, text="●", bg=c["card"], fg=c["green"],
                       font=("Segoe UI", 10))
        dot.pack(side="left")
        self._reg(dot, "card_dot")
        lbl = tk.Label(r, textvariable=self.var_status, bg=c["card"], fg=c["fg"],
                       font=("Segoe UI", 10, "bold"))
        lbl.pack(side="left", padx=(5, 0))
        self._reg(lbl, "card_lbl")
        vdiv(1)

        r = stat_card("Fiziksel Kol ID", 2)
        lbl = tk.Label(r, textvariable=self.var_phys_s, bg=c["card"], fg=c["fg"],
                       font=("Segoe UI", 10))
        lbl.pack(side="left")
        self._reg(lbl, "card_lbl")
        vdiv(3)

        r = stat_card("Sanal Kol ID", 4)
        lbl = tk.Label(r, textvariable=self.var_virt_s, bg=c["card"], fg=c["fg"],
                       font=("Segoe UI", 10))
        lbl.pack(side="left")
        self._reg(lbl, "card_lbl")
        vdiv(5)

        r = stat_card("Titreşim", 6)
        cv = tk.Canvas(r, width=54, height=18, bg=c["card"],
                       highlightthickness=0)
        cv.pack(side="left")
        self._reg(cv, "card_cv")
        self._vib_bars_cvs.append(cv)
        lbl = tk.Label(r, textvariable=self.var_rumble_s, bg=c["card"],
                       fg=c["muted"], font=("Segoe UI", 9))
        lbl.pack(side="left", padx=(7, 0))
        self._reg(lbl, "card_muted")
        vdiv(7)

        r = stat_card("Pil Durumu", 8)
        cv = tk.Canvas(r, width=42, height=19, bg=c["card"],
                       highlightthickness=0)
        cv.pack(side="left")
        self._reg(cv, "card_cv")
        self._batt_cvs.append(cv)
        lbl = tk.Label(r, textvariable=self.var_batt_s, bg=c["card"],
                       fg=c["muted"], font=("Segoe UI", 9))
        lbl.pack(side="left", padx=(7, 0))
        self._reg(lbl, "card_muted")

        # --- govde: sol menu + sayfalar ---
        body = tk.Frame(self.root, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=10, pady=(8, 0))
        self._body = body

        side = tk.Frame(body, bg=c["side_bg"], width=205)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self._reg(side, "side")

        self._nav = {}
        for key, icon, text in (("panel", "☰", "Kontrol Paneli"),
                                ("test", "◎", "Kalibrasyon ve Test"),
                                ("settings", "⚙", "Gelişmiş Ayarlar")):
            item = tk.Frame(side, bg=c["side_bg"], cursor="hand2")
            item.pack(fill="x", padx=8, pady=(8 if key == "panel" else 2, 0))
            strip = tk.Frame(item, width=3, bg=c["side_bg"])
            strip.pack(side="left", fill="y")
            ic = tk.Label(item, text=icon, bg=c["side_bg"], fg=c["muted"],
                          font=("Segoe UI", 11), padx=8, pady=7)
            ic.pack(side="left")
            tx = tk.Label(item, text=text, bg=c["side_bg"], fg=c["fg"],
                          font=("Segoe UI", 10), anchor="w")
            tx.pack(side="left", fill="x", expand=True)
            self._nav[key] = {"frame": item, "strip": strip,
                              "icon": ic, "text": tx}
            for w in (item, ic, tx, strip):
                w.bind("<Button-1>", lambda e, k=key: self._show_page(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))

        self._pages = {}
        for key in ("panel", "test", "settings"):
            fr = tk.Frame(body, bg=c["bg"])
            self._pages[key] = fr

        # sayfa: Kontrol Paneli (gunluk)
        pf = self._pages["panel"]
        ttk.Label(pf, text="Uygulama Günlüğü",
                  style="Muted.TLabel").pack(anchor="e")
        self.log = scrolledtext.ScrolledText(
            pf, wrap="none", font=("Consolas", 9), state="disabled",
            relief="flat", bd=0, background=c["log_bg"],
            foreground=c["log_fg"])
        self.log.pack(fill="both", expand=True, pady=(4, 0))

        # sayfa: Kalibrasyon ve Test
        tf = self._pages["test"]
        ttk.Label(tf, text="Kalibrasyon ve Test",
                  style="Muted.TLabel").pack(anchor="e")
        self._build_test_tab(tf)

        # sayfa: Gelismis Ayarlar
        sf = self._pages["settings"]
        self._build_settings_page(sf)

        # --- alt durum cubugu ---
        bottom = tk.Frame(self.root, bg=c["card"], highlightthickness=1,
                          highlightbackground=c["divider"])
        bottom.pack(fill="x", padx=10, pady=(8, 0))
        self._reg(bottom, "card_bord")
        bi = tk.Frame(bottom, bg=c["card"])
        bi.pack(fill="x", padx=12, pady=6)
        self._reg(bi, "card")

        dot = tk.Label(bi, text="●", bg=c["card"], fg=c["green"],
                       font=("Segoe UI", 10))
        dot.pack(side="left")
        self._reg(dot, "card_dot")
        l1 = tk.Label(bi, textvariable=self.var_status, bg=c["card"], fg=c["fg"],
                      font=("Segoe UI", 9, "bold"))
        l1.pack(side="left", padx=(4, 16))
        self._reg(l1, "card_lbl")

        t1 = tk.Label(bi, text="Fiziksel Kol ID", bg=c["card"], fg=c["muted"],
                      font=("Segoe UI", 9))
        t1.pack(side="left")
        self._reg(t1, "card_muted")
        v1 = tk.Label(bi, textvariable=self.var_phys_s, bg=c["card"], fg=c["fg"],
                      font=("Segoe UI", 9))
        v1.pack(side="left", padx=(4, 16))
        self._reg(v1, "card_lbl")

        t2 = tk.Label(bi, text="Sanal Kol ID", bg=c["card"], fg=c["muted"],
                      font=("Segoe UI", 9))
        t2.pack(side="left")
        self._reg(t2, "card_muted")
        v2 = tk.Label(bi, textvariable=self.var_virt_s, bg=c["card"], fg=c["fg"],
                      font=("Segoe UI", 9))
        v2.pack(side="left", padx=(4, 16))
        self._reg(v2, "card_lbl")

        dv = tk.Frame(bi, width=1, bg=c["divider"])
        dv.pack(side="left", fill="y", padx=2, pady=1)
        self._reg(dv, "divider_line")

        t3 = tk.Label(bi, text="Titreşim", bg=c["card"], fg=c["muted"],
                      font=("Segoe UI", 9))
        t3.pack(side="left", padx=(12, 0))
        self._reg(t3, "card_muted")
        cv = tk.Canvas(bi, width=54, height=18, bg=c["card"],
                       highlightthickness=0)
        cv.pack(side="left", padx=(6, 16))
        self._reg(cv, "card_cv")
        self._vib_bars_cvs.append(cv)

        t4 = tk.Label(bi, text="Pil Durumu", bg=c["card"], fg=c["muted"],
                      font=("Segoe UI", 9))
        t4.pack(side="left")
        self._reg(t4, "card_muted")
        cv = tk.Canvas(bi, width=42, height=19, bg=c["card"],
                       highlightthickness=0)
        cv.pack(side="left", padx=(6, 0))
        self._reg(cv, "card_cv")
        self._batt_cvs.append(cv)

        # --- ipucu satiri ---
        ttk.Label(self.root, style="Muted.TLabel",
                  text="İpucu: Kolların canlı değerleri 'Kalibrasyon ve Test' "
                       "sayfasında; koyu/açık tema 'Görünüm' menüsünde."
                  ).pack(fill="x", padx=12, pady=(6, 8))

        self._show_page(self._page)
        self._apply_panel_mode()

    # ------------------------------------------------------------- yerlesim
    def _reg(self, widget, kind):
        self._theme_widgets.append((widget, kind))

    def _mirror(self, src, dst, prefix):
        def upd(*_a):
            t = src.get()
            dst.set(t[len(prefix):] if t.startswith(prefix) else t)
        src.trace_add("write", upd)
        upd()

    def _popup_menu(self, menu, widget):
        try:
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _show_page(self, name):
        if name not in self._pages:
            return
        self._page = name
        for k, f in self._pages.items():
            if k == name:
                f.pack(fill="both", expand=True, padx=(10, 0))
            else:
                f.pack_forget()
        self._style_nav()

    def _style_nav(self):
        c = self._colors
        for key, it in self._nav.items():
            active = (key == self._page)
            bg = c["sel_bg"] if active else c["side_bg"]
            it["frame"].configure(bg=bg)
            it["icon"].configure(bg=bg,
                                 fg=c["accent"] if active else c["muted"])
            it["text"].configure(bg=bg, fg=c["accent"] if active else c["fg"])
            it["strip"].configure(bg=c["accent"] if active else bg)

    def _nav_hover(self, key, on):
        if key == self._page:
            return
        job = self._nav_hover_job.get(key)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._nav_hover_job[key] = None
        if on:
            self._nav_bg(key, "hover")
        else:
            self._nav_hover_job[key] = self.root.after(
                40, lambda k=key: self._nav_bg(k, "normal"))

    def _nav_bg(self, key, mode):
        it = self._nav.get(key)
        if it is None or key == self._page:
            return
        bg = self._colors["hover"] if mode == "hover" else self._colors["side_bg"]
        it["frame"].configure(bg=bg)
        it["icon"].configure(bg=bg)
        it["text"].configure(bg=bg)
        it["strip"].configure(bg=bg)

    def _set_panel_mode(self, mode):
        mode = str(mode).lower()
        if mode not in ("sade", "detayli"):
            return
        self._panel_mode = mode
        self.cfg["panel_mode"] = mode
        save_config(self.cfg)
        try:
            self._panel_var.set(mode)
        except Exception:
            pass
        self._apply_panel_mode()
        self._log("Panel düzeni: %s."
                  % ("Detaylı" if mode == "detayli" else "Sade"))
        self._log_file("---- panel duzeni: %s ----" % mode)

    def _apply_panel_mode(self):
        try:
            if self._panel_mode == "detayli":
                self._stats_row.pack(fill="x", padx=10, pady=(8, 0),
                                     before=self._body)
            else:
                self._stats_row.pack_forget()
        except Exception:
            pass

    def _bind_keys(self):
        self.root.bind("<F5>", lambda e: self._start())
        self.root.bind("<F6>", lambda e: self._stop())
        self.root.bind("<Control-t>", lambda e: self._theme_toggle())

    # ------------------------------------------------------------------ tema
    def _recolor_menu(self, m):
        c = self._colors
        try:
            m.configure(bg=c["menu_bg"], fg=c["menu_fg"],
                        activebackground=c["accent"],
                        activeforeground="#ffffff",
                        selectcolor=c["menu_bg"], bd=0, relief="flat")
        except tk.TclError:
            pass

    def _setup_styles(self):
        c = self._colors
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=c["bg"], foreground=c["fg"],
                        fieldbackground=c["entry_bg"],
                        bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"],
                        troughcolor=c["entry_bg"], focuscolor=c["bg"],
                        font=("Segoe UI", 10))
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("Head.TLabel", background=c["bg"], foreground=c["fg"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", background=c["bg"],
                        foreground=c["muted"])
        style.configure("TCheckbutton", background=c["bg"], foreground=c["fg"],
                        focuscolor=c["bg"], indicatorcolor=c["entry_bg"])
        style.map("TCheckbutton",
                  background=[("active", c["bg"])],
                  foreground=[("disabled", c["muted"])],
                  indicatorcolor=[("selected", c["accent"]),
                                  ("!selected", c["entry_bg"])])
        style.configure("TButton", background=c["panel2"], foreground=c["fg"],
                        bordercolor=c["border"], lightcolor=c["panel2"],
                        darkcolor=c["panel2"], focuscolor=c["bg"],
                        padding=(12, 5), relief="flat")
        style.map("TButton",
                  background=[("pressed", c["sel_bg"]),
                              ("active", c["sel_bg"]),
                              ("disabled", c["bg"])],
                  foreground=[("disabled", c["muted"])])
        style.configure("Accent.TButton", background=c["accent"],
                        foreground="#ffffff", bordercolor=c["accent"],
                        lightcolor=c["accent"], darkcolor=c["accent"],
                        focuscolor=c["accent"], padding=(12, 5))
        style.map("Accent.TButton",
                  background=[("pressed", c["accent"]),
                              ("active", c["accent"]),
                              ("disabled", c["bg"])],
                  foreground=[("disabled", c["muted"])])
        style.configure("TNotebook", background=c["bg"],
                        bordercolor=c["border"],
                        lightcolor=c["bg"], darkcolor=c["bg"],
                        tabmargins=(2, 6, 2, 0))
        style.configure("TNotebook.Tab", background=c["bg"],
                        foreground=c["muted"], padding=(16, 7),
                        bordercolor=c["border"],
                        lightcolor=c["bg"], darkcolor=c["bg"])
        style.map("TNotebook.Tab",
                  background=[("selected", c["panel"])],
                  foreground=[("selected", c["accent"])],
                  lightcolor=[("selected", c["panel"])],
                  darkcolor=[("selected", c["panel"])])
        style.configure("TLabelframe", background=c["bg"],
                        bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"])
        style.configure("TLabelframe.Label", background=c["bg"],
                        foreground=c["muted"], font=("Segoe UI", 9, "bold"))
        style.configure("TProgressbar", background=c["accent"],
                        troughcolor=c["entry_bg"], bordercolor=c["entry_bg"],
                        lightcolor=c["accent"], darkcolor=c["accent"],
                        thickness=10)
        style.configure("TEntry", fieldbackground=c["entry_bg"],
                        foreground=c["fg"], bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"],
                        insertcolor=c["fg"], padding=3)
        style.configure("TCombobox", fieldbackground=c["entry_bg"],
                        background=c["panel2"], foreground=c["fg"],
                        arrowcolor=c["fg"], bordercolor=c["border"],
                        lightcolor=c["border"], darkcolor=c["border"],
                        padding=3)
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["entry_bg"])],
                  foreground=[("readonly", c["fg"])],
                  selectbackground=[("readonly", c["entry_bg"])],
                  selectforeground=[("readonly", c["fg"])])

    def _apply_theme(self):
        c = self._colors
        try:
            self.root.configure(bg=c["bg"])
        except tk.TclError:
            return
        self._apply_dark_titlebar()
        self.root.option_add("*TCombobox*Listbox*Background", c["entry_bg"])
        self.root.option_add("*TCombobox*Listbox*Foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox*selectBackground", c["sel_bg"])
        self.root.option_add("*TCombobox*Listbox*selectForeground", c["fg"])
        self._setup_styles()
        for m in self._menus:
            self._recolor_menu(m)
        # kayitli tk widget'lar (kartlar, kenar menu, alt cubuk...)
        for w, kind in getattr(self, "_theme_widgets", []):
            opts = THEME_KINDS.get(kind)
            if not opts:
                continue
            try:
                w.configure(**{k: c[v] for k, v in opts.items()})
            except tk.TclError:
                pass
        for f, lbl in getattr(self, "_menubtn_pairs", []):
            try:
                f.configure(bg=c["card"])
                lbl.configure(bg=c["card"], fg=c["fg"])
            except tk.TclError:
                pass
        try:
            self.log.configure(background=c["log_bg"], foreground=c["log_fg"],
                               insertbackground=c["log_fg"],
                               selectbackground=c["sel_bg"])
            self.log.tag_config("err", foreground=c["err"])
            self.log.tag_config("ok", foreground=c["ok"])
            self.log.vbar.configure(bg=c["panel2"], troughcolor=c["bg"],
                                    activebackground=c["accent"],
                                    bd=0, relief="flat",
                                    highlightbackground=c["bg"])
        except (tk.TclError, AttributeError):
            pass
        # Cubuk daireleri: renkler bir sonraki karede yeniden cizilir.
        for side, cv in self._stick_cvs.items():
            try:
                cv.configure(bg=c["canvas_bg"])
                cv.delete("all")
            except tk.TclError:
                pass
            self._stick_items[side] = None
        # Dugme lambalari
        for lbl in self._btn_widgets.values():
            try:
                lbl.configure(bg=c["btn_face"], fg=c["btn_fg"])
            except tk.TclError:
                pass
        self._btn_last = {}
        # Titresim gucu kaydiricisi (tk.Scale)
        try:
            self._vib_scale.configure(
                bg=c["bg"], fg=c["fg"], troughcolor=c["entry_bg"],
                activebackground=c["accent"], highlightbackground=c["bg"],
                highlightthickness=0, bd=0, relief="flat",
                sliderrelief="flat")
        except tk.TclError:
            pass
        self._style_nav()
        self._draw_vib_bars()
        self._draw_batt_icon()

    def _resolve_theme(self, choice):
        """'system' secilirse Windows uygulama temasina bakilir."""
        if choice == "system":
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Themes\Personalize",
                        0, winreg.KEY_READ) as k:
                    val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
                return "light" if int(val) == 1 else "dark"
            except OSError:
                return "dark"
        return choice if choice in THEMES else "dark"

    def _system_theme_poll(self):
        try:
            if self._theme_choice == "system":
                name = self._resolve_theme("system")
                if name != self._theme_name:
                    self._theme_name = name
                    self._colors = dict(THEMES[name])
                    self._apply_theme()
                    self._log("Tema (sistem değişti): %s."
                              % THEMES[name]["label"])
                    self._log_file("---- tema (sistem): %s ----" % name)
        finally:
            self.root.after(5000, self._system_theme_poll)

    def _apply_dark_titlebar(self):
        """Windows baslik cubugunu temaya uydurur (koyu/acik)."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            val = ctypes.c_int(1 if self._theme_name == "dark" else 0)
            for attr in (20, 19):
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
                    break
                except Exception:
                    continue
        except Exception:
            pass

    def _draw_vib_bars(self):
        """Titreşim gucu gostergesi: 6 cubuk, guc oraninca yesil yanar."""
        c = self._colors
        pct = int(self._live_settings.get("rumble_scale", 100))
        n_on = int(round(max(0, min(150, pct)) / 150.0 * 6))
        heights = (6, 8, 10, 12, 14, 18)
        for cv in getattr(self, "_vib_bars_cvs", []):
            try:
                cv.delete("all")
                for i, hgt in enumerate(heights):
                    x0 = i * 9
                    col = c["green"] if i < n_on else c["divider"]
                    cv.create_rectangle(x0, 18 - hgt, x0 + 6, 18,
                                        fill=col, outline=col)
            except tk.TclError:
                pass

    def _draw_batt_icon(self):
        """Pil ikonu: seviye biliniyorsa yesil dolar (sarj: mavi)."""
        c = self._colors
        lvl = self._batt_level
        if lvl is not None and 0 <= lvl <= 3:
            frac = (0.15, 0.4, 0.65, 0.95)[lvl]
        else:
            frac = 0.0
        for cv in getattr(self, "_batt_cvs", []):
            try:
                cv.delete("all")
                cv.create_rectangle(1, 3, 37, 16, outline=c["muted"], width=2)
                cv.create_rectangle(37, 7, 41, 12,
                                    outline=c["muted"], fill=c["muted"])
                if frac > 0:
                    w = int(round(32 * frac))
                    col = c["accent"] if self._batt_charging else c["green"]
                    cv.create_rectangle(4, 6, 4 + w, 13,
                                        fill=col, outline=col)
            except tk.TclError:
                pass

    def _set_theme(self, choice):
        choice = str(choice).lower()
        if choice not in ("dark", "light", "system"):
            return
        self._theme_choice = choice
        self._theme_name = self._resolve_theme(choice)
        self._colors = dict(THEMES[self._theme_name])
        self.cfg["theme"] = choice
        save_config(self.cfg)
        self._apply_theme()
        try:
            self._theme_var.set(choice)
        except Exception:
            pass
        self._log("Tema: %s%s."
                  % (THEMES[self._theme_name]["label"],
                     " (sistem)" if choice == "system" else ""))
        self._log_file("---- tema: %s ----" % choice)

    def _theme_toggle(self):
        self._set_theme("light" if self._theme_name == "dark" else "dark")

    def _intro(self):
        self._log("Gamepad Fixer v%s hazır." % APP_VERSION)
        self._log("1) Kolu aç (Xbox düğmesi).")
        self._log("2) 'Başlat'a bas; kolu SERBEST bırak (merkez kalibrasyonu ~1.3 sn).")
        self._log("3) Oyun/sitede ÜRÜN 028e olan 'Xbox 360 Controller'ı seç (fiziksel = 02e0).")
        self._log("4) İlk kolun SANAL olmasını istersen: kolu kapat -> Başlat ->")
        self._log("   'Fiziksel kol bekleniyor...' yazınca kolu aç. Sanal kol 1. sıraya geçer.")
        self._log("5) 'Kalibrasyon ve Test' sayfası: canlı düğme/tetik paneli,")
        self._log("   kollar (dairesellik + Avg Error), titreşim düğmeleri ve gücü.")
        self._log("   'Ölü Bölge Testi' düğmesi: kolları serbest bırak; 3 sn")
        self._log("   ölçüp ölü bölge önerisi sunar (onaylarsan uygular).")
        self._log("6) Üstte pil göstergesi; 'Windows ile otomatik başlat' kutusu ile")
        self._log("   bilgisayar açılınca uygulama kendiliğinden başlar (simge durumunda).")
        self._log("7) 'Görünüm' menüsü (Ctrl+T): koyu / açık / sistem teması;")
        self._log("   Panel Düzeni: Sade / Detaylı. Sol taraftan sayfa seçilir.")
        self._log("")

    # ------------------------------------------------------------------ log
    def _log(self, line):
        self._log_lines.append(line)
        if len(self._log_lines) > 3000:
            del self._log_lines[:1000]
        tag = None
        if any(k in line for k in ("HATA", "UYARI", "Traceback",
                                   "BEKLENMEYEN", "[tk-hata]")):
            tag = "err"
        elif ("hazır." in line or "SANAL KOL AKTIF" in line):
            tag = "ok"
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", line + "\n", tag)
        else:
            self.log.insert("end", line + "\n")
        try:
            end_line = int(self.log.index("end-1c").split(".")[0])
            if end_line > 2500:
                self.log.delete("1.0", "600.0")
        except Exception:
            pass
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_file(self, line):
        """Satiri kalici app.log dosyasina da yazar (sessizce gec)."""
        if self._file_log is None:
            return
        try:
            self._file_log.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), line))
        except (OSError, ValueError):
            self._file_log = None

    def _poll(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self._on_fixer_done()
                else:
                    self._handle_line(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle_line(self, line):
        self._log_file(line)
        self._log(line)

        m = re.match(r"Sanal kol yuvasi: (\d+)", line)
        if m:
            self._vslot = int(m.group(1))
            self.var_virt.set("Sanal kol: yuva %s ✓" % m.group(1))
        m = re.match(r"Fiziksel kol: yuva (\d+)", line)
        if m:
            self._pslot = int(m.group(1))
            self.var_phys.set("Fiziksel kol: yuva %s" % m.group(1))
        if "Fiziksel kol bekleniyor" in line:
            self.var_status.set("Durum: Fiziksel kol bekleniyor...")
        if "Fiziksel kol ayrildi" in line:
            self._pslot = None
            self.var_phys.set("Fiziksel kol: ayrıldı, bekleniyor...")
            self.var_batt.set("Pil: -")
        m = re.match(r"Yeniden baglandi: yuva (\d+)", line)
        if m:
            self.var_phys.set("Fiziksel kol: yuva %s" % m.group(1))
        if "SANAL KOL AKTIF" in line:
            self.var_status.set("Durum: Çalışıyor ✓")
        if line.startswith("Titresim: XInput + Bluetooth HID"):
            self.var_rumble.set("Titreşim: açık (XInput + BT HID)")
        elif line.startswith("Titresim: XInput aktarimi"):
            self.var_rumble.set("Titreşim: açık (XInput)")
        elif line.startswith("Titresim aktarimi: KAPALI"):
            self.var_rumble.set("Titreşim: kapalı")
        m = re.search(r"L ham=\(([^)]+)\) -> duz=\(([^)]+)\).*R ham=\(([^)]+)\) -> duz=\(([^)]+)\)", line)
        if m:
            self.var_live.set("Canlı (düzeltilmiş)   Sol: %s   Sağ: %s"
                              % (m.group(2), m.group(4)))
        if "Durdurma istegi" in line:
            self.var_status.set("Durum: Durduruluyor...")
        if "Gorusuruz" in line:
            self.var_status.set("Durum: Durduruldu")
            self.var_live.set("Canlı: -")

    # ------------------------------------------------------------------ baslat/durdur
    def _build_argv(self):
        c = self.cfg
        argv = [
            "--deadzone", "%g" % float(c["deadzone"]),
            "--circularity", "%g" % float(c["circularity"]),
            "--crosstalk", "%g" % float(c["crosstalk"]),
            "--smooth", "%g" % float(c["smooth"]),
            "--rate", str(int(c["rate"])),
            "--rumble-scale",
            str(int(self._live_settings.get("rumble_scale", 100))),
        ]
        if int(c["slot"]) >= 0:
            argv += ["--slot", str(int(c["slot"]))]
        if c["invert_y"]:
            argv += ["--invert-y"]
        if c["no_rumble"]:
            argv += ["--no-rumble"]
        return argv

    def _start(self, auto=False):
        if self._running:
            return
        pid = another_fixer_pid()
        if pid and auto:
            self._log_file("(otomatik baslatma) baska duzeltici PID %d "
                           "calisiyor; yine de baslatiliyor" % pid)
        if pid and not auto:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Başka bir düzeltici çalışıyor görünüyor (PID %d).\n\n"
                    "İki düzeltici birlikte iki sanal kol oluşturur; karışıklık "
                    "olabilir.\nYine de başlatılsın mı?" % pid):
                return

        argv = self._build_argv()
        gc.collect()
        self._stop_event = threading.Event()
        self._running = True
        self._last_exit = 0

        self.var_status.set("Durum: Başlatılıyor...")
        self.var_phys.set("Fiziksel kol: -")
        self.var_virt.set("Sanal kol: -")
        self.var_rumble.set("Titreşim: -")
        self.var_live.set("Canlı: -")
        self._log("-" * 66)
        self._log("Başlatılıyor: %s" % time.strftime("%H:%M:%S"))
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self._thread = threading.Thread(target=self._worker, args=(argv,), daemon=True)
        self._thread.start()

    def _worker(self, argv):
        writer = LineWriter(self._queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                import gamepad_fixer
                gamepad_fixer.main(argv=argv, stop_event=self._stop_event,
                                   live=self._live_settings)
        except SystemExit as ex:
            code = ex.code if ex.code is not None else 0
            self._last_exit = code if isinstance(code, int) else 1
            if self._last_exit:
                writer.write("[uygulama] Düzeltici başlatılamadı (çıkış kodu: %s)."
                             % self._last_exit)
        except Exception:
            self._last_exit = 1
            writer.write("[uygulama] BEKLENMEYEN HATA:")
            writer.write(traceback.format_exc())
        finally:
            writer.flush()
            gc.collect()
            self._queue.put(None)

    def _stop(self):
        if not self._running or self._stop_event is None:
            return
        self._stop_event.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Durum: Durduruluyor...")

    def _on_fixer_done(self):
        self._running = False
        self._vslot = None
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if self._last_exit:
            self.var_status.set("Durum: Hata! (günlüğe bak)")
            if self._auto_mode:
                self._log("(otomatik başlatma) Düzeltici başlatılamadı; "
                          "ayrıntı: günlük dosyası.")
            else:
                messagebox.showerror(
                    APP_TITLE,
                    "Düzeltici başlatılamadı.\n\n"
                    "Olası nedenler:\n"
                    "  - ViGEmBus servisi durmuş olabilir\n"
                    "  - vgamepad/sürücü sorunu\n\n"
                    "Ayrıntı: günlük penceresi.")
        elif not self.var_status.get().startswith("Durum: Durduruldu"):
            self.var_status.set("Durum: Durduruldu")

    # ------------------------------------------------------------------ test ekrani
    def _build_test_tab(self, parent):
        """Sitedeki test panosunun uygulama ici esdegeri: canli dugme
        degerleri, iki cubuk dairesi (dairesellik testi + Avg Error) ve
        titresim dugmeleri."""
        self.var_test_info = tk.StringVar(value="İzlenen kol: -")
        ttk.Label(parent, textvariable=self.var_test_info,
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(parent, textvariable=self.var_live,
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        mid = ttk.Frame(parent)
        mid.pack(fill="both", expand=True, pady=(8, 0))

        # Sol: canli dugme paneli (basinca yesil yanar) + LT/RT cubuklari
        bbox = ttk.LabelFrame(mid, text="Düğmeler / Tetikler", padding=6)
        bbox.pack(side="left", anchor="n")

        def mkbtn(name, r, c, colspan=1, width=4):
            lbl = tk.Label(bbox, text=name, width=width, relief="ridge", bd=1,
                           font=("Segoe UI", 9, "bold"),
                           bg=self._colors["btn_face"],
                           fg=self._colors["btn_fg"])
            lbl.grid(row=r, column=c, columnspan=colspan, padx=2, pady=2,
                     sticky="ew")
            self._btn_widgets[name] = lbl

        mkbtn("LB", 0, 0)
        mkbtn("RB", 0, 1)
        for r_i, tname in ((1, "LT"), (2, "RT")):
            ttk.Label(bbox, text=tname, width=3,
                      font=("Segoe UI", 9, "bold")).grid(
                row=r_i, column=0, sticky="e")
            bar = ttk.Progressbar(bbox, length=95, maximum=255)
            bar.grid(row=r_i, column=1, padx=2, pady=2, sticky="ew")
            tvar = tk.StringVar(value="0%")
            ttk.Label(bbox, textvariable=tvar, width=5).grid(
                row=r_i, column=2, sticky="w")
            self._trig_widgets[tname] = (bar, tvar)
        mkbtn("Y", 3, 0)
        mkbtn("X", 3, 1)
        mkbtn("B", 3, 2)
        mkbtn("A", 3, 3)
        mkbtn("Back", 4, 0, colspan=2, width=8)
        mkbtn("Start", 4, 2, colspan=2, width=8)
        mkbtn("L3", 5, 0, colspan=2, width=8)
        mkbtn("R3", 5, 2, colspan=2, width=8)
        mkbtn("↑", 6, 0)
        mkbtn("←", 6, 1)
        mkbtn("↓", 6, 2)
        mkbtn("→", 6, 3)

        # Sag: L/R cubuk daireleri
        sticks = ttk.Frame(mid)
        sticks.pack(side="left", fill="both", expand=True, padx=(10, 0))
        for side, title in (("L", "L Çubuğu"), ("R", "R Çubuğu")):
            box = ttk.LabelFrame(sticks, text=title, padding=4)
            box.pack(side="left", fill="both", expand=True, padx=4)
            cv = tk.Canvas(box, width=170, height=170,
                           bg=self._colors["canvas_bg"],
                           highlightthickness=0)
            cv.pack()
            self._stick_cvs[side] = cv
            avar = tk.StringVar(value="EKSEN: -")
            ttk.Label(box, textvariable=avar,
                      font=("Consolas", 8)).pack(pady=(3, 0))
            self._axis_vars[side] = avar

        # Alt: dairesellik testi + titresim dugmeleri
        bot = ttk.Frame(parent)
        bot.pack(fill="x", pady=(10, 0))
        self.btn_dztest = ttk.Button(bot, text="Ölü Bölge Testi",
                                     command=self._dz_test_start)
        self.btn_dztest.pack(side="left")
        self.btn_circ = ttk.Button(bot, text="Dairesellik Testi",
                                   command=self._toggle_circ)
        self.btn_circ.pack(side="left", padx=(14, 0))
        self._flip_xvar = tk.BooleanVar(value=self._flip_x)
        self.btn_flipx = ttk.Checkbutton(bot, text="⇄ Sağ-sol",
                                         variable=self._flip_xvar,
                                         command=self._flip_toggle)
        self.btn_flipx.pack(side="left", padx=(10, 0))
        self._flip_var = tk.BooleanVar(value=self._flip_y)
        self.btn_flipy = ttk.Checkbutton(bot, text="⇅ Yukarı-aşağı",
                                         variable=self._flip_var,
                                         command=self._flip_toggle)
        self.btn_flipy.pack(side="left", padx=(6, 0))
        ttk.Label(bot, text="Kolları yavaşça çevirin.",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        self.btn_vibinf = ttk.Button(bot, text="Titreşim, sonsuz",
                                     command=self._vib_inf_toggle)
        self.btn_vibinf.pack(side="right")
        self.btn_vib1 = ttk.Button(bot, text="Titreşim, 1 saniye",
                                   command=self._vib_once)
        self.btn_vib1.pack(side="right", padx=(0, 8))

        bot2 = ttk.Frame(parent)
        bot2.pack(fill="x", pady=(6, 0))
        ttk.Label(bot2, text="Titreşim gücü:").pack(side="left")
        self._vib_scale = tk.Scale(
            bot2, from_=0, to=150, resolution=5, orient="horizontal",
            length=210, showvalue=0, bd=0, relief="flat",
            highlightthickness=0, sliderrelief="flat",
            bg=self._colors["bg"], fg=self._colors["fg"],
            troughcolor=self._colors["entry_bg"],
            activebackground=self._colors["accent"])
        self._vib_scale.set(self._live_settings["rumble_scale"])
        self._vib_scale.pack(side="left", padx=(6, 4))
        self.var_vib_scale = tk.StringVar(
            value="%d%%" % self._live_settings["rumble_scale"])
        ttk.Label(bot2, textvariable=self.var_vib_scale, width=5,
                  font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Label(bot2, style="Muted.TLabel",
                  text="(test düğmeleri + oyundan gelen titreşim bu oranla "
                       "çalışır; 150'ye kadar güçlendirme)").pack(
            side="left", padx=(8, 0))
        self._vib_scale.configure(command=self._vib_scale_moved)

    # ------------------------------------------ olu bolge testi (otomatik)
    def _dz_test_from_menu(self):
        """Araçlar menüsünden: test sayfasını açıp ölçümü başlatır."""
        try:
            self._show_page("test")
        except Exception:
            pass
        self._dz_test_start()

    def _dz_physical_slot(self):
        """Ölçüm için fiziksel yuva (sanal kol yuvası asla kullanılmaz)."""
        gf = self._gf_mod
        if gf is None:
            try:
                import gamepad_fixer as gf
                self._gf_mod = gf
            except Exception:
                return None
        ps = self._pslot
        if ps is not None and gf.xinput_read(ps) is not None:
            return ps
        try:
            for s in sorted(gf.connected_slots()):
                if s != self._vslot:
                    return s
        except Exception:
            pass
        return None

    def _dz_test_start(self):
        """Kolları serbest bırakıp 3 sn'lik merkez kayması ölçümü başlatır."""
        if self._dz_cal is not None:
            return
        slot = self._dz_physical_slot()
        if self._gf_mod is None:
            messagebox.showerror(APP_TITLE, "Düzeltici modülü yüklenemedi.")
            return
        if slot is None:
            messagebox.showwarning(
                "Ölü Bölge Testi",
                "Fiziksel kol bulunamadı.\n\n"
                "Kolu açın (Xbox düğmesi) ve tekrar deneyin.")
            return
        if not messagebox.askokcancel(
                "Ölü Bölge Testi",
                "Kolları serbest bırakın (üzerine dokunmayın).\n\n"
                "Tamam'a bastıktan sonra 3 saniye boyunca çubukların\n"
                "merkez kayması ölçülecek; sonunda yazılım bir ölü bölge\n"
                "önerisi sunacak (onaylamadan hiçbir şey uygulanmaz)."):
            return
        self._dz_cal = {"slot": slot, "ends": time.time() + 3.0,
                        "left": [], "right": []}
        self.btn_dztest.configure(text="Ölçülüyor... 3", state="disabled")
        self._log("Ölü bölge testi: kolları serbest bırakın; 3 sn ölçüm "
                  "başladı (fiziksel kol, yuva %d)." % slot)

    def _dz_cal_tick(self):
        """Ölçüm sürerken her karede ham fiziksel değerleri toplar."""
        cal = self._dz_cal
        if cal is None:
            return
        gf = self._gf_mod
        if gf is None:
            return
        st = gf.xinput_read(cal["slot"])
        if st is not None:
            g = st.Gamepad
            cal["left"].append((gf.norm_axis(g.sThumbLX),
                                gf.norm_axis(g.sThumbLY)))
            cal["right"].append((gf.norm_axis(g.sThumbRX),
                                 gf.norm_axis(g.sThumbRY)))
        kalan = int(cal["ends"] - time.time() + 0.999)
        try:
            self.btn_dztest.configure(text="Ölçülüyor... %d" % max(1, kalan))
        except Exception:
            pass
        if time.time() >= cal["ends"]:
            self._dz_cal_finish()

    @staticmethod
    def _dz_stick_stats(samples):
        """Dinlenme örneklerinden (kayma, zarf, titreme) çıkarır. Yok: None.

        kayma : ortalamadan merkeze uzaklık (ham duruş sapması, ör. 0.152)
        raw98 : ham okumaların %98'lik zarfı (en kötü duruş)
        res98 : ortalamadan sapmanın %98'lik zarfı (kalan titreme)
        """
        pts = [(x, y) for (x, y) in samples if math.hypot(x, y) < 0.5]
        if len(pts) < 40:
            return None
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        i98 = min(n - 1, int(round(0.98 * (n - 1))))
        raw98 = sorted(math.hypot(x, y) for x, y in pts)[i98]
        res98 = sorted(math.hypot(x - mx, y - my) for x, y in pts)[i98]
        return {"kayma": math.hypot(mx, my), "raw98": raw98, "res98": res98}

    @staticmethod
    def _dz_suggest(need):
        """Gerekli zarfa güvenlik payı ekler; 2 haneye yuvarlar (0.02-0.5)."""
        v = math.floor((need + 0.01) * 100.0 + 0.5) / 100.0
        return max(0.02, min(0.5, v))

    @staticmethod
    def _dz_fmt(st):
        if st is None:
            return "-"
        return "%.3f (±%.3f)" % (st["kayma"], st["res98"])

    def _dz_cal_finish(self):
        cal = self._dz_cal
        self._dz_cal = None
        try:
            self.btn_dztest.configure(text="Ölü Bölge Testi", state="normal")
        except Exception:
            pass
        sl = self._dz_stick_stats(cal["left"])
        sr = self._dz_stick_stats(cal["right"])
        if sl is None and sr is None:
            messagebox.showwarning(
                "Ölü Bölge Testi",
                "Ölçüm başarısız: çubuklar serbest değildi ya da kol\n"
                "bağlantısı koptu.\n\nKolları bırakıp tekrar deneyin.")
            self._log("Ölü bölge testi başarısız (geçerli örnek yok); "
                      "kollar serbest miydi?")
            return

        def gerekli(st):
            if st is None:
                return 0.0
            # Öneri, ölçülen dinlenme kaymasının tamamını kapatır; merkez
            # düzeltmesi devreye girmese bile kayma oyunda görünmez.
            return st["raw98"]

        need = max(gerekli(sl), gerekli(sr))
        sug = self._dz_suggest(need)
        try:
            cur = float(self.cfg.get("deadzone", 0.12))
        except (TypeError, ValueError):
            cur = 0.12
        self._log("Ölü bölge testi: Sol kayma %s, Sağ kayma %s -> öneri "
                  "%.2f (şu an %.2f)."
                  % (self._dz_fmt(sl), self._dz_fmt(sr), sug, cur))
        self._dz_result_dialog(sl, sr, sug, cur, need)

    def _dz_result_dialog(self, sl, sr, sug, cur, need):
        """Sonuç penceresi: ölçüm dökümü + düzenlenebilir öneri + Uygula."""
        bg = self._colors["bg"]
        card = self._colors["card"]
        fg = self._colors["fg"]
        top = tk.Toplevel(self.root)
        top.title("Ölü Bölge Testi — Sonuç")
        top.configure(bg=bg)
        top.transient(self.root)
        top.resizable(False, False)
        try:
            top.iconbitmap(resource_path(os.path.join("assets",
                                                      "gamepad.ico")))
        except Exception:
            pass
        body = tk.Frame(top, bg=card, padx=16, pady=14)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(body, text="Ölçüm tamamlandı (3 sn, kollar serbest)",
                 bg=card, fg=fg, font=("Segoe UI", 10, "bold")).pack(
            anchor="w")
        tk.Label(body, justify="left", bg=card, fg=fg, font=("Consolas", 9),
                 text=("   Sol çubuk : kayma %s\n"
                       "   Sağ çubuk : kayma %s"
                       % (self._dz_fmt(sl), self._dz_fmt(sr)))).pack(
            anchor="w", pady=(6, 0))

        not_txt = ("Öneri, ölçülen dinlenme kaymasını tamamen kapatacak\n"
                   "şekilde kaymanın üzerine küçük bir pay eklenerek "
                   "hesaplandı.")
        tk.Label(body, text=not_txt, justify="left", bg=card, fg=fg,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))

        row = tk.Frame(body, bg=card)
        row.pack(anchor="w", pady=(12, 0))
        tk.Label(row, text="Ölü bölge:", bg=card, fg=fg,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        var = tk.StringVar(value="%.2f" % sug)
        ent = ttk.Entry(row, textvariable=var, width=8)
        ent.pack(side="left", padx=(8, 6))
        tk.Label(row, text="(şu an %.2f)" % cur, bg=card, fg=fg,
                 font=("Segoe UI", 9)).pack(side="left")

        warn = tk.Label(body, text="", justify="left", bg=card, fg=fg,
                        font=("Segoe UI", 9))
        warn.pack(anchor="w", pady=(6, 0))

        def kontrol(*_a):
            try:
                v = float(var.get().replace(",", "."))
            except ValueError:
                warn.configure(text="Geçerli bir sayı gir (örn: 0.03).",
                               fg=self._colors["err"])
                return
            if v < need:
                warn.configure(
                    text="Not: %.3f altında kayma oyunda hafif görünebilir."
                    % need, fg=self._colors["err"])
            elif v > 0.4:
                warn.configure(text="Not: çok büyük değer hassasiyeti "
                                    "düşürür.", fg=self._colors["muted"])
            else:
                warn.configure(text="")

        var.trace_add("write", kontrol)
        kontrol()

        btns = tk.Frame(body, bg=card)
        btns.pack(anchor="e", pady=(12, 0))
        ttk.Button(btns, text="Uygula ve Kaydet", style="Accent.TButton",
                   command=lambda: self._dz_apply(var.get(), top)).pack(
            side="left")
        ttk.Button(btns, text="İptal", command=top.destroy).pack(
            side="left", padx=(8, 0))

        top.bind("<Return>", lambda _e: self._dz_apply(var.get(), top))
        top.bind("<Escape>", lambda _e: top.destroy())
        top.update_idletasks()
        rx = (self.root.winfo_rootx()
              + (self.root.winfo_width() - top.winfo_width()) // 2)
        ry = (self.root.winfo_rooty()
              + (self.root.winfo_height() - top.winfo_height()) // 3)
        top.geometry("+%d+%d" % (max(0, rx), max(0, ry)))
        try:
            top.grab_set()
        except Exception:
            pass
        ent.focus_set()
        ent.selection_range(0, "end")

    def _dz_apply(self, raw, top):
        """Onaylanan ölü bölgeyi kaydeder; köprü çalışıyorsa yeniden
        başlatır."""
        try:
            v = float(str(raw).replace(",", "."))
        except ValueError:
            messagebox.showerror("Ölü Bölge Testi",
                                 "Geçerli bir sayı gir (örn: 0.03).")
            return
        if not (0.0 <= v <= 0.9):
            messagebox.showerror("Ölü Bölge Testi",
                                 "Değer 0 ile 0.9 arasında olmalı.")
            return
        try:
            top.destroy()
        except Exception:
            pass
        v = round(v, 3)
        self.cfg["deadzone"] = v
        if save_config(self.cfg):
            self._log("Ölü bölge %.3f olarak kaydedildi." % v)
        else:
            self._log("UYARI: ölü bölge kaydedilemedi (config.json).")
        self._refresh_settings_fields()
        if self._running:
            self._log("Yeni değeri uygulamak için düzeltici yeniden "
                      "başlatılıyor; kolu serbest bırakın (merkez "
                      "kalibrasyonu ~1.3 sn sürer)...")
            self._stop()
            self.root.after(300, self._dz_restart_when_idle)
        else:
            self._log("Ölü bölgenin geçerli olması için 'Başlat'a (F5) "
                      "basın.")

    def _dz_restart_when_idle(self):
        """Durdurma tamamlanınca köprüyü yeni ölü bölgeyle başlatır."""
        if self._running:
            self.root.after(300, self._dz_restart_when_idle)
            return
        self._log("Düzeltici yeni ölü bölgeyle başlatılıyor (%.3f)."
                  % float(self.cfg.get("deadzone", 0.12)))
        self._start()

    def _test_target_slot(self):
        """Test ekrani icin okunacak yuva: varsa SANAL kol (oyunun gordugu
        duzeltilmis deger), yoksa fiziksel kol."""
        gf = self._gf_mod
        if gf is None:
            try:
                import gamepad_fixer as gf
                self._gf_mod = gf
            except Exception:
                return None, None
        vs = self._vslot
        if vs is not None and gf.xinput_read(vs) is not None:
            return vs, "sanal"
        ps = self._pslot
        if ps is not None and gf.xinput_read(ps) is not None:
            return ps, "fiziksel"
        try:
            for s in sorted(gf.connected_slots()):
                return s, "fiziksel"
        except Exception:
            pass
        return None, None

    def _test_tick(self):
        try:
            self._dz_cal_tick()
            self._test_update()
        except Exception:
            if not self._tick_err_logged:
                self._tick_err_logged = True
                self._on_tk_error(*sys.exc_info())
        self.root.after(16, self._test_tick)

    def _test_update(self):
        gf = self._gf_mod
        if gf is None:
            self._test_target_slot()   # modul yuklemeyi dener
        slot, kind = self._test_target_slot()

        self._tick_n += 1
        upd = (self._tick_n % 6 == 1)   # etiketler ~100 ms'de bir tazelenir
        try:
            vis = (self._page == "test")
        except Exception:
            vis = True
        if vis:
            draw = self._draw_stick
        else:
            draw = lambda *a: None

        if slot is None or self._gf_mod is None:
            if upd:
                self.var_test_info.set(
                    "İzlenen kol: yok  —  kolu aç (Xbox düğmesi) ve Başlat'a bas.")
                self._set_vib_buttons(False)
                for side in ("L", "R"):
                    self._axis_vars[side].set("EKSEN: -")
            self._update_button_widgets(None)
            draw("L", 0.0, 0.0)
            draw("R", 0.0, 0.0)
            return

        gf = self._gf_mod
        st = gf.xinput_read(slot)
        if st is None:
            return
        g = st.Gamepad

        if upd:
            if kind == "sanal":
                self.var_test_info.set(
                    "İzlenen kol: SANAL (yuva %d) ✓ — oyunun gördüğü düzeltilmiş "
                    "değerler" % slot)
            else:
                self.var_test_info.set(
                    "İzlenen kol: FİZİKSEL (yuva %d) — ham değerler "
                    "(düzeltilmiş için Başlat)" % slot)

            self._set_vib_buttons(True)

        self._update_button_widgets(g)
        self._batt_poll()

        def norm(v):
            return max(-1.0, min(1.0, v / 32767.0))

        lx, ly = norm(g.sThumbLX), norm(g.sThumbLY)
        rx, ry = norm(g.sThumbRX), norm(g.sThumbRY)

        if upd:
            self._axis_vars["L"].set("EKSEN 0: %+.4f   EKSEN 1: %+.4f" % (lx, ly))
            self._axis_vars["R"].set("EKSEN 2: %+.4f   EKSEN 3: %+.4f" % (rx, ry))

        if self._circ["on"]:
            self._circ_sample("L", lx, ly)
            self._circ_sample("R", rx, ry)
        draw("L", lx, ly)
        draw("R", rx, ry)

    def _set_vib_buttons(self, on):
        state = "normal" if on else "disabled"
        for btn in (self.btn_vib1, self.btn_vibinf):
            try:
                if str(btn["state"]) != state:
                    btn.configure(state=state)
            except Exception:
                pass

    BTN_BITS = (("A", 0x1000), ("B", 0x2000), ("X", 0x4000), ("Y", 0x8000),
                ("LB", 0x0100), ("RB", 0x0200), ("Back", 0x0020),
                ("Start", 0x0010), ("L3", 0x0040), ("R3", 0x0080),
                ("↑", 0x0001), ("↓", 0x0002), ("←", 0x0004), ("→", 0x0008))

    def _update_button_widgets(self, g):
        """Test panelindeki dugme lambalarini ve LT/RT cubuklarini tazeler."""
        bits = lt = rt = 0
        if g is not None:
            bits = int(getattr(g, "wButtons", 0))
            lt = int(getattr(g, "bLeftTrigger", 0))
            rt = int(getattr(g, "bRightTrigger", 0))
        for name, bit in self.BTN_BITS:
            on = bool(bits & bit)
            if self._btn_last.get(name) != on:
                self._btn_last[name] = on
                lbl = self._btn_widgets.get(name)
                if lbl is not None:
                    c = self._colors
                    lbl.configure(bg=c["btn_press"] if on else c["btn_face"],
                                  fg=c["btn_press_fg"] if on else c["btn_fg"])
        for name, val in (("LT", lt), ("RT", rt)):
            if self._trig_last.get(name) != val:
                self._trig_last[name] = val
                bar, txt = self._trig_widgets[name]
                bar["value"] = val
                txt.set("%d%%" % round(val / 2.55))

    # ---------------- pil gostergesi (arka planda okunur)
    def _batt_poll(self):
        """Her ~5 sn'de bir pil bilgisini arka planda okur (arayuz donmaz)."""
        now = time.time()
        if not self._batt_busy and now - self._batt_t >= 5.0:
            self._batt_t = now
            self._batt_busy = True
            threading.Thread(target=self._batt_worker, daemon=True).start()
        res = self._batt_next
        if res is not None:
            self._batt_next = None
            txt, raw, lvl, chg = res
            self.var_batt.set(txt)
            self._batt_level = lvl
            self._batt_charging = chg
            self._draw_batt_icon()
            if raw and not self._batt_raw_logged:
                self._batt_raw_logged = True
                self._log_file("[Pil] HID raporu: %s"
                               % " ".join("%02X" % b for b in raw))

    def _batt_worker(self):
        txt = "Pil: -"
        raw = None
        lvl = None
        chg = False
        try:
            import gamepad_fixer as gf
            info, raw = gf.hid_battery_read()
            if info is not None:
                lvl = min(3, max(0, int(info.get("level", 0))))
                lv = ("boş", "az", "orta", "dolu")[lvl]
                chg = bool(info.get("charging"))
                if info.get("charging"):
                    txt = "Pil: şarj oluyor (%s)" % lv
                elif info.get("mode") == 0:
                    txt = "Pil: kablolu"
                    lvl = None
                elif not info.get("online"):
                    txt = "Pil: -"
                    lvl = None
                else:
                    txt = "Pil: %s" % lv
            else:
                slot = self._pslot
                if slot is None:
                    cands = [s for s in gf.connected_slots()
                             if s != self._vslot]
                    slot = min(cands) if cands else None
                if slot is not None:
                    bt, bl = gf.battery_info(slot)
                    if bt == 1:
                        txt = "Pil: kablolu"
                    elif bt in (2, 3) and bl in (0, 1, 2, 3):
                        txt = "Pil: %s" % ("boş", "az", "orta", "dolu")[bl]
                        lvl = bl
        except Exception:
            txt = "Pil: -"
            lvl = None
        self._batt_next = (txt, raw, lvl, chg)
        self._batt_busy = False

    # ---------------- dairesellik (site ile ayni yontem: 11.25° kovalar)
    def _flip_toggle(self):
        """Test ekranindaki cubuk ciziminin X/Y yonunu aninda degistirir."""
        self._flip_x = bool(self._flip_xvar.get())
        self._flip_y = bool(self._flip_var.get())
        self.cfg["panel_flip_x"] = self._flip_x
        self.cfg["panel_flip_y"] = self._flip_y
        save_config(self.cfg)
        for side in ("L", "R"):
            st = self._stick_items.get(side)
            if st is not None:
                cv = self._stick_cvs.get(side)
                for it, _ in st.get("slice_map", {}).values():
                    if cv is not None:
                        try:
                            cv.delete(it)
                        except Exception:
                            pass
                st["slice_map"] = {}
                st["bver"] = -999      # dilimler yeni yonle yeniden cizilsin
        self._log("Çubuk yönü: sağ-sol %s, yukarı-aşağı %s — kalıcı kaydedildi."
                  % ("çevrilmiş" if self._flip_x else "düz",
                     "çevrilmiş" if self._flip_y else "düz"))
        self._log_file("---- panel yonu degistirildi: X=%s Y=%s ----"
                       % ("ters" if self._flip_x else "duz",
                          "cevrilmis" if self._flip_y else "duz"))

    def _toggle_circ(self):
        c = self._circ
        c["on"] = not c["on"]
        if c["on"]:
            for side in ("L", "R"):
                c[side]["buckets"] = {}
                c[side]["ver"] += 1
            self.btn_circ.configure(
                text="Dairesellik Testi: AÇIK (sıfırlamak için tıkla)")
            self._log("Dairesellik testi açık: her iki çubuğu daire çizer gibi "
                      "yavaşça çevir.")
        else:
            self.btn_circ.configure(text="Dairesellik Testi")

    def _circ_sample(self, side, x, y):
        r = math.hypot(x, y)
        if r <= 0.2:
            return
        step = math.pi / 16.0
        ang = round(math.atan2(y, x) / step) * step
        if ang == -math.pi:
            ang = math.pi
        key = round(ang, 4)
        bk = self._circ[side]["buckets"]
        if r > bk.get(key, 0.0):
            bk[key] = r
            self._circ[side]["ver"] += 1

    def _circ_avg(self, side):
        vals = list(self._circ[side]["buckets"].values())
        if not vals:
            return None
        return math.sqrt(sum((1.0 - v) ** 2 for v in vals) / len(vals)) * 100.0

    @staticmethod
    def _mix(c1, c2, t):
        t = max(0.0, min(1.0, t))
        return "#%02x%02x%02x" % tuple(
            int(a + (b - a) * t) for a, b in zip(c1, c2))

    def _draw_stick(self, side, x, y):
        cv = self._stick_cvs.get(side)
        if cv is None:
            return
        w, h = int(cv["width"]), int(cv["height"])
        cx, cy = w / 2.0, h / 2.0
        R = min(cx, cy) - 8

        st = self._stick_items.get(side)
        if st is None or st.get("R") != R:
            # Kalıcı öğeler: her karede silip yeniden çizmek yerine yalnızca
            # koordinatlar güncellenir (60 fps akıcılık için).
            cv.delete("all")
            st = {"R": R, "slice_map": {}, "bver": -1, "bver_t": 0.0,
                  "err": None}
            c = self._colors
            st["circ"] = cv.create_oval(cx - R, cy - R, cx + R, cy + R,
                                        outline=c["axis"])
            st["h1"] = cv.create_line(cx - R, cy, cx + R, cy, fill=c["grid"])
            st["v1"] = cv.create_line(cx, cy - R, cx, cy + R, fill=c["grid"])
            st["line"] = cv.create_line(cx, cy, cx, cy, fill=c["trail"])
            st["dot"] = cv.create_oval(0, 0, 0, 0, fill=c["dot"],
                                       outline="")
            st["err1"] = cv.create_text(cx, cy + 22, text="Avg Error:",
                                        font=("Segoe UI", 8), fill=c["muted"],
                                        state="hidden")
            st["err2"] = cv.create_text(cx, cy + 42, text="",
                                        font=("Segoe UI", 13, "bold"),
                                        fill=c["fg"], state="hidden")
            self._stick_items[side] = st

        # XInput: y YUKARI pozitif; ekran: y AŞAĞI pozitif.
        # "Sağ-sol / Yukarı-aşağı" kutuları çizimi anında aynalar.
        fx = -1.0 if self._flip_x else 1.0
        fy = -1.0 if self._flip_y else 1.0
        px, py = cx + fx * x * R, cy + fy * y * R
        cv.coords(st["line"], cx, cy, px, py)
        cv.coords(st["dot"], px - 4, py - 4, px + 4, py + 4)

        on = self._circ["on"]
        cir = self._circ[side]

        if on:
            now_t = time.time()
            if cir["ver"] != st["bver"] and now_t - st["bver_t"] >= 0.033:
                st["bver"] = cir["ver"]
                st["bver_t"] = now_t
                step = math.pi / 16.0
                base = (0x40, 0x40, 0xff)
                smap = st["slice_map"]
                for ang, rad in cir["buckets"].items():
                    old = smap.get(ang)
                    if old is not None and old[1] == rad:
                        continue          # değişmeyen dilimi elleme (titremez)
                    if old is not None:
                        cv.delete(old[0])
                    f = max(-1.0, min(1.0, (rad - 1.0) * 5.0))
                    if f < 0:
                        col = self._mix(base, (0x22, 0xcc, 0x22), -f)
                    else:
                        col = self._mix(base, (0xee, 0x33, 0x33), f)
                    p1x = cx + fx * math.cos(ang - step / 2.0) * rad * R
                    p1y = cy + fy * math.sin(ang - step / 2.0) * rad * R
                    p2x = cx + fx * math.cos(ang + step / 2.0) * rad * R
                    p2y = cy + fy * math.sin(ang + step / 2.0) * rad * R
                    smap[ang] = (cv.create_polygon(
                        cx, cy, p1x, p1y, p2x, p2y, fill=col,
                        outline=col, width=1), rad)
                for key in ("line", "dot", "err1", "err2"):
                    cv.tag_raise(st[key])
            avg = self._circ_avg(side)
            txt = "-" if avg is None else "%.1f%%" % avg
            if txt != st["err"]:
                st["err"] = txt
                cv.itemconfigure(st["err2"], text=txt)
            if not st.get("shown"):
                st["shown"] = True
                cv.itemconfigure(st["err1"], state="normal")
                cv.itemconfigure(st["err2"], state="normal")
        else:
            if st["slice_map"]:
                for it, _ in st["slice_map"].values():
                    cv.delete(it)
                st["slice_map"] = {}
                st["bver"] = -1
            if st.get("shown"):
                st["shown"] = False
                cv.itemconfigure(st["err1"], state="hidden")
                cv.itemconfigure(st["err2"], state="hidden")

    # ---------------- titresim dugmeleri (site gibi)
    def _vib_power(self):
        pct = int(self._live_settings.get("rumble_scale", 100))
        return max(0, min(255, int(round(255 * pct / 100.0))))

    def _vib_scale_moved(self, val):
        try:
            v = max(0, min(150, int(float(val))))
        except (TypeError, ValueError):
            return
        self._live_settings["rumble_scale"] = v
        self.cfg["rumble_scale"] = v
        try:
            self.var_vib_scale.set("%d%%" % v)
        except Exception:
            pass
        self._draw_vib_bars()
        if self._scale_save_job is not None:
            try:
                self.root.after_cancel(self._scale_save_job)
            except Exception:
                pass
        self._scale_save_job = self.root.after(700, self._save_scale_cfg)

    def _save_scale_cfg(self):
        self._scale_save_job = None
        save_config(self.cfg)
        self._log_file("[ayar] titresim gucu = %d%%"
                       % int(self._live_settings.get("rumble_scale", 100)))

    def _vib_once(self):
        slot, kind = self._test_target_slot()
        gf = self._gf_mod
        if slot is None or gf is None:
            self._log("Titreşim: bağlı kol yok.")
            return
        gf.xinput_set_vibration(slot, self._vib_power(), self._vib_power())
        self._log("Titreşim: 1 saniyelik gönderildi (%s yuva %d, güç %%%d)."
                  % (kind, slot, self._live_settings.get("rumble_scale", 100)))
        try:
            self.root.after(1000, lambda: gf.xinput_set_vibration(slot, 0, 0))
        except Exception:
            pass

    def _vib_inf_toggle(self):
        if self._vibinf_on:
            self._vib_inf_stop()
            self._log("Sonsuz titreşim durduruldu.")
        else:
            self._vibinf_on = True
            self.btn_vibinf.configure(text="Titreşim, sonsuz: AÇIK (durdur)")
            self._log("Sonsuz titreşim açık (900 ms'de bir tazelenir).")
            self._vib_inf_tick()

    def _vib_inf_tick(self):
        if not self._vibinf_on:
            return
        slot, kind = self._test_target_slot()
        gf = self._gf_mod
        if slot is None or gf is None:
            self._log("Sonsuz titreşim: kol koptu, durduruldu.")
            self._vib_inf_stop(send_zero=False)
            return
        self._vib_last = (gf, slot)
        gf.xinput_set_vibration(slot, self._vib_power(), self._vib_power())
        self._vib_after = self.root.after(900, self._vib_inf_tick)

    def _vib_inf_stop(self, send_zero=True):
        self._vibinf_on = False
        if self._vib_after is not None:
            try:
                self.root.after_cancel(self._vib_after)
            except Exception:
                pass
            self._vib_after = None
        if send_zero and self._vib_last is not None:
            gf, slot = self._vib_last
            try:
                gf.xinput_set_vibration(slot, 0, 0)
            except Exception:
                pass
        self._vib_last = None
        try:
            self.btn_vibinf.configure(text="Titreşim, sonsuz")
        except Exception:
            pass

    # ------------------------------------------------------------------ ayarlar
    def _open_settings(self):
        self._show_page("settings")

    def _build_settings_page(self, parent):
        ttk.Label(parent, text="Gelişmiş Ayarlar",
                  style="Muted.TLabel").pack(anchor="e")

        form = tk.Frame(parent, bg=self._colors["bg"])
        form.pack(anchor="w", pady=(8, 0))
        c = self.cfg
        rows = [
            ("Ölü bölge (deadzone)", "deadzone", "0 - 0.9 · 0 = kapalı · örn: 0.12"),
            ("Dairesellik düzeltmesi", "circularity", "0 - 1 · kare kapı -> daire · Xbox: 1.0"),
            ("Eksen sızıntısı (crosstalk)", "crosstalk", "0 - 0.5 · genelde 0"),
            ("Yumuşatma (saniye)", "smooth", "0 - 0.5 · 0 = kapalı · örn: 0.03"),
            ("Güncelleme hızı (Hz)", "rate", "30 - 1000 · varsayılan 120"),
        ]
        self._set_entries = {}
        for i, (label, key, tip) in enumerate(rows):
            pady = (10 if i == 0 else 3, 0)
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w",
                                             padx=(0, 10), pady=pady)
            e = ttk.Entry(form, width=10)
            e.insert(0, "%g" % c[key])
            e.grid(row=i, column=1, sticky="w", pady=pady)
            self._set_entries[key] = e
            ttk.Label(form, text=tip, style="Muted.TLabel").grid(
                row=i, column=2, sticky="w", padx=(14, 0), pady=pady)

        base = len(rows)
        ttk.Label(form, text="Giriş yuvası").grid(
            row=base, column=0, sticky="w", padx=(0, 10), pady=(12, 0))
        self._slot_var = tk.StringVar(
            value="Otomatik" if int(c["slot"]) < 0 else str(int(c["slot"])))
        ttk.Combobox(form, textvariable=self._slot_var,
                     values=["Otomatik", "0", "1", "2", "3"],
                     width=9, state="readonly").grid(
            row=base, column=1, sticky="w", pady=(12, 0))

        self._inv_var = tk.BooleanVar(value=bool(c["invert_y"]))
        ttk.Checkbutton(form, text="Y eksenini ters çevir",
                        variable=self._inv_var).grid(
            row=base + 1, column=0, columnspan=2, sticky="w",
            padx=(0, 10), pady=(12, 0))
        self._rum_var = tk.BooleanVar(value=not c["no_rumble"])
        ttk.Checkbutton(form, text="Titreşim iletimi (oyun titreşimi -> kol)",
                        variable=self._rum_var).grid(
            row=base + 2, column=0, columnspan=2, sticky="w",
            padx=(0, 10), pady=(5, 0))
        ttk.Label(form, text="Değişiklikler bir sonraki Başlat ile geçerli olur.",
                  style="Muted.TLabel").grid(
            row=base + 3, column=0, columnspan=3, sticky="w", pady=(14, 0))

        btns = tk.Frame(parent, bg=self._colors["bg"])
        btns.pack(anchor="w", pady=(16, 0))
        ttk.Button(btns, text="Kaydet", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")
        ttk.Button(btns, text="Varsayılan Değerler",
                   command=self._settings_defaults).pack(side="left", padx=(8, 0))

    def _save_settings(self):
        try:
            new = {
                "deadzone": float(self._set_entries["deadzone"].get()
                                  .replace(",", ".")),
                "circularity": float(self._set_entries["circularity"].get()
                                     .replace(",", ".")),
                "crosstalk": float(self._set_entries["crosstalk"].get()
                                   .replace(",", ".")),
                "smooth": float(self._set_entries["smooth"].get()
                                .replace(",", ".")),
                "rate": int(float(self._set_entries["rate"].get()
                                  .replace(",", "."))),
            }
        except ValueError:
            messagebox.showerror("Hatalı değer",
                                 "Lütfen sayıları nokta ile yaz (örn: 0.12).")
            return
        if not (0 <= new["deadzone"] <= 0.9 and 0 <= new["circularity"] <= 1
                and 0 <= new["crosstalk"] <= 0.5 and 0 <= new["smooth"] <= 0.5
                and 30 <= new["rate"] <= 1000):
            messagebox.showerror("Aralık dışı",
                                 "Değerler izin verilen aralığın dışında.")
            return
        sel = self._slot_var.get()
        new["slot"] = -1 if sel == "Otomatik" else int(sel)
        new["invert_y"] = bool(self._inv_var.get())
        new["no_rumble"] = not bool(self._rum_var.get())
        self.cfg.update(new)
        if save_config(self.cfg):
            self._log("Ayarlar kaydedildi (bir sonraki Başlat'ta geçerli).")
        else:
            self._log("UYARI: ayarlar diske kaydedilemedi.")

    def _settings_defaults(self):
        for key, e in self._set_entries.items():
            e.delete(0, "end")
            e.insert(0, "%g" % DEFAULTS[key])
        self._slot_var.set("Otomatik")
        self._inv_var.set(False)
        self._rum_var.set(True)
        self._log("Alanlar varsayılan değerlerle dolduruldu "
                  "(kaydetmek için 'Kaydet').")

    def _refresh_settings_fields(self):
        try:
            for key, e in self._set_entries.items():
                e.delete(0, "end")
                e.insert(0, "%g" % float(self.cfg[key]))
            self._slot_var.set("Otomatik" if int(self.cfg["slot"]) < 0
                               else str(int(self.cfg["slot"])))
            self._inv_var.set(bool(self.cfg["invert_y"]))
            self._rum_var.set(not bool(self.cfg["no_rumble"]))
        except Exception:
            pass

    def _reset_settings(self):
        if messagebox.askyesno(APP_TITLE, "Ayarlar varsayılanlara döndürülsün mü?"):
            self.cfg.update(DEFAULTS)
            if save_config(self.cfg):
                self._log("Ayarlar varsayılanlara döndürüldü.")
            v = int(self.cfg.get("rumble_scale", 100))
            self._live_settings["rumble_scale"] = v
            try:
                self._vib_scale.set(v)
                self.var_vib_scale.set("%d%%" % v)
            except Exception:
                pass
            self._set_theme(str(self.cfg.get("theme", "dark")))
            self._refresh_settings_fields()
            self._set_panel_mode(str(self.cfg.get("panel_mode", "detayli")))

    # ------------------------------------------------------------------ titresim testi
    def _rumble_test(self):
        if self._rumble_thread is not None and self._rumble_thread.is_alive():
            messagebox.showinfo(APP_TITLE, "Titreşim testi zaten çalışıyor.")
            return
        self._log("")
        self._log("Titreşim testi başlatılıyor (kolu elinde tut)...")
        self._rumble_thread = threading.Thread(target=self._rumble_worker, daemon=True)
        self._rumble_thread.start()

    def _rumble_worker(self):
        writer = LineWriter(self._queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                import gamepad_fixer as gf
                import vgamepad as vg

                writer.write("=" * 58)
                writer.write("TİTREŞİM TESTİ - kolu elinde tut; kısaca titreyecek")
                writer.write("=" * 58)

                slot = None
                for _try in range(15):
                    # 1) Once BT ile bagli fiziksel kol (pil turu 0) ara
                    for s in range(4):
                        if gf.xinput_read(s) is None:
                            continue
                        bt, bl = gf.battery_info(s)
                        if bt == 0:  # BT fiziksel kol boyle gorunur
                            slot = s
                            break
                    # 2) Yoksa kablolu fiziksel: bagli ilk yuva
                    #    (duzeltici calisiyorsa sanal kol secilse bile oradan
                    #     fiziksele aktarilir; titresim yine ulasir)
                    if slot is None:
                        cand = sorted(gf.connected_slots())
                        if cand:
                            slot = cand[0]
                    if slot is not None:
                        break
                    writer.write("  Kol bekleniyor... kolu aç (Xbox düğmesi).")
                    time.sleep(2.0)
                if slot is None:
                    writer.write("Fiziksel kol bulunamadı; test iptal edildi.")
                    return
                self._test_handles["slot"] = slot
                bt2, _ = gf.battery_info(slot)
                tur = "BT fiziksel" if bt2 == 0 else "kablolu/aktarimli"
                writer.write("Fiziksel kol: yuva %d (%s)" % (slot, tur))

                patterns = [
                    ("1) İnce titreşim (küçük motor)", 0, 90),
                    ("2) Kalın titreşim (büyük motor)", 120, 0),
                    ("3) İkisi birden (güçlü)", 120, 90),
                ]

                def run(send):
                    for name, large, small in patterns:
                        ok = send(large, small)
                        writer.write("   %s ... (%s)" % (name, "OK" if ok else "HATA"))
                        time.sleep(0.55)
                        send(0, 0)
                        time.sleep(0.9)

                writer.write("")
                writer.write("--- A) XInput yolu ---")
                run(lambda l, s: gf.xinput_set_vibration(slot, l, s))

                writer.write("--- B) Bluetooth HID yolu ---")
                rumbler = gf.HidRumbler()
                if rumbler.open():
                    self._test_handles["rumbler"] = rumbler
                    run(rumbler.send)
                else:
                    writer.write("  HID kanalı açılamadı (hidapi/kol); B atlandı.")
                    rumbler = None

                writer.write("--- C) Uçtan uca (sanal kol -> iletim -> fiziksel) ---")
                before = gf.connected_slots()
                pad = vg.VX360Gamepad()
                pad.reset()
                pad.update()
                self._test_handles["pad"] = pad
                time.sleep(0.4)
                new = gf.connected_slots() - before
                if new:
                    vslot = sorted(new)[0]
                    counters = {"n": 0, "x": 0, "h": 0}

                    def on_rumble(client, target, large_motor, small_motor,
                                  led_number, user_data):
                        counters["n"] += 1
                        counters["x"] += 1 if gf.xinput_set_vibration(
                            slot, large_motor, small_motor) else 0
                        if rumbler is not None:
                            counters["h"] += 1 if rumbler.send(
                                large_motor, small_motor) else 0

                    pad.register_notification(on_rumble)
                    time.sleep(0.2)
                    for name, large, small in patterns:
                        writer.write("   (oyun) %s ..." % name)
                        gf.xinput_set_vibration(vslot, large, small)
                        time.sleep(0.55)
                        gf.xinput_set_vibration(vslot, 0, 0)
                        time.sleep(0.9)
                    pad.reset()
                    pad.update()
                    writer.write("  Bildirim: %d | XInput iletim: %d | HID iletim: %d"
                                 % (counters["n"], counters["x"], counters["h"]))
                else:
                    writer.write("  Sanal kol oluşmadı; C atlandı.")

                writer.write(">>> Test bitti. Hangi bölümlerde titrediğini not al.")
        except Exception:
            writer.write("[uygulama] Test hatası:")
            writer.write(traceback.format_exc())
        finally:
            writer.flush()
            self._cleanup_test_handles()

    def _cleanup_test_handles(self):
        sh = dict(self._test_handles)
        self._test_handles = {}
        if not sh:
            return
        try:
            import gamepad_fixer as gf
            if sh.get("slot") is not None:
                gf.xinput_set_vibration(sh["slot"], 0, 0)
        except Exception:
            pass
        try:
            if sh.get("rumbler") is not None:
                sh["rumbler"].send(0, 0)
                sh["rumbler"].close()
        except Exception:
            pass
        try:
            if sh.get("pad") is not None:
                sh["pad"].reset()
                sh["pad"].update()
        except Exception:
            pass

    # ------------------------------------------------------------------ yardim
    def _help(self):
        messagebox.showinfo(
            "Nasıl Kullanılır?",
            "1) Kolu aç (Xbox düğmesi).\n"
            "2) 'Başlat'a bas, kolu SERBEST bırak — merkez kalibrasyonu ~1.3 sn sürer.\n"
            "3) Oyun/sitede İKİ kol görünür:\n"
            "      • Xbox Wireless Controller (fiziksel, 02e0)\n"
            "      • Xbox 360 Controller (sanal, 028e)  <- BUNU SEÇ\n"
            "   Tarayıcıda sopayı oynat ki kollar listelensin.\n"
            "   'Kalibrasyon ve Test' sayfasından canlı izle: düğmeler,\n"
            "   çubuklar (Dairesellik Testi + Avg Error) ve titreşim düğmeleri.\n"
            "   'Ölü Bölge Testi': kolları serbest bırak-tıkla; 3 sn ölçüp\n"
            "   ölü bölgeyi ölçülen kaymaya göre önerir, onaylarsan uygular.\n"
            "4) Durdurmak için: 'Durdur' (F6) ya da pencereyi kapat.\n\n"
            "İlk kolun SANAL olmasını istersen (oyun otomatik onu alsın):\n"
            "  Kolu kapat -> Başlat -> 'Fiziksel kol bekleniyor...' yazınca kolu aç.\n\n"
            "Ayarlar menüsünden ölü bölge, dairesellik ve diğer filtre değerlerini\n"
            "değiştirebilirsin (bir sonraki Başlat'ta geçerli olur).\n\n"
            "Görünüm menüsü (veya Ctrl+T) ile koyu / açık tema seçebilirsin.")

    def _about(self):
        messagebox.showinfo(
            "Hakkında",
            "%s v%s\n\n"
            "Fiziksel XInput kolunu okuyup düzeltir:\n"
            "  • dairesel ölü bölge  • merkez kayması temizliği\n"
            "  • kare kapı -> daire düzeltmesi (Avg Error %%21 -> ~%%0)\n"
            "  • yumuşatma + snap\n"
            "  • canlı düğme/tetik test paneli, titreşim gücü ayarı\n"
            "  • pil göstergesi, Windows ile otomatik başlatma\n"
            "  • koyu/açık tema (Görünüm menüsü)\n\n"
            "Düzeltilmiş veriyi sanal Xbox 360 kolu (ViGEmBus, ürün 028e)\n"
            "olarak sisteme sunar; titreşim fiziksel kola aktarılır."
            % (APP_TITLE, APP_VERSION))

    # ------------------------------------------------------------------ otomatik baslatma
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    AUTOSTART_NAME = "GamepadFixer"

    def _autostart_cmd(self):
        if getattr(sys, "frozen", False):
            target, argstr = sys.executable, "--start-min"
        else:
            target = sys.executable
            argstr = '"%s" --start-min' % os.path.join(
                BASEDIR, "gamepad_fixer_gui.py")
        return '"%s" %s' % (target, argstr)

    def _autostart_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0,
                                winreg.KEY_READ) as k:
                val, _ = winreg.QueryValueEx(k, self.AUTOSTART_NAME)
            return bool(val)
        except OSError:
            return False

    def _autostart_set(self, enable):
        try:
            import winreg
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as k:
                if enable:
                    winreg.SetValueEx(k, self.AUTOSTART_NAME, 0,
                                      winreg.REG_SZ, self._autostart_cmd())
                else:
                    try:
                        winreg.DeleteValue(k, self.AUTOSTART_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except OSError:
            return False

    def _autostart_toggle(self):
        enable = bool(self._autostart_var.get())
        if self._autostart_set(enable):
            if enable:
                self._log("Windows ile otomatik başlatma AÇIK: bilgisayar "
                          "açılınca uygulama simge durumunda kendiliğinden başlar.")
                self._log_file("---- otomatik baslatma: acik ----")
            else:
                self._log("Windows ile otomatik başlatma KAPALI.")
                self._log_file("---- otomatik baslatma: kapali ----")
        else:
            self._autostart_var.set(not enable)
            messagebox.showerror(APP_TITLE,
                                 "Otomatik başlatma ayarı kaydedilemedi "
                                 "(kayıt defteri erişimi).")

    # ------------------------------------------------------------------ kapanis
    def _on_tk_error(self, exc_type, exc_value, tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, tb))
        for ln in text.rstrip().splitlines():
            self._log("[tk-hata] " + ln)

    def _on_close(self):
        self._vib_inf_stop()
        if self._running and self._stop_event is not None:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(3.5)
        self._cleanup_test_handles()
        self._log_file("---- uygulama kapatildi ----")
        if self._file_log is not None:
            try:
                self._file_log.close()
            except OSError:
                pass
            self._file_log = None
        self.root.destroy()


def _configure_dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    argv = list(sys.argv[1:])

    # Windows ile otomatik baslatma: simge durumunda ac + kopruyu baslat
    start_min = "--start-min" in argv
    if start_min:
        argv.remove("--start-min")

    # Pencersiz exe testi icin: --clilog dosya (ciktilari dosyaya yaz)
    if "--clilog" in argv:
        i = argv.index("--clilog")
        try:
            path = argv[i + 1]
        except IndexError:
            path = "gamepad_gui_log.txt"
        del argv[i:i + 2]
        try:
            f = open(path, "w", encoding="utf-8", buffering=1)
            sys.stdout = f
            sys.stderr = f
        except OSError:
            pass

    print("Gamepad Fixer GUI basliyor (argv: %s)" % argv)

    if "--cli" in argv:
        argv.remove("--cli")
        import gamepad_fixer
        gamepad_fixer.main(argv=argv)
        return

    _configure_dpi()
    app = FixerApp()
    if start_min:
        app._auto_mode = True
        app.root.after(400, app.root.iconify)
        if os.environ.get("GPF_NO_AUTO_BRIDGE"):
            app._log("(test) Otomatik köprü başlatma atlandı.")
        else:
            app.root.after(1400, lambda: app._start(auto=True))
    if "--smoke-gui" in argv:
        def _smoke_circ():
            # dairesellik + Avg Error çizim yolunu da dene (gorunmez test)
            app._show_page("test")
            app._toggle_circ()
            app._circ_sample("L", 0.90, 0.40)
            app._circ_sample("L", 0.20, -0.92)
            app._circ_sample("R", -0.95, 0.10)
        app.root.after(600, _smoke_circ)
        app.root.after(2500, app.root.destroy)
    if "--smoke-run" in argv:
        app.root.after(600, app._start)
        app.root.after(9000, app._stop)
        smoke_deadline = time.time() + 45

        def _smoke_finish():
            if app._running and time.time() < smoke_deadline:
                app.root.after(300, _smoke_finish)
            else:
                app.root.after(500, app.root.destroy)

        app.root.after(9500, _smoke_finish)
    app.root.mainloop()
    if "--smoke-gui" in argv or "--smoke-run" in argv:
        print("smoke: running=%s thread_alive=%s" % (
            app._running, bool(app._thread and app._thread.is_alive())), flush=True)
        print("=== GUI LOG ===", flush=True)
        print("\n".join(app._log_lines), flush=True)
        print("=== GUI LOG SONU ===", flush=True)
    print("GUI kapatildi.", flush=True)


if __name__ == "__main__":
    main()
