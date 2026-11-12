"""Genereaza exemple (mockup + mesaj + continut Shopify) pentru produse deja potrivite, FARA sa trimita nimic.
    python examples.py   -> out/examples/<id>.txt + out/mockup_<id>.png + out/content/content_<id>.*"""
import json
import sys

import config
import content
import llm
import mockup
import notify
import page

EX = [
    ("https://miximo.ro/products/kit-zgarieturi", "1005005245638422", 2.33, "Kit reparare zgarieturi auto"),
    ("https://24deals.ro/products/set-3-colanti-vatuiti-fara-cusatura-marime-universala-sezon-toamna-iarna", "1005008728964309", 8.75,
     "Set 3 colanti vatuiti fara cusatura, toamna-iarna"),
    ("https://nexisolution.ro/products/carlige-autoadezive", "1005010572659996", 8.03, "Set 20 carlige autoadezive"),
]
sel = sys.argv[1:] or [e[1] for e in EX]
outdir = config.OUT / "examples"
outdir.mkdir(parents=True, exist_ok=True)
for url, aid, aprice, fallback in EX:
    if aid not in sel:
        continue
    info = page.fetch_page(url)
    title = (info.get("title") or fallback).split(" - Magazin")[0].strip()
    urls = page.gallery(url, [info.get("image")])
    img = next((p for p in (page.download_image(u, "ex_") for u in urls[:3]) if p), None)
    sell = info.get("price") if info.get("currency") == "RON" and info.get("price") and 15 <= info["price"] <= 1500 else None
    econ = config.profit_at_roas(sell or 0, aprice)
    desc = f"{fallback} (Romanian store title: {title})"
    note = f"AliExpress {aprice:.2f} lei; minimum sell price for 20% profit {econ['min_price']:.0f} lei" + (f"; competitor sells at {sell:.0f} lei" if sell else "")
    ang, bf, why, card = llm.angles_bf(desc, title, note)
    m = {"title": title, "ali_price": aprice, "ali_url": f"https://www.aliexpress.com/item/{aid}.html", "ali_item_id": aid,
         "ali_title": fallback, "sell_price": sell, "econ": econ, "page_name": url.split("/")[2], "started": "-", "days_running": 0,
         "ad_url": "-", "link_url": url, "angles": ang, "black_friday": bf, "bf_reason": why, "card": card, "pet": False}
    cv = content.build(m, desc)
    mk = mockup.make(m, cv, img)
    m["text"] = notify.build_message(m)
    rep = {"ali_id": aid, "ali_price": aprice, "ali_url": m["ali_url"], "store": url, "competitor_price": sell,
           "min_price": round(econ["min_price"]), "recommended": cv and cv["price"], "compare_at": cv and cv["compare_at"],
           "title": cv and cv["title"], "nota": card.get("nota"), "nota_reason": card.get("nota_reason"),
           "score": {k: card.get(k) for k in ("wow", "problema", "nesaturat", "upsell", "compact")}, "bf": bf,
           "mockup": mk, "content": cv and cv["html_path"]}
    (outdir / f"{aid}.txt").write_text(m["text"] + "\n\n" + (content.short_message(cv) if cv else ""), encoding="utf8")
    (outdir / f"{aid}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf8")
    print(json.dumps(rep, ensure_ascii=False), flush=True)
