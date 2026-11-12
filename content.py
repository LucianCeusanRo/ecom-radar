"""Continut gata de pus in Shopify pentru fiecare pont: titlu, hook, beneficii, descriere HTML, pret, FAQ.

Format modelat dupa paginile de pe MOBIL ale magazinelor COD de referinta (pe desktop arata alt site): titlu
"Brand – beneficiu", hook bold centrat, 3-6 beneficii cu emoji, rand de incredere (curier / ramburs / retur),
oferta pe pachete 1/2/4 buc (cu pret pe bucata si economie calculata), infografice, problema -> solutie,
"Cum se folosește" in pasi, specificatii, "Ce primești în pachet", FAQ (acordeon), livrare & retur.
Fara recenzii inventate, fara termene de livrare promise (marfa vine de la furnizor), romana cu ș/ț corecte.
Generat cu Claude (llm.py) daca e disponibil, altfel cu Ollama, altfel sablon. Fisiere: out/content/content_<aliId>.*
"""
import html
import json
import math

import config
import llm
import rules
import vlm

try:
    import ro_text
except ImportError:  # pragma: no cover
    ro_text = None

DIR = config.OUT / "content"
ACCENT = getattr(config, "PAGE_ACCENT", "#1f9d46")
INK = "#13233a"
TRUST = [("🚚", "Livrare prin curier", "în toată țara"), ("💸", "Plată ramburs", "plătești la primire"),
         ("↩️", "Retur 14 zile", "conform legii")]
FAQ_PAY = {"q": "Cum plătesc comanda?", "a": "Ramburs, direct la curier, când primești coletul."}
FAQ_SHIP = {"q": "Cât durează livrarea?", "a": "Comanda pleacă prin curier, iar termenul estimat de livrare îl primești "
                                                 "la confirmarea comenzii. Plătești doar când primești coletul."}
FAQ_RET = {"q": "Pot returna produsul?", "a": "Da, ai 14 zile să returnezi produsul, conform legii."}


def _round9(x: float) -> int:
    """Pret psihologic: 47 -> 49, 83 -> 89, 121 -> 129."""
    x = max(19, math.ceil(x))
    return int(math.ceil((x + 1) / 10.0) * 10 - 1)


def prices(m: dict) -> tuple[int, int]:
    econ = m.get("econ") or {}
    floor = econ.get("min_price") or 0
    sell = m.get("sell_price") or 0
    price = _round9(max(floor, 49))  # cel putin pragul de profit (20% la ROAS 4)
    if sell:  # aliniat la pretul concurentului (cel mai apropiat ...9), dar niciodata sub prag
        price = max(price, int(round((sell + 1) / 10.0) * 10 - 1))
    compare = _round9(price * 1.45)
    return price, compare


def _fix(d):
    return ro_text.fix(d) if ro_text else d


def normalize(c: dict) -> dict:
    """Romana corecta + FAQ-urile de plata / livrare / retur standard (fara termene promise)."""
    c = _fix(dict(c))
    faq = [f for f in (c.get("faq") or []) if isinstance(f, dict) and f.get("q") and f.get("a")]
    keep = []
    for f in faq:
        q = f["q"].lower()
        if "livr" in q or "durea" in q or "plăt" in q or "plat" in q or "retur" in q:
            continue  # le punem pe ale noastre, corecte
        keep.append(f)
    c["faq"] = keep[:4] + [FAQ_PAY, FAQ_SHIP, FAQ_RET]
    return c


def _generate(desc: str, title: str, angles: list[dict], image_texts: list[str] | None = None) -> tuple[dict, str]:
    """Claude -> Ollama -> sablon fara AI. Nu esueaza niciodata."""
    if llm.available():
        try:
            try:
                return llm.shop_content(desc, title, angles, image_texts=image_texts), getattr(llm, "LAST_PROVIDER", "") or "claude"
            except TypeError:  # llm.py vechi
                return llm.shop_content(desc, title, angles), getattr(llm, "LAST_PROVIDER", "") or "claude"
        except llm.LlmError as e:
            print(f"[content] AI text indisponibil ({str(e)[:60]}); incerc Ollama", flush=True)
    if vlm.enabled():
        try:
            prompt = llm.CONTENT_PROMPT.format(note=llm.DATA_NOTE, desc=desc, title=title[:160],
                                               angles="; ".join(a["name"] for a in angles[:5]),
                                               imgtexts=llm.image_texts_block(image_texts) if hasattr(llm, "image_texts_block") else "")
            d = vlm._chat(prompt, num_predict=1800)
            if d.get("title") and d.get("bullets"):
                return d, "ollama"
        except Exception as e:  # noqa: BLE001
            print(f"[content] Ollama indisponibil ({str(e)[:60]}); folosesc sablonul", flush=True)
    return rules.content(title, angles), "sablon"


def _e(s) -> str:
    return html.escape(str(s or "").strip())


def _lei(x) -> str:
    x = float(x)
    return (f"{x:.2f}".replace(".", ",") if abs(x - round(x)) > 0.001 else f"{int(round(x))}") + " lei"


OFFER_BADGES = getattr(config, "PAGE_OFFER_BADGES", ("Cel mai ieftin 👍", "Preferata clienților 😍", "Cel mai avantajos pachet 💰"))
CTA_TEXT = getattr(config, "PAGE_CTA_TEXT", "Comandă acum - Plata la livrare")
CTA_HREF = getattr(config, "PAGE_CTA_HREF", "#es-popup-button")   # butonul EasySell (formular ramburs) din pagina


def _vid(v: dict) -> str:
    return str(v.get("id") or "").rsplit("/", 1)[-1]


def offers_html(variants: list[dict], thumb: str | None = None) -> str:
    """Pachete in stilul magazinele COD de referinta (mobil): randuri cu poza mica, numele pachetului, insigna maro si pretul in dreapta;
    pachetul din mijloc evidentiat (fundal bej, chenar mai inchis). Pret / buc si economia sunt calculate din preturile
    reale ale variantelor. Cu id-ul variantei, randul e link (?variant=...) si selecteaza pachetul in pagina."""
    vs = [v for v in (variants or []) if v.get("price") is not None][:3]
    if len(vs) < 2:
        return ""
    unit = float(vs[0]["price"])
    mid = 1 if len(vs) == 3 else None
    rows = []
    for i, v in enumerate(vs):
        name = str(v.get("option") or v.get("title") or "")
        try:
            n = int(name.split()[0])
        except (ValueError, IndexError):
            n = i + 1
        p = float(v["price"])
        save = unit * n - p
        badge = OFFER_BADGES[0] if i == 0 else (OFFER_BADGES[2] if i == len(vs) - 1 else OFFER_BADGES[1])
        hi = i == mid
        sub = (f'<div style="font-size:12px;color:#5b4a3a;margin-top:3px">{_lei(p / n)} / buc · economisești {_lei(save)}</div>'
               if n > 1 and save > 0.5 else "")
        img = (f'<td style="width:62px;padding:8px 0 8px 8px;vertical-align:middle"><img src="{_e(thumb)}" alt="" '
               f'style="width:56px;height:56px;object-fit:cover;border-radius:8px;display:block" loading="lazy"></td>' if thumb else "")
        row = (f'<table style="width:100%;border-collapse:separate;border-spacing:0;margin:0 0 10px;'
               f'border:{"3px solid #9c7b5e" if hi else "1px solid #d9d9d9"};border-radius:10px;'
               f'background:{"#e5d3b3" if hi else "#fff"}"><tr>{img}'
               f'<td style="padding:10px 8px;vertical-align:middle;text-align:left">'
               f'<div style="font-weight:800;font-size:16px;color:#111">{_e(name)}</div>'
               f'<span style="display:inline-block;margin-top:4px;padding:3px 9px;border-radius:5px;font-size:13px;'
               f'font-weight:700;color:#fff;background:{"#2b1a10" if hi else "#9c795e"}">{_e(badge)}</span>{sub}</td>'
               f'<td style="padding:10px 12px 10px 4px;vertical-align:middle;text-align:right;white-space:nowrap;'
               f'font-weight:800;font-size:17px;color:#111">{_lei(p)}</td></tr></table>')
        vid = _vid(v)
        rows.append(f'<a href="?variant={vid}" style="text-decoration:none;color:inherit;display:block">{row}</a>' if vid.isdigit() else row)
    return ('<h3 style="text-align:center;margin:26px 0 10px">🎁 Alege pachetul</h3>'
            '<div style="max-width:560px;margin:0 auto">' + "".join(rows) + '</div>'
            '<p style="text-align:center;margin:2px 0 0;font-size:13px;color:#555">Pachetul se alege și mai sus, la '
            '<strong>Pachet</strong>.</p>')


def cta_html(text: str | None = None, href: str | None = None) -> str:
    """Buton verde in descriere; duce la formularul de comanda ramburs (EasySell) din pagina."""
    return (f'<p style="text-align:center;margin:18px 0"><a href="{_e(href or CTA_HREF)}" style="display:inline-block;'
            f'background:{ACCENT};color:#fff;font-weight:800;font-size:17px;padding:14px 22px;border-radius:10px;'
            f'text-decoration:none;max-width:520px;width:100%;box-sizing:border-box">🛍️ {_e(text or CTA_TEXT)}</a></p>')


def sticky_html(text: str = "Comandă acum - Ramburs la livrare", href: str | None = None) -> str:
    """Buton rosu fix jos (stil COD). Implicit OPRIT (config.PAGE_STICKY_CTA): EasySell are deja butonul lui fix
    jos (#es-sticky) pe magazinul propriu, doua butoane suprapuse ar strica pagina."""
    return (f'<a href="{_e(href or CTA_HREF)}" style="position:fixed;left:10px;right:10px;bottom:10px;z-index:50;'
            'display:block;text-align:center;background:#f20000;color:#fff;font-weight:800;font-size:17px;'
            f'padding:15px 10px;border-radius:12px;text-decoration:none;box-shadow:0 4px 14px rgba(0,0,0,.25)">🛍️ {_e(text)}</a>')


def render_shopify(c: dict, variants: list[dict] | None = None, images: dict | None = None) -> str:
    """Descrierea HTML pentru Shopify (fara pret: il afiseaza tema din variante). images: {"beneficii": url, ...}."""
    images = images or {}
    S = f'style="text-align:center;margin:28px 0 8px;color:{INK}"'
    img = (lambda k, alt: f'<p style="text-align:center;margin:18px 0"><img src="{_e(images[k])}" alt="{_e(alt)}" '
           f'style="width:100%;max-width:640px;height:auto;border-radius:14px" loading="lazy"></p>' if images.get(k) else "")
    p = ['<div style="line-height:1.55">']
    if c.get("hook"):
        p.append(f'<p style="text-align:center;font-size:18px;font-weight:800;color:{INK};margin:4px 0 14px">{_e(c["hook"])}</p>')
    bl = (c.get("bullets") or [])[:6]
    if bl:
        p.append('<div style="text-align:center"><div style="display:inline-block;text-align:left;max-width:560px">')
        p += [f'<p style="margin:7px 0;font-weight:600">{_e(b)}</p>' for b in bl]
        p.append("</div></div>")
    p.append('<table style="width:100%;border-collapse:collapse;margin:18px 0 6px;text-align:center;table-layout:fixed"><tr>'
             + "".join(f'<td style="padding:8px 4px;vertical-align:top"><div style="font-size:26px">{i}</div>'
                       f'<div style="font-weight:800;font-size:13px;color:{INK}">{_e(t)}</div>'
                       f'<div style="font-size:12px;color:#555">{_e(s)}</div></td>' for i, t, s in TRUST) + "</tr></table>")
    p.append(offers_html(variants or [], images.get("thumb")))
    p.append(cta_html())
    p.append(img("beneficii", "Beneficii"))
    if c.get("tagline"):
        p.append(f'<h3 {S}>{_e(c["tagline"])}</h3>')
    desc = c.get("description") or []
    if isinstance(desc, str):
        desc = [desc]
    p += [f'<p style="text-align:center">{_e(x)}</p>' for x in desc[:3]]
    steps = [s for s in (c.get("how_to") or []) if isinstance(s, dict)]
    if steps:
        p.append(f'<h3 {S}>Cum se folosește</h3>')
        p.append(img("cum_se_foloseste", "Cum se folosește"))
        p += [f'<p style="text-align:center;margin:10px 0"><strong>{n}. {_e(s.get("title"))}</strong><br>{_e(s.get("text"))}</p>'
              for n, s in enumerate(steps[:4], 1)]
    specs = [s for s in (c.get("specs") or []) if isinstance(s, dict) and s.get("k") and s.get("v")]
    if specs:
        p.append(f'<h3 {S}>Specificații</h3>')
        p.append('<table style="width:100%;border-collapse:collapse;font-size:15px">' + "".join(
            f'<tr style="border-bottom:1px solid #e6ebf0"><td style="padding:9px 8px;font-weight:700;color:#3b4b60;width:42%">'
            f'{_e(s["k"])}</td><td style="padding:9px 8px">{_e(s["v"])}</td></tr>' for s in specs[:8]) + "</table>")
    pkg = [x for x in (c.get("package") or []) if x]
    if pkg:
        p.append(f'<h3 {S}>Ce primești în pachet</h3>')
        p += [f'<p style="text-align:center;margin:6px 0">📦 {_e(x)}</p>' for x in pkg[:4]]
    p.append(img("specificatii", "Specificații și conținutul pachetului"))
    warn = [x for x in (c.get("warnings") or []) if x]
    if warn:   # avertismente de siguranta din fisa furnizorului (ex. H302/H315), nu se inventeaza
        p.append('<div style="border:1px solid #f0c36d;background:#fff8e6;border-radius:12px;padding:12px 14px;margin:18px 0">'
                 '<p style="margin:0 0 4px;font-weight:800">⚠️ Atenție</p>'
                 + "".join(f'<p style="margin:2px 0">{_e(x)}</p>' for x in warn[:4]) + "</div>")
    faq = [f for f in (c.get("faq") or []) if isinstance(f, dict)]
    if faq:
        p.append(f'<h3 {S}>Întrebări frecvente</h3>')
        p += [f'<details style="border:1px solid #e1e7ee;border-radius:12px;padding:12px 14px;margin:8px 0">'
              f'<summary style="font-weight:700;cursor:pointer">{_e(f.get("q"))}</summary>'
              f'<p style="margin:8px 0 0">{_e(f.get("a"))}</p></details>' for f in faq[:8]]
    p.append(f'<div style="background:#f1faf4;border-radius:14px;padding:14px;margin:22px 0 4px;text-align:center">'
             f'<p style="margin:0 0 4px;font-weight:800;color:{INK}">Livrare și retur</p>'
             '<p style="margin:0">🚚 Livrare prin curier în toată țara · 💸 Plata ramburs, la primirea coletului · '
             '↩️ 14 zile drept de retur</p></div>')
    p.append(cta_html())
    if getattr(config, "PAGE_STICKY_CTA", False):
        p.append(sticky_html())
    p.append("</div>")
    return "\n".join(x for x in p if x)


def render_html(c: dict, price: int, compare: int, variants: list[dict] | None = None, images: dict | None = None) -> str:
    """Varianta pentru fisierul .html / Discord: randul de pret (incepe cu <p><s>, scos de shopify_body) + descrierea."""
    pct = round((1 - price / compare) * 100) if compare else 0
    return "\n".join([f"<p><s>{compare},00 lei</s> &nbsp;<strong>{price},00 lei</strong> &nbsp;🔥 Reducere {pct}%</p>",
                      render_shopify(c, variants, images)])


def build(m: dict, desc: str) -> dict | None:
    """Genereaza si salveaza continutul. None la esec (pontul se trimite oricum, fara continut)."""
    try:
        c, engine = _generate(desc, m["title"], m.get("angles") or [], m.get("img_texts"))
    except Exception as e:  # noqa: BLE001
        print(f"[content] nu am putut genera continutul: {str(e)[:120]}", flush=True)
        return None
    c = normalize(c)
    price, compare = prices(m)
    body = render_html(c, price, compare)
    DIR.mkdir(parents=True, exist_ok=True)
    base = DIR / f"content_{m['ali_item_id']}"
    page = (f"<!-- Titlu produs: {_e(c['title'])} -->\n<!-- Pret: {price} lei | Pret comparat (compare-at): {compare} lei -->\n"
            f"<!-- Sursa: {_e(m.get('link_url', ''))} | AliExpress: {_e(m['ali_url'])} -->\n"
            f"<h1>{_e(c['title'])}</h1>\n{body}\n")
    base.with_suffix(".html").write_text(page, encoding="utf8")
    txt = [f"TITLU: {c['title']}", f"PREȚ: {price} lei   (compare-at: {compare} lei)", "", f"HOOK: {c.get('hook', '')}", "",
           "BENEFICII:"] + [f"  {b}" for b in (c.get("bullets") or [])[:6]] + ["", "DESCRIERE (HTML pentru Shopify):", body]
    base.with_suffix(".txt").write_text("\n".join(txt), encoding="utf8")
    base.with_suffix(".json").write_text(json.dumps({**c, "price": price, "compare_at": compare, "engine": engine},
                                                    ensure_ascii=False, indent=1), encoding="utf8")
    return {"title": c["title"], "hook": c.get("hook", ""), "bullets": (c.get("bullets") or [])[:6], "faq": c.get("faq") or [],
            "price": price, "compare_at": compare, "html_path": str(base.with_suffix(".html")),
            "txt_path": str(base.with_suffix(".txt")), "engine": engine,
            "image_texts_ro": c.get("image_texts_ro") or []}


def short_message(cv: dict, discord: bool = True) -> str:
    """Varianta scurta pentru WhatsApp (mesaj separat, dupa pont)."""
    t = lambda s, n=140: " ".join(str(_fix(s)).replace("*", " ").replace("_", " ").replace("~", " ").split())[:n]  # noqa: E731
    lines = ["🛍️ *Pagina de produs gata pentru Shopify*",
             f"*Titlu:* {t(cv['title'], 120)}",
             f"*Preț:* {cv['price']} lei (compare-at {cv['compare_at']} lei)",
             f"*Hook:* {t(cv['hook'])}", ""]
    lines += [t(b, 90) for b in cv["bullets"]]
    if cv.get("faq"):
        lines += ["", "*Întrebări frecvente:*"] + [f"• {t(f.get('q'), 80)} – {t(f.get('a'), 160)}" for f in cv["faq"][:3]
                                                    if isinstance(f, dict)]
    lines += ["", "📄 Descrierea HTML completă (copy-paste în Shopify): " +
              ("atașată pe Discord." if discord else f"out/content/{cv['html_path'].replace(chr(92), '/').split('/')[-1]}")]
    return "\n".join(lines)
