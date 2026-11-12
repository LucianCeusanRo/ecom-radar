"""Ecom Radar - builds data/ecom_products.json (+ .js for the offline index.html) from ads-monitor outputs.

READ-ONLY over ads-monitor (never writes there). Real data only: every field comes from ads-monitor files
(run_*.json logs, WhatsApp message text in *.log, out/examples, out/content, out/mockup_*.png, state.json)
or from the competitor's public product page (og:image). Missing values stay null - nothing is invented.

  python build_data.py            # normal (fetches at most a few missing store images)
  python build_data.py --no-fetch # offline, no network
Scheduled every 15 min by Windows task "EcomRadarRefresh".
"""
from __future__ import annotations

import glob
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path

ADS = Path(os.environ.get("ADS_MONITOR_DIR", str(Path(__file__).resolve().parent.parent)))
OUT = ADS / "out"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
IMG = DATA / "img"
CACHE = DATA / "img_cache.json"
NO_FETCH = "--no-fetch" in sys.argv
MAX_FETCH = 15
LOCK = DATA / "build.lock"


def log(*a):
    print("[ecom]", *a, flush=True)


def rtext(p: Path) -> str:
    """utf-8 / utf-8-sig / utf-16 (PowerShell Tee-Object writes UTF-16 LE)."""
    try:
        raw = p.read_bytes()
    except OSError:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 64 and raw[1:64:2].count(0) > 24):
        return _demojibake(raw.decode("utf-16", errors="replace"))
    return raw.decode("utf-8-sig", errors="replace")


def _demojibake(t: str) -> str:
    """PowerShell console logs: UTF-8 bytes shown as cp437/cp850 and saved as UTF-16. Repair line by line."""
    if not any(m in t for m in ("≡ƒ", "─", "╚", "ÔÇ")):
        return t
    out = []
    for line in t.splitlines(True):
        fixed = line
        for cp in ("cp437", "cp850"):
            try:
                fixed = line.encode(cp).decode("utf-8")
                break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        out.append(fixed)
    return "".join(out)


def rjson(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def fnum(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", ".").strip().rstrip("."))
    except ValueError:
        return None


# ---------------------------------------------------------------- ads-monitor config (read by regex, not imported)
def monitor_cfg() -> dict:
    t = rtext(ADS / "config.py")

    def g(name, default):
        m = re.search(r"(?m)^%s\s*=\s*([^\s#]+)" % name, t)
        if not m:
            return default
        v = m.group(1)
        if v in ("True", "False"):
            return v == "True"
        f = fnum(v)
        return f if f is not None else default
    return {"ROAS": g("ROAS", 4.0), "MIN_PROFIT_PCT": g("MIN_PROFIT_PCT", 20.0), "SHIP_ALI": g("SHIP_ALI", 10.0),
            "SHIP_RO": g("SHIP_RO", 20.0), "PLATITOR_TVA": g("PLATITOR_TVA", False), "TVA": g("TVA", 0.21)}


CFG = monitor_cfg()

# Team formula (neplatitor TVA) - same defaults as the dashboard calculator
TEAM = {"delivery": 0.8, "courier": 20.0, "fee": 0.5, "ads_vat": 0.21, "roas": 4.0, "target_pct": 20.0}


def team_profit(price, cost, roas=4.0, n=1, p=TEAM):
    """B=0.8A, E=20N+0.5N, ads=A/ROAS*1.21, H=0.8*cost*N, profit%=(B-E-ads-H)/B."""
    if not price or cost is None:
        return None
    A = price * n
    B = p["delivery"] * A
    E = (p["courier"] + p["fee"]) * n
    ads = A / roas * (1 + p["ads_vat"])
    H = p["delivery"] * cost * n
    prof = B - E - ads - H
    base = B - E - H
    be = (A * (1 + p["ads_vat"]) / base) if base > 0 else None
    den = p["delivery"] * (1 - p["target_pct"] / 100) - (1 + p["ads_vat"]) / roas
    minp = ((p["courier"] + p["fee"] + p["delivery"] * cost) / den) if den > 0 else None
    return {"A": round(A, 2), "B": round(B, 2), "E": round(E, 2), "ads": round(ads, 2), "H": round(H, 2),
            "profit": round(prof, 2), "pct": round(prof / B * 100, 1) if B else None,
            "breakeven_roas": round(be, 2) if be else None, "min_price_target": round(minp, 2) if minp else None}


def monitor_econ(sell, ali):
    """Exact copy of ads-monitor config.profit_at_roas (read-only reimplementation)."""
    if ali is None:
        return None
    ship = CFG["SHIP_ALI"] + CFG["SHIP_RO"]
    tva = CFG["TVA"]
    if CFG["PLATITOR_TVA"]:
        goods, ad_mult, rev_mult = ali, 1.0, 1 / (1 + tva)
    else:
        goods, ad_mult, rev_mult = ali * (1 + tva), 1 + tva, 1.0
    costs = goods + ship
    sell = sell or 0
    ad = sell / CFG["ROAS"] * ad_mult
    profit = sell * rev_mult - costs - ad
    margin = rev_mult - ad_mult / CFG["ROAS"] - CFG["MIN_PROFIT_PCT"] / 100
    return {"profit": round(profit, 2) if sell else None, "pct": round(profit / sell * 100, 1) if sell else None,
            "ad_cost": round(ad, 2) if sell else None, "costs": round(costs, 2),
            "min_price": round(costs / margin, 2) if margin > 0 else None}


def round9(x):
    x = max(19, math.ceil(x))
    return int(math.ceil((x + 1) / 10.0) * 10 - 1)


def monitor_prices(min_price, sell):
    """Same rule as ads-monitor content.prices(): >= profit floor (min 49), aligned to competitor ...9."""
    price = round9(max(min_price or 0, 49))
    if sell:
        price = max(price, int(round((sell + 1) / 10.0) * 10 - 1))
    return price, round9(price * 1.45)


# ---------------------------------------------------------------- WhatsApp message blocks in logs
RUN_HDR = re.compile(r"\[run\]\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+target=(\w+)")
STATUS = re.compile(r"(?m)^\[\d+/\d+\]\s+(.+?)\s+(OK\s+[\d.]+\s+lei)(.*)$")


def parse_block(chunk: str) -> dict | None:
    m = re.search(r"aliexpress\.com/item/(\d{8,})\.html", chunk)
    if not m:
        return None
    d = {"ali_id": m.group(1), "ali_url": "https://www.aliexpress.com/item/%s.html" % m.group(1)}
    lines = [l.rstrip() for l in chunk.splitlines()]
    for i, l in enumerate(lines):
        s = l.strip()
        if "📦" in s and not d.get("title"):
            d["title"] = s.split("📦", 1)[1].strip()
    if not d.get("title"):
        for i, l in enumerate(lines):
            if re.search(r"Produs\s+(PET\s+)?g(a|ă)sit", l) and i + 1 < len(lines):
                d["title"] = re.sub(r"^\S+\s+", "", lines[i + 1].strip()); break
    if re.search(r"Produs\s+PET", chunk):
        d["pet"] = True
    if (x := re.search(r"NOTA\s+(\d+(?:[.,]\d+)?)\s*/\s*10\*?(?:\s*[-–]\s*(.+))?", chunk)):
        d["nota"] = fnum(x.group(1)); d["nota_reason"] = (x.group(2) or "").strip() or None
    if (x := re.search(r"AliExpress:\s*([\d.,]+)\s*lei", chunk)):
        d["ali_price"] = fnum(x.group(1))
    if (x := re.search(r"RO cu \*?([\d.,]+)\s*lei", chunk)):
        d["competitor_price"] = fnum(x.group(1))
    if (x := re.search(r"(?m)^\S+\s+(.+?)\s+·\s+(?:reclam\S*\s+porni\S*|produs nou \S+ magazin din)\s+(\d{4}-\d{2}-\d{2}|-|\S+)", chunk)):
        d["page"] = x.group(1).strip(); d["ad_started"] = x.group(2) if re.match(r"\d{4}-", x.group(2)) else None
    if "produs nou" in chunk and "magazin din" in chunk:
        d["source"] = "Shopify feed"
    if (x := re.search(r"Link reclam\S*:\s*(\S+)", chunk)) or (x := re.search(r"Reclamele active ale magazinului:\s*(\S+)", chunk)):
        u = x.group(1)
        d["ad_url"] = u if u.startswith("http") else None
    if (x := re.search(r"Link magazin:\s*(\S+)", chunk)):
        d["store_url"] = x.group(1) if x.group(1).startswith("http") else None
    if (x := re.search(r"Reclama din (\w+)", chunk)):
        d["country"] = x.group(1)
    if re.search(r"Potrivit pentru Black Friday", chunk):
        d["bf"] = True
        if (x := re.search(r"Black Friday / Craciun\*?\s*-\s*(.+)", chunk)):
            d["bf_reason"] = x.group(1).strip()
    if (x := re.search(r"Scor produs:\s*\*?(\d+)/(\d+)\*?(.*)", chunk)):
        d["score_total"] = int(x.group(1)); d["score_max"] = int(x.group(2))
        sc = {}
        for k, pat in (("wow", r"WOW vizual (\d)/5"), ("problema", r"problema clara (\d)/5"), ("nesaturat", r"nesaturat (\d)/5"),
                       ("upsell", r"upsell/cantitate (\d)/5"), ("compact", r"compact (\d)/5")):
            if (y := re.search(pat, x.group(3))):
                sc[k] = int(y.group(1))
        if sc:
            d["score"] = sc
    if (x := re.search(r"CTA final:\s*[„\"',]*(.+?)[”\"]*\s*$", chunk, re.M)):
        d["cta"] = x.group(1).strip()
    if (x := re.search(r"Verificat (?:automat|doar vizual)[^\n]*?[:(]\s*(.+?)\)?\s*$", chunk, re.M)):
        d["verified"] = x.group(1).strip().rstrip(")")
    # angles (old: "1. *Name* – „hook”"; new: "1. *Name*" + "   - „hook”" lines)
    angles, cur, on = [], None, False
    for l in lines:
        s = l.strip()
        if "Unghiuri de marketing" in s:
            on = True; continue
        if not on:
            continue
        if not s:
            if angles:
                break
            continue
        if (x := re.match(r"^\d+\.\s*\*(.+?)\*\s*(?:[–-]\s*[„\"](.+?)[”\"]?)?\s*$", s)):
            cur = {"name": x.group(1).strip(), "hooks": [x.group(2).strip()] if x.group(2) else []}
            angles.append(cur); continue
        if cur is not None and (x := re.match(r"^[-–•]\s*[„\"',]*(.+?)[”\"]*$", s)):
            cur["hooks"].append(x.group(1).strip()); continue
        if not re.match(r"^\d+\.", s):
            break
    if angles:
        d["angles"] = angles
    if "Pont MANUAL" in chunk:
        d["source"] = "Manual"
        if re.search(r"tiktok", chunk, re.I):
            d["source_detail"] = "TikTok (pont manual)"
    st = STATUS.search(chunk)
    if st:
        d["page"] = d.get("page") or st.group(1).strip()
        d["sent_flag"] = "TRIMIS" in st.group(3)
    return d


def blocks_from_file(p: Path) -> list[dict]:
    t = rtext(p)
    if "aliexpress.com/item/" not in t:
        return []
    mtime = datetime.fromtimestamp(p.stat().st_mtime)
    hdrs = [(m.start(), m.group(1), m.group(2), m.group(3)) for m in RUN_HDR.finditer(t)]
    parts = [(m.start()) for m in re.finditer(r"(?m)^--- POTRIVIRE.*$", t)]
    chunks = []
    if parts:
        for i, s in enumerate(parts):
            e = parts[i + 1] if i + 1 < len(parts) else len(t)
            c = t[s:e]
            st = STATUS.search(c)
            if st:
                c = c[:st.end()]
            chunks.append((s, c))
    else:
        chunks.append((0, t))
    out = []
    for pos, c in chunks:
        b = parse_block(c)
        if not b:
            continue
        h = [x for x in hdrs if x[0] <= pos]
        if h:
            b["found_at"] = "%s %s" % (h[-1][1], h[-1][2]); b["target"] = h[-1][3]
        else:
            b["found_at"] = mtime.strftime("%Y-%m-%d %H:%M"); b["found_at_basis"] = "mtime"
        b["_file"] = p.name
        out.append(b)
    return out


# ---------------------------------------------------------------- run JSON logs
def run_entries():
    ents, days = [], {}
    for f in sorted(glob.glob(str(OUT / "run_*.json"))):
        m = re.search(r"run_(\d{8})_(\d{4})", f)
        if not m:
            continue
        at = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
        d = rjson(Path(f), [])
        if not isinstance(d, list):
            continue
        day = days.setdefault(at.strftime("%Y-%m-%d"), {"date": at.strftime("%Y-%m-%d"), "runs": 0, "checked": 0, "ok": 0, "sent": 0})
        day["runs"] += 1
        for x in d:
            if not isinstance(x, dict):
                continue
            day["checked"] += 1
            if str(x.get("result", "")).startswith("OK"):
                day["ok"] += 1
                if x.get("sent_to") == "prod":
                    day["sent"] += 1
                e = dict(x); e["_at"] = at.strftime("%Y-%m-%d %H:%M"); e["_file"] = Path(f).name
                for k in ("content", "mockup"):
                    if (y := re.search(r"(\d{13,})", str(x.get(k) or ""))):
                        e["ali_id"] = y.group(1)
                if (y := re.search(r"OK\s+([\d.]+)", x["result"])):
                    e["ali_price"] = fnum(y.group(1))
                ents.append(e)
    return ents, sorted(days.values(), key=lambda r: r["date"])


# ---------------------------------------------------------------- images
def thumb(src: Path, name: str, width: int) -> str | None:
    dst = IMG / name
    try:
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            return name
        from PIL import Image
        im = Image.open(src).convert("RGB")
        if im.width > width:
            im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
        tmp = dst.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=84, optimize=True, progressive=True)
        os.replace(tmp, dst)
        return name
    except Exception as e:
        log("thumb fail", src.name, type(e).__name__)
        return None


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
_fetches = [0]


def store_image(url: str, key: str, cache: dict) -> str | None:
    name = "st_%s.jpg" % key
    if (IMG / name).exists():
        return name
    c = cache.get(key) or {}
    if NO_FETCH or not url or _fetches[0] >= MAX_FETCH or (c and time.time() - c.get("ts", 0) < 86400):
        return None
    _fetches[0] += 1
    try:
        from urllib.parse import quote
        url = quote(url, safe=":/?&=%#+@,;~")
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ro-RO,ro;q=0.9"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(800_000).decode("utf-8", "ignore")
        m = (re.search(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)', html)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', html))
        if not m:
            raise ValueError("fara og:image")
        src = m.group(1).replace("&amp;", "&")
        if src.startswith("//"):
            src = "https:" + src
        req = urllib.request.Request(quote(src, safe=":/?&=%#+@,;~"), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(6_000_000)
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > 720:
            im = im.resize((720, int(im.height * 720 / im.width)), Image.LANCZOS)
        im.save(IMG / name, "JPEG", quality=84, optimize=True, progressive=True)
        cache[key] = {"ok": True, "ts": time.time(), "src": src}
        return name
    except Exception as e:
        cache[key] = {"ok": False, "ts": time.time(), "err": "%s: %s" % (type(e).__name__, str(e)[:80])}
        return None


# ---------------------------------------------------------------- merge
# copy of ads-monitor config.Q4_RE (keywords only, used for the "posibil Q4" list)
Q4_RE = (r"\b(cadou\w*|craciun\w*|mos|brad\w*|ornament\w*|decor\w*|iarna|ger|zapad\w*|incalz\w*|termic\w*|"
         r"electric\w*|led|lumin\w*|lamp\w*|proiector\w*|gadget\w*|portabil\w*|wireless|usb|bucatar\w*|"
         r"organizator\w*|pled\w*|patur\w*|sosete|manusi|fular|caciul\w*|parbriz|dezghet\w*|umidificator\w*|"
         r"masaj\w*|set|kit|jucari\w*|copii)\b")


def is_q4(*texts) -> bool:
    t = unicodedata.normalize("NFKD", " ".join(str(x) for x in texts if x)).encode("ascii", "ignore").decode().lower()
    return bool(re.search(Q4_RE, t))


def shopify_drafts() -> dict:
    """Drafturi Shopify create de ads-monitor (out/shopify_drafts.json): aliId -> link admin. Doar pentru admin:
    stau sub payload["monitor_cfg"], pe care ecom_routes nu il trimite vizitatorilor publici."""
    d = rjson(OUT / "shopify_drafts.json", {}) or {}
    return {str(k): {"admin_url": v.get("admin_url"), "created": v.get("created")} for k, v in d.items()
            if isinstance(v, dict) and str(v.get("admin_url", "")).startswith("https://admin.shopify.com/")}


def source_of(p: dict) -> str:
    if p.get("source"):
        return p["source"]
    links = " ".join(str(p.get(k) or "") for k in ("ad_url", "store_url", "page", "key"))
    if re.search(r"bazarul", links, re.I):
        return "Bazarul"
    if re.search(r"tiktok", links, re.I):
        return "TikTok"
    if re.search(r"facebook\.com|fb\.com", links, re.I):
        return "Meta"
    return "Meta"  # ads-monitor default pipeline = Meta Ad Library


def build():
    DATA.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)
    cache = rjson(CACHE, {}) or {}
    state = rjson(ADS / "state.json", {}) or {}
    sent_items = state.get("sent_items") or {}

    files = sorted(set(glob.glob(str(ADS / "*.log")) + glob.glob(str(OUT / "*.log")) +
                   glob.glob(str(OUT / "examples" / "*.txt")) + [str(OUT / "coop_tip_text.txt")]))
    blocks = []
    for f in files:
        p = Path(f)
        if p.exists():
            blocks += blocks_from_file(p)
    runs, days = run_entries()

    prods: dict[str, dict] = {}

    def upd(p, d, prefer=False):
        for k, v in d.items():
            if k.startswith("_") or v in (None, "", [], {}):
                continue
            if prefer or p.get(k) in (None, "", [], {}):
                p[k] = v

    # 1) message blocks (have ali id)
    for b in blocks:
        p = prods.setdefault(b["ali_id"], {"ali_id": b["ali_id"], "files": []})
        p["files"].append(b["_file"])
        sent = bool(b.get("sent_flag")) and b.get("target") in (None, "prod")
        if sent:
            p["sent"] = True
        # keep earliest found_at, but prefer data from the most complete block
        fa = b.get("found_at")
        if fa and (not p.get("found_at") or fa < p["found_at"]):
            p["found_at"] = fa
        upd(p, {k: v for k, v in b.items() if k not in ("found_at", "sent_flag", "target")})
        if b.get("angles") and any(a.get("hooks") and len(a["hooks"]) > 1 for a in b["angles"]):
            p["angles"] = b["angles"]
    by_title = {}
    for aid, p in prods.items():
        by_title.setdefault(norm(p.get("title"))[:50], aid)

    # 2) run JSON logs (dates, sent_to, profit, score/nota, bf, content/mockup paths)
    for e in runs:
        aid = e.get("ali_id") or by_title.get(norm(e.get("title"))[:50])
        if not aid:
            aid = "k_" + norm((e.get("key") or e.get("title") or ""))[:60]
        p = prods.setdefault(aid, {"ali_id": aid if aid.isdigit() else None, "files": []})
        p["files"].append(e["_file"])
        if e.get("sent_to") == "prod":
            p["sent"] = True
            p["sent_to"] = "prod"
        if not p.get("found_at") or e["_at"] < p["found_at"] or p.get("found_at_basis") == "mtime":
            p["found_at"] = e["_at"]; p.pop("found_at_basis", None)
        card = e.get("score") or {}
        upd(p, {"title": e.get("title"), "page": e.get("page"), "ad_started": e.get("started"),
                "key": e.get("key"), "ali_price": e.get("ali_price"), "monitor_profit_pct": e.get("profit_pct"),
                "bf": e.get("black_friday") or None, "nota": card.get("nota"), "nota_reason": card.get("nota_reason"),
                "score": {k: card[k] for k in ("wow", "problema", "nesaturat", "upsell", "compact") if k in card} or None,
                "judge": e.get("judge")})
        if e.get("angles") and not p.get("angles"):
            p["angles"] = [{"name": a, "hooks": []} for a in e["angles"]]
        if not p.get("store_url") and e.get("key"):
            p["store_url"] = "https://" + e["key"]

    # 3) examples (rich: nota, recommended price) + content files
    for f in glob.glob(str(OUT / "examples" / "*.json")):
        ex = rjson(Path(f), {}) or {}
        aid = str(ex.get("ali_id") or "")
        if not aid:
            continue
        p = prods.setdefault(aid, {"ali_id": aid, "files": []})
        p["files"].append("examples/" + Path(f).name)
        upd(p, {"nota": ex.get("nota"), "nota_reason": ex.get("nota_reason"), "competitor_price": ex.get("competitor_price"),
                "store_url": ex.get("store"), "ali_price": ex.get("ali_price"), "ali_url": ex.get("ali_url"),
                "score": {k: v for k, v in (ex.get("score") or {}).items() if v is not None} or None}, prefer=True)
        if ex.get("bf") is not None:
            p["bf"] = bool(ex.get("bf")) or bool(p.get("bf"))
        if ex.get("recommended"):
            p["recommended_price"] = ex["recommended"]; p["compare_at"] = ex.get("compare_at"); p["price_basis"] = "continut ads-monitor"

    for aid, p in prods.items():
        if not p.get("ali_id"):
            continue
        cj = OUT / "content" / ("content_%s.json" % aid)
        if cj.exists():
            c = rjson(cj, {}) or {}
            p["content"] = {k: c.get(k) for k in ("title", "hook", "bullets", "tagline", "description", "how_to", "faq", "price", "compare_at", "engine")}
            p["content"]["txt"] = rtext(cj.with_suffix(".txt")) or None
            p["content"]["html"] = rtext(cj.with_suffix(".html")) or None
            if c.get("price"):
                p["recommended_price"] = c["price"]; p["compare_at"] = c.get("compare_at"); p["price_basis"] = "continut ads-monitor"
            p["shop_title"] = c.get("title")

    # 4) derived (formulas only, on real inputs) + images
    # dev dry-runs (dry*.log + the run_*.json written in the same minutes): first tests on 21.09, prices not validated
    dry_files = set()
    for f in glob.glob(str(ADS / "dry*.log")):
        dry_files.add(Path(f).name)
        mt = datetime.fromtimestamp(Path(f).stat().st_mtime)
        for r in glob.glob(str(OUT / ("run_%s_*.json" % mt.strftime("%Y%m%d")))):
            m = re.search(r"run_(\d{8})_(\d{4})", r)
            rt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
            if abs((rt - mt).total_seconds()) <= 600:
                dry_files.add(Path(r).name)
    out, excluded = [], []
    for aid, p in prods.items():
        if p.get("ali_price") is None:
            continue
        fl = set(p.get("files") or [])
        if fl and all(f in dry_files for f in fl) and not p.get("sent") and aid not in sent_items:
            # only seen in the first dev dry-runs (21.09, before prices were validated): not shown
            excluded.append({"id": aid, "title": p.get("title"), "files": sorted(fl)})
            continue
        if p.get("ali_id") and not p.get("ali_url"):
            p["ali_url"] = "https://www.aliexpress.com/item/%s.html" % p["ali_id"]
        if p.get("ali_id") and p["ali_id"] in sent_items:
            p["sent"] = True
            p["sent_date"] = sent_items[p["ali_id"]]
        econ = monitor_econ(p.get("competitor_price"), p["ali_price"])
        p["monitor_econ"] = econ
        if not p.get("recommended_price"):
            rp, ca = monitor_prices(econ and econ["min_price"], p.get("competitor_price"))
            p["recommended_price"], p["compare_at"], p["price_basis"] = rp, ca, "regula ads-monitor (calculat)"
        p["team"] = team_profit(p["recommended_price"], p["ali_price"], TEAM["roas"])
        p["source"] = source_of(p)
        p["q4_kw"] = is_q4(p.get("title"), p.get("shop_title"))
        p.setdefault("source_detail", {"Meta": "Meta Ad Library", "Shopify feed": "Magazin Shopify (produs nou)"}.get(p["source"]))
        imgs = []
        if p.get("ali_id"):
            mk = OUT / ("mockup_%s.png" % p["ali_id"])
            if mk.exists() and (n := thumb(mk, "mk_%s.jpg" % p["ali_id"], 720)):
                imgs.append({"src": n, "kind": "mockup", "label": "Mockup telefon"})
            mt = OUT / ("match_%s.jpg" % p["ali_id"])
            if mt.exists() and (n := thumb(mt, "mt_%s.jpg" % p["ali_id"], 900)):
                imgs.append({"src": n, "kind": "match", "label": "Magazin vs AliExpress"})
        key = p.get("ali_id") or re.sub(r"[^A-Za-z0-9_-]", "", aid)[:60]
        if p.get("store_url"):
            if (n := store_image(p["store_url"], key, cache)):
                imgs.append({"src": n, "kind": "store", "label": "Poza magazin concurent"})
        p["images"] = imgs
        p["id"] = key
        p["found_date"] = (p.get("found_at") or "")[:10] or None
        p["files"] = sorted(set(p.get("files") or []))
        out.append(p)

    out.sort(key=lambda x: (x.get("found_at") or ""), reverse=True)
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tz": "Europe/Bucharest",
               "source_dir": str(ADS), "monitor_cfg": dict(CFG, shopify_drafts=shopify_drafts()), "team_formula": TEAM, "products": out,
               "runs_by_day": days[-60:], "last_run": (runs[-1]["_at"] if runs else None),
               "counts": {"products": len(out), "sent": sum(1 for x in out if x.get("sent")),
                          "with_nota": sum(1 for x in out if x.get("nota") is not None),
                          "with_image": sum(1 for x in out if x.get("images")), "log_files": len(files),
                          "excluded_dev_dry_runs": len(excluded)}, "excluded": excluded}
    last_runs = sorted(glob.glob(str(OUT / "run_*.json")))
    if last_runs:
        m = re.search(r"run_(\d{8})_(\d{4})", last_runs[-1])
        payload["last_run"] = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M")
    js = json.dumps(payload, ensure_ascii=False)
    for name, body in (("ecom_products.json", js), ("ecom_products.js", "window.ECOM_DATA=" + js + ";\n")):
        tmp = DATA / (name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, DATA / name)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    # offline copy: index.html = app.html + local data script
    app = rtext(HERE / "app.html")
    if app:
        (HERE / "index.html").write_text(app.replace("<!--ECOM_DATA-->", '<script src="data/ecom_products.js"></script>'),
                                         encoding="utf-8")
    log("ok: %d produse (%d trimise, %d cu nota, %d cu poza), %d fisiere log, fetch=%d" % (
        len(out), payload["counts"]["sent"], payload["counts"]["with_nota"], payload["counts"]["with_image"], len(files), _fetches[0]))
    return payload


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 600:
        log("alt build ruleaza; ies"); sys.exit(0)
    LOCK.write_text(str(os.getpid()))
    try:
        build()
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
