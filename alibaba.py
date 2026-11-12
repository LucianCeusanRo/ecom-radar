"""Sursa de rezerva pentru pretul de cost: Alibaba.com (cautare dupa cuvinte cheie), folosita cand AliExpress
blocheaza (anti-bot) sau e in cooldown. Pretul e ANGRO (cel mai mic pret/bucata, cu MOQ), deci pontul e marcat clar
"verifica pe AliExpress". Fara Shopify draft pentru produsele cu sursa Alibaba. Esec/blocare => None (fara potrivire).
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
import time
from urllib.parse import quote_plus

import config
import market
import rules

STATE = {"blocked": False, "requests": 0, "last": 0.0}
CACHE_FILE = config.OUT / "alibaba_cache.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
EXTRA_EN = {"abur": "steam", "aburi": "steam", "par": "hair", "parul": "hair", "rola": "roller", "scame": "lint",
            "pisici": "cat", "caini": "dog", "animale": "pet", "adezive": "adhesive", "perete": "wall",
            "tablete": "tablets", "spalat": "washing", "rufe": "laundry", "masina": "machine", "lanterna": "flashlight",
            "dulap": "cabinet", "sertar": "drawer", "covor": "carpet", "pernuta": "pillow", "termos": "thermos"}


def keywords(title: str) -> list[str]:
    """Cuvinte EN in ordinea din titlul RO (dictionarul din rules + cateva extra). Gol = nu stim sa cautam."""
    out: list[str] = []
    for w in market.emag_query(title) + re.findall(r"[a-z]{3,}", market.ascii_low(title)):
        en = EXTRA_EN.get(w) or rules.RO_EN.get(w, "").split(" ")[0]
        if en and en not in out:
            out.append(en)
    return out[:4]


def _num(s: str) -> float | None:
    m = re.search(r"(\d[\d.,]*)", s or "")
    if not m:
        return None
    v = m.group(1)
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".") if v.rfind(",") > v.rfind(".") else v.replace(",", "")
    else:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def to_lei(price: str) -> float | None:
    """'15,22-18,29 RON' -> 15.22 ; '$1.20-2.00' -> 1.20 * USD_RON ; 'EUR 1,10' -> * EUR_RON (cel mai mic pret)."""
    v = _num(price)
    if v is None:
        return None
    p = price.upper()
    if "RON" in p or "LEI" in p:
        return v
    if "€" in price or "EUR" in p:
        return v * getattr(config, "EUR_RON", 5.0)
    return v * getattr(config, "USD_RON", 4.6)   # implicit USD


def parse(page: str) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r'"price":"([^"]+)","productId":"(\d+)"', page):
        pid = m.group(2)
        if pid in seen:
            continue
        seen.add(pid)
        win = page[max(0, m.start() - 1500): m.end() + 1500]
        url = re.search(r'"productUrl":"([^"]+)"', page[m.end(): m.end() + 600])
        url = ("https:" + url.group(1).replace("\\/", "/")) if url else f"https://www.alibaba.com/product-detail/_{pid}.html"
        slug = re.search(r"product-detail/([^_?]+)_", url)
        title = re.search(r'"(?:title|subject|productTitle)":"([^"]{6,200})"', win)
        moq = re.search(r'(?:"moq[A-Za-z]*":"?|Min\. order:\s*)(\d[\d,.]*)\s*([A-Za-z]*)', win)
        img = re.search(r'"(?:mainImage|imageUrl|image)":"([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', win)
        out.append({"item_id": f"alibaba_{pid}", "price_raw": m.group(1), "price": to_lei(m.group(1)), "url": url.split("?")[0],
                    "title": (title.group(1) if title else (slug.group(1).replace("-", " ") if slug else "")),
                    "moq": moq.group(1).replace(",", "") if moq else "?",
                    "image": ("https:" + img.group(1).replace("\\/", "/")) if img and img.group(1).startswith("//")
                    else (img.group(1).replace("\\/", "/") if img else None)})
    return out


def _fetch(url: str) -> str | None:
    try:
        from curl_cffi import requests as cr
        r = cr.get(url, impersonate="chrome", timeout=30)
    except ImportError:
        import requests
        r = requests.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8,ro;q=0.6"})
    except Exception as e:  # noqa: BLE001
        print(f"[alibaba] eroare retea: {str(e)[:80]}", flush=True)
        return None
    if r.status_code != 200 or "punish" in r.text[:200000] or "captcha" in r.text[:5000].lower():
        STATE["blocked"] = True
        print(f"[alibaba] pagina anti-bot (HTTP {r.status_code}); sar Alibaba pana la rularea urmatoare", flush=True)
        return None
    return r.text


def _cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lookup(title: str) -> dict | None:
    """Cea mai ieftina oferta Alibaba relevanta (pret/bucata in lei, intre COST_MIN si COST_MAX) sau None."""
    if not getattr(config, "ALIBABA_FALLBACK", True):
        return None
    words = keywords(title)
    if not words:
        return None
    q = " ".join(words)
    cache = _cache()
    c = cache.get(q)
    if not c or (dt.date.today() - dt.date.fromisoformat(c["date"])).days >= getattr(config, "ALIBABA_CACHE_DAYS", 3):
        if STATE["blocked"] or STATE["requests"] >= getattr(config, "ALIBABA_MAX_PER_RUN", 15):
            return None
        wait = getattr(config, "ALIBABA_DELAY", 8.0) - (time.time() - STATE["last"])
        if STATE["requests"] and wait > 0:
            time.sleep(wait + random.uniform(0, 3))
        STATE["requests"] += 1
        STATE["last"] = time.time()
        page = _fetch("https://www.alibaba.com/trade/search?SearchText=" + quote_plus(q))
        if page is None:
            return None
        c = {"date": dt.date.today().isoformat(), "offers": parse(page)[:60]}
        cache[q] = c
        try:
            config.OUT.mkdir(exist_ok=True)
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    need = min(2, len(words))
    rel = []
    for o in c["offers"]:
        t = o["title"].lower()
        if o["price"] and sum(1 for w in words if w in t) >= need and config.COST_MIN <= o["price"] <= config.COST_MAX:
            rel.append(o)
    if not rel:
        return None
    best = min(rel, key=lambda o: o["price"])
    return {**best, "source": "alibaba", "query": q, "best_similarity": False, "hits": 1, "best_rank": 1}
