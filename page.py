"""Ia titlul, poza si pretul de vanzare de pe pagina produsului din reclama."""
import hashlib
import html
import io
import re

import requests
from PIL import Image

try:
    from curl_cffi import requests as creq  # amprenta TLS de Chrome: mult mai putine 403 Cloudflare
except ImportError:  # pragma: no cover
    creq = None

import config

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36", "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8"}


def _get(url: str, timeout: int = 20):
    """GET cu amprenta de Chrome (curl_cffi) daca e disponibil, altfel requests."""
    if creq is not None:
        return creq.get(url, impersonate="chrome", timeout=timeout, allow_redirects=True)
    return requests.get(url, headers=UA, timeout=timeout)


def _meta(h: str, prop: str):
    pat = re.escape(prop)
    m = (re.search(rf'<meta[^>]+(?:property|name)=["\']{pat}["\'][^>]+content=["\']([^"\']*)', h, re.I)
         or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{pat}["\']', h, re.I))
    return html.unescape(m.group(1)) if m else None


def _num(s):
    if not s:
        return None
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _price_currency(h: str):
    """(pret, moneda) doar din surse cu moneda explicita: meta product/og sau JSON-LD Offer. Altfel (None, None)."""
    price = _meta(h, "product:price:amount") or _meta(h, "og:price:amount")
    cur = _meta(h, "product:price:currency") or _meta(h, "og:price:currency")
    if price and cur:
        return _num(price), cur.upper()
    for pat in (r'"price"\s*:\s*"?([\d.,]+)"?\s*,\s*"priceCurrency"\s*:\s*"([A-Za-z]{3})"',
                r'"priceCurrency"\s*:\s*"([A-Za-z]{3})"\s*,\s*"price"\s*:\s*"?([\d.,]+)"?'):
        m = re.search(pat, h)
        if m:
            a, b = m.groups()
            return (_num(a), b.upper()) if pat.startswith('"price"') else (_num(b), a.upper())
    return None, None


def fetch_page(url: str) -> dict:
    """Date de pe pagina produsului. Daca pagina nu e disponibila intoarce {"_status": cod} (404/403/...) sau
    {"_status": "retea"}; apelantul decide: 404/410 = produs mort, 403/429/503 = magazin blocat, retea = temporar."""
    try:
        r = _get(url)
    except Exception:  # noqa: BLE001
        return {"_status": "retea"}
    if r.status_code != 200:
        return {"_status": r.status_code}
    h = r.content.decode("utf-8", errors="replace")  # nu ne bazam pe charset-ul (adesea lipsa) din antet
    low = h[:4000].lower()
    if "just a moment" in low and "cloudflare" in h.lower():
        return {"_status": 403}
    price, cur = _price_currency(h)
    img = _meta(h, "og:image")
    if img and img.startswith("//"):
        img = "https:" + img
    return {"title": _meta(h, "og:title") or "", "image": img, "price": price, "currency": cur}


def download_image(url: str, tag: str = "") -> str | None:
    """Descarca o imagine si o salveaza ca JPEG. Intoarce calea locala sau None."""
    if not url:
        return None
    config.IMG.mkdir(parents=True, exist_ok=True)
    path = config.IMG / f"{tag}{hashlib.md5(url.encode()).hexdigest()[:12]}.jpg"
    if path.exists():
        return str(path)
    try:
        r = _get(url)
        if r.status_code != 200:
            return None
        Image.open(io.BytesIO(r.content)).convert("RGB").save(path, "JPEG", quality=92)
        return str(path)
    except Exception:
        return None


def gallery(url: str, extra: list[str | None] = ()) -> list[str]:
    """Toate pozele produsului: galeria Shopify (/products/x.json) + poze suplimentare (og:image, creativ reclama)."""
    urls: list[str] = []
    m = re.match(r"(https?://[^/]+)/(?:collections/[^/]+/)?products/([^/?#]+)", url)
    if m:
        try:
            r = _get(f"{m.group(1)}/products/{m.group(2)}.json")
            if r.status_code == 200:
                urls += [i["src"] for i in r.json().get("product", {}).get("images", []) if i.get("src")]
        except Exception:  # noqa: BLE001
            pass
    urls += [u for u in extra if u]
    seen, out = set(), []
    for u in urls:
        if u.startswith("//"):
            u = "https:" + u
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out
