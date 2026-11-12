"""Rute pentru Meta Ad Library: IP-ul direct + proxy-uri, cu sanatate si cooldown per ruta (out/meta_pool.json).

De ce: pe 24.09-01.10 toate proxy-urile din proxies.txt erau "arse" la Meta (rate limit la prima cerere), iar
rotatia oarba la fiecare cerere facea ca TOATE cererile sa esueze, desi IP-ul direct mergea. Acum:
  - ruta preferata = IP direct (daca nu e in cooldown), cu cereri rare (page-size 30, pauze);
  - o ruta limitata intra in cooldown exponential (direct: 15 min -> 6 h; proxy: 12 h -> 7 zile);
  - proxy-urile se incearca doar ca rezerva, cele dovedite bune primele, cele necunoscute esantionat.
"""
import json
import random
import time

import config

POOL_FILE = config.OUT / "meta_pool.json"
DIRECT = "direct"


def _load() -> dict:
    try:
        return json.loads(POOL_FILE.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def _save(d: dict):
    config.OUT.mkdir(exist_ok=True)
    tmp = POOL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf8")
    tmp.replace(POOL_FILE)


def _proxies() -> dict[str, dict]:
    out = {}
    for p in config.load_proxies():
        out[p["server"].replace("http://", "")] = p
    return out


def raw_proxy(route: str) -> str | None:
    """host:port:user:pass pentru colector; None pentru ruta directa."""
    if route == DIRECT:
        return None
    p = _proxies().get(route)
    if not p:
        return None
    return f"{route}:{p.get('username', '')}:{p.get('password', '')}"


def cooling(route: str, d: dict | None = None) -> bool:
    d = _load() if d is None else d
    return d.get(route, {}).get("cool_until", 0) > time.time()


def routes(max_proxies: int = 4) -> list[str]:
    """Ordinea de incercare: direct (daca nu e in cooldown), apoi proxy-uri bune, apoi cateva necunoscute."""
    d = _load()
    out = [] if cooling(DIRECT, d) or not config.META_DIRECT else [DIRECT]
    if not config.USE_PROXY_META:
        return out
    good, unknown = [], []
    for r in _proxies():
        if cooling(r, d):
            continue
        s = d.get(r, {})
        (good if s.get("ok", 0) > 0 else unknown).append(r)
    good.sort(key=lambda r: (-d[r].get("last_ok", 0)))
    random.shuffle(unknown)
    return out + (good + unknown)[:max_proxies]


def mark(route: str, ok: bool, rate_limited: bool = False):
    d = _load()
    s = d.setdefault(route, {"ok": 0, "fails": 0})
    if ok:
        s["ok"] = s.get("ok", 0) + 1
        s["fails"] = 0
        s["last_ok"] = time.time()
        s.pop("cool_until", None)
    elif rate_limited:
        s["fails"] = s.get("fails", 0) + 1
        if route == DIRECT:
            cool = min(6 * 3600, 15 * 60 * 2 ** (s["fails"] - 1))
        else:
            cool = min(7 * 86400, 12 * 3600 * s["fails"])
        s["cool_until"] = time.time() + cool
    _save(d)


def summary() -> str:
    d = _load()
    px = _proxies()
    good = sum(1 for r in px if d.get(r, {}).get("ok", 0) > 0 and not cooling(r, d))
    cool = sum(1 for r in px if cooling(r, d))
    direct = "cooldown" if cooling(DIRECT, d) else "ok"
    return f"direct={direct}, proxy bune={good}, proxy in cooldown={cool}/{len(px)}"
