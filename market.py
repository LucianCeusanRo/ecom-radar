"""Semnale de piata pentru scor (19 oct 2026):
  - eMAG: cate oferte relevante exista deja si la ce pret (saturare pentru clientul care compara);
  - public 30+: categoriile cumparate de oameni seriosi (animale, casa, bucatarie, gradina, auto, sanatate, DIY)
    urca, moda / lenjerie / gadgeturi de trend coboara;
  - "in crestere": reclame pornite recent, numar de reclame care creste intre rulari (history.db), penalizare
    pentru produsele care ruleaza de luni de zile sau sunt vandute de multe magazine (aceeasi oferta AliExpress).
Toate se folosesc doar cu date colectate real; orice esec => fara ajustare (nu blocheaza pontul).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import random
import re
import sqlite3
import time
import unicodedata
from urllib.parse import quote

import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


def ascii_low(*t: str) -> str:
    return unicodedata.normalize("NFKD", " ".join(x for x in t if x)).encode("ascii", "ignore").decode().lower()


# ------------------------------------------------------------------ public 30+
CATEGORY_RX = {
    "pet": r"\b(cain\w*|catel\w*|pisic\w*|animal\w*|pet|pets|lesa|zgard\w*|litier\w*|acvari\w*|purici|blana|dog|cat|puppy)\b",
    "casa": r"\b(curat\w*|mop|aspirator\w*|praf|pete|calcar|anticalcar|detergent\w*|organiz\w*|depozit\w*|raft\w*|sertar\w*|"
            r"dulap\w*|baie|dus|toaleta|wc|perete|carlig\w*|casa|casei|locuint\w*|umerase|rufe|haine de uscat|uscator|"
            r"mucegai|umiditate|dezumidific\w*|lavet\w*|burete\w*)\b",
    "bucatarie": r"\b(bucatar\w*|cutit\w*|tigai\w*|oala|oale|razatoare|feliator\w*|tocator\w*|condiment\w*|cafea|ceai|"
                 r"aragaz\w*|cuptor\w*|vase|farfuri\w*|tava|tavi|blender|mixer|patiser\w*|alimente|frigider\w*)\b",
    "gradina": r"\b(gradin\w*|plant\w*|ghiveci\w*|furtun\w*|iarba|gazon|seminte|stropit\w*|irig\w*|curte|terasa|balcon|"
               r"solar\w*|daunator\w*|insect\w*|tantar\w*|cartita)\b",
    "auto": r"\b(auto|masin\w*|parbriz\w*|volan\w*|anvelop\w*|caroserie|portbagaj\w*|sofer\w*|bord|scaun auto|zgarieturi)\b",
    "sanatate": r"\b(masaj\w*|masor\w*|ortoped\w*|spate|coloana|postur\w*|genunchi|talp\w*|perna|perne|somn|durere\w*|"
                r"relax\w*|termofor\w*|saltea|incalz\w*|circulati\w*|varice|articulat\w*|tensiune|reumat\w*|confort)\b",
    "diy": r"\b(surubelnit\w*|burghi\w*|bormasin\w*|scul\w*|cheie|chei|clest\w*|adeziv\w*|lipici|repar\w*|fixare|"
           r"nivela|ruleta|lanterna|atelier|bricolaj|polizor\w*|fierastrau|silicon|izolat\w*)\b",
    "gadget": r"\b(gaming|gamer|rgb|tiktok|fidget|anime|kawaii|selfie|vape|airpods|casti|smartwatch|husa telefon|"
              r"folie|trending|viral\w*|neon|proiector galaxie|mini ventilator)\b",
    "adolescenti": r"\b(adolesc\w*|teen\w*|ghiozdan\w*|kpop|k-pop|scoala|liceu)\b",
    "fashion": r"\b(rochi\w*|bluz\w*|tricou\w*|blugi|pantalon\w*|fust\w*|geac\w*|hanorac\w*|colier\w*|cercei|bratar\w*|"
               r"inel\w*|bijuter\w*|machiaj|gene|unghii|parfum\w*|sandale|pantofi|adidasi|cizme|poset\w*|geanta)\b",
    "lenjerie": r"\b(sutien\w*|lenjeri\w*|chilot\w*|tanga|corset\w*|push ?up|lingerie|bra|body modelator|"
                r"marime|marimi|xxl|xl|s/m|m/l|l/xl)\b",
}
CATEGORY_NAMES = {"pet": "animale", "casa": "casă", "bucatarie": "bucătărie", "gradina": "grădină", "auto": "auto",
                  "sanatate": "sănătate/confort", "diy": "bricolaj", "gadget": "gadget de trend",
                  "adolescenti": "adolescenți", "fashion": "modă", "lenjerie": "lenjerie/mărimi"}


def audience(*texts: str) -> dict:
    """{"cats": [...], "weight": float, "label": str}. Pondere = cea mai buna categorie pozitiva + cea mai rea negativa."""
    t = ascii_low(*texts).replace("-", " ")
    w = getattr(config, "CATEGORY_WEIGHTS", {})
    cats = [k for k, rx in CATEGORY_RX.items() if re.search(rx, t)]
    pos = max([w.get(k, 0) for k in cats if w.get(k, 0) > 0] or [0])
    neg = min([w.get(k, 0) for k in cats if w.get(k, 0) < 0] or [0])
    weight = pos + neg if getattr(config, "AUDIENCE_ENABLED", True) else 0.0
    return {"cats": cats, "weight": round(weight, 2),
            "label": ", ".join(CATEGORY_NAMES.get(k, k) for k in cats) or "general"}


# ------------------------------------------------------------------ istoric (in crestere)
def _db():
    con = sqlite3.connect(str(getattr(config, "HISTORY_DB", config.ROOT / "history.db")), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS snap (key TEXT, day TEXT, ads INT, new7 INT, first TEXT, last TEXT, page TEXT,"
                " PRIMARY KEY(key, day))")
    con.execute("CREATE TABLE IF NOT EXISTS ali_seen (item_id TEXT, key TEXT, page TEXT, day TEXT, PRIMARY KEY(item_id, key))")
    return con


def record_snapshot(stats: dict) -> int:
    """Salveaza, pentru fiecare produs vazut in reclamele de azi: nr. reclame active, reclame noi (7 zile), prima/ultima."""
    if not stats:
        return 0
    day = dt.date.today().isoformat()
    try:
        con = _db()
        with con:
            for key, s in stats.items():
                con.execute("INSERT INTO snap VALUES (?,?,?,?,?,?,?) ON CONFLICT(key, day) DO UPDATE SET "
                            "ads=max(ads, excluded.ads), new7=max(new7, excluded.new7), "
                            "first=min(first, excluded.first), last=max(last, excluded.last)",
                            (key, day, s["ads"], s["new7"], s["first"], s["last"], s.get("page", "")))
            con.execute("DELETE FROM snap WHERE day < ?", ((dt.date.today() - dt.timedelta(days=60)).isoformat(),))
        con.close()
    except sqlite3.Error as e:
        print(f"[market] istoric indisponibil: {e}", flush=True)
        return 0
    return len(stats)


def previous(key: str) -> dict | None:
    """Cea mai veche observatie din ultimele 14 zile (inainte de azi) pentru produs."""
    try:
        con = _db()
        row = con.execute("SELECT day, ads, new7 FROM snap WHERE key=? AND day<? AND day>=? ORDER BY day LIMIT 1",
                          (key, dt.date.today().isoformat(), (dt.date.today() - dt.timedelta(days=14)).isoformat())).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    return {"day": row[0], "ads": row[1], "new7": row[2]} if row else None


def note_ali(item_id: str, key: str, page: str) -> int:
    """Memoreaza ce magazin vinde oferta AliExpress; intoarce cate magazine DIFERITE au aceeasi oferta (istoric)."""
    try:
        con = _db()
        with con:
            con.execute("INSERT OR IGNORE INTO ali_seen VALUES (?,?,?,?)", (str(item_id), key, page or "", dt.date.today().isoformat()))
        n = con.execute("SELECT COUNT(DISTINCT COALESCE(NULLIF(page,''), key)) FROM ali_seen WHERE item_id=?", (str(item_id),)).fetchone()[0]
        con.close()
        return int(n)
    except sqlite3.Error:
        return 1


def rising(c: dict, advertisers: int | None = None) -> dict:
    """Semnal "in crestere" din datele reale: varsta reclamelor, reclame noi, crestere fata de rularile anterioare."""
    out = {"adj": 0.0, "rising": False, "notes": []}
    if not getattr(config, "RISING_ENABLED", True):
        return out
    s = c.get("ad_stats") or {}
    today = dt.date.today()
    try:
        first = dt.date.fromisoformat(s.get("first") or c.get("started"))
    except (TypeError, ValueError):
        first = None
    age = (today - first).days if first else c.get("days_running")
    fresh = age is not None and config.RISING_MIN_DAYS <= age <= config.RISING_MAX_DAYS
    new7 = s.get("new7", 0)
    prev = previous(c["key"]) if s else None
    grew = bool(prev and s.get("ads", 0) > (prev.get("ads") or 0))
    growing = grew or new7 >= config.RISING_MIN_NEW_ADS
    if fresh:
        out["adj"] += 1
        out["notes"].append(f"pornit de {age} zile")
    if growing:
        out["adj"] += 1
        out["notes"].append(f"reclame {prev['ads']}→{s.get('ads')} din {prev['day']}" if grew
                            else f"{new7} reclame noi în 7 zile")
    if s.get("ads"):
        out["ads"] = s["ads"]
    out["rising"] = fresh and growing or (growing and age is not None and age <= config.RISING_MAX_DAYS * 2)
    if age is not None and age >= config.SATURATED_DAYS:
        out["adj"] -= 2
        out["rising"] = False
        out["notes"].append(f"reclame vechi de {age} zile (saturat)")
    if advertisers and advertisers >= config.SATURATED_ADVERTISERS:
        out["adj"] -= 2
        out["rising"] = False
        out["notes"].append(f"{advertisers} magazine cu aceeași ofertă")
    out["age"] = age
    out["advertisers"] = advertisers
    return out


def prio(c: dict) -> float:
    """Ordinea de verificare (inainte de pagina produsului): public 30+ + semnale de crestere, din datele reclamei."""
    p = audience(c.get("ad_title", ""), c.get("link_url", "").rsplit("/", 1)[-1], c.get("page_name", ""))["weight"]
    s = c.get("ad_stats") or {}
    age = c.get("days_running") or 0
    if getattr(config, "RISING_ENABLED", True):
        if config.RISING_MIN_DAYS <= age <= config.RISING_MAX_DAYS:
            p += 1
        if s.get("new7", 0) >= config.RISING_MIN_NEW_ADS:
            p += 1
        if age >= config.SATURATED_DAYS:
            p -= 2
    if c.get("pet"):
        p += 0.5
    if c.get("q4"):
        p += 0.5
    return p


# ------------------------------------------------------------------ eMAG
STOP = {"pentru", "cu", "din", "si", "de", "la", "in", "pe", "fara", "set", "buc", "bucati", "bucata", "oferta", "promo",
        "reducere", "nou", "noua", "original", "premium", "pro", "max", "plus", "super", "ultra", "mini", "the", "and",
        "for", "with", "un", "o", "al", "ale", "lui", "sau", "tip", "model", "calitate", "cadou", "gratuit", "livrare",
        "transport", "pachet", "ramburs", "azi", "doar", "acum", "lei", "multifunctional", "multifunctionala", "portabil",
        "portabila", "inteligent", "inteligenta", "smart", "rapid", "rapida", "usor", "usoara", "eficient", "eficienta",
        "profesional", "profesionala", "imbunatatit", "imbunatatita", "2026", "2025", "marime", "marimi",
        "culoare", "culori", "bucata", "bucati", "produs", "accesoriu", "accesorii"}
STATE = {"blocked": False, "requests": 0}
_CACHE: dict | None = None
CACHE_FILE = config.OUT / "emag_cache.json"


def emag_query(title: str) -> list[str]:
    """Cuvintele cheie pentru eMAG din titlul RO: segmentul descriptiv, fara brand, cuvinte de umplutura si cifre."""
    segs = [x.strip() for x in re.split(r"\s+[–—|:-]\s+|\s*[|(\[]\s*", title or "") if x.strip()]
    best: list[str] = []
    for seg in segs or [title or ""]:
        words = [w for w in re.findall(r"[a-z]{3,}", ascii_low(seg)) if w not in STOP]
        if len(words) > len(best):
            best = words
    seen, out = set(), []
    for w in best:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:4]


def _price(chunk: str) -> float | None:
    m = re.search(r'product-new-price[^>]*>\s*([\d.]+)\s*<sup>(?:<small[^>]*>[^<]*</small>)?\s*(\d{2})', chunk)
    if m:
        return float(m.group(1).replace(".", "") + "." + m.group(2))
    m = re.search(r'product-new-price[^>]*>\s*([\d.]+)', chunk)
    return float(m.group(1).replace(".", "")) if m else None


def parse_emag(page: str) -> list[dict]:
    out = []
    for chunk in page.split('<div class="card-item')[1:]:
        name = re.search(r'data-name="([^"]+)"', chunk)
        if not name:
            continue
        cat = re.search(r'data-category-name="([^"]*)"', chunk)
        url = re.search(r'href="(https://www\.emag\.ro/[^"]+/pd/[^"/]+/?)"', chunk)
        out.append({"title": html.unescape(name.group(1)), "category": html.unescape(cat.group(1)) if cat else "",
                    "price": _price(chunk), "url": url.group(1) if url else ""})
    return out


def _prefix(w: str) -> str:
    return w[:5] if len(w) > 5 else w[:4] if len(w) >= 4 else w


def relevant(words: list[str], offers: list[dict]) -> list[dict]:
    """Oferte care chiar sunt acelasi tip de produs: contin primul cuvant (substantivul) + restul cuvintelor
    (toate pana la 3 cuvinte, toate minus unul peste 3). Potrivire pe prefix (abur ~ aburi, pisici ~ pisicilor)."""
    if not words:
        return []
    need = len(words) if len(words) <= 3 else len(words) - 1
    out = []
    for o in offers:
        toks = re.findall(r"[a-z]{3,}", ascii_low(o["title"], o.get("category", "")))
        has = lambda w: any(t.startswith(_prefix(w)) for t in toks)  # noqa: E731
        if has(words[0]) and sum(1 for w in words if has(w)) >= need:
            out.append(o)
    return out


def _cache() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE = {}
    return _CACHE


def _save_cache():
    try:
        config.OUT.mkdir(exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_cache(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE_FILE)
    except OSError:
        pass


def emag_lookup(title: str, min_price: float | None = None) -> dict | None:
    """Cauta pe eMAG. Intoarce {"query","n","cheap","under_min","min","median","url"} sau None (oprit/blocat/limita)."""
    if not getattr(config, "EMAG_ENABLED", True):
        return None
    words = emag_query(title)
    if not words:
        return None
    q = " ".join(words)
    c = _cache().get(q)
    fresh = c and (dt.date.today() - dt.date.fromisoformat(c["date"])).days < getattr(config, "EMAG_CACHE_DAYS", 3)
    if not fresh:
        if STATE["blocked"] or STATE["requests"] >= getattr(config, "EMAG_MAX_PER_RUN", 20):
            return None
        import requests
        if STATE["requests"]:
            time.sleep(getattr(config, "EMAG_DELAY", 4.0) + random.uniform(0, 2))
        STATE["requests"] += 1
        url = "https://www.emag.ro/search/" + quote(q)
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.5",
                                                       "Accept": "text/html,application/xhtml+xml"})
        except requests.RequestException as e:
            print(f"[emag] eroare retea ({str(e)[:60]}); continui fara eMAG", flush=True)
            return None
        offers = parse_emag(r.text) if r.status_code == 200 else []
        no_results = r.status_code in (200, 404) and re.search(r"0 rezultate|Nu am gasit|nu a returnat", r.text, re.I)
        if r.status_code not in (200, 404) or (not offers and not no_results):
            STATE["blocked"] = True
            print(f"[emag] blocat/neasteptat (HTTP {r.status_code}); sar eMAG pana la urmatoarea rulare", flush=True)
            return None
        c = {"date": dt.date.today().isoformat(), "url": url,
             "offers": [{"t": o["title"][:90], "p": o["price"], "c": o["category"][:40]} for o in offers[:60]]}
        _cache()[q] = c
        _save_cache()
    offers = [{"title": o["t"], "price": o["p"], "category": o.get("c", "")} for o in c["offers"]]
    rel = [o for o in relevant(words, offers) if o["price"]]
    prices = sorted(o["price"] for o in rel)
    cheap_lim = getattr(config, "EMAG_CHEAP_PRICE", 60.0)
    return {"query": q, "url": c["url"], "n": len(rel), "cheap": sum(1 for p in prices if p < cheap_lim),
            "under_min": sum(1 for p in prices if min_price and p < min_price),
            "min": prices[0] if prices else None, "median": prices[len(prices) // 2] if prices else None,
            "examples": [f"{o['price']:.0f} lei {o['title'][:50]}" for o in sorted(rel, key=lambda o: o["price"])[:3]]}


def emag_verdict(e: dict | None) -> dict:
    """{"drop": bool, "adj": float, "nesaturat": 1-5 | None, "line": text pentru mesaj}"""
    if not e:
        return {"drop": False, "adj": 0.0, "nesaturat": None, "line": "eMAG: neverificat"}
    n, cheap = e["n"], e["cheap"]
    line = (f"eMAG: {n} oferte, de la {e['min']:.0f} lei" if n else "eMAG: 0 oferte relevante (nesaturat)")
    if cheap:
        line += f" · {cheap} sub {config.EMAG_CHEAP_PRICE:.0f} lei"
    adj = 0.0
    drop = False
    if n == 0:
        adj += 1
    if e["under_min"]:
        adj -= 1
    if cheap >= 2:
        adj -= 1
    if n >= config.EMAG_SATURATED_OFFERS:
        adj -= 1
    if cheap >= config.EMAG_DROP_CHEAP_OFFERS:
        if config.EMAG_DROP:
            drop = True
        else:
            adj -= 1
    nes = 5 if n == 0 else 4 if n <= 2 else 3 if n <= 5 else 2 if n <= 12 else 1
    return {"drop": drop, "adj": adj, "nesaturat": nes, "line": line}
