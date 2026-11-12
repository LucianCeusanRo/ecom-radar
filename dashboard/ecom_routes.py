"""/ecom - Ecom Radar: products found by ads-monitor (separate tool, NOT betting).

Admin only, same signed sp_admin session as /admin and /emag (live_dashboard.admin_auth.verify_session).
GET  /ecom               -> dashboard SPA (ECOM_DASH_DIR/app.html)
                            not logged in -> 303 /admin/login?next=/ecom ; member -> 403
GET  /api/ecom/products  -> data/ecom_products.json (built by build_data.py every 15 min) ; non-admin 401
GET  /ecom/img/<name>    -> data/img/<name> (thumbnails only, strict name check) ; non-admin 401
POST /api/ecom/refresh   -> rebuild data now (background, max once / 60 s) ; non-admin 401
Read-only over ads-monitor; data folder overridable with env ECOM_DASH_DIR.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

router = APIRouter()

ECOM_DIR = Path(os.environ.get("ECOM_DASH_DIR", str(Path(__file__).resolve().parent)))
DATA_FILE = ECOM_DIR / "data" / "ecom_products.json"
IMG_DIR = ECOM_DIR / "data" / "img"
APP_FILE = ECOM_DIR / "app.html"
_IMG_RE = re.compile(r"^(mk|mt|st)_[A-Za-z0-9_-]{1,80}\.jpg$")
_NO_CACHE = {"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow",
             "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
_cache = {"mt": None, "data": None}
_refresh = {"at": 0.0}
_lock = threading.Lock()


def _is_admin(request: Request) -> bool:
    from live_dashboard.admin_auth import COOKIE_NAME, verify_session
    try:
        return bool(verify_session(request.cookies.get(COOKIE_NAME)).get("ok"))
    except Exception:
        return False


def _member_name(request: Request) -> Optional[str]:
    try:
        from live_dashboard.member_accounts import member_from_token
        m = member_from_token(request.cookies.get("sp_member"))
        return (m.get("username") or "cont membru") if m else None
    except Exception:
        return None


def _deny() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "admin_required"}, status_code=401, headers=_NO_CACHE)


def _page_fallback(msg: str) -> str:
    return ('<!DOCTYPE html><html lang="ro"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>'
            '<title>Ecom Radar</title></head><body style="font:16px system-ui;background:#0B0F1A;color:#E8EDF7;padding:24px">'
            '<h1>🛒 Ecom Radar</h1><p>%s</p><p><a style="color:#A855F7" href="/admin">Inapoi la consola</a></p></body></html>') % msg


@router.get("/ecom", response_class=HTMLResponse)
@router.get("/ecom/", response_class=HTMLResponse)
def ecom_page(request: Request):
    if not _is_admin(request):
        member = _member_name(request)
        if member:
            return HTMLResponse(_page_fallback("Esti conectat cu un cont de membru. Ecom Radar se vede doar din contul admin. "
                                               '<a style="color:#A855F7" href="/admin/logout?next=/ecom">Iesi si intra cu contul admin</a>'),
                                status_code=403, headers=_NO_CACHE)
        return RedirectResponse(url="/admin/login?next=/ecom", status_code=303, headers=_NO_CACHE)
    try:
        html = APP_FILE.read_text(encoding="utf-8")
    except OSError:
        html = _page_fallback("Lipseste app.html in %s." % ECOM_DIR)
    return HTMLResponse(html, headers=_NO_CACHE)


@router.get("/api/ecom/products")
def ecom_products(request: Request):
    if not _is_admin(request):
        return _deny()
    try:
        mt = DATA_FILE.stat().st_mtime_ns
    except OSError:
        return JSONResponse({"ok": False, "error": "no_data", "products": [], "generated_at": None},
                            status_code=200, headers=_NO_CACHE)
    with _lock:
        if _cache["mt"] != mt:
            for _ in range(4):
                try:
                    _cache["data"] = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                    _cache["mt"] = mt
                    break
                except (OSError, ValueError):
                    time.sleep(0.05)
        data = _cache["data"] or {"products": []}
    return JSONResponse(dict(data, ok=True), headers=_NO_CACHE)


@router.get("/ecom/img/{name}")
def ecom_img(name: str, request: Request):
    if not _is_admin(request):
        return _deny()
    if not _IMG_RE.match(name or ""):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404, headers=_NO_CACHE)
    p = IMG_DIR / name
    if not p.is_file():
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404, headers=_NO_CACHE)
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600", "X-Robots-Tag": "noindex"})


@router.post("/api/ecom/refresh")
def ecom_refresh(request: Request):
    if not _is_admin(request):
        return _deny()
    now = time.time()
    if now - _refresh["at"] < 60:
        return JSONResponse({"ok": False, "error": "prea_des (max 1/minut)"}, headers=_NO_CACHE)
    _refresh["at"] = now
    script = ECOM_DIR / "build_data.py"
    if not script.exists():
        return JSONResponse({"ok": False, "error": "lipseste build_data.py"}, headers=_NO_CACHE)
    py = str(Path(sys.executable).with_name("pythonw.exe"))
    if not Path(py).exists():
        py = sys.executable
    subprocess.Popen([py, str(script)], cwd=str(ECOM_DIR), creationflags=0x08000000, close_fds=True)
    return JSONResponse({"ok": True}, headers=_NO_CACHE)


@router.api_route("/api/ecom/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def ecom_api_other(rest: str, request: Request):
    if not _is_admin(request):
        return _deny()
    return JSONResponse({"ok": False, "error": "not_found"}, status_code=404, headers=_NO_CACHE)
