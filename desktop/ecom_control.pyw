# -*- coding: utf-8 -*-
"""Ecom Monitor – panoul local al monitorului de produse (ads-monitor).

Meniu: Dashboard · Setări · Ponturi · Loguri · Test.
- „Pornire cu PC-ul” ON = task-urile AdsMonitor (din oră în oră + la login) și EcomRadarRefresh (15 min) active;
  OFF = dezactivate + oprește rularea curentă. La deschidere aplicația NU mai schimbă task-urile: comutatorul arată
  starea reală a task-urilor.
- Setările se salvează în ads-monitor\\settings.json (config.py păstrează valorile implicite).
Rulare:  pythonw ecom_control.pyw            (fereastra normală)
         python  ecom_control.pyw --screenshots <dir>   (salvează câte o captură pentru fiecare pagină și iese)
"""
from __future__ import annotations

import ctypes
import datetime as dt
import importlib
import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MONITOR = ROOT / "ads-monitor"
OUT = MONITOR / "out"
PY = MONITOR / ".venv" / "Scripts" / "python.exe"
FLAG = HERE / "autostart.json"
TASKS = ("AdsMonitor", "EcomRadarRefresh")
CREATE_NO_WINDOW = 0x08000000
sys.path.insert(0, str(MONITOR))

# --- culori / fonturi
BG = "#F5F6F8"
CARD = "#FFFFFF"
LINE = "#E3E5EA"
INK = "#14161B"
MUTED = "#626873"
SIDE = "#111318"
SIDE_HI = "#22252D"
ACCENT = "#E0312B"
GREEN = "#12955A"
AMBER = "#B7791F"
F = "Segoe UI"


# ---------------------------------------------------------------------------- sistem
def ps(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)


def tasks_info() -> dict:
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $r=@(); foreach($n in @('AdsMonitor','EcomRadarRefresh')){"
              "$t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; if(-not $t){continue}; $i=$t|Get-ScheduledTaskInfo;"
              "$rep=($t.Triggers | Where-Object { $_.Repetition.Interval } | Select-Object -First 1).Repetition.Interval;"
              "$r+=[pscustomobject]@{name=$n; state=\"$($t.State)\"; last=$(if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm')}else{''});"
              "next=$(if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm')}else{''}); result=$i.LastTaskResult; interval=\"$rep\"}};"
              "ConvertTo-Json -InputObject $r -Compress")
    try:
        r = ps(script)
        data = json.loads(r.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if isinstance(data, dict):
        data = [data]
    return {d["name"]: d for d in data}


def enabled_state(state: str) -> bool:
    return state.lower() in ("ready", "running", "queued")


def set_tasks(on: bool) -> None:
    verb = "Enable" if on else "Disable"
    ps("; ".join(f"{verb}-ScheduledTask -TaskName '{n}' -ErrorAction SilentlyContinue | Out-Null" for n in TASKS))


def set_interval(minutes: int) -> str:
    script = ("$t=Get-ScheduledTask -TaskName 'AdsMonitor' -ErrorAction Stop; $ch=0; foreach($tr in $t.Triggers){"
              f"if($tr.Repetition -and $tr.Repetition.Interval){{$tr.Repetition.Interval='PT{int(minutes)}M'; $ch++}}}};"
              "if($ch){Set-ScheduledTask -TaskName 'AdsMonitor' -Trigger $t.Triggers -ErrorAction Stop | Out-Null}; \"ok $ch\"")
    r = ps(script)
    return (r.stdout or r.stderr or "").strip()[-200:]


def save_flag(on: bool) -> None:
    FLAG.write_text(json.dumps({"on": on}, indent=2), encoding="utf-8")


def monitor_pids() -> list[int]:
    me = os.getpid()
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*ads-monitor*' -or "
              "$_.CommandLine -like '*run.py --target*') -and $_.CommandLine -notlike '*ecom_control*' -and "
              "$_.CommandLine -notlike '*--selftest*' -and $_.Name -match 'python|cmd|node' } | Select-Object -ExpandProperty ProcessId")
    try:
        r = ps(script)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(x) for x in (r.stdout or "").split() if x.strip().isdigit() and int(x) != me]


def run_active() -> int | None:
    """PID-ul rulării din out/run.lock dacă procesul chiar trăiește."""
    try:
        pid = int((OUT / "run.lock").read_text().strip())
    except (OSError, ValueError):
        return None
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return pid if ok and code.value == 259 else None


def stop_monitor() -> int:
    pids = monitor_pids()
    if pids:
        subprocess.run(["taskkill", "/F", "/T"] + [x for p in pids for x in ("/PID", str(p))], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    return len(pids)


def start_monitor() -> None:
    if not PY.exists():
        raise FileNotFoundError(str(PY))
    OUT.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "ADS_NO_JITTER": "1"}
    subprocess.Popen(["cmd.exe", "/c", '"%s" run.py --target prod >> out\\task.log 2>&1' % PY], cwd=str(MONITOR), env=env,
                     creationflags=CREATE_NO_WINDOW)


def tail(path: Path, n: int = 400) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return data.splitlines()[-n:]


def last_summary() -> str:
    for line in reversed(tail(OUT / "task.log", 3000)):
        if line.startswith("[run] gata:"):
            return line.removeprefix("[run] gata: ")
    return ""


def load_tips(days: int = 30, files: int = 300) -> list[dict]:
    out = []
    lim = dt.datetime.now() - dt.timedelta(days=days)
    logs = sorted(OUT.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:files]
    for f in logs:
        if dt.datetime.fromtimestamp(f.stat().st_mtime) < lim:
            break
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        when = dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m %H:%M")
        for e in items if isinstance(items, list) else []:
            if not str(e.get("result", "")).startswith("OK"):
                continue
            card = e.get("score") or {}
            nota = (e.get("nota") or {}).get("final") or card.get("nota") or ""
            out.append({"when": when, "title": e.get("title") or e.get("key", ""), "page": e.get("page", ""),
                        "nota": nota, "ali": e.get("ali_price") or str(e.get("result", "")).split(" ")[1],
                        "emag": (e.get("emag") or {}).get("line", ""), "rising": bool((e.get("rising") or {}).get("rising")),
                        "sent": e.get("sent_to") or "", "link": e.get("link_url") or ("https://" + e.get("key", "")),
                        "ali_url": e.get("ali_url", ""), "shopify": (e.get("shopify") or {}).get("admin_url", "")})
    return out


# ---------------------------------------------------------------------------- setari
def load_config(defaults: bool = False) -> dict:
    """Valorile curente din config (cu settings.json) sau implicite (fără)."""
    src = (MONITOR / "config.py").read_text(encoding="utf-8")
    ns: dict = {"__file__": str(MONITOR / "config.py"), "__name__": "config_view"}
    old = os.environ.get("ECOM_SETTINGS_OFF")
    if defaults:
        os.environ["ECOM_SETTINGS_OFF"] = "1"
    try:
        import settings
        importlib.reload(settings)
        exec(compile(src, str(MONITOR / "config.py"), "exec"), ns)  # noqa: S102 (fișierul local al monitorului)
    finally:
        if defaults:
            if old is None:
                os.environ.pop("ECOM_SETTINGS_OFF", None)
            else:
                os.environ["ECOM_SETTINGS_OFF"] = old
    return ns


# ---------------------------------------------------------------------------- UI
class App:
    PAGES = (("dashboard", "Dashboard", "◉"), ("setari", "Setări", "⚙"), ("ponturi", "Ponturi", "★"),
             ("loguri", "Loguri", "≡"), ("test", "Test", "✓"))

    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        root.title("Ecom Monitor")
        root.configure(bg=BG)
        root.geometry("1180x760")
        root.minsize(980, 640)
        self._style()
        self.side = tk.Frame(root, bg=SIDE, width=210)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.main = tk.Frame(root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        tk.Label(self.side, text="Ecom Monitor", bg=SIDE, fg="#FFFFFF", font=(F, 15, "bold")).pack(anchor="w", padx=20, pady=(22, 0))
        tk.Label(self.side, text="magazinul propriu · ponturi", bg=SIDE, fg="#8A909C", font=(F, 9)).pack(anchor="w", padx=20, pady=(0, 18))
        self.nav: dict[str, tk.Label] = {}
        for key, label, icon in self.PAGES:
            b = tk.Label(self.side, text=f"  {icon}   {label}", bg=SIDE, fg="#D6D9DF", font=(F, 11), anchor="w",
                         padx=14, pady=10, cursor="hand2")
            b.pack(fill="x", padx=10, pady=1)
            b.bind("<Button-1>", lambda _e, k=key: self.show(k))
            self.nav[key] = b
        self.side_status = tk.Label(self.side, text="", bg=SIDE, fg="#8A909C", font=(F, 9), justify="left", anchor="w",
                                    wraplength=180)
        self.side_status.pack(side="bottom", fill="x", padx=20, pady=18)
        self.pages: dict[str, tk.Frame] = {}
        self.current = ""
        self.info: dict = {}
        self.build_dashboard()
        self.build_settings()
        self.build_tips()
        self.build_logs()
        self.build_test()
        self.show("dashboard")
        self.refresh_status()
        self.root.after(200, self._pump)
        self.root.after(1500, self._log_tick)

    # --- stil
    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", font=(F, 10), rowheight=28, background=CARD, fieldbackground=CARD, bordercolor=LINE)
        st.configure("Treeview.Heading", font=(F, 10, "bold"), background="#EEF0F3", foreground=INK, relief="flat")
        st.map("Treeview", background=[("selected", "#FDE7E6")], foreground=[("selected", INK)])
        st.configure("TCombobox", padding=4)
        st.configure("Vertical.TScrollbar", background="#D9DCE2", troughcolor=BG, bordercolor=BG, arrowcolor=MUTED)

    def page(self, key: str, title: str, subtitle: str) -> tk.Frame:
        f = tk.Frame(self.main, bg=BG)
        head = tk.Frame(f, bg=BG)
        head.pack(fill="x", padx=28, pady=(22, 10))
        tk.Label(head, text=title, bg=BG, fg=INK, font=(F, 20, "bold")).pack(anchor="w")
        tk.Label(head, text=subtitle, bg=BG, fg=MUTED, font=(F, 10)).pack(anchor="w")
        self.pages[key] = f
        return f

    def card(self, parent, **pack) -> tk.Frame:
        c = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        c.pack(**({"fill": "x", "padx": 28, "pady": 6} | pack))
        return c

    def button(self, parent, text, cmd, kind="dark", **pack):
        colors = {"dark": (INK, "#FFFFFF"), "accent": (ACCENT, "#FFFFFF"), "light": ("#FFFFFF", INK), "green": (GREEN, "#FFFFFF")}
        bg, fg = colors[kind]
        b = tk.Button(parent, text=text, command=cmd, relief="flat", bd=0, cursor="hand2", bg=bg, fg=fg,
                      activebackground=bg, activeforeground=fg, font=(F, 10, "bold"), padx=16, pady=8,
                      highlightthickness=1 if kind == "light" else 0, highlightbackground=LINE)
        b.pack(**({"side": "left", "padx": (0, 8)} | pack))
        return b

    def show(self, key: str):
        if self.current:
            self.pages[self.current].pack_forget()
            self.nav[self.current].configure(bg=SIDE, fg="#D6D9DF", font=(F, 11))
        self.pages[key].pack(fill="both", expand=True)
        self.nav[key].configure(bg=SIDE_HI, fg="#FFFFFF", font=(F, 11, "bold"))
        self.current = key
        if key == "ponturi":
            self.load_tips()
        if key == "loguri":
            self._log_tick(force=True)

    # --- thread helpers
    def bg(self, fn, done=None):
        def work():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001
                res = e
            if done:
                self.q.put((done, res))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                fn, res = self.q.get_nowait()
                fn(res)
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    # ------------------------------------------------------------------ Dashboard
    def build_dashboard(self):
        f = self.page("dashboard", "Dashboard", "Starea monitorului, rularea următoare și ultimele ponturi")
        top = tk.Frame(f, bg=BG)
        top.pack(fill="x", padx=22)
        self.kpi = {}
        for i, (key, label) in enumerate((("state", "Monitor"), ("ads", "AdsMonitor"), ("radar", "EcomRadarRefresh"),
                                          ("next", "Următoarea rulare"))):
            top.grid_columnconfigure(i, weight=1, uniform="kpi")
            c = tk.Frame(top, bg=CARD, highlightbackground=LINE, highlightthickness=1)
            c.grid(row=0, column=i, sticky="nsew", padx=6, pady=4)
            tk.Label(c, text=label.upper(), bg=CARD, fg=MUTED, font=(F, 8, "bold")).pack(anchor="w", padx=14, pady=(12, 0))
            v = tk.Label(c, text="…", bg=CARD, fg=INK, font=(F, 15, "bold"), anchor="w")
            v.pack(anchor="w", padx=14)
            s = tk.Label(c, text="", bg=CARD, fg=MUTED, font=(F, 9), anchor="w", justify="left", wraplength=210)
            s.pack(anchor="w", padx=14, pady=(0, 12))
            self.kpi[key] = (v, s)

        sw = self.card(f, pady=(12, 6))
        left = tk.Frame(sw, bg=CARD)
        left.pack(side="left", padx=16, pady=14)
        tk.Label(left, text="Pornire cu PC-ul", bg=CARD, fg=INK, font=(F, 12, "bold")).pack(anchor="w")
        tk.Label(left, text="ON: AdsMonitor rulează automat (la login și la interval), EcomRadarRefresh la 15 minute.\n"
                            "OFF: task-urile sunt dezactivate și rularea curentă se oprește.",
                 bg=CARD, fg=MUTED, font=(F, 9), justify="left").pack(anchor="w")
        box = tk.Frame(sw, bg="#ECEEF1")
        box.pack(side="right", padx=16)
        self.off_btn = tk.Label(box, text="OFF", font=(F, 10, "bold"), padx=18, pady=7, cursor="hand2")
        self.on_btn = tk.Label(box, text="ON", font=(F, 10, "bold"), padx=18, pady=7, cursor="hand2")
        self.off_btn.pack(side="left", padx=2, pady=2)
        self.on_btn.pack(side="left", padx=2, pady=2)
        self.off_btn.bind("<Button-1>", lambda _e: self.set_switch(False))
        self.on_btn.bind("<Button-1>", lambda _e: self.set_switch(True))
        self.switch_on: bool | None = None
        self.paint_switch()

        act = tk.Frame(f, bg=BG)
        act.pack(fill="x", padx=28, pady=(6, 4))
        self.button(act, "▶  Pornește acum", self.start_now, "accent")
        self.button(act, "■  Oprește", self.stop_now, "light")
        self.button(act, "↻  Reîmprospătează", self.refresh_status, "light")
        self.dash_msg = tk.Label(act, text="", bg=BG, fg=MUTED, font=(F, 10))
        self.dash_msg.pack(side="left", padx=10)

        tk.Label(f, text="Ultimele ponturi", bg=BG, fg=INK, font=(F, 12, "bold")).pack(anchor="w", padx=28, pady=(14, 4))
        c = self.card(f, fill="both", expand=True, pady=(0, 22))
        self.dash_tips = ttk.Treeview(c, columns=("when", "nota", "title", "info"), show="headings", height=6)
        for col, txt, w in (("when", "Când", 100), ("nota", "NOTA", 60), ("title", "Produs", 420), ("info", "Piață", 360)):
            self.dash_tips.heading(col, text=txt, anchor="w")
            self.dash_tips.column(col, width=w, anchor="w", stretch=col in ("title", "info"))
        self.dash_tips.pack(fill="both", expand=True, padx=1, pady=1)
        self.dash_tips.bind("<Double-1>", lambda _e: self._open_selected(self.dash_tips, self.dash_rows))
        self.dash_rows: list[dict] = []

    def paint_switch(self):
        on = self.switch_on
        self.on_btn.configure(bg=GREEN if on else "#ECEEF1", fg="#FFFFFF" if on else MUTED)
        self.off_btn.configure(bg=ACCENT if on is False else "#ECEEF1", fg="#FFFFFF" if on is False else MUTED)

    def set_switch(self, on: bool):
        if not on and not messagebox.askyesno("Ecom Monitor", "Opresc monitorul? Task-urile se dezactivează și nu mai caută produse până pui ON."):
            return
        self.switch_on = on
        self.paint_switch()
        self.dash_msg.configure(text="Aplic…")

        def work():
            save_flag(on)
            set_tasks(on)
            if not on:
                stop_monitor()
        self.bg(work, lambda _r: (self.dash_msg.configure(text="Pornit: rulează automat." if on else "Oprit."), self.refresh_status()))

    def start_now(self):
        def work():
            if run_active() or monitor_pids():
                return "Rulează deja."
            start_monitor()
            return "Pornit. Urmărește pagina Loguri."
        self.bg(work, lambda r: (self.dash_msg.configure(text=str(r)), self.root.after(2500, self.refresh_status)))

    def stop_now(self):
        if not messagebox.askyesno("Ecom Monitor", "Opresc rularea curentă? (task-urile rămân active)"):
            return
        self.bg(stop_monitor, lambda n: (self.dash_msg.configure(text="Oprit." if n else "Nu rula nimic."), self.refresh_status()))

    def refresh_status(self):
        def work():
            return {"tasks": tasks_info(), "pid": run_active(), "summary": last_summary(), "tips": load_tips(days=30, files=150)}
        self.bg(work, self._apply_status)

    def _apply_status(self, d):
        if isinstance(d, Exception):
            self.dash_msg.configure(text=f"Eroare: {d}")
            return
        self.info = d
        t = d["tasks"]
        a, r = t.get("AdsMonitor", {}), t.get("EcomRadarRefresh", {})
        pid = d["pid"]
        v, s = self.kpi["state"]
        v.configure(text="Rulează acum" if pid else "În așteptare", fg=GREEN if pid else INK)
        s.configure(text=(f"PID {pid}" if pid else "") + (f"\nUltima: {d['summary'][:60]}" if d["summary"] else ""))
        for key, info in (("ads", a), ("radar", r)):
            v, s = self.kpi[key]
            on = enabled_state(info.get("state", ""))
            v.configure(text=("Activ" if on else "Dezactivat") if info else "Lipsește", fg=GREEN if on else ACCENT)
            s.configure(text=f"Ultima rulare: {info.get('last') or '–'}" + (f"\nInterval: {self._iv(info.get('interval'))}" if info.get("interval") else ""))
        v, s = self.kpi["next"]
        v.configure(text=(a.get("next") or "–")[-5:] if a.get("next") else "–")
        s.configure(text=(a.get("next") or "nu e programată")[:10] + "\nAdsMonitor")
        on = bool(a) and enabled_state(a.get("state", "")) and enabled_state(r.get("state", "") or "ready")
        if self.switch_on is None or not self.dash_msg.cget("text").startswith("Aplic"):
            self.switch_on = on
            self.paint_switch()
        self.side_status.configure(text=("● Monitor activ" if on else "● Monitor oprit") + f"\n{dt.datetime.now():%H:%M:%S}",
                                   fg="#6EE7A8" if on else "#FCA5A5")
        self.dash_rows = d["tips"][:12]
        self.dash_tips.delete(*self.dash_tips.get_children())
        for i, e in enumerate(self.dash_rows):
            info = " · ".join(x for x in (("📈 în creștere" if e["rising"] else ""), e["emag"], ("trimis " + e["sent"]) if e["sent"] else "") if x)
            self.dash_tips.insert("", "end", iid=str(i), values=(e["when"], e["nota"], e["title"][:80], info))
        if getattr(self, "_auto_id", None):
            self.root.after_cancel(self._auto_id)
        self._auto_id = self.root.after(30000, self._auto_refresh)

    def _auto_refresh(self):
        self._auto_id = None
        if self.current == "dashboard":
            self.refresh_status()
        else:
            self._auto_id = self.root.after(30000, self._auto_refresh)

    @staticmethod
    def _iv(s: str) -> str:
        import re
        m = re.fullmatch(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?", s or "")
        if not m:
            return s
        h, mi = int(m.group(1) or 0), int(m.group(2) or 0)
        return f"{h * 60 + mi} min"

    def _open_selected(self, tree, rows, field="link"):
        sel = tree.selection()
        if sel:
            url = rows[int(sel[0])].get(field)
            if url:
                webbrowser.open(url)

    # ------------------------------------------------------------------ Setari
    def build_settings(self):
        import settings
        self.settings = settings
        f = self.page("setari", "Setări", "Toate setările monitorului. Se aplică de la rularea următoare.")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(0, 6))
        self.button(bar, "Salvează", self.save_settings, "accent")
        self.button(bar, "Anulează modificările", self.fill_settings, "light")
        self.button(bar, "Valori implicite", self.reset_settings, "light")
        self.set_msg = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(F, 10))
        self.set_msg.pack(side="left", padx=10)

        outer = tk.Frame(f, bg=BG)
        outer.pack(fill="both", expand=True, padx=(28, 12), pady=(0, 16))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.set_canvas = canvas

        def wheel(e):
            if self.current == "setari":
                canvas.yview_scroll(int(-e.delta / 120), "units")
        self.root.bind_all("<MouseWheel>", wheel)

        self.widgets: dict[str, tuple] = {}
        self.errs: dict[str, tk.Label] = {}
        groups: dict[str, list] = {}
        for fd in settings.SCHEMA:
            groups.setdefault(fd["group"], []).append(fd)
        cols = tk.Frame(inner, bg=BG)
        cols.pack(fill="both", expand=True)
        colw = [tk.Frame(cols, bg=BG), tk.Frame(cols, bg=BG)]
        colw[0].pack(side="left", fill="both", expand=True, padx=(0, 8))
        colw[1].pack(side="left", fill="both", expand=True, padx=(8, 0))
        order = ["Rulare", "Preț și notă", "AI", "eMAG", "Căutări", "Public 30+", "În creștere"]
        for i, g in enumerate(order):
            fields = groups.get(g, [])
            box = tk.Frame(colw[0 if g in ("Rulare", "Preț și notă", "AI", "eMAG") else 1], bg=CARD,
                           highlightbackground=LINE, highlightthickness=1)
            box.pack(fill="x", pady=6)
            tk.Label(box, text=g, bg=CARD, fg=INK, font=(F, 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 6))
            row = 1
            for fd in fields:
                row = self._field(box, fd, row)
            if g == "AI":
                row = self._gemini_row(box, row)
            box.grid_columnconfigure(1, weight=1)
            tk.Frame(box, bg=CARD, height=8).grid(row=row, column=0)
        self.fill_settings()

    def _field(self, box, fd, row):
        t = fd["type"]
        tk.Label(box, text=fd["label"], bg=CARD, fg=INK, font=(F, 10), anchor="w", justify="left", wraplength=230).grid(
            row=row, column=0, sticky="nw", padx=(14, 8), pady=4)
        cell = tk.Frame(box, bg=CARD)
        cell.grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=4)
        if t == "bool":
            var = tk.BooleanVar()
            w = tk.Checkbutton(cell, variable=var, text="Da", bg=CARD, activebackground=CARD, font=(F, 10), anchor="w")
            w.pack(anchor="w")
            get = var.get
            put = var.set
        elif t == "choice":
            var = tk.StringVar()
            w = ttk.Combobox(cell, textvariable=var, values=fd["choices"], state="readonly", width=12, font=(F, 10))
            w.pack(anchor="w")
            get, put = var.get, var.set
        elif t == "list":
            w = tk.Text(cell, height=4, width=30, font=(F, 10), relief="solid", bd=1, highlightthickness=0, wrap="none")
            w.pack(fill="x")
            get = lambda w=w: w.get("1.0", "end").strip()  # noqa: E731
            def put(v, w=w):
                w.delete("1.0", "end")
                w.insert("1.0", "\n".join(v or []))
        else:
            var = tk.StringVar()
            w = tk.Entry(cell, textvariable=var, font=(F, 10), relief="solid", bd=1,
                         width=10 if t in ("int", "float") else 34)
            w.pack(anchor="w" if t in ("int", "float") else "w", fill=None if t in ("int", "float") else "x", ipady=3)
            get = var.get
            put = lambda v, var=var: var.set(", ".join(v) if isinstance(v, list) else ("" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))))  # noqa: E731
        err = tk.Label(cell, text=fd.get("help", ""), bg=CARD, fg=MUTED, font=(F, 8), anchor="w", justify="left", wraplength=320)
        if fd.get("help"):
            err.pack(anchor="w")
        self.widgets[fd["key"]] = (get, put, err, fd)
        return row + 1

    def _gemini_row(self, box, row):
        tk.Label(box, text="Cheie Gemini API", bg=CARD, fg=INK, font=(F, 10), anchor="w").grid(row=row, column=0, sticky="nw", padx=(14, 8), pady=4)
        cell = tk.Frame(box, bg=CARD)
        cell.grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=4)
        self.gem_var = tk.StringVar()
        tk.Entry(cell, textvariable=self.gem_var, show="•", font=(F, 10), relief="solid", bd=1, width=34).pack(fill="x", ipady=3)
        self.gem_lbl = tk.Label(cell, text="", bg=CARD, fg=MUTED, font=(F, 8), anchor="w", justify="left", wraplength=320)
        self.gem_lbl.pack(anchor="w")
        return row + 1

    def _gem_status(self):
        has = bool(self.settings.gemini_key())
        self.gem_lbl.configure(text=("✔ Cheie configurată (nu se afișează). Lasă gol ca să o păstrezi." if has else
                                     "Nicio cheie. Ia una gratuit din aistudio.google.com → Get API key și lipește-o aici."),
                               fg=GREEN if has else AMBER)

    def fill_settings(self, values: dict | None = None):
        cur = values or load_config()
        for k, (get, put, err, fd) in self.widgets.items():
            put(self.settings.get(cur, k))
            err.configure(text=fd.get("help", ""), fg=MUTED)
        self.gem_var.set("")
        self._gem_status()
        self.set_msg.configure(text="")

    def save_settings(self):
        values, bad = {}, []
        for k, (get, put, err, fd) in self.widgets.items():
            try:
                values[k] = self.settings.validate(fd, get())
                err.configure(text=fd.get("help", ""), fg=MUTED)
                if not fd.get("help"):
                    err.pack_forget()
            except ValueError as e:
                bad.append(fd["label"])
                err.configure(text=f"✖ {e}", fg=ACCENT)
                err.pack(anchor="w")
        if values.get("COST_MIN", 0) > values.get("COST_MAX", 10):
            bad.append("Preț minim > preț maxim")
        if bad:
            self.set_msg.configure(text="Corectează: " + ", ".join(bad[:4]), fg=ACCENT)
            return
        key = self.gem_var.get().strip()
        if key:
            try:
                self.settings.save_gemini_key(key)
            except ValueError as e:
                self.set_msg.configure(text=f"Cheia Gemini: {e}", fg=ACCENT)
                return
        defaults = load_config(defaults=True)
        diff = {k: v for k, v in values.items() if v != self.settings.get(defaults, k)}
        old_iv = load_config().get("SCHEDULE_INTERVAL_MIN")
        self.settings.save(diff)
        msg = f"Salvat ({len(diff)} diferite de implicit)."
        if values.get("SCHEDULE_INTERVAL_MIN") != old_iv:
            res = set_interval(values["SCHEDULE_INTERVAL_MIN"])
            msg += " Interval task: " + ("aplicat." if res.startswith("ok") else f"eroare ({res[:80]})")
        self.set_msg.configure(text=msg, fg=GREEN)
        self.fill_settings()
        self.set_msg.configure(text=msg, fg=GREEN)

    def reset_settings(self):
        if not messagebox.askyesno("Ecom Monitor", "Revin la valorile implicite din config.py? (cheia Gemini rămâne)"):
            return
        self.settings.save({})
        self.fill_settings()
        self.set_msg.configure(text="Valorile implicite au fost restaurate.", fg=GREEN)

    # ------------------------------------------------------------------ Ponturi
    def build_tips(self):
        f = self.page("ponturi", "Ponturi", "Produsele găsite în ultimele 30 de zile. Dublu-click deschide pagina magazinului.")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(0, 6))
        self.button(bar, "Magazin", lambda: self._open_selected(self.tips, self.tip_rows, "link"), "dark")
        self.button(bar, "AliExpress", lambda: self._open_selected(self.tips, self.tip_rows, "ali_url"), "light")
        self.button(bar, "Draft Shopify", lambda: self._open_selected(self.tips, self.tip_rows, "shopify"), "light")
        self.button(bar, "↻", self.load_tips, "light")
        self.tip_msg = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(F, 10))
        self.tip_msg.pack(side="left", padx=10)
        c = self.card(f, fill="both", expand=True, pady=(0, 22))
        cols = (("when", "Când", 90), ("nota", "NOTA", 50), ("title", "Produs", 290), ("page", "Magazin", 110),
                ("ali", "Ali (lei)", 65), ("emag", "eMAG", 170), ("tag", "Stare", 120))
        self.tips = ttk.Treeview(c, columns=[x[0] for x in cols], show="headings")
        for col, txt, w in cols:
            self.tips.heading(col, text=txt, anchor="w")
            self.tips.column(col, width=w, anchor="w", stretch=col in ("title", "emag"))
        sb = ttk.Scrollbar(c, orient="vertical", command=self.tips.yview)
        self.tips.configure(yscrollcommand=sb.set)
        self.tips.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb.pack(side="right", fill="y")
        self.tips.tag_configure("rise", foreground=GREEN)
        self.tips.bind("<Double-1>", lambda _e: self._open_selected(self.tips, self.tip_rows))
        self.tip_rows: list[dict] = []

    def load_tips(self):
        self.bg(load_tips, self._fill_tips)

    def _fill_tips(self, rows):
        if isinstance(rows, Exception):
            self.tip_msg.configure(text=f"Eroare: {rows}")
            return
        self.tip_rows = rows
        self.tips.delete(*self.tips.get_children())
        for i, e in enumerate(rows):
            tag = " · ".join(x for x in ("📈 în creștere" if e["rising"] else "", f"trimis {e['sent']}" if e["sent"] else "",
                                         "draft" if e["shopify"] else "") if x)
            try:
                ali = f"{float(e['ali']):.2f}"
            except (TypeError, ValueError):
                ali = str(e["ali"])
            self.tips.insert("", "end", iid=str(i), values=(e["when"], e["nota"], e["title"][:90], e["page"][:24], ali,
                                                           e["emag"].removeprefix("eMAG: "), tag),
                             tags=("rise",) if e["rising"] else ())
        self.tip_msg.configure(text=f"{len(rows)} ponturi")

    # ------------------------------------------------------------------ Loguri
    def build_logs(self):
        f = self.page("loguri", "Loguri", "Urmărire live a ads-monitor\\out\\task.log")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(0, 6))
        self.log_file = tk.StringVar(value="task.log")
        ttk.Combobox(bar, textvariable=self.log_file, values=["task.log", "manual.log"], state="readonly", width=14,
                     font=(F, 10)).pack(side="left", padx=(0, 10))
        self.follow = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Derulare automată", variable=self.follow, bg=BG, activebackground=BG, font=(F, 10)).pack(side="left")
        self.button(bar, "Deschide folderul", lambda: os.startfile(str(OUT)), "light", padx=(10, 0))
        c = self.card(f, fill="both", expand=True, pady=(0, 22))
        self.log_txt = tk.Text(c, font=("Cascadia Mono", 9), bg="#0F1115", fg="#D7DAE0", insertbackground="#FFFFFF",
                               relief="flat", wrap="none", padx=10, pady=8)
        sb = ttk.Scrollbar(c, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        self.log_txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, col in (("ok", "#6EE7A8"), ("err", "#FCA5A5"), ("warn", "#FCD34D"), ("hi", "#93C5FD")):
            self.log_txt.tag_configure(tag, foreground=col)
        self._log_sig = None

    def _log_tick(self, force=False):
        if self.current == "loguri" or force:
            p = OUT / self.log_file.get()
            try:
                sig = (p.stat().st_size, p.stat().st_mtime, p.name)
            except OSError:
                sig = None
            if force or sig != self._log_sig:
                self._log_sig = sig
                lines = tail(p, 500)
                self.log_txt.configure(state="normal")
                self.log_txt.delete("1.0", "end")
                for ln in lines:
                    low = ln.lower()
                    tag = ("err" if ("eroare" in low or "fail" in low or "esuat" in low or "blocheaza" in low) else
                           "ok" if ("trimis" in low or " ok " in low or "pass" in low or "potrivire" in low) else
                           "warn" if ("atentie" in low or "warn" in low or "watchdog" in low) else
                           "hi" if ln.startswith("[run]") else "")
                    self.log_txt.insert("end", ln + "\n", tag)
                self.log_txt.configure(state="disabled")
                if self.follow.get():
                    self.log_txt.see("end")
        if not force:
            self.root.after(2000, self._log_tick)

    # ------------------------------------------------------------------ Test
    def build_test(self):
        f = self.page("test", "Test", "Verifică toate componentele fără să trimită nimic (selftest).")
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", padx=28, pady=(0, 6))
        self.t_btn1 = self.button(bar, "Test rapid (fără AI)", lambda: self.run_test(True), "accent")
        self.t_btn2 = self.button(bar, "Test complet (cu AI)", lambda: self.run_test(False), "dark")
        self.t_res = tk.Label(bar, text="", bg=BG, fg=MUTED, font=(F, 11, "bold"))
        self.t_res.pack(side="left", padx=12)
        c = self.card(f, fill="both", expand=True, pady=(0, 22))
        self.t_txt = tk.Text(c, font=("Cascadia Mono", 9), bg="#0F1115", fg="#D7DAE0", relief="flat", wrap="word", padx=10, pady=8)
        self.t_txt.pack(fill="both", expand=True)
        for tag, col in (("PASS", "#6EE7A8"), ("FAIL", "#FCA5A5"), ("WARN", "#FCD34D")):
            self.t_txt.tag_configure(tag, foreground=col)
        last = OUT / "selftest.json"
        if last.exists():
            try:
                res = json.loads(last.read_text(encoding="utf-8"))
                when = dt.datetime.fromtimestamp(last.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
                self.t_txt.insert("end", f"Ultimul test: {when}\n\n")
                for st, name, info in res:
                    self.t_txt.insert("end", f"{st:4}  {name}: {info}\n", st)
            except (OSError, ValueError):
                pass

    def run_test(self, no_ai: bool):
        self.t_btn1.configure(state="disabled")
        self.t_btn2.configure(state="disabled")
        self.t_txt.delete("1.0", "end")
        self.t_res.configure(text="Rulez… (1-3 minute)", fg=MUTED)
        cmd = [str(PY), "run.py", "--selftest"] + (["--no-ai"] if no_ai else [])

        def work():
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            p = subprocess.Popen(cmd, cwd=str(MONITOR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 encoding="utf-8", errors="replace", env=env, creationflags=CREATE_NO_WINDOW)
            for line in p.stdout:
                self.q.put((self._test_line, line))
            return p.wait()
        self.bg(work, self._test_done)

    def _test_line(self, line: str):
        tag = next((t for t in ("PASS", "FAIL", "WARN") if f"] {t}" in line), "")
        self.t_txt.insert("end", line, tag)
        self.t_txt.see("end")

    def _test_done(self, code):
        self.t_btn1.configure(state="normal")
        self.t_btn2.configure(state="normal")
        ok = code == 0
        self.t_res.configure(text="✔ Totul în regulă (0 FAIL)" if ok else f"✖ Au fost erori (cod {code})", fg=GREEN if ok else ACCENT)


def screenshots(app: App, root: tk.Tk, outdir: Path):
    from PIL import ImageGrab
    outdir.mkdir(parents=True, exist_ok=True)
    root.attributes("-topmost", True)
    seq = [("dashboard", 0), ("setari", 0), ("setari", 0.5), ("setari", 1.0), ("ponturi", 0), ("loguri", 0), ("test", 0)]
    run_test = "--with-test" in sys.argv

    def grab_to(name):
        root.update()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(outdir / name)

    def shot(i=0):
        if i >= len(seq):
            if run_test:
                app.run_test(True)
                wait_test()
            else:
                root.destroy()
            return
        key, scroll = seq[i]
        app.show(key)
        if key == "setari":
            app.set_canvas.yview_moveto(scroll)
        root.update()

        def grab():
            grab_to(f"{i + 1}_{key}" + (f"_{int(scroll * 100)}" if key == "setari" else "") + ".png")
            shot(i + 1)
        root.after(4000 if i == 0 else 2000, grab)

    def wait_test():
        if str(app.t_btn1.cget("state")) == "disabled":
            root.after(2000, wait_test)
            return
        root.after(1000, lambda: (grab_to(f"{len(seq) + 1}_test_rulat.png"), root.destroy()))
    root.after(500, shot)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except tk.TclError:
        pass
    app = App(root)
    if "--screenshots" in sys.argv:
        screenshots(app, root, Path(sys.argv[sys.argv.index("--screenshots") + 1]))
    root.mainloop()


if __name__ == "__main__":
    main()
