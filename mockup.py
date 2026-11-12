"""Mockup de telefon (1080x2340, o singura imagine): pagina de produs pe mobil in stilul magazinul propriu.
Fara stele/recenzii inventate si fara termene de livrare promise (marfa vine de la furnizor).

Ruleaza ca proces separat (Playwright sync nu poate porni de doua ori in acelasi fir cu cautarea AliExpress):
    python mockup.py date.json out.png
date.json: {"image": cale_poza, "title", "price", "compare_at", "bullets": [...], "store": "magazinul propriu"}
"""
import base64
import html
import io
import json
import subprocess
import sys
from pathlib import Path

import config

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box;margin:0;padding:0}
body{width:360px;height:780px;overflow:hidden;font-family:'Segoe UI',Roboto,Arial,sans-serif;background:#fff;color:#111}
.status{height:30px;display:flex;justify-content:space-between;align-items:center;padding:0 18px;font-size:13px;font-weight:600}
.ann{background:#111;color:#fff;font-size:10.5px;font-weight:700;text-align:center;padding:7px 4px;letter-spacing:.3px}
.hdr{height:50px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;border-bottom:1px solid #eee}
.logo{font-size:19px;font-weight:800;letter-spacing:-.3px}
.ic{font-size:20px;width:24px;text-align:center}
.img{width:360px;height:338px;background:#f6f6f6;display:flex;align-items:center;justify-content:center;overflow:hidden}
.img img{width:100%;height:100%;object-fit:cover;object-position:center top}
.dots{display:flex;gap:5px;justify-content:center;margin:8px 0 2px}.dots i{width:6px;height:6px;border-radius:50%;background:#ccc}
.dots i:first-child{background:#111}
.c{padding:6px 16px 0}
.stars{color:#f5a623;font-size:14px;letter-spacing:1px}.stars span{color:#555;font-size:11.5px;letter-spacing:0;margin-left:4px}
h1{font-size:18px;line-height:1.22;font-weight:800;margin:8px 0 7px;text-align:center}
.price{display:flex;align-items:center;justify-content:center;gap:9px;margin-bottom:9px}
.old{color:#888;text-decoration:line-through;font-size:15px}.new{color:#d0021b;font-size:23px;font-weight:800}
.badge{background:#d0021b;color:#fff;font-size:11px;font-weight:800;padding:3px 7px;border-radius:4px}
ul{list-style:none;margin:0 0 10px}li{font-size:12.6px;line-height:1.3;margin:3px 0}
.btn{display:block;background:#1f9d46;color:#fff;text-align:center;font-size:16px;font-weight:800;padding:14px 0;border-radius:10px;
     letter-spacing:.2px;box-shadow:0 3px 0 #157a35}
.trust{display:flex;justify-content:space-between;margin-top:10px;font-size:10.5px;color:#333;text-align:center}
.trust div{flex:1}.trust b{display:block;font-size:17px;margin-bottom:2px}
.ship{margin-top:9px;background:#eaf7ee;color:#1d7a3a;font-size:11.5px;font-weight:700;text-align:center;padding:7px;border-radius:6px}
</style></head><body>
<div class="status"><span>9:41</span><span>5G &nbsp;🔋</span></div>
<div class="ann">💸 PLATĂ RAMBURS LA LIVRARE • ↩️ RETUR 14 ZILE</div>
<div class="hdr"><span class="ic">☰</span><span class="logo">{store}</span><span class="ic">🛒</span></div>
<div class="img"><img src="{img}"></div>
<div class="dots"><i></i><i></i><i></i><i></i></div>
<div class="c">
<h1>{title}</h1>
<div class="price"><span class="old">{compare},00 lei</span><span class="new">{price},00 lei</span><span class="badge">-{pct}%</span></div>
<ul>{bullets}</ul>
<a class="btn">🛒 Comandă acum – Plata la livrare</a>
<div class="ship">✅ Livrare prin curier · Plata ramburs la primire</div>
<div class="trust"><div><b>🚚</b>Livrare prin curier</div><div><b>💸</b>Plata la livrare</div><div><b>↩️</b>Retur 14 zile</div></div>
</div></body></html>"""


def _data_uri(path: str) -> str:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1000, 1000))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def render(d: dict, out: str):
    from playwright.sync_api import sync_playwright
    e = html.escape
    title = d["title"] if len(d["title"]) <= 90 else d["title"][:87].rsplit(" ", 1)[0] + "…"
    pct = round((1 - d["price"] / d["compare_at"]) * 100) if d.get("compare_at") else 0
    page = (TEMPLATE.replace("{store}", e(d.get("store") or config.STORE_NAME)).replace("{img}", _data_uri(d["image"]))
            .replace("{title}", e(title)).replace("{price}", str(d["price"])).replace("{compare}", str(d["compare_at"]))
            .replace("{pct}", str(pct))
            .replace("{bullets}", "".join(f"<li>{e(str(b)[:80])}</li>" for b in (d.get("bullets") or [])[:4])))
    with sync_playwright() as pw:
        ch = d.get("channel", config.BROWSER_CHANNEL)
        b = pw.chromium.launch(channel=ch, headless=True) if ch else pw.chromium.launch(headless=True)
        try:
            pg = b.new_page(viewport={"width": 360, "height": 780}, device_scale_factor=3)
            pg.set_content(page, wait_until="load")
            pg.screenshot(path=out, full_page=False)
        finally:
            b.close()


def make(m: dict, cv: dict | None, image: str) -> str | None:
    """Apelat din run.py: scrie out/mockup_<aliId>.png intr-un proces separat. None la esec (pontul pleaca oricum)."""
    import content
    if cv:
        title, price, compare, bullets = cv["title"], cv["price"], cv["compare_at"], cv["bullets"]
    else:
        price, compare = content.prices(m)
        title = m["title"]
        bullets = [f"✅ {a['name']}" for a in (m.get("angles") or [])[:4]]
    out = config.OUT / f"mockup_{m['ali_item_id']}.png"
    spec = config.OUT / f"mockup_{m['ali_item_id']}.json"
    spec.write_text(json.dumps({"image": image, "title": title, "price": price, "compare_at": compare,
                                "bullets": bullets, "store": config.STORE_NAME}, ensure_ascii=False), encoding="utf8")
    try:
        r = subprocess.run([sys.executable, str(Path(__file__)), str(spec), str(out)], capture_output=True, text=True,
                           encoding="utf8", errors="replace", timeout=120, cwd=str(config.ROOT))
        if r.returncode != 0 or not out.exists():
            print(f"[mockup] esec: {(r.stderr or r.stdout)[-200:]}", flush=True)
            return None
    except subprocess.TimeoutExpired:
        print("[mockup] timeout", flush=True)
        return None
    finally:
        spec.unlink(missing_ok=True)
    return str(out)


if __name__ == "__main__":
    render(json.loads(Path(sys.argv[1]).read_text(encoding="utf8")), sys.argv[2])
