"""Colecteaza reclame din Meta Ad Library (gratuit, fara API key) si pastreaza doar candidatii buni."""
import datetime as dt
import json
import os
import random
import re
import subprocess
import time
import unicodedata
from urllib.parse import unquote, urlparse

import sys

import config
import metapool


def canonical(url: str) -> str:
    """Cheie stabila pentru un produs: fara www/port/query, cu path decodat, fara /collections/x/, prefix de limba sau .html."""
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = unquote(p.path).lower().rstrip("/")
    path = re.sub(r"^/collections/[^/]+", "", path)
    path = re.sub(r"^/(ro|en|hu|bg)(?=/)", "", path)
    path = re.sub(r"\.html?$", "", path)
    return f"{host}{path}"


RATE_PAUSE = 15
_consec_fail = 0  # esecuri totale la rand (0 reclame + rate limit): intrerupator pentru colectare
DEADLINE: float | None = None  # time.monotonic() la care colectarea trebuie sa se opreasca (setat de run.py)
REQUESTS = 0      # cereri HTTP catre Meta in rularea curenta (din statisticile colectorului)
STATS = {"meta_ok": 0, "meta_limited": 0, "ads": 0}
# Statistici pe produs din TOATE reclamele active vazute in rulare (fara filtrul de varsta), pentru "in crestere":
# {cheie: {"ads": nr reclame active, "new7": pornite in ultimele 7 zile, "first": cea mai veche, "last": cea mai noua}}
KEY_STATS: dict[str, dict] = {}


def out_of_time() -> bool:
    return DEADLINE is not None and time.monotonic() > DEADLINE


def out_of_budget() -> bool:
    return REQUESTS >= config.META_MAX_REQUESTS_PER_RUN


def _once(query: str, max_ads: int, page_id: str | None, route: str = metapool.DIRECT,
          country: str | None = None) -> tuple[list[dict], bool, bool]:
    """O rulare a colectorului pe o ruta (direct/proxy). Intoarce (reclame, limitat, rate_limit_explicit)."""
    global REQUESTS
    config.OUT.mkdir(exist_ok=True)
    country = country or config.COUNTRY
    tag = f"page_{page_id}" if page_id else re.sub(r"[^a-z0-9]+", "_", query.lower())
    if country != config.COUNTRY:
        tag = f"{country.lower()}__{tag}"
    out = config.OUT / f"ads_{tag}.json"
    tmp_out = config.OUT / f"ads_{tag}.part.json"
    tmp_out.unlink(missing_ok=True)
    if sys.platform == "win32":
        exe = config.ROOT / ".venv" / "Scripts" / "meta-ads-collector.exe"
    else:
        exe = config.ROOT / ".venv" / "bin" / "meta-ads-collector"
    base = ["--page-ids", page_id] if page_id else ["-q", query, "--search-type", "exact"]
    # page-size 30 (max): de 3 ori mai putine cereri decat implicitul 10 => mult sub pragul de rate limit
    cmd = [str(exe), *base, "-c", country, "-s", "active", "--sort-by", "relevancy", "-n", str(max_ads),
           "--page-size", str(config.META_PAGE_SIZE), "--delay", str(config.META_DELAY),
           "--has-image", "--no-download-media", "-o", str(tmp_out)]
    raw = metapool.raw_proxy(route)
    cmd += ["--proxy", raw] if raw else ["--no-proxy"]
    env = {k: v for k, v in os.environ.items() if k != "META_ADS_PROXY"}
    env["PYTHONIOENCODING"] = "utf-8"
    failed = False
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf8", errors="replace", timeout=600, env=env)
        log = (r.stdout or "") + (r.stderr or "")
        failed = r.returncode != 0
    except subprocess.TimeoutExpired:
        log, failed = "timeout", True
    rate = "Rate limit" in log or "rate limit" in log
    limited = failed or rate or "Max retries" in log or "Search request failed" in log \
        or "Failed to initialize" in log or not tmp_out.exists()
    ads: list[dict] = []
    if tmp_out.exists():
        try:
            d = json.loads(tmp_out.read_text(encoding="utf8"))
            ads = d.get("ads", [])
            REQUESTS += int(((d.get("metadata") or {}).get("stats") or {}).get("requests_made") or 1)
        except ValueError:
            limited = True
            REQUESTS += 1
        if ads:  # nu suprascriem cache-ul bun cu un rezultat gol
            tmp_out.replace(out)
        else:
            tmp_out.unlink(missing_ok=True)
    else:
        REQUESTS += 1
    metapool.mark(route, ok=not limited, rate_limited=rate or (limited and not ads))
    STATS["meta_ok" if not limited else "meta_limited"] += 1
    return ads, limited, rate


def run_query(query: str, max_ads: int, page_id: str | None = None, attempts: int = 2,
              country: str | None = None) -> tuple[list[dict], bool]:
    """Ca _once, dar pe rute sanatoase: direct intai, apoi rezerva proxy. Intoarce (reclame, complet)."""
    global _consec_fail
    best: list[dict] = []
    tried = 0
    for route in metapool.routes(max_proxies=attempts):
        if tried >= attempts:
            break
        tried += 1
        ads, limited, rate = _once(query, max_ads, page_id, route, country)
        if len(ads) > len(best):
            best = ads
        if not limited:
            _consec_fail = 0
            STATS["ads"] += len(best)
            return best, True
        print(f"[collect] Meta limiteaza ruta {'direct' if route == metapool.DIRECT else 'proxy'} "
              f"({len(ads)} reclame); incercarea {tried}/{attempts}", flush=True)
        time.sleep(3)
    _consec_fail = _consec_fail + 1 if not best else 0
    STATS["ads"] += len(best)
    return best, False


def blocked() -> bool:
    """Intrerupator: prea multe esecuri la rand SAU nicio ruta disponibila => oprim colectarea."""
    return _consec_fail >= config.MAX_CONSEC_FAILS or not metapool.routes(max_proxies=1)


def meta_ready(wait_minutes: int = 0) -> bool:
    """Test mic la pornire pe rutele disponibile (direct, apoi cateva proxy). Fara asteptari lungi."""
    for route in metapool.routes(max_proxies=config.META_PREFLIGHT_PROXIES):
        ads, limited, _ = _once("cumpără acum", 1, None, route)
        if ads and not limited:
            print(f"[collect] Meta OK pe ruta {'direct' if route == metapool.DIRECT else 'proxy'}", flush=True)
            return True
        print(f"[collect] Meta limiteaza ruta {'direct' if route == metapool.DIRECT else 'proxy'}", flush=True)
    print(f"[collect] Meta indisponibil acum ({metapool.summary()})", flush=True)
    return False


def _candidate(ad: dict, now: dt.datetime):
    start = ad.get("delivery_start_time")
    if not start:
        return None
    started = dt.datetime.fromisoformat(start.replace("Z", "")).replace(tzinfo=None)
    age = (now - started).days
    if age > config.DAYS or age < config.MIN_DAYS:  # fereastra: pornita acum MIN_DAYS..DAYS zile, inca activa
        return None
    for cr in ad.get("creatives") or []:
        link = cr.get("link_url")
        if not link or not re.search(config.PRODUCT_PATH_RE, link, re.I):
            continue
        host = urlparse(link).netloc.lower()
        if any(b in host for b in config.BLOCK_DOMAINS):
            continue
        return {
            "key": canonical(link),
            "ad_id": ad.get("id"),
            "ad_url": f"https://www.facebook.com/ads/library/?id={ad.get('id')}",
            "page_name": (ad.get("page") or {}).get("name", ""),
            "started": started.strftime("%Y-%m-%d"),
            "days_running": (now - started).days,
            "link_url": link,
            "ad_title": cr.get("title") or "",
            "creative_image": cr.get("image_url"),
        }
    return None


def _note_stats(ad: dict, now: dt.datetime):
    start = ad.get("delivery_start_time")
    if not start:
        return
    try:
        started = dt.datetime.fromisoformat(start.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return
    for cr in ad.get("creatives") or []:
        link = cr.get("link_url")
        if link and re.search(config.PRODUCT_PATH_RE, link, re.I):
            key = canonical(link)
            day = started.strftime("%Y-%m-%d")
            s = KEY_STATS.setdefault(key, {"ads": 0, "new7": 0, "first": day, "last": day, "ids": set(),
                                           "page": (ad.get("page") or {}).get("name", "")})
            aid = ad.get("id") or id(ad)
            if aid in s["ids"]:
                return
            s["ids"].add(aid)
            s["ads"] += 1
            s["new7"] += 1 if (now - started).days <= 7 else 0
            s["first"], s["last"] = min(s["first"], day), max(s["last"], day)
            return


def key_stats() -> dict:
    """KEY_STATS fara campurile interne (pentru istoric)."""
    return {k: {kk: vv for kk, vv in v.items() if kk != "ids"} for k, v in KEY_STATS.items()}


def load_pages() -> dict:
    if config.PAGES_FILE.exists():
        try:
            return json.loads(config.PAGES_FILE.read_text(encoding="utf8"))
        except ValueError:
            pass
    return {pid: {"name": n} for pid, n in config.SEED_PAGES.items()}


def save_pages(pages: dict):
    tmp = config.PAGES_FILE.with_suffix(".tmp")  # scriere atomica: un crash nu lasa fisierul pe jumatate scris
    tmp.write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(config.PAGES_FILE)


def _store_page(ad: dict):
    """(id, nume) daca reclama duce spre o pagina de produs a unui magazin (nu brand mare), altfel None."""
    for cr in ad.get("creatives") or []:
        link = cr.get("link_url") or ""
        if re.search(config.PRODUCT_PATH_RE, link, re.I) and not any(
                b in urlparse(link).netloc.lower() for b in config.BLOCK_DOMAINS):
            pg = ad.get("page") or {}
            host = (urlparse(link).hostname or "").lower().removeprefix("www.")
            return (pg.get("id"), pg.get("name", ""), host) if pg.get("id") else None
    return None


def is_pet(*texts: str) -> bool:
    """Produs/magazin pentru animale de companie? (text fara diacritice, litere mici)"""
    t = unicodedata.normalize("NFKD", " ".join(x for x in texts if x)).encode("ascii", "ignore").decode().lower()
    return bool(re.search(config.PET_RE, t))


def get_candidates(queries=None, max_ads=None, scan_pages: int | None = None, pet_queries=None,
                   use_cache: bool = False) -> list[dict]:
    """Descopera magazine noi (cautari generale + cautari pet), apoi scaneaza magazinele dupa reclame in fereastra
    MIN_DAYS..DAYS. Magazinele de pet se scaneaza primele; produsele de pet primesc flag-ul `pet`."""
    now = dt.datetime.now()
    pages = load_pages()
    found: dict[str, dict] = {}

    def take(ads, pet_source=False, country=None):
        kept = 0
        for ad in ads:
            _note_stats(ad, now)
            sp = _store_page(ad) if not (country and country != config.COUNTRY) else None  # doar magazine RO in lista
            if sp:
                pages.setdefault(sp[0], {"name": sp[1]})
                if sp[2]:
                    pages[sp[0]]["domain"] = sp[2]
            c = _candidate(ad, now)
            if not c:
                continue
            text_pet = is_pet(c["ad_title"], c["link_url"], c["page_name"])
            c["pet"] = pet_source or text_pet
            if country and country != config.COUNTRY:
                c["country"] = country
            if text_pet and sp:  # magazinul e "pet" doar daca un produs al lui chiar arata a pet (nu doar pentru ca a aparut in cautare)
                pages[sp[0]]["pet"] = True
            kept += 1
            old = found.get(c["key"])
            if old is None or c["started"] < old["started"]:
                found[c["key"]] = c
        return kept

    if use_cache:  # fara cereri catre Meta: refolosim reclamele colectate in ultimele 24h (out/ads_*.json)
        pet_tags = {re.sub(r"[^a-z0-9]+", "_", q.lower()) for q in config.PET_QUERIES}
        n_files = 0
        for f in sorted(config.OUT.glob("ads_*.json")):
            if time.time() - f.stat().st_mtime > 24 * 3600:
                continue
            try:
                ads = json.loads(f.read_text(encoding="utf8")).get("ads", [])
            except ValueError:
                continue
            n_files += 1
            stem = f.stem.removeprefix("ads_")
            cc = stem.split("__", 1)[0].upper() if "__" in stem else None
            take(ads, stem in pet_tags, cc)
        save_pages(pages)
        print(f"[collect] din cache: {n_files} fisiere, {len(found)} candidate unice", flush=True)
        return _with_stats(found)

    today = dt.date.today().isoformat()
    try:  # optional: API-ul oficial (doar daca exista meta_adlib_token.txt)
        import metaapi
        metaapi.discover(pages, (queries or config.QUERIES)[:3])
    except Exception as e:  # noqa: BLE001
        print(f"[metaapi] sarit: {e}", flush=True)

    # 1) Magazinele din lista (sursa cea mai buna: magazine de dropshipping care lanseaza produse acum) se scaneaza
    #    PRIMELE, cat timp Meta inca raspunde. Pet primele, apoi cele scanate cel mai demult; esecurile se reiau.
    slots = scan_pages or config.MAX_PAGES_PER_RUN
    by_age = lambda pid: pages[pid].get("last_scan", "")  # noqa: E731  (cele nescanate / scanate demult primele)
    pets = sorted((p for p in pages if pages[p].get("pet")), key=by_age)
    others = sorted((p for p in pages if not pages[p].get("pet")), key=by_age)
    pet_slots = min(len(pets), max(1, int(slots * 0.6)))  # pet primele, dar max 60% din locuri
    ids = pets[:pet_slots] + others[: slots - pet_slots]
    ids += [p for p in pets[pet_slots:] if p not in ids][: max(0, slots - len(ids))]
    failed = []
    for n, pid in enumerate(ids, 1):
        if blocked() or out_of_time() or out_of_budget():
            why = "bugetul de cereri pe rulare" if out_of_budget() else "Meta ne blocheaza sau timpul a expirat"
            print(f"[collect] {why}: opresc scanarea la magazinul {n}/{len(ids)}; restul la urmatoarea rulare.", flush=True)
            break
        ads, ok = run_query("", config.PAGE_SCAN_MAX_ADS, page_id=pid)
        kept = take(ads)  # pet se decide per produs; flag-ul magazinului e doar pentru ordinea scanarii
        if ok:
            pages[pid]["last_scan"] = today
        else:
            failed.append(pages[pid]["name"])
        print(f"[collect] magazin {n}/{len(ids)} {pages[pid]['name'][:26]}: {len(ads)} reclame, {kept} candidate"
              + ("" if ok else "  (INCOMPLET)"), flush=True)
        if n % 10 == 0:
            save_pages(pages)
        time.sleep(config.PAUSE_BETWEEN + random.uniform(0, config.PAUSE_JITTER))
    save_pages(pages)
    if failed:
        print(f"[collect] ATENTIE: {len(failed)} magazine incomplete (rate limit), reluate la urmatoarea rulare: {failed[:8]}", flush=True)

    # 2) Cautarile (descopera magazine noi) cu ce a mai ramas din "cota" Meta. Pornim de fiecare data de la alta fraza,
    #    ca frazele de la coada listei sa nu fie mereu sarite cand Meta ne limiteaza.
    qlist = [("", q, False) for q in (queries if queries is not None else config.QUERIES)] +             [("PET ", q, True) for q in (pet_queries or [])]
    rot_file = config.OUT / "query_rotation.json"
    try:
        start = int(json.loads(rot_file.read_text(encoding="utf8")).get("next", 0)) % max(1, len(qlist))
    except (OSError, ValueError):
        start = 0
    done = 0
    for label, q, pet in (qlist[start:] + qlist[:start])[: getattr(config, "QUERIES_PER_RUN", len(qlist))]:
        if blocked() or out_of_time() or out_of_budget():
            print("[collect] Meta ne blocheaza / buget de cereri / timp expirat: sar peste restul cautarilor (continui de aici data viitoare).", flush=True)
            break
        ads, ok = run_query(q, max_ads or config.MAX_ADS_PER_QUERY)
        n = take(ads, pet)
        print(f"[collect] cautare {label}'{q}': {len(ads)} reclame, {n} candidate" + ("" if ok else "  (INCOMPLET: rate limit)"), flush=True)
        done += 1  # rotim oricum (si la esec), ca sa nu blocam pool-ul pe acelasi cuvant
        time.sleep(config.PAUSE_BETWEEN + random.uniform(0, config.PAUSE_JITTER))
    # 3) Piete vecine (PL/HU): produse care inca n-au ajuns in RO. Putine cautari, prin rotatie.
    fq = [(cc, q) for cc, qs in getattr(config, "FOREIGN_QUERIES", {}).items() for q in qs]
    try:
        fstart = int(json.loads(rot_file.read_text(encoding="utf8")).get("foreign", 0)) % max(1, len(fq))
    except (OSError, ValueError):
        fstart = 0
    fdone = 0
    for cc, q in (fq[fstart:] + fq[:fstart])[: getattr(config, "FOREIGN_PER_RUN", 0)]:
        if blocked() or out_of_time() or out_of_budget():
            break
        ads, ok = run_query(q, config.FOREIGN_MAX_ADS, country=cc)
        n = take(ads, False, cc)
        print(f"[collect] cautare {cc} '{q}': {len(ads)} reclame, {n} candidate" + ("" if ok else "  (INCOMPLET)"), flush=True)
        fdone += 1 if ok else 0
        time.sleep(config.PAUSE_BETWEEN + random.uniform(0, config.PAUSE_JITTER))
    if qlist:
        config.OUT.mkdir(exist_ok=True)
        rot_file.write_text(json.dumps({"next": (start + done) % len(qlist),
                                        "foreign": (fstart + fdone) % max(1, len(fq))}), encoding="utf8")
    save_pages(pages)
    return _with_stats(found)


def _with_stats(found: dict) -> list[dict]:
    out = []
    for c in found.values():
        s = KEY_STATS.get(c["key"])
        if s:
            c["ad_stats"] = {k: v for k, v in s.items() if k != "ids"}
        out.append(c)
    return out
