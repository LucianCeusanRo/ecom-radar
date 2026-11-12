"""Monitor: reclame recente RO -> produs pe AliExpress 1-10 lei (verificat local cu model de vedere) -> WhatsApp.

Exemple:
  python run.py --target none --limit 5          # doar afiseaza, nu trimite nimic (nu scrie starea)
  python run.py --target test --max-send 3      # trimite pe "Test grup" (stare separata: state_test.json)
  python run.py --target prod                    # trimite pe "Ponturi Ecom" (state.json)
  python run.py --target prod --cache            # fara cereri catre Meta: foloseste reclamele colectate in ultimele 24h

Reguli de siguranta: produsele TRIMISE nu se mai trimit niciodata (nici aceeasi oferta AliExpress prin alt magazin);
erorile de infrastructura (Ollama, pagina indisponibila, AliExpress gol/anti-bot) NU marcheaza produsul ca verificat.
"""
import argparse
import contextlib
import ctypes
import datetime as dt
import json
import shutil
import os
import random
import subprocess
import sys
import threading
import time
import traceback

import requests

import collect
import config
import content
import feedback
import alibaba
import llm
import market
import metapool
import mockup
import notify
import page
import rules
import shopfeed
import vlm
from aliexpress import AliSearcher
from errors import AliBlocked, InfraError, VlmError

MAX_JUDGE = 6           # cate oferte AliExpress ieftine verifica modelul per produs (Ollama, una cate una)
MAX_JUDGE_LLM = 10      # cate oferte primeste Claude intr-un singur apel
MAX_INFRA_FAILS = 3     # oprim rularea dupa atatea esecuri de infrastructura la rand
STALE_LOCK_HOURS = 1.5  # un lock mai vechi de atat, chiar daca procesul mai traieste, se considera blocat (procesul e oprit)
PRODUCT_TIMEOUT_MIN = 20  # watchdog: un singur produs nu are voie sa dureze mai mult (ex. Playwright blocat la close)
HARD_EXTRA_MIN = 15       # watchdog: oprire fortata la MAX_RUNTIME_MIN + atat (rularile nu se mai suprapun / blocheaza ore)
LOCK = config.OUT / "run.lock"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# --- stare -------------------------------------------------------------------------
def state_path(target: str):
    return config.ROOT / ("state_test.json" if target == "test" else "state.json")


def load_state(path) -> dict:
    if not path.exists():
        return {"seen": {}, "sent_items": {}}
    try:
        st = json.loads(path.read_text(encoding="utf8"))
    except ValueError:
        bad = path.with_suffix(f".corrupt-{dt.datetime.now():%Y%m%d%H%M%S}")
        path.rename(bad)
        raise SystemExit(f"[run] EROARE: {path.name} era corupt; l-am mutat in {bad.name}. Verifica manual inainte sa continui.")
    st.setdefault("seen", {})
    st.setdefault("sent_items", {})
    return st


def save_state(st: dict, path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(path)


def recently_seen(st: dict, key: str) -> bool:
    s = st["seen"].get(key)
    if not s:
        return False
    if s.get("sent") or str(s.get("result", "")).startswith("OK"):
        return True  # trimis o data = nu se mai trimite niciodata (fereastra de 14-30 zile depaseste orice expirare)
    return (dt.date.today() - dt.date.fromisoformat(s["date"])).days < config.NEG_DAYS


# --- lock ----------------------------------------------------------------------------
def pid_alive(pid: int) -> bool:
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def acquire_lock() -> bool:
    config.OUT.mkdir(exist_ok=True)
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
        except ValueError:
            pid = 0
        fresh = (time.time() - LOCK.stat().st_mtime) < STALE_LOCK_HOURS * 3600
        if pid and pid_alive(pid):
            if fresh:
                return False  # alta rulare chiar e activa
            kill_stale_run(pid)  # rulare blocata de ore: o oprim, altfel tine fisierele (task.log) ocupate
    LOCK.write_text(str(os.getpid()))
    return True


def kill_stale_run(pid: int):
    """Opreste o rulare veche blocata (doar daca e chiar run.py din ads-monitor) + procesul parinte venv."""
    ps = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
          "if($p -and $p.CommandLine -match 'run\\.py'){ Stop-Process -Id $p.ProcessId -Force; "
          "$q=Get-CimInstance Win32_Process -Filter ('ProcessId='+$p.ParentProcessId); "
          "if($q -and $q.CommandLine -match 'run\\.py'){ Stop-Process -Id $q.ProcessId -Force }; 'killed' }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        if "killed" in (r.stdout or ""):
            print(f"[run] am oprit rularea veche blocata (PID {pid})", flush=True)
    except (OSError, subprocess.SubprocessError):
        pass


# --- watchdog ----------------------------------------------------------------------
WD = {"beat": time.monotonic(), "hard": None, "what": "pornire", "run_log": None}


def beat(what: str):
    WD["beat"] = time.monotonic()
    WD["what"] = what


def ollama_unload():
    """Elibereaza RAM/VRAM: modelul Ollama se descarca imediat la finalul rularii (keep_alive=0)."""
    try:
        requests.post(f"{config.OLLAMA_URL}/api/generate", json={"model": config.VLM_MODEL, "keep_alive": 0}, timeout=15)
    except Exception:  # noqa: BLE001
        pass


def _hard_exit(reason: str):
    print(f"[watchdog] OPRIRE FORTATA: {reason} (ultimul pas: {WD['what']}). Rularea urmatoare continua de aici.", flush=True)
    try:
        if WD["run_log"] is not None:
            (config.OUT / f"run_{dt.datetime.now():%Y%m%d_%H%M}.json").write_text(
                json.dumps(WD["run_log"] + [{"result": f"watchdog: {reason}", "step": WD["what"]}], ensure_ascii=False,
                           indent=1), encoding="utf8")
    except Exception:  # noqa: BLE001
        pass
    for fn in (kill_orphan_browsers, cleanup_images, ollama_unload):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    LOCK.unlink(missing_ok=True)
    sys.stdout.flush()
    os._exit(4)


def start_watchdog():
    WD["hard"] = time.monotonic() + (config.MAX_RUNTIME_MIN + HARD_EXTRA_MIN) * 60
    beat("start")

    def loop():
        while True:
            time.sleep(20)
            now = time.monotonic()
            if now > WD["hard"]:
                _hard_exit(f"rularea a depasit {config.MAX_RUNTIME_MIN + HARD_EXTRA_MIN} min")
            if WD["what"].startswith("produs") and now - WD["beat"] > PRODUCT_TIMEOUT_MIN * 60:
                _hard_exit(f"un produs dureaza de peste {PRODUCT_TIMEOUT_MIN} min (browser/retea blocate)")
    threading.Thread(target=loop, name="watchdog", daemon=True).start()


def kill_orphan_browsers() -> int:
    """Inchide browserele headless ramase de la o rulare oprita fortat. Edge-ul normal (fara --headless) nu e atins."""
    ps = ("Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'msedge.exe' -and "
          "$_.CommandLine -match '--headless' -and $_.CommandLine -match '--user-data-dir') -or "
          "($_.Name -eq 'node.exe' -and $_.CommandLine -match 'ads-monitor' -and $_.CommandLine -match 'playwright') } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; 1 } | Measure-Object | "
          "Select-Object -ExpandProperty Count")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
        n = int((r.stdout or "0").strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0
    if n:
        print(f"[run] am inchis {n} procese de browser ramase de la o rulare anterioara", flush=True)
    return n


def cleanup_images() -> int:
    """Sterge pozele de lucru (out/img/*) si imaginile comparative deja trimise (out/match_*.jpg).
    Se apeleaza la finalul fiecarei rulari: mesajele au ajuns pe WhatsApp, fisierele locale nu mai sunt necesare."""
    n = 0
    for f in list(config.IMG.glob("*")) + list(config.OUT.glob("match_*.jpg")):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    n += notify.cleanup_outbound()
    for d in (config.OUT / "ro_img").glob("*"):   # poze RO de lucru: sunt deja urcate in draftul Shopify
        n += sum(1 for f in d.rglob("*") if f.is_file())
        shutil.rmtree(d, ignore_errors=True)
    if n:
        print(f"[run] am sters {n} poze temporare", flush=True)
    return n


def _detached(cmd: list[str]):
    flags = 0x00000008 | 0x00000200 | getattr(subprocess, "CREATE_NO_WINDOW", 0)  # DETACHED | NEW_PROCESS_GROUP
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)


def ensure_ollama(wait_s: int = 120) -> bool:
    """Watchdog: daca Ollama nu raspunde, il porneste (ollama serve) si asteapta."""
    if ollama_ready():
        return True
    exe = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if not os.path.exists(exe):
        exe = "ollama"
    print("[watchdog] Ollama nu raspunde: il pornesc", flush=True)
    try:
        _detached([exe, "serve"])
    except OSError as e:
        print(f"[watchdog] nu pot porni Ollama: {e}", flush=True)
        return False
    end = time.time() + wait_s
    while time.time() < end:
        time.sleep(5)
        if ollama_ready():
            print("[watchdog] Ollama pornit", flush=True)
            return True
    return False


def gateway_ok() -> bool:
    try:
        r = subprocess.run(notify._openclaw_cmd() + ["channels", "status"], capture_output=True, text=True,
                           encoding="utf8", errors="replace", timeout=60)
        out = (r.stdout or "") + (r.stderr or "")
        return "Gateway reachable" in out and "connected" in out
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_gateway(wait_s: int = 90) -> bool:
    """Watchdog: daca gateway-ul OpenClaw (WhatsApp) nu e pornit/conectat, porneste task-ul 'OpenClaw Gateway'."""
    if gateway_ok():
        return True
    print("[watchdog] gateway-ul OpenClaw/WhatsApp nu e conectat: pornesc task-ul 'OpenClaw Gateway'", flush=True)
    subprocess.run(["schtasks", "/Run", "/TN", "OpenClaw Gateway"], capture_output=True, timeout=30)
    end = time.time() + wait_s
    while time.time() < end:
        time.sleep(10)
        if gateway_ok():
            print("[watchdog] gateway conectat", flush=True)
            return True
    print("[watchdog] gateway-ul tot nu e conectat (trimiterile vor esua si se reiau la rularea urmatoare)", flush=True)
    return False


def ollama_ready() -> bool:
    try:
        tags = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=10).json()
        return any(m["name"] == config.VLM_MODEL for m in tags.get("models", []))
    except Exception:  # noqa: BLE001
        return False


class PageGone(Exception):
    """Pagina produsului nu mai exista (404/410) sau magazinul ne blocheaza (403/Cloudflare): rezultat REAL, nu infra.
    Produsul primeste cooldown (NEG_DAYS) si NU conteaza la cele 3 esecuri la rand."""

    def __init__(self, status):
        super().__init__(f"pagina {status}")
        self.status = status


def domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def domain_bad(st: dict, url: str) -> bool:
    b = st.get("bad_domains", {}).get(domain_of(url))
    return bool(b and b.get("n", 0) >= 2 and b.get("until", "") > dt.date.today().isoformat())


def note_bad_domain(st: dict, url: str, status):
    d = st.setdefault("bad_domains", {}).setdefault(domain_of(url), {"n": 0})
    d["n"] = d.get("n", 0) + 1
    d["status"] = str(status)
    d["until"] = (dt.date.today() + dt.timedelta(days=config.BAD_DOMAIN_DAYS)).isoformat()


def is_q4(*texts: str) -> bool:
    import re
    import unicodedata
    t = unicodedata.normalize("NFKD", " ".join(x for x in texts if x)).encode("ascii", "ignore").decode().lower()
    return bool(re.search(config.Q4_RE, t))


# --- potrivire -----------------------------------------------------------------------
def find_match(ali: AliSearcher, c: dict, title: str, gallery: list[str], log: dict, desc_cache: dict):
    """Cauta pe AliExpress dupa poza, filtreaza 1-10 lei, iar modelul decide daca e acelasi produs.
    Viteza: a doua poza doar daca prima n-a dat oferte 1-10 lei SAU VLM a respins toate ofertele ieftine."""
    results: dict[str, dict] = {}

    def ingest(src: str):
        for rank, r in enumerate(ali.search(src), 1):
            e = results.setdefault(r["item_id"], {**r, "hits": 0, "best_rank": rank})
            e["hits"] += 1
            e["best_rank"] = min(e["best_rank"], rank)
            e["best_similarity"] = e["best_similarity"] or r["best_similarity"]

    def cheap_list():
        out = [r for r in results.values()
               if r["price"] and config.COST_MIN <= r["price"] <= config.COST_MAX and r["image"]]
        out.sort(key=lambda r: (-r["hits"], not r["best_similarity"], r["best_rank"]))
        return out

    def judge(cheap):
        if not vlm.enabled():
            desc_cache.setdefault(c["key"], title)
            log["judge"] = "regula"
            r, why = rules.match(cheap, title)
            return r, [why]
        if getattr(config, "LLM_JUDGE", False) and llm.available():
            try:
                if c["key"] not in desc_cache:
                    desc_cache[c["key"]] = vlm.describe(title, gallery)  # vederea ramane locala (Ollama)
                lst = cheap[:MAX_JUDGE_LLM]
                idx, v = llm.judge_batch(desc_cache[c["key"]], title, lst)
                log["judge"] = "claude"
                return (lst[idx] if idx is not None else None), [f"[{desc_cache[c['key']][:90]}]"] + v
            except llm.LlmError as e:
                print(f"[llm] judecata Claude esuata ({str(e)[:80]}); trec pe Ollama", flush=True)
        log["judge"] = "ollama"
        verdicts = []
        for r in cheap[:MAX_JUDGE]:
            try:
                ok, why = vlm.same_product(c["key"], title, gallery, r["title"], desc_cache)
            except VlmError as e:  # Ollama a picat in timpul rularii: restul rularii pe reguli
                print(f"[vlm] Ollama a picat ({str(e)[:60]}); trec pe modul fara AI", flush=True)
                vlm.DISABLED = True
                desc_cache.setdefault(c["key"], title)
                log["judge"] = "regula"
                rr, w = rules.match(cheap, title)
                return rr, verdicts + [w]
            verdicts.append(f"{r['price']}lei {'DA' if ok else 'nu'}: {why[:110]}")
            if ok:
                return r, verdicts
        return None, verdicts

    images = gallery[: config.ALI_IMAGES_PER_PRODUCT]
    if not images:
        log["ali_results"] = 0
        return None

    ingest(images[0])
    cheap = cheap_list()
    if results and not cheap:
        # Carduri gasite, niciuna 1-10 lei: a doua poza nu ajuta la pret.
        log["ali_results"], log["ali_cheap"] = len(results), 0
        log["ali_skip_extra_images"] = "fara_ieftine_dupa_prima_poza"
        return None

    match, verdicts = judge(cheap) if cheap else (None, [])
    if match:
        log["ali_results"], log["ali_cheap"] = len(results), len(cheap)
        log["verdicts"] = verdicts
        return match

    # Fara match: mai incercam o poza doar daca n-am avut deloc rezultate SAU VLM a zis nu la ieftine.
    if len(images) > 1 and (not results or cheap):
        judged_ids = {r["item_id"] for r in cheap[:max(MAX_JUDGE, MAX_JUDGE_LLM)]} if cheap else set()
        ingest(images[1])
        cheap2 = [r for r in cheap_list() if r["item_id"] not in judged_ids]
        match2, verdicts2 = judge(cheap2) if cheap2 else (None, [])
        verdicts = verdicts + verdicts2
        if match2:
            log["ali_results"], log["ali_cheap"] = len(results), len(cheap_list())
            log["verdicts"] = verdicts
            return match2
        cheap = cheap_list()

    if not results:
        log["ali_results"] = 0
        return None
    log["ali_results"], log["ali_cheap"] = len(results), len(cheap)
    log["verdicts"] = verdicts
    return None


def process(c: dict, ali: AliSearcher, log: dict, desc_cache: dict, sent_ids: set):
    """Intoarce mesajul-dict daca produsul trece toate filtrele; None = rezultat negativ REAL (motivul e in log).
    Esecurile de infrastructura ridica InfraError/VlmError/AliBlocked (produsul nu se marcheaza ca vazut)."""
    info = page.fetch_page(c["link_url"])
    status = info.get("_status")
    if status in (404, 410, 403, 401, 451) or (isinstance(status, int) and status >= 500 and status != 503):
        raise PageGone(status)
    if status is not None:  # 429/503/retea: temporar, dar NU e o problema de infrastructura a noastra
        raise PageGone(f"temporar_{status}")
    title = (info.get("title") or c["ad_title"] or "").strip()
    log["title"] = title
    urls = page.gallery(c["link_url"], [info.get("image"), c["creative_image"]])
    gallery = [p for p in (page.download_image(u, "g_") for u in urls[:6]) if p]
    if not title or not gallery:
        log["result"] = "fara_titlu_sau_poze"
        return None
    slug = c["link_url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    aud = market.audience(title, c.get("ad_title", ""), slug)
    log["audience"] = aud
    if getattr(config, "AUDIENCE_ENABLED", True) and aud["weight"] <= getattr(config, "AUDIENCE_DROP_AT", -3):
        log["result"] = f"public_nepotrivit_30+({aud['label']})"
        return None
    best = None
    if not ali_in_cooldown():
        try:
            best = find_match(ali, c, title, gallery, log, desc_cache)
        except AliBlocked as e:
            ali_cooldown(str(e))
    if best is None and ali_in_cooldown() and getattr(config, "ALIBABA_FALLBACK", True):
        best = alibaba.lookup(title)   # rezerva: pret angro de pe Alibaba (fara verificare vizuala)
        log["judge"] = "alibaba"
        log["alibaba_query"] = alibaba.keywords(title)
        if best:
            desc_cache.setdefault(c["key"], title)
            log["ali_cheap"] = 1
        elif alibaba.STATE["blocked"] or alibaba.STATE["requests"] >= getattr(config, "ALIBABA_MAX_PER_RUN", 15):
            raise InfraError("AliExpress in cooldown si Alibaba indisponibil/plafon atins")
        else:
            log["no_state"] = True   # rezultat Alibaba negativ (cautare dupa cuvinte): reverificam cu AliExpress mai tarziu
    if not best:
        log["result"] = "fara_potrivire_verificata" if log.get("ali_cheap") else "fara_oferta_1-10_lei"
        return None
    log["ali_url"], log["ali_price"], log["link_url"] = best["url"], best["price"], c["link_url"]
    if best["item_id"] in sent_ids:
        log["result"] = f"deja_trimis(oferta_AliExpress_{best['item_id']})"
        return None
    ali_img = page.download_image(best["image"], "ali_") if best.get("image") else None
    if not ali_img and best.get("source") == "alibaba":
        ali_img = gallery[0]   # Alibaba fara poza descarcabila: pastram poza magazinului
    if not ali_img:
        raise InfraError("poza AliExpress nu se poate descarca")
    desc = desc_cache[c["key"]]
    team_rules = feedback.rules_text()
    if team_rules and vlm.enabled():
        try:
            bad, which = vlm.breaks_user_rules(desc, title, team_rules)
        except VlmError:
            bad, which = False, ""
        if bad:
            log["result"] = f"respins_de_regula_invatata({which[:60]})"
            return None
    sp = info.get("price")
    sell = sp if (info.get("currency") == "RON" and sp and 15 <= sp <= 1500) else None
    econ = config.profit_at_roas(sell, best["price"]) if sell else config.profit_at_roas(0, best["price"])
    log["profit_pct"] = round(econ["pct"], 1) if sell else None
    if sell and econ["pct"] < config.MIN_PROFIT_PCT:
        # Pretul practicat de concurent nu lasa profit la ROAS 3 => produsul nu merita promovat.
        log["result"] = (f"profit_sub_{config.MIN_PROFIT_PCT:.0f}%_la_ROAS_{config.ROAS:.0f}"
                         f"({econ['pct']:.0f}%, minim {econ['min_price']:.0f} lei)")
        return None
    em = None
    if not c.get("country") or c.get("country") == config.COUNTRY:  # titlu RO => cautare pe eMAG
        try:
            em = market.emag_lookup(title, econ["min_price"])
        except Exception as e:  # noqa: BLE001
            print(f"[emag] sarit: {str(e)[:80]}", flush=True)
    ev = market.emag_verdict(em)
    log["emag"] = {**(em or {}), "line": ev["line"], "adj": ev["adj"]}
    if ev["drop"]:
        log["result"] = (f"saturat_eMAG({em['n']} oferte, {em['cheap']} sub {config.EMAG_CHEAP_PRICE:.0f} lei, "
                         f"de la {em['min']:.0f} lei)")
        return None
    bf, bf_reason, card = False, "", {}
    ang = None
    if llm.available(need=2):  # 2 apeluri: scor/unghiuri + continut Shopify; altfel totul pe Ollama
        try:
            _e0 = config.profit_at_roas(0, best["price"])
            _sp = info.get("price") if info.get("currency") == "RON" else None
            note = (f"AliExpress {best['price']:.2f} lei; minimum sell price for 20% profit {_e0['min_price']:.0f} lei"
                    + (f"; competitor sells at {_sp:.0f} lei" if _sp else "")
                    + (f"; eMAG Romania already has {em['n']} similar offers from {em['min']:.0f} lei"
                       if em and em.get("n") else "; no similar offers found on eMAG Romania" if em else ""))
            ang, bf, bf_reason, card = llm.angles_bf(desc, title, note)
            log["score"] = card
        except llm.LlmError as e:
            print(f"[llm] unghiuri Claude esuate ({str(e)[:80]}); trec pe Ollama", flush=True)
    if ang is None and vlm.enabled():
        try:
            ang = vlm.angles(desc, title)
        except VlmError as e:
            print(f"[vlm] unghiuri Ollama esuate ({str(e)[:60]}); folosesc sabloanele", flush=True)
    if ang is None:
        ang = rules.angles(title)
    if not card:
        card = rules.card(title, econ, sell, best["price"])
        bf = is_q4(title, desc)
    log["black_friday"] = bf
    log["angles"] = [a["name"] for a in ang]
    if len(ang) < config.MIN_ANGLES:
        log["result"] = f"sub_{config.MIN_ANGLES}_unghiuri_marketing({len(ang)})"
        return None
    # --- ajustari pe date reale: public 30+, eMAG, in crestere (istoric reclame + magazine cu aceeasi oferta Ali)
    advertisers = market.note_ali(best["item_id"], c["key"], c.get("page_name", ""))
    rs = market.rising(c, advertisers)
    log["rising"] = rs
    card = dict(card)
    if ev["nesaturat"] is not None:
        card["nesaturat"] = ev["nesaturat"]
    adj_parts = []
    if aud["weight"]:
        adj_parts.append((aud["weight"], f"public 30+: {aud['label']}"))
    if ev["adj"]:
        adj_parts.append((ev["adj"], ev["line"]))
    if rs["adj"]:
        adj_parts.append((rs["adj"], "; ".join(rs["notes"])))
    total = sum(a for a, _ in adj_parts)
    base = card.get("nota") or 5
    card["nota_base"] = base
    card["nota"] = max(1, min(10, int(round(base + max(-4.0, min(3.0, total))))))
    card["ajustari"] = [f"{a:+g} {why}" for a, why in adj_parts]
    log["nota"] = {"baza": base, "final": card["nota"], "ajustari": card["ajustari"]}
    if card["nota"] < getattr(config, "WA_MIN_NOTA", 0):
        log["result"] = f"nota_{card['nota']}_sub_pragul_{config.WA_MIN_NOTA}"
        return None
    pet = bool(c.get("pet")) or collect.is_pet(desc)
    m = {**c, "title": title, "ali_price": best["price"], "ali_url": best["url"], "ali_title": best["title"],
         "ali_item_id": best["item_id"], "sell_price": sell, "angles": ang, "pet": pet, "econ": econ,
         "black_friday": bf, "bf_reason": bf_reason, "desc": desc, "card": card,
         "store_img": gallery[0], "ali_img": ali_img, "ali_img_url": best.get("image"),
         "no_ai": log.get("judge") in ("regula", "alibaba"),
         "price_source": best.get("source", "aliexpress"), "moq": best.get("moq"),
         "market": {"emag": ev["line"], "emag_url": (em or {}).get("url"), "audience": aud["label"],
                    "aud_w": aud["weight"], "rising": rs["rising"], "rising_notes": rs["notes"]}}
    m["image"] = notify.side_by_side(gallery[0], ali_img, config.OUT / f"match_{best['item_id']}.jpg")
    m["text"] = notify.build_message(m)
    log["result"] = f"OK {best['price']} lei"
    return m


def ro_pictures(m: dict, cv: dict | None, log: dict) -> dict | None:
    """Poze AliExpress cu textul in romana (max RO_IMAGES_MAX) + infografice RO. Orice esec -> None (pozele originale)."""
    if not cv or not getattr(config, "RO_IMAGES", True) or m.get("price_source") == "alibaba":
        return None
    try:
        import ro_images
        beat("poze in romana")
        ro = ro_images.build_for_match(m, cv, config.OUT / "ro_img" / str(m["ali_item_id"]),
                                       getattr(config, "RO_IMAGES_MAX", 4), getattr(config, "BROWSER_CHANNEL", None),
                                       getattr(config, "RO_INFOGRAPHICS", True))
    except Exception as e:  # noqa: BLE001
        log["ro_images_error"] = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"[ro_images] sarit (folosesc pozele originale): {str(e)[:120]}", flush=True)
        return None
    log["ro_images"] = {"photos": len(ro.get("photos") or []), "infographics": sorted((ro.get("infographics") or {})),
                        "notes": (ro.get("notes") or [])[:4]}
    print(f"[ro_images] {len(ro.get('photos') or [])} poze RO + {len(ro.get('infographics') or {})} infografice", flush=True)
    return ro


def shopify_draft(m: dict, cv: dict | None, mk: str | None, target: str, log: dict, ro: dict | None = None) -> dict | None:
    """Pont bun (NOTA >= SHOPIFY_MIN_NOTA) -> produs DRAFT pe magazinul propriu (shopify_api.py).
    Orice eroare e doar logata: pontul/rularea merg mai departe."""
    if m.get("price_source") == "alibaba":
        log["shopify"] = "sarit (sursa pret Alibaba)"
        return None
    if not getattr(config, "SHOPIFY_AUTO_DRAFT", False) or target not in getattr(config, "SHOPIFY_DRAFT_TARGETS", ("prod",)):
        return None
    try:
        import shopify_api
        r = shopify_api.from_match(m, cv, mk, ro=ro) if ro else shopify_api.from_match(m, cv, mk)
    except Exception as e:  # noqa: BLE001
        log["shopify_error"] = str(e)[:200]
        print(f"[shopify] draft esuat (pontul merge mai departe): {str(e)[:150]}", flush=True)
        return None
    if not r or r.get("deleted"):
        return None
    log["shopify"] = {"id": r["legacy_id"], "admin_url": r["admin_url"], "created": r["created"]}
    print(f"[shopify] draft {'creat' if r['created'] else 'existent'}: {r['admin_url']}", flush=True)
    return r


ALI_COOLDOWN_FILE = config.OUT / "ali_cooldown.json"
_ALI_RUN_BLOCKED = {"on": False}


def ali_in_cooldown() -> bool:
    """AliExpress blocat in rularea asta SAU cooldown salvat (out/ali_cooldown.json) inca activ."""
    if _ALI_RUN_BLOCKED["on"]:
        return True
    try:
        until = json.loads(ALI_COOLDOWN_FILE.read_text(encoding="utf8")).get("until", "")
    except (OSError, ValueError):
        return False
    return until > dt.datetime.now().isoformat(timespec="minutes")


def ali_cooldown(reason: str):
    """Nu mai oprim cautarile: AliExpress intra in cooldown, restul rularii merge pe Alibaba."""
    _ALI_RUN_BLOCKED["on"] = True
    if "plafon" in reason:   # plafonul de cautari pe rulare nu e blocare: fara cooldown salvat
        print(f"[run] {reason}; restul rularii pe Alibaba", flush=True)
        return
    mins = getattr(config, "ALI_COOLDOWN_MIN", 120)
    until = (dt.datetime.now() + dt.timedelta(minutes=mins)).isoformat(timespec="minutes")
    try:
        ALI_COOLDOWN_FILE.write_text(json.dumps({"until": until, "reason": reason[:120]}), encoding="utf8")
    except OSError:
        pass
    print(f"[run] AliExpress blocheaza ({reason[:80]}): cooldown {mins} min; continui cu Alibaba", flush=True)


@contextlib.contextmanager
def ali_session():
    """AliSearcher doar daca AliExpress nu e in cooldown; blocare la pornire => cooldown + Alibaba (ali=None)."""
    if ali_in_cooldown():
        print("[run] AliExpress in cooldown: folosesc Alibaba pentru pretul de cost", flush=True)
        yield None
        return
    try:
        sess = AliSearcher()
        ali = sess.__enter__()
    except AliBlocked as e:
        ali_cooldown(str(e))
        yield None
        return
    try:
        yield ali
    finally:
        sess.__exit__(None, None, None)


def title_or(m: dict) -> str:
    return m.get("title") or m.get("ad_title") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["none", "test", "prod"], default="none")
    ap.add_argument("--limit", type=int, default=config.MAX_CANDIDATES, help="verifica cel mult N produse")
    ap.add_argument("--max-send", type=int, default=config.MAX_SEND_PER_RUN)
    ap.add_argument("--queries", type=int, default=0, help="doar primele N fraze de cautare (-1 = fara cautari, doar magazinele)")
    ap.add_argument("--max-ads", type=int, default=0, help="reclame per fraza")
    ap.add_argument("--pages", type=int, default=0, help="scaneaza doar primele N magazine din lista")
    ap.add_argument("--no-pet", action="store_true", help="sari peste cautarile speciale de produse pet")
    ap.add_argument("--cache", action="store_true", help="fara cereri catre Meta: foloseste reclamele colectate in ultimele 24h")
    ap.add_argument("--no-state", action="store_true", help="ignora produsele deja verificate")
    ap.add_argument("--discord", choices=["auto", "on", "off"], default="auto",
                    help="auto = Discord doar pentru --target prod (testele NU posteaza pe Discord)")
    ap.add_argument("--no-content", action="store_true", help="nu genera pagina de produs Shopify")
    ap.add_argument("--no-feed", action="store_true", help="fara sursa de rezerva (produse noi din magazinele Shopify)")
    ap.add_argument("--feed-only", action="store_true", help="doar sursa magazine Shopify (fara Meta)")
    ap.add_argument("--no-ai", action="store_true", help="fara Claude si fara Ollama: doar reguli (test de rezilienta)")
    ap.add_argument("--selftest", action="store_true", help="verifica toate componentele fara sa trimita nimic")
    args = ap.parse_args()
    if os.environ.get("ADS_NO_AI") == "1":
        args.no_ai = True
    if args.selftest:
        import selftest
        return selftest.run(no_ai=args.no_ai)

    if args.target == "prod" and getattr(config, "WA_TARGET", "prod") == "test":
        print("[run] setarea WA_TARGET=test: ponturile merg pe Test grup (fara Discord si fara drafturi)", flush=True)
        args.target = "test"
    if config.SETTINGS_IGNORED:
        print(f"[run] ATENTIE: setari invalide ignorate din settings.json: {config.SETTINGS_IGNORED}", flush=True)
    if not acquire_lock():
        print("[run] alta rulare e activa (out/run.lock). Ies.")
        return 0
    run_log: list[dict] = []
    sent = 0
    if args.target == "prod" and os.environ.get("ADS_NO_JITTER") != "1":
        d = random.uniform(0, config.START_JITTER_MIN * 60)  # rularile nu pornesc fix la :00 (trafic mai natural)
        print(f"[run] pornesc peste {d / 60:.1f} min (jitter)", flush=True)
        time.sleep(d)
    WD["run_log"] = run_log
    start_watchdog()
    kill_orphan_browsers()
    try:
        if args.target != "none" and getattr(config, "WA_ENABLED", True):
            ensure_gateway()
        if args.no_ai:
            vlm.DISABLED = True
            config.LLM_ENABLED = False
        elif not ensure_ollama():
            print(f"[run] ATENTIE: Ollama nu raspunde / lipseste {config.VLM_MODEL}: continui in modul FARA AI (reguli)", flush=True)
            vlm.DISABLED = True
        deadline = time.monotonic() + config.MAX_RUNTIME_MIN * 60
        collect.DEADLINE = deadline
        use_discord = args.discord == "on" or (args.discord == "auto" and args.target == "prod"
                                                and getattr(config, "DISCORD_ENABLED", True))
        print(f"[run] {dt.datetime.now():%Y-%m-%d %H:%M} target={args.target} discord={'da' if use_discord else 'nu'} "
              f"ai={'+'.join(llm.providers()) or 'nu'} meta: {metapool.summary()}", flush=True)
        meta_ok = False
        if args.feed_only:
            args.cache = True
        if not args.cache and not (meta_ok := collect.meta_ready(config.PREFLIGHT_WAIT_MIN)):
            print("[run] Meta Ad Library limiteaza cererile: nu colectez reclame noi, verific produsele din reclamele salvate in ultimele 24h.")
            args.cache = True  # fallback: candidate din cache; ce a fost deja verificat/trimis e filtrat de stare
        spath = state_path(args.target if args.target != "none" else "prod")  # dry-run citeste starea prod, nu o scrie
        st = load_state(spath)
        queries = [] if args.queries == -1 else (config.QUERIES[: args.queries] if args.queries else config.QUERIES)
        pet_q = [] if args.no_pet else config.PET_QUERIES
        cands = [] if args.feed_only else collect.get_candidates(queries, args.max_ads or None, args.pages or None, pet_q,
                                                                use_cache=args.cache)
        n_meta = len(cands)
        n_hist = market.record_snapshot(collect.key_stats())  # istoric pentru "in crestere" (toate produsele vazute)
        if n_hist:
            print(f"[market] istoric: {n_hist} produse salvate in history.db", flush=True)
        if not args.no_state:
            cands = [c for c in cands if not recently_seen(st, c["key"])]
        n_fresh_meta = len(cands)
        print(f"[run] Meta: {collect.STATS['ads']} reclame, {n_meta} candidate, {n_fresh_meta} noi (nevazute); "
              f"cereri Meta {collect.REQUESTS}", flush=True)
        # Sursa de rezerva: cand Meta e limitat sau da prea putine produse noi, luam produse NOI din magazinele
        # care fac reclame (Shopify /products.json). Etichetate in mesaj ca "produs nou in magazin".
        if not args.no_feed and (args.feed_only or n_fresh_meta < config.FEED_TRIGGER):
            feed = shopfeed.candidates(collect.load_pages(), deadline=deadline)
            keys = {c["key"] for c in cands}
            feed = [f for f in feed if f["key"] not in keys and (args.no_state or not recently_seen(st, f["key"]))]
            cands += feed[: config.FEED_MAX_CANDIDATES]
        cands = [c for c in cands if not domain_bad(st, c["link_url"])]
        for c in cands:
            c["q4"] = is_q4(c.get("ad_title", ""), c["link_url"])
        # ordinea: reclame dovedite inaintea produselor din magazin; in cadrul lor prioritate public 30+ / in crestere
        # (market.prio: categorie, reclame pornite recent, reclame noi; pet si Q4 dau un mic bonus), apoi vechime
        for c in cands:
            c["prio"] = market.prio(c)
        cands.sort(key=lambda c: (c.get("source") == "magazin", -c["prio"], c["started"]))
        # plafon pe magazin: un brand nu mananca toata rularea (viteza)
        per_page, capped = {}, []
        limit_page = getattr(config, "MAX_PER_PAGE", 3)
        for c in cands:
            name = c.get("page_name") or "?"
            n = per_page.get(name, 0)
            if n >= limit_page:
                continue
            per_page[name] = n + 1
            capped.append(c)
        cands = capped[: args.limit]
        print(f"[run] {len(cands)} produse de verificat", flush=True)

        infra_fails, desc_cache = 0, {}
        sent_ids = set(st["sent_items"])
        write_state = args.target != "none"
        beat("pornesc browserul AliExpress")
        with ali_session() as ali:
            for i, c in enumerate(cands, 1):
                if time.monotonic() > deadline:
                    print(f"[run] limita de timp ({config.MAX_RUNTIME_MIN} min) atinsa; ma opresc, restul la urmatoarea rulare.")
                    break
                beat(f"produs {i}/{len(cands)} {c['page_name'][:30]}")
                log = {"key": c["key"], "page": c["page_name"], "started": c["started"]}
                run_log.append(log)
                label = f"[{i}/{len(cands)}] {c['page_name'][:22]:22}"
                try:
                    m = process(c, ali, log, desc_cache, sent_ids)
                    infra_fails = 0
                except PageGone as e:
                    temp = str(e.status).startswith("temporar")
                    log["result"] = f"pagina_indisponibila_{e.status}"
                    print(f"{label} {log['result']}" + (" (reiau mai tarziu)" if temp else " (cooldown, nu mai incerc curand)"), flush=True)
                    if write_state:
                        if not temp:
                            st["seen"][c["key"]] = {"date": dt.date.today().isoformat(), "result": log["result"]}
                        if e.status in (403, 401, 451) or temp:
                            note_bad_domain(st, c["link_url"], e.status)
                        save_state(st, spath)
                    continue
                except AliBlocked as e:
                    log["result"] = f"anti_bot: {e}"
                    ali_cooldown(str(e))   # nu mai oprim cautarile: restul rularii pe Alibaba
                    continue
                except Exception as e:  # noqa: BLE001  (InfraError, VlmError, retea etc.): esec temporar, NU rezultat negativ
                    log["result"] = f"eroare_temporara: {str(e)[:120]}"
                    log["trace"] = traceback.format_exc()[-400:]
                    infra_fails += 1
                    print(f"{label} {log['result']}", flush=True)
                    if infra_fails >= MAX_INFRA_FAILS:
                        print(f"[run] {MAX_INFRA_FAILS} esecuri la rand, opresc rularea (Ollama/AliExpress/retea?).")
                        break
                    continue  # nu marcam produsul ca verificat: se reia data viitoare
                if m:
                    print(f"\n--- POTRIVIRE {i}/{len(cands)} ---\n{m['text']}\n", flush=True)
                    if args.target == "none":
                        pass
                    elif sent >= args.max_send:
                        log["result"] += " (netrimis: limita pe rulare; reluat data viitoare)"
                        print(f"{label} {log['result']}", flush=True)
                        continue
                    else:
                        cv = None
                        ro = None
                        if not args.no_content:
                            if getattr(config, "RO_IMAGES", True):
                                try:   # OCR pe pozele Ali: textele merg la traducere in acelasi apel care scrie pagina
                                    import ro_images
                                    ro_images.prepare(m, getattr(config, "RO_IMAGES_MAX", 4))
                                except Exception as e:  # noqa: BLE001
                                    print(f"[ro_images] OCR sarit: {str(e)[:100]}", flush=True)
                            cv = content.build(m, m.get("desc") or title_or(m))
                            log["content"] = cv["html_path"] if cv else None
                            ro = ro_pictures(m, cv, log)
                        # O singura imagine pe WhatsApp: mockup de telefon cu pagina de produs (rezerva: poza comparativa)
                        mk = mockup.make(m, cv, (ro or {}).get("hero") or m.get("store_img") or m.get("ali_img"))
                        log["mockup"] = mk
                        wa_img = mk or m["image"]
                        # Canale independente: WhatsApp (daca e pornit in setari) SI Discord (daca e pornit). Pontul e
                        # "trimis" daca a ajuns pe cel putin un canal; dedupe-ul (sent_items/seen) ramane la fel.
                        wa_on = getattr(config, "WA_ENABLED", True)
                        ok_wa = bool(notify.send(config.GROUPS[args.target], m["text"], wa_img)) if wa_on else False
                        log["sent_wa"] = ok_wa if wa_on else "oprit"
                        ok_dc = False
                        if use_discord:  # doar prod (sau --discord on): mockup + poza comparativa + pagina Shopify .html
                            try:
                                extra = [p for p in ([m["image"]] if mk else []) + ([cv["html_path"]] if cv else []) if p]
                                ok_dc = bool(notify.send_discord(m["text"], wa_img, extra))
                            except Exception as e:  # noqa: BLE001
                                print(f"[notify] discord exception: {e}", flush=True)
                        log["sent_discord"] = ok_dc
                        if ok_wa or ok_dc:
                            sd = shopify_draft(m, cv, mk, args.target, log, ro)  # DRAFT pe magazinul propriu (NOTA >= prag)
                            if ok_wa and cv:
                                time.sleep(2)
                                log["sent_content_wa"] = notify.send(config.GROUPS[args.target], content.short_message(cv, use_discord)
                                                                     + ("\n\n🛍️ Draft creat pe magazinul propriu" if sd else ""))
                            if ok_dc and sd:
                                try:
                                    notify.send_discord(f"🛍️ Draft Shopify (admin): {sd['admin_url']}")
                                except Exception:  # noqa: BLE001
                                    pass
                            sent += 1
                            log["sent_to"] = args.target + ("" if ok_wa else " (doar Discord)")
                            sent_ids.add(m["ali_item_id"])
                            st["sent_items"][m["ali_item_id"]] = dt.date.today().isoformat()
                            st["seen"][c["key"]] = {"date": dt.date.today().isoformat(), "result": log["result"], "sent": True}
                            save_state(st, spath)
                            time.sleep(4)
                            print(f"{label} {log['result']} -> TRIMIS (whatsapp={log['sent_wa']}, discord={ok_dc})", flush=True)
                            if sent >= args.max_send:
                                break
                            continue
                        else:
                            log["result"] += " (trimitere esuata pe toate canalele)"
                            print(f"{label} {log['result']} (whatsapp={log['sent_wa']}, discord={ok_dc})", flush=True)
                            continue  # nu marcam ca vazut: reincercam data viitoare
                print(f"{label} {log['result']}", flush=True)
                if write_state and not (m and args.target == "none") and not log.get("no_state"):
                    st["seen"][c["key"]] = {"date": dt.date.today().isoformat(), "result": log["result"]}
                    save_state(st, spath)
        beat("final")
        print(f"[run] gata: {sent} trimise, {sum(str(l.get('result')).startswith('OK') for l in run_log)} potriviri, "
              f"{len(run_log)} verificate; meta: {metapool.summary()}")
        return 0
    finally:
        try:
            config.OUT.mkdir(exist_ok=True)
            (config.OUT / f"run_{dt.datetime.now():%Y%m%d_%H%M}.json").write_text(
                json.dumps(run_log, ensure_ascii=False, indent=1), encoding="utf8")
        except OSError:
            pass
        beat("curatenie")
        kill_orphan_browsers()  # nimic nu ramane deschis dupa rulare
        cleanup_images()        # pozele de lucru nu se pastreaza
        ollama_unload()         # RAM/VRAM eliberat intre rulari
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
