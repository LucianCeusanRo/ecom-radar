"""Shopify (magazinul propriu) pentru Ecom Radar: ponturile bune devin automat produse DRAFT (nepublicate).

Autentificare: aplicatia custom "Ecom Radar" (Dev Dashboard), grant client_credentials.
  - client_id in config.py (SHOPIFY_CLIENT_ID), secretul DOAR in shopify_client_secret.txt (nu in cod / loguri).
  - tokenul (valabil ~24h) se tine in .shopify_token.json cu ora de expirare; se reinnoieste singur cand mai are
    sub 1h (sau imediat dupa un 401). Tokenul si secretul nu se afiseaza niciodata.
API: Admin GraphQL (SHOPIFY_API_VERSION, implicit 2026-10), header X-Shopify-Access-Token.
Dedupe: fiecare draft are tag-ul "ali:<aliId>" + metafield ecom_radar.ali_id; inainte de creare cautam
(evidenta locala out/shopify_drafts.json + cautare dupa tag in Shopify) => acelasi produs Ali nu se creeaza de doua ori.

CLI:
  python shopify_api.py --test                         # nume magazin + numar produse (fara secrete)
  python shopify_api.py --from-content <package.json | content_<aliId>.json> [--title ..] [--images u1,u2]
                        [--mockup cale.png] [--variants "1 buc:89:139,2 buc:129:189,4 buc:219:319"] [--dry-run]
  python shopify_api.py --verify <aliId | id produs>   # verifica un draft (status, variante, poze)
"""
import argparse
import datetime as dt
import html
import json
import math
import mimetypes
import re
import sys
import time
from pathlib import Path

import requests

import config

SHOP = getattr(config, "SHOPIFY_SHOP", "")
ADMIN_STORE = getattr(config, "SHOPIFY_ADMIN_STORE", "")
API_VERSION = getattr(config, "SHOPIFY_API_VERSION", "2026-10")
CLIENT_ID = getattr(config, "SHOPIFY_CLIENT_ID", "")
VENDOR = getattr(config, "SHOPIFY_VENDOR", "My Store")
BASE_TAG = getattr(config, "SHOPIFY_TAG", "ecom-radar")
SECRET_FILE = config.ROOT / "shopify_client_secret.txt"
TOKEN_FILE = config.ROOT / ".shopify_token.json"
DRAFTS_FILE = config.OUT / "shopify_drafts.json"
REFRESH_MARGIN_S = 3600          # reinnoim tokenul cand mai are sub 1h
TIMEOUT = 40
OPTION = "Pachet"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EcomRadar/1.0"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class ShopifyError(Exception):
    pass


# --- secrete (niciodata in loguri) ------------------------------------------------------
_TOKEN: dict = {}


def _secret() -> str:
    try:
        s = SECRET_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        s = ""
    if not s:
        raise ShopifyError(f"lipseste secretul aplicatiei ({SECRET_FILE.name})")
    return s


def _scrub(text) -> str:
    """Scoate secretul si tokenul din orice mesaj de eroare inainte sa ajunga in log."""
    t = str(text)
    for s in (_TOKEN.get("access_token"), _secret_or_none()):
        if s:
            t = t.replace(s, "***")
    return re.sub(r"shp[a-z]{2}_[A-Za-z0-9]{10,}", "***", t)


def _secret_or_none():
    try:
        return _secret()
    except ShopifyError:
        return None


def _load_cache() -> dict:
    try:
        d = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return d if d.get("access_token") and d.get("shop") == SHOP else {}
    except (OSError, ValueError):
        return {}


def token_info() -> dict:
    """Doar metadate (fara token): cat mai e valabil, scope-uri."""
    tok = _TOKEN or _load_cache()
    left = tok.get("expires_at", 0) - time.time()
    return {"valid_for_min": round(left / 60) if tok else None, "scope": tok.get("scope", ""),
            "obtained_at": tok.get("obtained_at_iso")}


def get_token(force: bool = False) -> str:
    """Token Admin API din cache; cerere noua (client_credentials) daca lipseste / expira in < 1h / force."""
    global _TOKEN
    now = time.time()
    if not force:
        tok = _TOKEN or _load_cache()
        if tok.get("access_token") and tok.get("expires_at", 0) - now > REFRESH_MARGIN_S:
            _TOKEN = tok
            return tok["access_token"]
    try:
        r = requests.post(f"https://{SHOP}/admin/oauth/access_token", timeout=TIMEOUT,
                          data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": _secret()},
                          headers={"Accept": "application/json"})
    except requests.RequestException as e:
        raise ShopifyError(f"token: retea {type(e).__name__}") from None
    if r.status_code != 200:
        raise ShopifyError(f"token: HTTP {r.status_code} {_scrub(r.text[:200])}")
    d = r.json()
    if not d.get("access_token"):
        raise ShopifyError("token: raspuns fara access_token")
    _TOKEN = {"shop": SHOP, "access_token": d["access_token"], "scope": d.get("scope", ""),
              "expires_at": now + int(d.get("expires_in") or 86399),
              "obtained_at_iso": dt.datetime.now().isoformat(timespec="seconds")}
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_TOKEN), encoding="utf-8")
    tmp.replace(TOKEN_FILE)
    return _TOKEN["access_token"]


# --- GraphQL -------------------------------------------------------------------------------
def graphql(query: str, variables: dict | None = None, retries: int = 4) -> dict:
    """Admin GraphQL; reinnoieste tokenul la 401, asteapta la THROTTLED/429/5xx. Ridica ShopifyError."""
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    refreshed = False
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json={"query": query, "variables": variables or {}}, timeout=TIMEOUT,
                              headers={"X-Shopify-Access-Token": get_token(), "Content-Type": "application/json"})
        except requests.RequestException as e:
            if attempt < retries:
                time.sleep(2 + attempt * 2)
                continue
            raise ShopifyError(f"graphql: retea {type(e).__name__}") from None
        if r.status_code == 401 and not refreshed:
            get_token(force=True)
            refreshed = True
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(2 + attempt * 3)
            continue
        if r.status_code != 200:
            raise ShopifyError(f"graphql: HTTP {r.status_code} {_scrub(r.text[:300])}")
        d = r.json()
        errs = d.get("errors")
        if errs:
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errs if isinstance(e, dict)) \
                    and attempt < retries:
                time.sleep(3 + attempt * 3)
                continue
            raise ShopifyError(f"graphql: {_scrub(json.dumps(errs, ensure_ascii=False)[:500])}")
        return d.get("data") or {}
    raise ShopifyError("graphql: prea multe reincercari")


def admin_url(product_gid: str) -> str:
    return f"https://admin.shopify.com/store/{ADMIN_STORE}/products/{legacy_id(product_gid)}"


def legacy_id(gid: str) -> str:
    return str(gid).rsplit("/", 1)[-1]


# --- evidenta locala ---------------------------------------------------------------------
def _drafts() -> dict:
    try:
        return json.loads(DRAFTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_draft(ali_id: str, rec: dict):
    d = _drafts()
    d[str(ali_id)] = rec
    DRAFTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DRAFTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DRAFTS_FILE)


def draft_for(ali_id) -> dict | None:
    """Pentru dashboard: draftul creat (din evidenta locala), fara apel de retea."""
    return _drafts().get(str(ali_id))


Q_FIND = """query($q: String!) { products(first: 5, query: $q) { nodes { id title status handle tags } } }"""
Q_PRODUCT = """query($id: ID!) { product(id: $id) { id title status handle tags vendor productType totalInventory
  variants(first: 10) { nodes { id title price compareAtPrice sku } }
  media(first: 20) { nodes { id mediaContentType status alt ... on MediaImage { image { url } } } } } }"""


def find_by_ali(ali_id: str) -> dict | None:
    """Draft existent pentru oferta Ali: intai cautare dupa tag in Shopify, apoi evidenta locala (indexul de cautare
    Shopify are cateva secunde intarziere)."""
    tag = f"ali:{ali_id}"
    nodes = graphql(Q_FIND, {"q": f"tag:'{tag}'"}).get("products", {}).get("nodes", [])
    for n in nodes:
        if tag in (n.get("tags") or []):
            return n
    rec = _drafts().get(str(ali_id))
    if rec and rec.get("id"):
        p = graphql(Q_PRODUCT, {"id": rec["id"]}).get("product")
        if p:
            return p
        # sters manual din Shopify: NU il recream (decizia omului ramane)
        return {"id": rec["id"], "deleted": True, "title": rec.get("title"), "status": "DELETED"}
    return None


# --- creare draft ----------------------------------------------------------------------------
M_PRODUCT_SET = """mutation($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product { id title status handle variants(first: 10) { nodes { title price compareAtPrice } }
              media(first: 15) { nodes { mediaContentType status } } }
    userErrors { field message code } } }"""

M_STAGED = """mutation($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) { stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message } } }"""


def stage_upload(path: str) -> str | None:
    """Urca un fisier local (ex. mockup PNG) in Shopify; intoarce resourceUrl pentru media originalSource."""
    p = Path(path)
    if not p.is_file():
        return None
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    data = graphql(M_STAGED, {"input": [{"resource": "IMAGE", "filename": p.name, "mimeType": mime,
                                         "httpMethod": "POST", "fileSize": str(p.stat().st_size)}]})
    res = data.get("stagedUploadsCreate") or {}
    if res.get("userErrors") or not res.get("stagedTargets"):
        raise ShopifyError(f"stagedUploadsCreate: {res.get('userErrors')}")
    t = res["stagedTargets"][0]
    form = {x["name"]: x["value"] for x in t["parameters"]}
    with p.open("rb") as fh:
        r = requests.post(t["url"], data=form, files={"file": (p.name, fh, mime)}, timeout=120)
    if r.status_code not in (200, 201, 204):
        raise ShopifyError(f"upload mockup: HTTP {r.status_code}")
    return t["resourceUrl"]


def _files_input(images, title: str) -> tuple[list[dict], list[str]]:
    """images: URL public, cale locala (urcata prin stagedUploads), {"id": gid media existent} sau
    {"src": url|cale, "alt": "..."}. Ordinea se pastreaza. Intoarce (files, note)."""
    files, notes = [], []
    for i, it in enumerate(images or []):
        if not it:
            continue
        if isinstance(it, dict) and it.get("id"):
            files.append({"id": it["id"], **({"alt": it["alt"]} if it.get("alt") else {})})
            continue
        src = it.get("src") if isinstance(it, dict) else str(it)
        alt = (it.get("alt") if isinstance(it, dict) else None) or title[:120]
        if not src:
            continue
        if not re.match(r"^https?://", src):
            try:
                src = stage_upload(src)
            except Exception as e:  # noqa: BLE001  (o poza lipsa nu blocheaza draftul)
                notes.append(f"poza {i + 1}: {_scrub(str(e))[:100]}")
                src = None
            if not src:
                continue
        files.append({"originalSource": src, "contentType": "IMAGE", "alt": alt[:500]})
    return files, notes


def _money(x) -> str:
    return f"{float(x):.2f}"


def create_draft_product(title: str, body_html: str, vendor: str = VENDOR, product_type: str = "",
                         tags: list[str] | None = None, variants: list[dict] | None = None,
                         images: list[str] | None = None, ali_id: str | None = None, ali_url: str | None = None,
                         mockup_path: str | None = None, extra_metafields: list[dict] | None = None) -> dict:
    """Creeaza produsul cu status DRAFT (niciodata publicat). variants: [{"option": "1 buc", "price": 89,
    "compareAtPrice": 139}], images: URL-uri publice (Ali). Daca exista deja un draft pentru ali_id, il intoarce.
    Rezultat: {"id", "legacy_id", "admin_url", "created", "status", "title", "media"}."""
    if ali_id:
        ex = find_by_ali(str(ali_id))
        if ex:
            return {"id": ex["id"], "legacy_id": legacy_id(ex["id"]), "admin_url": admin_url(ex["id"]),
                    "created": False, "status": ex.get("status"), "title": ex.get("title"), "deleted": ex.get("deleted", False)}
    variants = variants or [{"option": "1 buc", "price": 0}]
    tags = list(dict.fromkeys([BASE_TAG] + (tags or []) + ([f"ali:{ali_id}"] if ali_id else [])))
    files, img_notes = _files_input(images, title)
    mock_note = "; ".join(img_notes) or None
    if mockup_path:
        try:
            ru = stage_upload(mockup_path)
            if ru:
                files.append({"originalSource": ru, "contentType": "IMAGE", "alt": f"{title[:100]} – mockup"})
        except Exception as e:  # noqa: BLE001  (mockup-ul e optional)
            mock_note = ((mock_note + "; ") if mock_note else "") + _scrub(str(e))[:150]
            print(f"[shopify] mockup sarit: {mock_note}", flush=True)
    metafields = []
    if ali_id:
        metafields.append({"namespace": "ecom_radar", "key": "ali_id", "type": "single_line_text_field", "value": str(ali_id)})
    if ali_url:
        metafields.append({"namespace": "ecom_radar", "key": "ali_url", "type": "url", "value": ali_url})
    metafields += extra_metafields or []
    inp = {
        "title": title[:255], "descriptionHtml": body_html, "vendor": vendor, "productType": product_type or "",
        "status": "DRAFT", "tags": tags,
        "productOptions": [{"name": OPTION, "position": 1, "values": [{"name": v["option"]} for v in variants]}],
        "variants": [{
            "optionValues": [{"optionName": OPTION, "name": v["option"]}],
            "price": _money(v["price"]),
            **({"compareAtPrice": _money(v["compareAtPrice"])} if v.get("compareAtPrice") else {}),
            "inventoryPolicy": "CONTINUE",
            "inventoryItem": {"tracked": False, **({"sku": v["sku"]} if v.get("sku") else {})},
        } for v in variants],
        "files": files,
        "metafields": metafields,
    }
    res = graphql(M_PRODUCT_SET, {"input": inp}).get("productSet") or {}
    if res.get("userErrors"):
        raise ShopifyError(f"productSet: {json.dumps(res['userErrors'], ensure_ascii=False)[:500]}")
    p = res.get("product")
    if not p:
        raise ShopifyError("productSet: fara produs in raspuns")
    out = {"id": p["id"], "legacy_id": legacy_id(p["id"]), "admin_url": admin_url(p["id"]), "created": True,
           "status": p["status"], "title": p["title"], "media": len(files), "mockup_skipped": mock_note}
    if ali_id:
        _save_draft(str(ali_id), {"id": p["id"], "admin_url": out["admin_url"], "title": p["title"],
                                  "created": dt.datetime.now().isoformat(timespec="seconds")})
    return out


# --- din continutul generat (content.py) ---------------------------------------------------
def _round9(x: float) -> int:
    x = max(19, math.ceil(x))
    return int(math.ceil((x + 1) / 10.0) * 10 - 1)


def bundle_variants(price: int, compare: int | None, ali_price: float | None = None, ali_id: str | None = None) -> list[dict]:
    """1 / 2 / 4 buc. Ex. 89/139 -> 2 buc 129/189, 4 buc 219/319; niciodata sub pragul de profit pentru N bucati."""
    def floor_n(n):
        if not ali_price:
            return 0
        rev, ad = (1 / (1 + config.TVA), 1.0) if config.PLATITOR_TVA else (1.0, 1 + config.TVA)
        margin = rev - ad / config.ROAS - config.MIN_PROFIT_PCT / 100
        goods = ali_price * (1 if config.PLATITOR_TVA else 1 + config.TVA)
        return (n * (goods + config.SHIP_ALI) + config.SHIP_RO) / margin if margin > 0 else 0
    out = []
    for n, mult in ((1, 1.0), (2, 1.45), (4, 2.45)):
        p = price if n == 1 else max(_round9(int(price * mult)), _round9(floor_n(n)))
        c = (compare if n == 1 and compare else _round9(int(p * 1.45)))
        out.append({"option": f"{n} buc", "price": p, "compareAtPrice": c,
                    "sku": f"ALI-{ali_id}-{n}" if ali_id else None})
    return out


def shopify_body(c: dict, price: int, compare: int) -> str:
    """Descrierea HTML (content.render_html) fara randul de pret: pretul il afiseaza tema din variante."""
    import content
    body = content.render_html(c, price, compare)
    return "\n".join(l for l in body.splitlines() if not l.startswith("<p><s>"))


def _content_full(cv: dict) -> dict:
    """content_<id>.json complet (descriere, how_to, faq) langa html_path; rezerva: dict-ul scurt cv."""
    hp = cv.get("html_path")
    if hp:
        hp = Path(hp)
        for cand in (hp.with_suffix(".json"), config.OUT / "content" / f"{hp.stem}.json"):
            try:
                return {**cv, **json.loads(cand.read_text(encoding="utf-8"))}
            except (OSError, ValueError):
                continue
    return cv


M_UPDATE = """mutation($id: ID!, $input: ProductSetInput!) {
  productSet(synchronous: true, identifier: {id: $id}, input: $input) {
    product { id title status handle vendor variants(first: 10) { nodes { title price compareAtPrice } }
              media(first: 20) { nodes { id mediaContentType status alt ... on MediaImage { image { url } } } } }
    userErrors { field message code } } }"""


def update_product(gid: str, **fields) -> dict:
    """Actualizeaza campuri ale unui produs (title, handle, descriptionHtml, vendor, files, seo...).
    ATENTIE: 'files' inlocuieste toata lista de media (pastreaza media existenta cu {"id": ...}).
    Statusul ramane DRAFT daca nu se cere explicit altceva; variantele si tag-urile nu se ating."""
    inp = {k: v for k, v in fields.items() if v is not None}
    inp.setdefault("status", "DRAFT")
    res = graphql(M_UPDATE, {"id": gid, "input": inp}).get("productSet") or {}
    if res.get("userErrors"):
        raise ShopifyError(f"productSet(update): {json.dumps(res['userErrors'], ensure_ascii=False)[:500]}")
    return res.get("product") or {}


def media_urls(gid: str, wait_s: int = 60) -> list[dict]:
    """Media produsului in ordine [{id, alt, url, status}], asteapta sa devina READY (max wait_s)."""
    t0 = time.time()
    while True:
        p = graphql(Q_PRODUCT, {"id": gid}).get("product") or {}
        nodes = (p.get("media") or {}).get("nodes") or []
        out = [{"id": n.get("id"), "alt": n.get("alt") or "", "status": n.get("status"),
                "url": ((n.get("image") or {}).get("url"))} for n in nodes]
        if all(x["status"] in ("READY", "FAILED") for x in out) or time.time() - t0 > wait_s:
            return out
        time.sleep(3)


def _render_body(c: dict, price: int, compare: int, variants=None, info_urls=None) -> str:
    """Descriere in stil pagina mobila (content.render_shopify); rezerva: shopify_body vechi."""
    try:
        import content
        if hasattr(content, "render_shopify"):
            return content.render_shopify(c, variants or [], info_urls or {})
    except Exception as e:  # noqa: BLE001
        print(f"[shopify] render_shopify sarit: {type(e).__name__}: {_scrub(str(e))[:120]}", flush=True)
    return shopify_body(c, price, compare)


INFO_ALT = {"beneficii": "beneficii", "cum_se_foloseste": "cum se folosește", "specificatii": "specificații"}


def ro_gallery(ro: dict | None, mockup_path: str | None, title: str, fallback_urls: list[str]) -> list:
    """Ordinea galeriei: poza RO 1, poza RO 2, infografic beneficii, restul pozelor RO, cum se foloseste,
    specificatii, mockup ultimul. Fara poze RO -> pozele originale Ali."""
    photos = [p for p in ((ro or {}).get("photos") or []) if p and Path(p).is_file()]
    info = {k: v for k, v in ((ro or {}).get("infographics") or {}).items() if v and Path(v).is_file()}
    imgs: list = [{"src": p, "alt": title[:120]} for p in photos] or list(fallback_urls)
    def ins(pos, key):
        if key in info:
            imgs.insert(min(pos, len(imgs)), {"src": info[key], "alt": f"{title[:90]} – {INFO_ALT[key]}"})
    ins(2, "beneficii")
    ins(len(imgs), "cum_se_foloseste")
    ins(len(imgs), "specificatii")
    if mockup_path and Path(mockup_path).is_file():
        imgs.append({"src": mockup_path, "alt": f"{title[:100]} – mockup"})
    return imgs


def _info_from_media(media: list[dict]) -> dict:
    out = {}
    for x in media:
        for k, a in INFO_ALT.items():
            if x.get("url") and x.get("alt", "").endswith("– " + a):
                out[k] = x["url"]
    return out


def from_match(m: dict, cv: dict | None, mockup_path: str | None = None, ro: dict | None = None) -> dict | None:
    """Hook pentru run.py: draft doar daca SHOPIFY_AUTO_DRAFT si NOTA >= SHOPIFY_MIN_NOTA. None = sarit.
    ro = rezultatul ro_images.build_for_match (poze romanizate + infografice); daca lipseste -> poza Ali originala."""
    if not getattr(config, "SHOPIFY_AUTO_DRAFT", False) or not cv:
        return None
    nota = (m.get("card") or {}).get("nota") or 0
    if nota < getattr(config, "SHOPIFY_MIN_NOTA", 6):
        return None
    c = _content_full(cv)
    price, compare = int(cv["price"]), int(cv.get("compare_at") or 0)
    aid = str(m["ali_item_id"])
    title = c.get("title") or m.get("title")
    tags = [f"nota:{nota}"] + (["pet"] if m.get("pet") else []) + (["q4"] if m.get("black_friday") else [])
    variants = bundle_variants(price, compare, m.get("ali_price"), aid)
    fallback = [u for u in [m.get("ali_img_url")] if u]
    images = ro_gallery(ro, mockup_path, title, fallback) if ro else fallback
    try:
        r = create_draft_product(
            title=title, body_html=_render_body(c, price, compare, variants), vendor=VENDOR,
            product_type="", tags=tags, variants=variants, images=images, ali_id=aid, ali_url=m.get("ali_url"),
            mockup_path=None if ro else mockup_path)
    except ShopifyError as e:
        if not ro:
            raise
        print(f"[shopify] poze RO respinse ({_scrub(str(e))[:120]}), reincerc cu poza Ali", flush=True)
        r = create_draft_product(
            title=title, body_html=_render_body(c, price, compare, variants), vendor=VENDOR,
            product_type="", tags=tags, variants=variants, images=fallback, ali_id=aid, ali_url=m.get("ali_url"),
            mockup_path=mockup_path)
        return r
    if r.get("created"):
        try:   # a doua trecere: infografice + poza mica in oferta + link-uri ?variant= (exista doar dupa creare)
            media = media_urls(r["id"], 45)
            info = _info_from_media(media)
            thumb = next((x["url"] for x in media if x.get("url") and not x.get("alt", "").endswith("mockup")), None)
            if thumb:
                info["thumb"] = thumb
            info = {k: v for k, v in info.items() if v}
            vnodes = ((graphql(Q_PRODUCT, {"id": r["id"]}).get("product") or {}).get("variants") or {}).get("nodes") or []
            vlive = [{"id": n["id"], "option": n["title"], "price": float(n["price"]),
                      "compareAtPrice": float(n["compareAtPrice"]) if n.get("compareAtPrice") else None} for n in vnodes]
            update_product(r["id"], descriptionHtml=_render_body(c, price, compare, vlive or variants, info))
        except Exception as e:  # noqa: BLE001
            print(f"[shopify] descriere finala sarita: {type(e).__name__}: {_scrub(str(e))[:120]}", flush=True)
    return r


# --- CLI ---------------------------------------------------------------------------------------
def cmd_test() -> int:
    d = graphql("{ shop { name myshopifyDomain primaryDomain { host } currencyCode } productsCount { count } }")
    s = d["shop"]
    ti = token_info()
    print(f"[shopify] OK magazin: {s['name']} | {s['primaryDomain']['host']} ({s['myshopifyDomain']}) | moneda {s['currencyCode']}")
    sc = [x for x in ti["scope"].split(",") if x]
    need = [x for x in ("write_products", "write_files") if x not in sc]
    print(f"[shopify] produse: {d['productsCount']['count']} | API {API_VERSION} | token valabil inca ~{ti['valid_for_min']} min "
          f"(obtinut {ti['obtained_at']}, reinnoire automata sub 60 min) | {len(sc)} scope-uri"
          + (f" | LIPSESC: {', '.join(need)}" if need else " | write_products + write_files OK"))
    return 0


def _parse_variants(s: str) -> list[dict]:
    out = []
    for part in s.split(","):
        name, p, *c = [x.strip() for x in part.split(":")]
        out.append({"option": name, "price": float(p), "compareAtPrice": float(c[0]) if c and c[0] else None})
    return out


def cmd_from_content(path: str, a) -> int:
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(d.get("content"), dict):            # package.json (make_package / run)
        cv, ali_url, ali_price = d["content"], d.get("ali_url"), d.get("ali_price")
        nota = (d.get("card") or {}).get("nota")
    else:                                             # content_<aliId>.json
        cv, ali_url, ali_price, nota = d, None, None, None
    mm = re.search(r"(\d{10,})", (ali_url or "") + " " + p.name)
    aid = a.ali_id or (mm.group(1) if mm else None)
    if not aid:
        raise SystemExit("nu gasesc id-ul AliExpress (foloseste --ali-id)")
    c = _content_full(cv)
    if p.parent.joinpath(f"content_{aid}.json").exists() and not c.get("description"):
        c = {**c, **json.loads(p.parent.joinpath(f"content_{aid}.json").read_text(encoding="utf-8"))}
    price, compare = int(c.get("price") or cv["price"]), int(c.get("compare_at") or cv.get("compare_at") or 0)
    variants = _parse_variants(a.variants) if a.variants else bundle_variants(price, compare, ali_price, aid)
    for v in variants:
        v.setdefault("sku", f"ALI-{aid}-{v['option'].split()[0]}")
    body = Path(a.body_html).read_text(encoding="utf-8") if a.body_html else shopify_body(c, price, compare)
    images = [u.strip() for u in (a.images or "").split(",") if u.strip()]
    tags = [t.strip() for t in (a.tags or "").split(",") if t.strip()] + ([f"nota:{nota}"] if nota else [])
    title = a.title or c.get("title")
    if a.dry_run:
        print(json.dumps({"title": title, "ali_id": aid, "variants": variants, "images": images, "mockup": a.mockup,
                          "tags": tags, "body_chars": len(body)}, ensure_ascii=False, indent=1))
        return 0
    r = create_draft_product(title=title, body_html=body, vendor=a.vendor or VENDOR, product_type=a.product_type or "",
                             tags=tags, variants=variants, images=images, ali_id=aid, ali_url=ali_url,
                             mockup_path=a.mockup)
    print(f"[shopify] {'CREAT' if r['created'] else 'EXISTA DEJA'} {r['status']}: {r['title']}\n[shopify] admin: {r['admin_url']}")
    return 0


def cmd_verify(ref: str) -> int:
    gid = ref if ref.startswith("gid://") else None
    p = None
    if not gid:   # numeric: intai id de produs Shopify, apoi id AliExpress
        p = graphql(Q_PRODUCT, {"id": f"gid://shopify/Product/{ref}"}).get("product")
        if not p and len(ref) >= 12:
            ex = find_by_ali(ref)
            gid = ex and not ex.get("deleted") and ex["id"]
    if gid and not p:
        p = graphql(Q_PRODUCT, {"id": gid}).get("product")
    if not p:
        print("[shopify] produsul nu exista")
        return 1
    print(f"[shopify] {p['status']} | {p['title']} | vendor {p['vendor']} | tip '{p['productType']}'")
    print(f"[shopify] tags: {', '.join(p['tags'])}")
    for v in p["variants"]["nodes"]:
        print(f"[shopify]   varianta {v['title']}: {v['price']} lei (compare-at {v['compareAtPrice']})")
    print(f"[shopify] media: " + ", ".join(f"{x['mediaContentType']}={x['status']}" for x in p["media"]["nodes"]))
    print(f"[shopify] admin: {admin_url(p['id'])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Shopify magazinul propriu – drafturi Ecom Radar")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--from-content")
    ap.add_argument("--verify")
    ap.add_argument("--title")
    ap.add_argument("--images", help="URL-uri publice separate prin virgula")
    ap.add_argument("--mockup", help="PNG local urcat prin stagedUploadsCreate")
    ap.add_argument("--variants", help='"1 buc:89:139,2 buc:129:189,4 buc:219:319"')
    ap.add_argument("--body-html", help="fisier HTML pentru descriere (implicit generat din content json)")
    ap.add_argument("--tags", help="tag-uri extra, separate prin virgula")
    ap.add_argument("--vendor")
    ap.add_argument("--product-type")
    ap.add_argument("--ali-id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        if a.test:
            return cmd_test()
        if a.from_content:
            return cmd_from_content(a.from_content, a)
        if a.verify:
            return cmd_verify(a.verify)
    except ShopifyError as e:
        print(f"[shopify] EROARE: {_scrub(e)}")
        return 2
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
