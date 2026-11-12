"""Mod FARA AI (Claude indisponibil SI Ollama picat): reguli simple, ca pipeline-ul sa trimita ponturi oricum.

- potrivire AliExpress: doar dupa semnalele cautarii dupa poza (oferta gasita de ambele poze / "Best similarity" /
  pe primele locuri) + cuvinte comune din titlu (dupa un mic dictionar RO->EN);
- unghiuri, hook-uri, CTA, scor si NOTA: sabloane pe categorii (casa, auto, pet, frumusete, copii, iarna/cadou, gadget);
- continut Shopify: sablon in acelasi format ca varianta AI.
Mesajul spune clar "verificat doar vizual (fara AI)", ca echipa sa stie sa se uite mai atent la poza comparativa.
"""
import re
import unicodedata

import config

RO_EN = {
    "lampa": "lamp light", "lumina": "light lamp led", "led": "led", "senzor": "sensor", "miscare": "motion",
    "perie": "brush", "pieptene": "comb", "caine": "dog pet", "caini": "dog pet", "pisica": "cat pet", "pisici": "cat pet",
    "masina": "car", "auto": "car", "zgarieturi": "scratch", "husa": "cover case", "suport": "holder stand",
    "telefon": "phone", "cablu": "cable", "incarcator": "charger", "set": "set", "carlige": "hook hooks",
    "autoadezive": "adhesive self", "bucatarie": "kitchen", "baie": "bathroom", "dus": "shower", "organizator": "organizer",
    "cutie": "box", "colanti": "leggings tights", "sosete": "socks", "manusi": "gloves", "caciula": "hat beanie",
    "fular": "scarf", "incalzitor": "heater warmer", "termic": "thermal", "patura": "blanket", "pled": "blanket",
    "jucarie": "toy", "copii": "kids children baby", "ceas": "watch", "ochelari": "glasses", "geanta": "bag",
    "rucsac": "backpack", "sticla": "bottle", "cana": "mug cup", "cutit": "knife", "foarfeca": "scissors",
    "masaj": "massage massager", "par": "hair", "unghii": "nail", "machiaj": "makeup", "oglinda": "mirror",
    "pantofi": "shoes shoe", "talpi": "insoles insole", "ortopedic": "orthopedic", "gradina": "garden", "plante": "plant",
    "curatare": "cleaning cleaner", "mop": "mop", "aspirator": "vacuum", "filtru": "filter", "robinet": "faucet tap",
    "prelata": "tarpaulin tarp", "ploaie": "rain", "parbriz": "windshield", "dezghetare": "defrost ice", "gheata": "ice",
    "brad": "christmas tree", "craciun": "christmas", "decor": "decor decoration", "ornament": "ornament",
    "cadou": "gift", "colier": "necklace", "bratara": "bracelet", "cercei": "earrings", "inel": "ring",
}

CATS = [  # (nume, regex pe titlu fara diacritice, unghiuri)
    ("pet", r"\b(cain|caini|catel|pisic|pet|animal|lesa|zgarda|blana)", [
        ("Iubire pentru animăluț", ["Animăluțul tău merită asta", "Uite cum reacționează cățelul meu"]),
        ("Casa fără păr și mizerie", ["Gata cu părul de pe canapea", "Curățenie în 2 minute, fără efort"]),
        ("Cadou pentru iubitorii de animale", ["Cadoul perfect pentru cine are un cățel", "Orice stăpân de pisică vrea asta"])]),
    ("auto", r"\b(auto|masin|parbriz|volan|scaun auto|zgarieturi|anvelop|caroserie)", [
        ("Economie față de service", ["Nu mai da sute de lei la service", "Am rezolvat asta acasă în 5 minute"]),
        ("Înainte / după", ["Uite diferența înainte și după", "Nu-mi venea să cred rezultatul"]),
        ("Cadou pentru șoferi", ["Cadoul ideal pentru orice șofer", "Fiecare mașină ar trebui să aibă asta"])]),
    ("iarna", r"\b(iarna|termic|vatuit|incalz|caciul|manusi|fular|pled|patur|ger|zapad|dezghet)", [
        ("Cald toată iarna", ["Gata cu frigul de anul ăsta", "Asta m-a salvat iarna trecută"]),
        ("Economie la încălzire", ["Stai la cald fără să dai mai mult pe facturi", "Căldură instant, fără efort"]),
        ("Cadou de sezon", ["Cadoul practic pe care chiar îl folosești", "Perfect pentru Crăciun"])]),
    ("frumusete", r"\b(par|unghii|machiaj|ten|piele|riduri|gene|sprancene|epilat|masaj)", [
        ("Rezultat de salon acasă", ["Rezultat de salon, fără programare", "Am renunțat la salon după asta"]),
        ("Economie de timp", ["Gata în 5 minute în fiecare dimineață", "Rutina mea a devenit mult mai simplă"]),
        ("Cadou pentru ea", ["Cadoul care chiar o bucură", "Orice femeie vrea asta în baie"])]),
    ("copii", r"\b(copii|copil|bebe|bebelus|jucari|scoala|educativ)", [
        ("Copilul ocupat, fără ecrane", ["Copilul meu nu se mai uită la telefon", "Ore întregi de joacă, fără tabletă"]),
        ("Siguranța copilului", ["Orice părinte ar trebui să aibă asta", "Liniște pentru tine, siguranță pentru el"]),
        ("Cadou pentru cei mici", ["Cel mai iubit cadou de anul ăsta", "Moș Crăciun o să fie un erou"])]),
    ("casa", r"\b(casa|bucatar|baie|dus|organiz|curat|mop|perete|carlig|raft|sertar|robinet|gradin|prelata)", [
        ("Problemă rezolvată", ["Gata cu mizeria din casă", "Trucul pe care îl folosesc toți acum"]),
        ("Economie de timp", ["Fac asta în 2 minute în loc de 20", "Cea mai bună investiție pentru casă"]),
        ("Înainte / după", ["Uite cum arăta înainte", "Diferența se vede din prima"])]),
]
GENERIC = [
    ("Problemă – soluție", ["Ai și tu problema asta?", "Uite cum am rezolvat-o în câteva secunde"]),
    ("Demonstrație", ["Uite cum funcționează", "Nu credeam că merge până nu l-am încercat"]),
    ("Cadou practic", ["Idee de cadou pe care chiar o folosește", "Cadoul perfect sub 100 de lei"]),
]
CTA = "Comandă acum – plătești la livrare, după ce verifici coletul."


def _ascii(*t: str) -> str:
    return unicodedata.normalize("NFKD", " ".join(x for x in t if x)).encode("ascii", "ignore").decode().lower()


def category(title: str) -> str:
    t = _ascii(title)
    for name, rx, _ in CATS:
        if re.search(rx, t):
            return name
    return "general"


def angles(title: str) -> list[dict]:
    t = _ascii(title)
    picked = next((a for _, rx, a in CATS if re.search(rx, t)), [])
    out = [{"name": n, "hook": h[0], "hooks": h} for n, h in picked] or []
    for n, h in GENERIC:
        if len(out) >= 3:
            break
        out.append({"name": n, "hook": h[0], "hooks": h})
    return out[:3]


def _words_en(title_ro: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]{3,}", _ascii(title_ro)):
        out.update(RO_EN.get(w, "").split())
    return out


def match(cheap: list[dict], store_title: str) -> tuple[dict | None, str]:
    """Potrivire fara AI: semnale vizuale puternice + (cand se poate) cuvinte comune in titlu."""
    words = _words_en(store_title)
    for r in cheap[:8]:
        ali_w = set(re.findall(r"[a-z]{3,}", r["title"].lower()))
        common = words & ali_w
        strong = (r.get("hits", 1) >= 2 and r.get("best_rank", 99) <= 3) or (r.get("best_similarity") and r.get("best_rank", 99) <= 2)
        if strong and (common or not words):
            return r, f"regula: vizual (poze={r.get('hits')}, loc={r.get('best_rank')}) + cuvinte {sorted(common)[:4]}"
    return None, "regula: niciun semnal vizual suficient de puternic"


def card(title: str, econ: dict, sell: float | None, ali_price: float) -> dict:
    """Scor 1-5 pe criteriile ghidului + NOTA 1-10, euristic."""
    cat = category(title)
    q4 = bool(re.search(config.Q4_RE, _ascii(title)))
    margin_ok = (not sell) or econ.get("pct", 0) >= config.MIN_PROFIT_PCT
    wow = 4 if cat in ("auto", "casa", "frumusete") else 3
    problema = 4 if cat in ("auto", "casa", "pet", "iarna") else 3
    nesat = 3
    upsell = 4 if ali_price <= 6 else 3
    compact = 3 if re.search(r"\b(prelata|patur|pled|scaun|raft|covor)", _ascii(title)) else 5
    nota = 4 + (1 if margin_ok else -1) + (1 if ali_price <= 5 else 0) + (1 if q4 else 0) + (1 if cat != "general" else 0)
    nota = max(1, min(7, nota))  # fara AI nu dam peste 7
    why = ("marjă bună la ROAS 4" if margin_ok else "marjă la limită") + (", potrivit sezonului" if q4 else "") + \
          f", categorie {cat} (scor automat, fără AI)"
    return {"wow": wow, "problema": problema, "nesaturat": nesat, "upsell": upsell, "compact": compact,
            "cta": CTA, "nota": nota, "nota_reason": why}


def content(title: str, angles_: list[dict]) -> dict:
    base = re.sub(r"\s+[-|–].*$", "", title).strip() or title
    words = base.split()
    branded = any(w[:1].isupper() and not w.isupper() for w in words[1:])  # are deja un nume propriu (ex. FlexiCalm)
    stop = {"pentru", "set", "din", "cu", "fara", "fără", "si", "și", "de", "la", "oferta", "ofertă"}
    noun = next((w for w in re.findall(r"[A-Za-zĂÂÎȘȚăâîșț]{4,}", base) if w.lower() not in stop), "Produs")
    hooks = [a["hook"] for a in angles_]
    return {
        "title": (base if branded else f"{noun.capitalize()}Pro – {base}")[:110],
        "hook": hooks[0] if hooks else "Soluția simplă pe care o cauți",
        "bullets": ["✅ Rezolvă rapid problema, fără efort", "⚡ Ușor de folosit, din prima zi",
                    "💪 Materiale rezistente, pentru folosire zilnică", "🎁 Idee practică de cadou",
                    "💸 Plata ramburs, la primirea coletului"],
        "tagline": "Simplu · Practic · Plată la livrare",
        "description": [f"{base} este alegerea practică pentru cine vrea rezultate fără bătăi de cap.",
                        "Comanzi acum, plătești la livrare după ce verifici coletul, iar dacă nu ești mulțumit ai drept de retur."],
        "how_to": [{"title": "Scoate din ambalaj", "text": "Produsul vine gata de folosit."},
                   {"title": "Folosește-l", "text": "Urmează instrucțiunile simple din pachet."},
                   {"title": "Bucură-te de rezultat", "text": "Diferența se vede din prima utilizare."}],
        "package": [f"1 × {base[:60]}"],
        "faq": [],  # plata / livrare / retur se adauga standard in content.normalize (fara termene promise)
    }
