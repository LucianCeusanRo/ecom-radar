"""Ecom Radar tab for the desktop app "Supreme Punter" (supreme_supervisor.pyw --panel).

Loaded by path from the panel; everything is inside try/except there, so a problem here never breaks the panel.
Reads data/ecom_products.json (built by build_data.py, scheduled every 15 min). Real data only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "data" / "ecom_products.json"
NO_WINDOW = 0x08000000
MAX_ROWS = 400  # plafon randuri in Treeview (panelul ramane fluid)
TEAM = {"delivery": 0.8, "courier": 20.0, "fee": 0.5, "ads_vat": 0.21, "roas": 4.0, "target_pct": 20.0}


def team_profit(price, cost, roas=4.0):
    if not price or cost is None:
        return None
    p = TEAM
    B = p["delivery"] * price
    prof = B - (p["courier"] + p["fee"]) - price / roas * (1 + p["ads_vat"]) - p["delivery"] * cost
    return prof, (prof / B * 100 if B else None)


def content_text(p: dict) -> str:
    c = p.get("content") or {}
    if c.get("txt"):
        return c["txt"]
    lines = ["TITLU: %s" % (c.get("title") or p.get("title") or ""),
             "PRET: %s lei%s" % (p.get("recommended_price") or "", "   (compare-at: %s lei)" % p["compare_at"] if p.get("compare_at") else ""), ""]
    for i, a in enumerate(p.get("angles") or [], 1):
        lines.append("%d. %s" % (i, a.get("name")))
        lines += ["   - %s" % h for h in a.get("hooks") or []]
    lines += ["", "(Continut Shopify complet negenerat de ads-monitor pentru acest produs.)"]
    return "\n".join(lines)


def build(parent, root, colors: dict | None = None, site: str = "http://127.0.0.1:8765/"):
    import tkinter as tk
    from tkinter import ttk

    c = {"BG": "#F2F2F4", "PANEL": "#FFFFFF", "TXT": "#111114", "MUTED": "#6B6B76", "ACC": "#E3001B", "EV": "#0A8F4E", "CRIT": "#C8102E"}
    c.update(colors or {})
    state = {"mt": None, "rows": [], "by_iid": {}, "sort": ("found_at", True)}

    head = tk.Frame(parent, bg=c["BG"])
    head.pack(fill="x", padx=14, pady=(12, 4))
    tk.Label(head, text="Ecom Radar", bg=c["BG"], fg=c["TXT"], font=("Segoe UI Semibold", 16)).pack(side="left")
    tk.Label(head, text="  produse gasite de ads-monitor (nu tine de pariuri)", bg=c["BG"], fg=c["MUTED"],
             font=("Segoe UI", 10)).pack(side="left", pady=(5, 0))
    info = tk.Label(parent, text="Se incarca...", bg=c["BG"], fg=c["MUTED"], font=("Segoe UI", 10), justify="left", anchor="w")
    info.pack(fill="x", padx=16)

    bar = tk.Frame(parent, bg=c["BG"])
    bar.pack(fill="x", padx=12, pady=6)

    def btn(text, cmd, primary=False):
        b = tk.Button(bar, text=text, command=cmd, font=("Segoe UI Semibold", 10), padx=12, pady=5, relief="flat",
                      bg=c["ACC"] if primary else c["PANEL"], fg="#FFFFFF" if primary else c["TXT"],
                      activebackground=c["ACC"], cursor="hand2", bd=1)
        b.pack(side="left", padx=3)
        return b

    def rebuild():
        py = str(Path(sys.executable).with_name("pythonw.exe"))
        if not Path(py).exists():
            py = sys.executable
        subprocess.Popen([py, str(HERE / "build_data.py")], cwd=str(HERE), creationflags=NO_WINDOW, close_fds=True)
        info.config(text="Reconstruiesc datele din ads-monitor/out ... (cateva secunde)")
        root.after(8000, lambda: reload(force=True))

    btn("Deschide dashboard online", lambda: webbrowser.open(site.rstrip("/") + "/ecom"), primary=True)
    btn("Deschide offline (index.html)", lambda: os.startfile(str(HERE / "index.html")))
    btn("Reconstruieste datele", rebuild)
    btn("Copiaza continut Shopify", lambda: copy_sel())
    btn("Folder", lambda: os.startfile(str(HERE)))

    fbar = tk.Frame(parent, bg=c["BG"])
    fbar.pack(fill="x", padx=14, pady=(2, 4))
    tk.Label(fbar, text="Cauta:", bg=c["BG"], fg=c["TXT"], font=("Segoe UI", 10)).pack(side="left")
    q = tk.StringVar()
    tk.Entry(fbar, textvariable=q, width=34, font=("Segoe UI", 10)).pack(side="left", padx=(4, 12))
    tk.Label(fbar, text="Sursa:", bg=c["BG"], fg=c["TXT"], font=("Segoe UI", 10)).pack(side="left")
    src = tk.StringVar(value="Toate")
    src_cb = ttk.Combobox(fbar, textvariable=src, values=["Toate"], width=14, state="readonly")
    src_cb.pack(side="left", padx=4)
    only_sent = tk.BooleanVar(value=False)
    tk.Checkbutton(fbar, text="doar trimise", variable=only_sent, bg=c["BG"], command=lambda: fill()).pack(side="left", padx=8)
    tk.Label(fbar, text="Dublu-click = AliExpress", bg=c["BG"], fg=c["MUTED"], font=("Segoe UI", 9)).pack(side="right")

    cols = (("found_at", "Gasit", 110), ("title", "Produs", 330), ("nota", "Nota", 55), ("ali_price", "Ali (lei)", 70),
            ("recommended_price", "Vinde la", 70), ("profit", "Profit/cmd @4", 95), ("pct", "Profit %", 70),
            ("source", "Sursa", 90), ("sent", "Trimis", 60))
    wrap = tk.Frame(parent, bg=c["BG"])
    wrap.pack(fill="both", expand=True, padx=12, pady=(0, 10))
    tv = ttk.Treeview(wrap, columns=[k for k, _, _ in cols], show="headings", height=18)
    for k, t, w in cols:
        tv.heading(k, text=t, command=lambda k=k: sort_by(k))
        tv.column(k, width=w, anchor="w" if k in ("title", "found_at", "source") else "e", stretch=(k == "title"))
    sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    tv.pack(side="left", fill="both", expand=True)
    tv.tag_configure("neg", foreground=c["CRIT"])
    tv.tag_configure("top", foreground=c["EV"])

    def row_vals(p):
        tp = team_profit(p.get("recommended_price"), p.get("ali_price"))
        return {"found_at": p.get("found_at") or "", "title": p.get("title") or "", "nota": p.get("nota"),
                "ali_price": p.get("ali_price"), "recommended_price": p.get("recommended_price"),
                "profit": round(tp[0], 1) if tp else None, "pct": round(tp[1], 1) if tp and tp[1] is not None else None,
                "source": p.get("source") or "", "sent": "da" if p.get("sent") else "-"}

    def sort_by(k):
        cur, rev = state["sort"]
        state["sort"] = (k, not rev if cur == k else k in ("found_at", "nota", "profit", "pct", "recommended_price"))
        fill()

    def fill():
        tv.delete(*tv.get_children())
        state["by_iid"].clear()
        k, rev = state["sort"]
        needle = q.get().strip().lower()
        rows = [(p, row_vals(p)) for p in state["rows"]]
        rows = [r for r in rows if (src.get() == "Toate" or r[1]["source"] == src.get())
                and (not only_sent.get() or r[0].get("sent"))
                and (not needle or needle in (r[1]["title"] + " " + str(r[0].get("page") or "")).lower())]

        def key(r):
            v = r[1].get(k)
            return (v is not None, v if v is not None else 0) if not isinstance(v, str) else (True, v.lower())
        rows.sort(key=key, reverse=rev)
        for p, v in rows[:MAX_ROWS]:
            tag = "neg" if (v["profit"] is not None and v["profit"] < 0) else ("top" if (v["nota"] or 0) >= 7 else "")
            iid = tv.insert("", "end", values=[("-" if v[x] is None else v[x]) for x, _, _ in cols], tags=(tag,))
            state["by_iid"][iid] = p

    def selected():
        sel = tv.selection()
        return state["by_iid"].get(sel[0]) if sel else None

    def copy_sel():
        p = selected()
        if not p:
            info.config(text="Selecteaza un produs din lista, apoi Copiaza continut Shopify.")
            return
        root.clipboard_clear()
        root.clipboard_append(content_text(p))
        info.config(text="Copiat in clipboard: %s" % (p.get("title") or "")[:80])

    def open_ali(_=None):
        p = selected()
        u = (p or {}).get("ali_url") or (p or {}).get("store_url")
        if u and str(u).startswith("http"):
            webbrowser.open(u)

    tv.bind("<Double-1>", open_ali)
    def fill_later(*_):  # debounce: tastarea in cautare nu reconstruieste lista la fiecare litera
        if state.get("after"):
            try:
                root.after_cancel(state["after"])
            except Exception:
                pass
        state["after"] = root.after(250, fill)
    q.trace_add("write", fill_later)
    src_cb.bind("<<ComboboxSelected>>", lambda _=None: fill())

    def apply(d, mt):
        state["mt"] = mt
        state["rows"] = [p for p in d.get("products") or [] if p.get("ali_price") is not None]
        srcs = sorted({p.get("source") or "" for p in state["rows"]} - {""})
        src_cb.configure(values=["Toate"] + srcs)
        cnt = d.get("counts") or {}
        info.config(text="%d produse (%s trimise pe grup, %s cu nota) - date generate %s, ultima rulare ads-monitor %s. "
                         "Profit = formula echipei, neplatitor TVA, ROAS 4." % (
                             len(state["rows"]), cnt.get("sent", "?"), cnt.get("with_nota", "?"),
                             d.get("generated_at") or "-", d.get("last_run") or "-"))
        fill()

    def reload(force=False):
        """Citirea + parsarea JSON-ului ruleaza intr-un thread; UI-ul se actualizeaza doar pe firul Tk (root.after)."""
        if state.get("loading"):
            return
        try:
            mt = DATA_FILE.stat().st_mtime_ns
        except OSError:
            info.config(text="Nu exista inca %s. Apasa Reconstruieste datele." % DATA_FILE)
            return
        if not force and mt == state["mt"]:
            return
        state["loading"] = True

        box = {}

        def work():  # fara apeluri Tk aici: doar citire + parsare
            try:
                box["d"] = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            except Exception as exc:
                box["err"] = "Nu pot citi datele: %s" % type(exc).__name__
            finally:
                box["done"] = True

        def poll():  # pe firul Tk
            if not box.get("done"):
                root.after(100, poll)
                return
            state["loading"] = False
            if "d" in box:
                apply(box["d"], mt)
            else:
                info.config(text=box.get("err", "Nu pot citi datele"))
        threading.Thread(target=work, name="ecom-load", daemon=True).start()
        root.after(100, poll)

    def tick():
        try:
            if parent.winfo_ismapped() or state["mt"] is None:  # tab ascuns: nu reincarcam degeaba
                reload()
        finally:
            root.after(60000, tick)

    root.after(1500, tick)  # incarcare lenesa: panelul porneste instant, datele vin dupa
    return {"reload": reload}
