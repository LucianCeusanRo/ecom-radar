"""Optional: API-ul oficial Meta Ad Library (graph.facebook.com/ads_archive) pentru DESCOPERIRE de magazine.

In UE (deci si RO) API-ul intoarce si reclame comerciale (DSA). Necesita: cont Facebook cu identitate verificata,
o aplicatie Meta for Developers si un access token (User token cu acces Ad Library). Pune token-ul in
meta_adlib_token.txt (o linie). Fara fisier => modulul nu face nimic. API-ul NU da linkul exact al produsului,
doar pagina + domeniul (ad_creative_link_captions), deci il folosim ca sa adaugam magazine noi in pages.json; scanarea
lor (reclame + produse) se face apoi normal.
"""
import json
import re
import urllib.parse
import urllib.request

import config

TOKEN_FILE = config.ROOT / "meta_adlib_token.txt"
API = "https://graph.facebook.com/v21.0/ads_archive"


def token() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def discover(pages: dict, terms: list[str], limit: int = 100) -> int:
    tok = token()
    if not tok:
        return 0
    added = 0
    for q in terms:
        params = {"search_terms": q, "ad_reached_countries": json.dumps([config.COUNTRY]), "ad_active_status": "ACTIVE",
                  "ad_type": "ALL", "fields": "page_id,page_name,ad_creative_link_captions,ad_delivery_start_time",
                  "limit": str(limit), "access_token": tok}
        try:
            with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(params)}", timeout=30) as r:
                data = json.loads(r.read().decode("utf8")).get("data", [])
        except Exception as e:  # noqa: BLE001
            print(f"[metaapi] eroare API oficial ('{q}'): {type(e).__name__}", flush=True)
            continue
        for ad in data:
            pid, name = ad.get("page_id"), ad.get("page_name") or ""
            caps = ad.get("ad_creative_link_captions") or []
            dom = next((c.lower().removeprefix("www.") for c in caps if re.match(r"^[\w.-]+\.[a-z]{2,}$", c or "", re.I)), "")
            if not pid or any(b in dom for b in config.BLOCK_DOMAINS):
                continue
            if pid not in pages:
                pages[pid] = {"name": name, "source": "api"}
                added += 1
            if dom and not pages[pid].get("domain"):
                pages[pid]["domain"] = dom
    print(f"[metaapi] API oficial: {added} magazine noi descoperite", flush=True)
    return added
