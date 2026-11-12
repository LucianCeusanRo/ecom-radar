"""python run.py --selftest [--no-ai] : verifica fiecare componenta, NU trimite nimic. Iesire 0 = fara FAIL."""
import json
import time
import traceback

import config

RES: list[tuple[str, str, str]] = []


def step(name: str, critical: bool = True):
    def deco(fn):
        t = time.time()
        try:
            info = fn() or ""
            RES.append(("PASS", name, f"{info} ({time.time() - t:.0f}s)"))
        except Exception as e:  # noqa: BLE001
            RES.append(("FAIL" if critical else "WARN", name, f"{type(e).__name__}: {str(e)[:160]}"))
            if critical:
                print(traceback.format_exc()[-600:], flush=True)
        print(f"[selftest] {RES[-1][0]:4} {name}: {RES[-1][2]}", flush=True)
        return fn
    return deco


def run(no_ai: bool = False) -> int:
    import collect, content, llm, metapool, mockup, notify, page, rules, shopfeed, vlm  # noqa: E401
    import run as runmod
    if no_ai:
        vlm.DISABLED = True
        config.LLM_ENABLED = False
    print(f"[selftest] mod {'FARA AI' if no_ai else 'normal'}", flush=True)
    ctx: dict = {}

    @step("profit ROAS4/20% neplatitor TVA (Ali 5/10/20 -> ~73/85/109 lei)")
    def _():
        v = [round(config.profit_at_roas(0, a)["min_price"]) for a in (5, 10, 20)]
        assert v == [72, 85, 109] or v == [73, 85, 109], v
        return v

    @step("Ollama", critical=False)
    def _():
        if no_ai:
            return "sarit (mod fara AI)"
        assert runmod.ollama_ready(), "Ollama nu raspunde"
        return config.VLM_MODEL

    @step("Claude (Cursor Agent CLI)", critical=False)
    def _():
        if no_ai:
            return "sarit (mod fara AI)"
        if "claude" not in config.LLM_PROVIDERS:
            return "nefolosit (nu e in ordinea AI: " + " > ".join(config.LLM_PROVIDERS) + ")"
        assert llm._exe(), "cursor-agent lipseste"
        return ("disponibil" if llm.available() else "indisponibil -> Ollama/reguli") + f"; furnizori activi: {llm.providers()}"

    @step("Gemini (API / CLI)", critical=False)
    def _():
        if no_ai:
            return "sarit (mod fara AI)"
        st = llm.gemini_status()
        assert llm._provider_ok("gemini"), st
        return st + "; ordine AI: " + " > ".join(config.LLM_PROVIDERS)

    @step("AI JSON parser (llm._extract_json)")
    def _():
        d = llm._extract_json('Raspuns: ```json\n{"index": 2, "reason": "ok"}\n```')
        assert d == {"index": 2, "reason": "ok"}, d
        return "ok"

    @step("setari (settings.json + validare)")
    def _():
        import settings
        assert config.COST_MAX <= 10 or "COST_MAX" in settings.load(), config.COST_MAX
        for f in settings.SCHEMA:
            settings.validate(f, settings.get(vars(config), f["key"]))   # valorile curente trebuie sa fie valide
        try:
            settings.validate(settings.BY_KEY["COST_MAX"], "abc")
            raise AssertionError("validarea nu respinge text la numar")
        except ValueError:
            pass
        return f"{len(settings.SCHEMA)} setari valide; Ali max {config.COST_MAX:g} lei; ignorate: {config.SETTINGS_IGNORED or 'niciuna'}"

    @step("public 30+ (categorii si ponderi)")
    def _():
        import market
        a = market.audience("Perie cu abur pentru pisici, îndepărtează părul")
        b = market.audience("Sutien push-up fără bretele, mărimi S-XXL")
        k = market.audience("Organizator sertar bucătărie")
        assert a["weight"] > 0 and b["weight"] <= config.AUDIENCE_DROP_AT and k["weight"] > 0, (a, b, k)
        return f"pisici {a['weight']:+g}, sutien {b['weight']:+g} (eliminat), bucătărie {k['weight']:+g}"

    @step("in crestere (istoric reclame, history.db)")
    def _():
        import datetime as dt
        import market
        today = dt.date.today()
        c = {"key": "selftest/products/x", "started": (today - dt.timedelta(days=6)).isoformat(), "days_running": 6,
             "ad_stats": {"ads": 4, "new7": 3, "first": (today - dt.timedelta(days=6)).isoformat(), "last": today.isoformat()}}
        r = market.rising(c, advertisers=1)
        old = market.rising({**c, "ad_stats": {**c["ad_stats"], "first": (today - dt.timedelta(days=120)).isoformat(), "new7": 0}},
                            advertisers=6)
        assert r["rising"] and r["adj"] > 0 and not old["rising"] and old["adj"] < 0, (r, old)
        con = market._db()
        con.execute("SELECT COUNT(*) FROM snap").fetchone()
        n = con.execute("SELECT COUNT(DISTINCT key) FROM snap").fetchone()[0]
        con.close()
        return f"nou {r['adj']:+g} (în creștere), vechi/saturat {old['adj']:+g}; istoric {n} produse"

    @step("eMAG saturare (1 cautare, cache)", critical=False)
    def _():
        import market
        e = market.emag_lookup("Perie cu abur pentru pisici", 71)
        assert e, "eMAG indisponibil/blocat (pipeline-ul continua fara eMAG)"
        v = market.emag_verdict(e)
        return f"'{e['query']}': {v['line']}; ajustare {v['adj']:+g}{' ELIMINAT' if v['drop'] else ''}"

    @step("Alibaba parser + cuvinte cheie")
    def _():
        import alibaba
        o = alibaba.parse('{"price":"5,22-8,29 RON","productId":"1","productUrl":"//www.alibaba.com/product-detail/Cat-Steam-Brush_1.html"} Min. order: 2 pieces')
        assert o and o[0]["price"] == 5.22 and o[0]["moq"] == "2", o
        kw = alibaba.keywords("Perie cu abur pentru pisici")
        assert "brush" in kw, kw
        return f"{kw}; {o[0]['price']} lei MOQ {o[0]['moq']}"

    @step("Alibaba cautare reala (1)", critical=False)
    def _():
        import alibaba
        e = alibaba.lookup("Cârlige adezive pentru perete")
        assert e, "fara oferta 1-10 lei sau Alibaba blocat (anti-bot)"
        return f"{e['query']}: {e['price']:.2f} lei/buc, MOQ {e['moq']} - {e['title'][:40]}"

    @step("gateway WhatsApp OpenClaw (fara trimitere)", critical=False)
    def _():
        assert runmod.gateway_ok(), "gateway neconectat"
        return "conectat"

    @step("Meta Ad Library (1 reclama, ruta directa)", critical=False)
    def _():
        ads, limited, _ = collect._once("cumpără acum", 1, None, metapool.DIRECT)
        assert ads and not limited, "limitat / 0 reclame"
        return f"{len(ads)} reclama; {metapool.summary()}"

    @step("sursa magazine Shopify (3 magazine)")
    def _():
        c = shopfeed.candidates(collect.load_pages(), max_shops=3)
        ctx["feed"] = c
        return f"{len(c)} produse noi"

    @step("pagina produs + galerie + poza")
    def _():
        cand = (ctx.get("feed") or [{"link_url": "https://example-store.ro/products/manuși-de-protecție-premium"}])[0]
        info = page.fetch_page(cand["link_url"])
        assert "_status" not in info, f"pagina {info.get('_status')}"
        urls = page.gallery(cand["link_url"], [info.get("image")])
        img = next((p for p in (page.download_image(u, "st_") for u in urls[:3]) if p), None)
        assert img, "nicio poza"
        ctx.update(title=info.get("title") or "Produs test", img=img, url=cand["link_url"])
        return ctx["title"][:50]

    @step("potrivire fara AI (reguli)")
    def _():
        r, why = rules.match([{"title": "LED Motion Sensor Light", "hits": 2, "best_rank": 1, "best_similarity": True,
                               "price": 5}], "Lampa LED cu senzor de miscare")
        assert r, why
        return why

    @step("unghiuri + scor + NOTA (" + ("reguli" if no_ai else "AI cu rezerva reguli") + ")")
    def _():
        econ = config.profit_at_roas(0, 5)
        ang = rules.angles(ctx.get("title", "Lampa LED"))
        card = rules.card(ctx.get("title", "Lampa LED"), econ, None, 5)
        assert len(ang) >= 3 and card.get("nota")
        ctx.update(angles=ang, card=card, econ=econ)
        return f"{len(ang)} unghiuri, NOTA {card['nota']}/10"

    @step("continut Shopify")
    def _():
        m = {"title": ctx.get("title", "Produs test"), "ali_item_id": "SELFTEST", "link_url": ctx.get("url", ""),
             "ali_url": "https://www.aliexpress.com/item/1.html", "sell_price": None, "econ": ctx.get("econ"),
             "angles": ctx.get("angles", [])}
        cv = content.build(m, m["title"])
        assert cv and cv["price"] >= 49
        ctx.update(m=m, cv=cv)
        return f"{cv['engine']}: {cv['title'][:40]} @ {cv['price']} lei"

    @step("romana corecta (diacritice, fara promisiuni de livrare)")
    def _():
        import ro_text
        t = ro_text.fix("Comanda acum si primesti livrare rapida in 1-3 zile. Pret şi calitate.")
        assert "ș" in t and "ş" not in t and "1-3 zile" not in t, t
        html = content.render_shopify(content.normalize(ctx["cv"]) if hasattr(content, "normalize") else ctx["cv"],
                                      [{"option": "1 buc", "price": 89}, {"option": "2 buc", "price": 129}])
        assert "Întrebări" in html or "<details" in html, "descriere fara FAQ"
        return f"ok; descriere {len(html)} caractere"

    @step("poze in romana + infografice (OCR local)", critical=False)
    def _():
        import ro_images
        m = {**ctx["m"], "ali_img": ctx["img"]}
        ro_images.prepare(m, 1)
        ro = ro_images.build_for_match(m, ctx["cv"], config.OUT / "ro_img" / "SELFTEST", 1,
                                       getattr(config, "BROWSER_CHANNEL", None), True)
        assert ro.get("photos") or ro.get("infographics"), ro.get("notes")
        return (f"OCR {'ok' if ro_images.ocr_available() else 'indisponibil'}; {len(m.get('img_texts') or [])} texte; "
                f"{len(ro['photos'])} poze, {len(ro['infographics'])} infografice")

    @step("mockup telefon 1080x2340")
    def _():
        mk = mockup.make(ctx["m"], ctx.get("cv"), ctx["img"])
        assert mk
        from PIL import Image
        with Image.open(mk) as im:
            assert im.size == (1080, 2340), im.size
        ctx["mockup"] = mk
        return mk

    @step("mesaj WhatsApp (fara trimitere)")
    def _():
        m = {**ctx["m"], "ali_price": 5.0, "ali_title": "test", "page_name": "Test", "started": "2026-10-01",
             "days_running": 5, "ad_url": "https://fb", "card": ctx.get("card"), "no_ai": no_ai, "pet": False}
        m["market"] = {"emag": "eMAG: 3 oferte, de la 79 lei", "audience": "animale", "aud_w": 2, "rising": True,
                       "rising_notes": ["pornit de 6 zile"]}
        tb = notify.build_message({**m, "price_source": "alibaba", "moq": "2", "ali_url": "https://www.alibaba.com/x"})
        assert "Sursă preț: Alibaba (angro, MOQ 2 buc)" in tb, tb
        txt = notify.build_message(m)
        assert "NOTA" in txt and "AliExpress" in txt and "eMAG:" in txt and "În creștere" in txt
        return f"{len(txt)} caractere"

    @step("AliExpress cautare dupa poza (1 cautare)", critical=False)
    def _():
        from aliexpress import AliSearcher
        with AliSearcher() as ali:
            res = ali.search(ctx["img"])
        assert res, "0 rezultate"
        return f"{len(res)} rezultate, {sum(1 for r in res if r['price'] and r['price'] <= 10)} sub 10 lei"

    for f in ([config.OUT / "mockup_SELFTEST.png"] + list((config.OUT / "content").glob("content_SELFTEST.*"))
              ):
        f.unlink(missing_ok=True)
    import shutil
    shutil.rmtree(config.OUT / "ro_img" / "SELFTEST", ignore_errors=True)
    fails = [r for r in RES if r[0] == "FAIL"]
    warns = [r for r in RES if r[0] == "WARN"]
    print(f"[selftest] REZULTAT: {len(RES) - len(fails) - len(warns)} PASS, {len(warns)} WARN, {len(fails)} FAIL", flush=True)
    (config.OUT / "selftest.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf8")
    return 1 if fails else 0
