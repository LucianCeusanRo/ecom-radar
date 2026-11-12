"""Screenshots of Ecom Radar (offline index.html = same UI + same data) + live login redirect."""
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE / "screens"
OUT.mkdir(exist_ok=True)
url = (HERE / "index.html").as_uri()
with sync_playwright() as pw:
    try:
        br = pw.chromium.launch(channel="msedge", headless=True)
    except Exception:
        br = pw.chromium.launch(headless=True)
    pg = br.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    for v in ("produse", "top", "calc", "bf", "istoric", "setari"):
        pg.goto(url + "#" + v)
        pg.wait_for_timeout(1400)
        pg.screenshot(path=str(OUT / ("desk_%s.png" % v)), full_page=(v != "produse"))
    pg.goto(url + "#produse"); pg.wait_for_timeout(1200)
    pg.click("[data-det]"); pg.wait_for_timeout(900)
    pg.screenshot(path=str(OUT / "desk_detalii.png"))
    pg.goto(url + "#produse"); pg.evaluate("localStorage.setItem('ecomRadar.v1', JSON.stringify({theme:'light'}))")
    pg.reload(); pg.wait_for_timeout(1400)
    pg.screenshot(path=str(OUT / "desk_produse_light.png"))
    pg.evaluate("localStorage.clear()")
    m = br.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    m.goto(url + "#produse"); m.wait_for_timeout(1500)
    m.screenshot(path=str(OUT / "mobile_produse.png"))
    m.goto(url + "#calc"); m.wait_for_timeout(1200)
    m.screenshot(path=str(OUT / "mobile_calc.png"))
    l = br.new_page(viewport={"width": 1200, "height": 800})
    r = l.goto(os.environ.get("ECOM_DASH_URL", "http://127.0.0.1:8765/ecom"), wait_until="domcontentloaded")
    l.wait_for_timeout(1500)
    print("live:", l.url, r.status if r else None)
    l.screenshot(path=str(OUT / "live_ecom_login.png"))
    br.close()
print("ok", sorted(p.name for p in OUT.glob("*.png")))
