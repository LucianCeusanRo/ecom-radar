"""Cautare pe AliExpress dupa poza (aceeasi cale ca in browser: iconita camera), gratuit, cu Edge instalat.

Protectii anti-blocare: rotatie de proxy (proxies.txt) — fiecare IP face putine cautari, apoi se schimba; la pagina
anti-bot ("punish"/captcha) IP-ul curent se arunca si se continua cu urmatorul. Pretul se citeste doar din liniile
cardului DUPA titlu, ignorand cupoane/transport/praguri.
"""
import json
import random
import re
import time

from playwright.sync_api import sync_playwright

import config
from errors import AliBlocked, InfraError

CAMERA_XY = (673, 38)  # iconita camera din bara de cautare, la viewport 1400x1000
# Cardul scrie ex. "3,36Lei", "26,1Lei", "12Lei", "1.234,5Lei". Lookbehind: sa nu prindem doar coada unui numar.
PRICE_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*Lei\b", re.I)
SKIP_LINE = re.compile(r"cupon|reducere|transport|livrare|peste|de la|extra|economise", re.I)


def _price(text: str):
    """Pretul curent = primul 'N Lei' din liniile cardului dupa titlu, ignorand cupoane/transport/praguri."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines[1:]:  # linia 0 este titlul (poate contine numere + 'Lei' in text SEO)
        if SKIP_LINE.search(line):
            continue
        m = PRICE_RE.search(line)
        if m:
            try:
                return float(m.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                return None
    return None


def _base_img(src: str | None):
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    m = re.match(r"(.*?\.(?:jpg|jpeg|png|webp))", src, re.I)
    return m.group(1) if m else src


def _blocked(pg) -> bool:
    if "punish" in pg.url or "_tmd_" in pg.url:
        return True
    html = pg.content().lower()
    return "captcha" in html or "slide to verify" in html or "/punish" in html


class AliSearcher:
    """Context manager: `with AliSearcher() as ali: ali.search(poza)`. Schimba singur IP-ul cand e nevoie."""

    POOL_FILE = config.OUT / "ali_pool.json"

    def _health(self) -> dict:
        try:
            return json.loads(self.POOL_FILE.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return {}

    def _score(self, ok: bool):
        """Scor de sanatate per proxy (persistat): cele bune primele data viitoare, cele moarte in pauza 6 h."""
        if not self._pool or self._pi < 0 or self._pi >= len(self._pool):
            return
        h = self._health()
        k = self._pool[self._pi]["server"].removeprefix("http://")
        s = h.setdefault(k, {"ok": 0, "bad": 0})
        if ok:
            s["ok"] += 1
            s["bad"] = 0
            s.pop("cool_until", None)
        else:
            s["bad"] += 1
            if s["bad"] >= 2:
                s["cool_until"] = time.time() + 6 * 3600
        try:
            self.POOL_FILE.write_text(json.dumps(h, indent=1), encoding="utf8")
        except OSError:
            pass

    def _ordered_pool(self) -> list[dict]:
        pool = config.load_proxies() if config.USE_PROXY_ALI else []
        h = self._health()
        now = time.time()
        live = [p for p in pool if h.get(p["server"].removeprefix("http://"), {}).get("cool_until", 0) < now]
        random.shuffle(live)
        live.sort(key=lambda p: -h.get(p["server"].removeprefix("http://"), {}).get("ok", 0) // 5)  # bune primele, restul amestecate
        return live or pool

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._pool = self._ordered_pool()
        self._pi = -1          # indexul proxy-ului curent
        self._browser = None
        self._ctx = None
        self._n_ip = 0         # cautari facute cu IP-ul curent
        self._n_total = 0
        self._last = 0.0
        self._rotate()
        return self

    def __exit__(self, *exc):
        self._close()
        self._pw.stop()

    # --- proxy / browser ------------------------------------------------------------
    def _close(self):
        for obj in (self._ctx, self._browser):
            if obj:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass
        self._browser = self._ctx = None

    def _rotate(self):
        """Deschide un browser nou cu urmatorul proxy din lista (sau fara proxy, daca lista e goala)."""
        self._close()
        kw = {"channel": config.BROWSER_CHANNEL, "headless": True}
        if self._pool:
            self._pi += 1
            if self._pi >= len(self._pool):
                raise AliBlocked(f"am epuizat toate cele {len(self._pool)} IP-uri din proxies.txt")
            kw["proxy"] = self._pool[self._pi]
        elif self._pi >= 0:
            raise AliBlocked("fara proxy disponibil si IP-ul direct e blocat")
        else:
            self._pi = 0
        # Profil persistent per proxy (cookies/istoric pastrate intre rulari => trafic mai natural, mai putine verificari)
        label = kw["proxy"]["server"].removeprefix("http://").replace(":", "_") if "proxy" in kw else "direct"
        prof = config.OUT / "ali_profiles" / label
        prof.mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(prof), locale="ro-RO", viewport={"width": 1400, "height": 1000}, **kw)
        self._ctx.add_cookies([{"name": "aep_usuc_f", "value": config.ALI_COOKIE,
                                "domain": ".aliexpress.com", "path": "/"}])
        self._n_ip = 0

    def ip_label(self) -> str:
        return self._pool[self._pi]["server"].removeprefix("http://") if self._pool else "direct"

    # --- cautare ---------------------------------------------------------------------
    def search(self, image_path: str) -> list[dict]:
        """Rezultatele pentru o poza. Un IP blocat (captcha SAU zero rezultate) e aruncat si se reincearca pe altul.
        Lista goala dupa toate reincercarile = poza chiar nu are potriviri."""
        if self._n_total >= config.ALI_MAX_SEARCHES:
            raise AliBlocked(f"plafon de {config.ALI_MAX_SEARCHES} cautari pe rulare atins")
        if self._n_ip >= config.ALI_SEARCHES_PER_IP:
            self._rotate()
        for _ in range(config.ALI_IP_RETRIES):
            wait = config.ALI_PAUSE + random.uniform(0.5, 3.0) - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self._n_ip += 1
            self._n_total += 1
            try:
                out = self._search_once(image_path)
            except AliBlocked as e:
                print(f"[ali] IP {self.ip_label()} blocat ({str(e)[:50]}); schimb IP-ul", flush=True)
                self._score(False)
                self._rotate()  # ridica AliBlocked doar daca nu mai sunt IP-uri
                continue
            except Exception as e:  # noqa: BLE001  timeout / browser inchis: proxy lent sau mort => alt IP, nu eroare
                if isinstance(e, InfraError) or not re.search(r"Timeout|Target .*closed|net::ERR|Connection", str(e)):
                    raise
                print(f"[ali] IP {self.ip_label()} lent/mort ({str(e)[:40]}); schimb IP-ul", flush=True)
                self._score(False)
                self._rotate()
                continue
            if out:
                self._score(True)
                return out
            self._score(False)
            # Zero rezultate: de obicei blocare "silentioasa" a IP-ului (pagina arata normal, dar fara carduri).
            print(f"[ali] IP {self.ip_label()} a intors 0 rezultate; schimb IP-ul si reincerc", flush=True)
            self._rotate()
        return []

    def _search_once(self, image_path: str) -> list[dict]:
        pg = self._ctx.new_page()
        try:
            pg.goto("https://www.aliexpress.com/", timeout=60000, wait_until="domcontentloaded")
            pg.wait_for_timeout(1500)
            if _blocked(pg):
                raise AliBlocked("pagina anti-bot la deschidere")
            try:
                pg.get_by_text("Don't allow").first.click(timeout=2000)
            except Exception:  # noqa: BLE001
                pass
            pg.mouse.click(*CAMERA_XY)
            pg.wait_for_timeout(400)
            fi = pg.locator("input[type=file]")
            if fi.count() == 0:
                if _blocked(pg):
                    raise AliBlocked("pagina anti-bot inainte de incarcare")
                raise InfraError("nu gasesc campul de incarcare (layout AliExpress schimbat?)")
            fi.first.set_input_files(image_path)
            pg.wait_for_url("**isNewImageSearch=y**", timeout=45000)
            pg.wait_for_timeout(3500)
            for _ in range(3):  # scroll ca sa se incarce mai multe rezultate
                pg.mouse.wheel(0, 2500)
                pg.wait_for_timeout(500)
            if _blocked(pg):
                raise AliBlocked("captcha dupa incarcarea pozei")
            out, seen = [], set()
            cards = pg.locator("a[href*='/item/']")
            for i in range(min(cards.count(), config.ALI_MAX_RESULTS * 2)):
                c = cards.nth(i)
                href = c.get_attribute("href") or ""
                m = re.search(r"/item/(\d+)\.html", href)
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                text = c.inner_text()
                img = c.locator("img").first
                src = (img.get_attribute("src") or img.get_attribute("data-src")) if img.count() else None
                out.append({
                    "item_id": m.group(1),
                    "url": f"https://www.aliexpress.com/item/{m.group(1)}.html",
                    "title": text.split("\n")[0][:140],
                    "price": _price(text),
                    "image": _base_img(src),
                    "best_similarity": "Best simi" in text,
                    "raw": " | ".join(text.splitlines())[:260],
                })
                if len(out) >= config.ALI_MAX_RESULTS:
                    break
            return out
        finally:
            try:
                pg.close()
            except Exception:  # noqa: BLE001
                pass
