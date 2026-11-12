"""Sursa de rezerva cand Meta Ad Library e limitat: produse NOI din magazinele Shopify care fac reclame in RO.

Magazinele vin din pages.json (descoperite prin Ad Library, cu domeniul salvat). Pentru fiecare citim
/products.json (public pe Shopify) si pastram produsele publicate in ultimele FEED_MAX_DAYS zile (dar mai vechi de
FEED_MIN_DAYS, ca sa nu fie doar un test de o zi). Candidatul are acelasi format ca unul din reclame, cu
source="magazin" si linkul catre reclamele active ale paginii in Ad Library.
"""
import datetime as dt
import json
import random
import time

try:
    from curl_cffi import requests as creq  # amprenta TLS de Chrome (mai putine 403 Cloudflare)
except ImportError:  # fara curl_cffi: requests simplu, ca run.py sa porneasca oricum
    creq = None
import requests as _plain


def _get(url: str, timeout: int = 20):
    if creq is not None:
        return creq.get(url, impersonate="chrome", timeout=timeout)
    return _plain.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"})

import collect
import config

STATE = config.OUT / "shopfeed_state.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf8")


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def candidates(pages: dict, max_shops: int | None = None, deadline: float | None = None) -> list[dict]:
    max_shops = max_shops or config.FEED_MAX_SHOPS
    st = _load()
    now = dt.datetime.now()
    today = dt.date.today().isoformat()
    shops = {}
    for pid, p in pages.items():
        dom = p.get("domain")
        if not dom or any(b in dom for b in config.BLOCK_DOMAINS):
            continue
        s = st.get(dom, {})
        if s.get("bad_until", "") > today:
            continue
        shops.setdefault(dom, (pid, p))
    # cele verificate cel mai demult primele (pet primele la egalitate)
    order = sorted(shops, key=lambda d: (st.get(d, {}).get("last", ""), not shops[d][1].get("pet"), random.random()))
    out: list[dict] = []
    n_ok = 0
    for dom in order[:max_shops]:
        if deadline and time.monotonic() > deadline:
            break
        pid, p = shops[dom]
        s = st.setdefault(dom, {})
        try:
            r = _get(f"https://{dom}/products.json?limit=100", timeout=20)
            ok = r.status_code == 200 and "products" in r.text[:200]
            prods = r.json().get("products", []) if ok else []
        except Exception:  # noqa: BLE001
            ok, prods = False, []
        s["last"] = today
        if not ok:
            s["fails"] = s.get("fails", 0) + 1
            if s["fails"] >= 2:  # nu e Shopify / blocat: pauza 14 zile
                s["bad_until"] = (dt.date.today() + dt.timedelta(days=14)).isoformat()
            continue
        s["fails"] = 0
        n_ok += 1
        kept = 0
        for pr in prods:
            pub = _parse(pr.get("published_at") or pr.get("created_at"))
            if not pub:
                continue
            age = (now - pub).days
            if age < config.FEED_MIN_DAYS or age > config.FEED_MAX_DAYS:
                continue
            imgs = [i.get("src") for i in pr.get("images") or [] if i.get("src")]
            if not imgs or not pr.get("handle"):
                continue
            link = f"https://{dom}/products/{pr['handle']}"
            out.append({
                "key": collect.canonical(link),
                "ad_id": None,
                "ad_url": f"https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=RO&view_all_page_id={pid}",
                "page_name": p.get("name", dom),
                "started": pub.strftime("%Y-%m-%d"),
                "days_running": age,
                "link_url": link,
                "ad_title": pr.get("title") or "",
                "creative_image": imgs[0],
                "pet": collect.is_pet(pr.get("title") or "", link, p.get("name", "")),
                "source": "magazin",
            })
            kept += 1
            if kept >= config.FEED_PER_SHOP:
                break
        time.sleep(0.5)
    _save(st)
    print(f"[feed] {n_ok}/{min(len(order), max_shops)} magazine Shopify citite, {len(out)} produse noi ({config.FEED_MIN_DAYS}-{config.FEED_MAX_DAYS} zile)", flush=True)
    return out
