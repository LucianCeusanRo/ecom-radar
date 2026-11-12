"""Setari monitor: reclame FB Ads Library (RO) -> produs pe AliExpress 1-10 lei -> WhatsApp."""
import os
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "out"
IMG = OUT / "img"
STATE_FILE = ROOT / "state.json"

# --- Ad Library ---------------------------------------------------------
COUNTRY = "RO"
QUERIES = [  # pool rotit: doar QUERIES_PER_RUN pe rulare (ritm lent, fara rate limit)
    "comandă acum", "cumpără acum", "black friday", "reducere", "reducere azi", "ofertă limitată", "1+1 gratis",
    "livrare gratuită", "stoc limitat", "cadou", "cadou crăciun", "plata la livrare", "doar azi",
]
QUERIES_PER_RUN = 3
# Piete vecine: produsele apar adesea intai in PL/HU, apoi in RO. 1-2 cautari per rulare, prin rotatie.
FOREIGN_QUERIES = {
    "PL": ["zamów teraz", "promocja dzisiaj", "black friday", "darmowa dostawa", "prezent na święta", "płatność przy odbiorze"],
    "HU": ["rendeld meg most", "akció ma", "black friday", "ingyenes szállítás", "karácsonyi ajándék", "utánvétes fizetés"],
}
FOREIGN_PER_RUN = 1
FOREIGN_MAX_ADS = 90
MAX_ADS_PER_QUERY = 120  # 4 cereri a 30 reclame; cota merge intai pe magazine
DAYS = 30      # cel mult N zile de la pornirea reclamei
MIN_DAYS = 3   # cel putin N zile (19.10: 5 -> 3, ca sa prindem produsele care abia incep sa creasca)

# Domenii care nu sunt magazine de dropshipping (branduri mari, marketplace-uri, retele sociale)
BLOCK_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "whatsapp.com", "wa.me", "google.com",
    "apple.com", "temu.com", "aliexpress.com", "emag.ro", "altex.ro", "trendyol.com",
    "huawei.com", "adidas", "intersport", "sephora", "modivo", "wolt", "takko", "oral-b",
    "pepita.com", "samsung.com", "flanco.ro", "cel.ro", "farmaciatei", "dr-max",
)
# Pagina de produs (Shopify /products/x, WooCommerce /produs/x etc.)
PRODUCT_PATH_RE = r"/(products?|produs|produse)/[^/?#]+"

# --- AliExpress ---------------------------------------------------------
COST_MIN = 1.0   # lei
COST_MAX = 10.0  # lei
ALI_COOKIE = "site=glo&c_tp=RON&region=RO&b_locale=ro_RO"
ALI_MAX_RESULTS = 40
BROWSER_CHANNEL = "msedge"  # foloseste Edge-ul instalat, fara descarcari

# --- WhatsApp (OpenClaw) --------------------------------------------------
GROUPS = {  # WhatsApp group JIDs (format 1203...@g.us), set in the environment / .env
    "test": os.environ.get("ECOM_WA_GROUP_TEST", ""),
    "prod": os.environ.get("ECOM_WA_GROUP_PROD", ""),
}
MAX_SEND_PER_RUN = 5   # buget mic: putine ponturi bune pe ora, nu spam

# --- Discord webhook (optional; file keeps the secret out of git/logs) --------
DISCORD_WEBHOOK_FILE = ROOT / "discord_webhook.txt"

def _load_discord_webhook() -> str:
    """Citeste URL-ul webhook din fisier. Lipseste/gol = Discord dezactivat."""
    try:
        url = DISCORD_WEBHOOK_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
    if url.startswith("https://discord.com/api/webhooks/") or url.startswith("https://discordapp.com/api/webhooks/"):
        return url
    return ""

DISCORD_WEBHOOK_URL = _load_discord_webhook()

MAX_CANDIDATES = 40  # cicluri mai scurte: 40 produse/rulare, task mai des
MAX_PER_PAGE = 3     # max produse din acelasi magazin pe o rulare (viteza + diversitate)

# --- Verificator vizual local (Ollama) -------------------------------------
OLLAMA_URL = "http://127.0.0.1:11434"
VLM_MODEL = "qwen3.8:27b"
VLM_TIMEOUT = 420  # secunde per apel (modelul de 27B nu incape complet in 16 GB VRAM)
VLM_KEEP_ALIVE = "5m"   # cat timp ramane modelul incarcat intre apeluri; la final run.py il descarca imediat

# --- Prioritate: produse pentru animale de companie ---------------------------
# Frazele astea descopera magazine de pet; produsele de pet sunt verificate primele si marcate in mesaj.
PET_QUERIES = ["pentru câini", "pentru pisici", "animale de companie", "accesorii câini", "accesorii pisici"]
PET_RE = (r"\b(pet|pets|cain\w*|catel\w*|pisic\w*|animale\w*|lesa|purici|papagal\w*|iepur\w*|acvariu\w*|"
          r"zgard\w*|cusca|litiera|hamster\w*|rozator\w*|pasari|puppy|dog|dogs)\b")  # pe text fara diacritice, litere mici

# --- Profitabilitate (la ce cheltuiala de reclama ramane profit) ---------------------
# La ROAS 3, reclama consuma 1/3 din pretul de vanzare. Din ce ramane scadem marfa si livrarea.
ROAS = 4.0               # venit / cheltuiala reclama (ghidul echipei: 20% profit la ROAS 4)
MIN_PROFIT_PCT = 20.0    # profit net minim, ca procent din pretul de vanzare
SHIP_ALI = 10.0          # transport estimat de la furnizor (lei/bucata)
SHIP_RO = 20.0           # curier in Romania, estimat (lei/comanda)
PLATITOR_TVA = False     # firma NEplatitoare de TVA: TVA 21% pe reclame si pe marfa importata e cost (nerecuperabil)
TVA = 0.21
STORE_NAME = os.environ.get("ECOM_STORE_NAME", "My Store")  # numele magazinului afisat in mockup-ul de telefon

# --- Unghiuri de marketing -------------------------------------------------------
MIN_ANGLES = 3  # produsul se trimite doar daca poate fi promovat din cel putin atatea unghiuri distincte

# --- Lista de magazine (scanate direct, la fiecare rulare) -------------------
# Fara sortare "cele mai recente" in Ad Library, reclamele noi se gasesc cel mai bine scanand magazinele care lanseaza
# produse acum. Lista se extinde singura cu magazinele descoperite prin cautarile de mai sus (fisier pages.json).
PAGES_FILE = ROOT / "pages.json"
PAGE_SCAN_MAX_ADS = 60
MAX_PAGES_PER_RUN = 20   # magazine per rulare (pet primele, apoi cele scanate cel mai demult)
MAX_CONSEC_FAILS = 3    # dupa atatea esecuri la rand (rate limit) oprim colectarea
PREFLIGHT_WAIT_MIN = 0   # fara asteptari: daca Meta e limitat pe toate rutele, trecem imediat pe cache + magazine
PAUSE_BETWEEN = 4       # secunde intre cereri Meta (+ jitter) — IP-ul direct e ruta principala acum
PAUSE_JITTER = 4
# --- Meta: rute + buget (vezi metapool.py). Cauza "0 ponturi" 24.09-01.10: proxy-urile erau toate limitate de Meta,
#     iar colectorul cerea cate 10 reclame/cerere (page-size implicit) => ~300 cereri/ora. Acum 30/cerere + buget.
META_DIRECT = True             # IP-ul PC-ului e ruta principala (cu cooldown automat daca e limitat)
META_PAGE_SIZE = 30            # reclame per cerere (maximul acceptat); de 3x mai putine cereri
META_DELAY = 3.0               # pauza colectorului intre paginile aceleiasi cautari
META_MAX_REQUESTS_PER_RUN = 110 # plafon de cereri Meta pe rulare (restul magazinelor la rularea urmatoare)
META_PREFLIGHT_PROXIES = 2     # cate proxy-uri incercam la pornire daca directul e limitat

# --- Sursa de rezerva: produse noi din magazinele Shopify cunoscute (shopfeed.py) ---------------
FEED_TRIGGER = 8         # folosim sursa de rezerva cand Meta da mai putin de N produse nevazute
FEED_MAX_SHOPS = 60      # magazine citite per rulare (cele citite cel mai demult primele)
FEED_PER_SHOP = 3        # produse noi luate per magazin
FEED_MIN_DAYS = 2        # produs publicat de cel putin N zile...
FEED_MAX_DAYS = 21       # ...si cel mult N zile
FEED_MAX_CANDIDATES = 25 # produse din magazine adaugate per rulare
BAD_DOMAIN_DAYS = 3      # magazin care da 403/Cloudflare de 2 ori: sarit N zile (nu mai consuma rularea)

# --- Model text Claude prin Cursor Agent CLI (optional; Ollama ramane rezerva) -------------------
LLM_ENABLED = True
START_JITTER_MIN = 6        # rularile programate pornesc la :00 + 0..6 min (aleator)
LLM_MODEL = "claude-sonnet-5-thinking-high"
LLM_TIMEOUT = 150
LLM_MAX_CALLS_PER_RUN = 5   # credite: Claude doar pentru scor+continut la produsele care au trecut deja pretul Ali + profitul
LLM_JUDGE = False           # judecata "acelasi produs?" ramane pe Ollama (local, gratuit)

# --- Black Friday / Q4: prioritate usoara (doar ordinea de verificare + eticheta in mesaj) ----------
Q4_RE = (r"\b(cadou\w*|craciun\w*|mos|brad\w*|ornament\w*|decor\w*|iarna|ger|zapad\w*|incalz\w*|termic\w*|"
         r"electric\w*|led|lumin\w*|lamp\w*|proiector\w*|gadget\w*|portabil\w*|wireless|usb|bucatar\w*|"
         r"organizator\w*|pled\w*|patur\w*|sosete|manusi|fular|caciul\w*|parbriz|dezghet\w*|umidificator\w*|"
         r"masaj\w*|set|kit|jucari\w*|copii)\b")
SEED_PAGES = {
    "1070141962855625": "Reduceri Garantate", "1289533627569899": "Casa Ofertelor", "166345523238995": "Betino",
    "1139103985951636": "BineMeritat.ro", "104406974327333": "Frilla", "597030180161269": "Uunnder EU",
    "109051095497644": "Home & Marker RO", "1278290455371878": "Plico Shop", "1149954281526203": "Simplio Shop",
    "594754897056786": "Homis Magazin", "205173463239050": "Teleshopescu", "1188489474338015": "Cumperi Azi",
    "261007557106610": "Magazinul Pretulino", "721450791042462": "Nordivo", "857125757485538": "Casa Gospodar",
    "324130814115222": "24Deals", "319550747912152": "Offersi", "1182420794964953": "Comanda Zilei",
    "863782960145441": "Preturi Speciale.Ro", "1159166643950212": "Chilipiro.ro", "999180253286154": "FloRad",
    "1251981294675402": "Modern Choice", "402143096325331": "Auto Vibe", "103045869504929": "Pinauto Shop",
    "113128506136635": "Amva", "153917134472438": "Magbun", "100701801781183": "HappyKoala.ro",
    "109335165196493": "IBES Beauty", "1295698036953247": "Optivia", "1184252261440874": "MiscShop",
}


# --- Stare, timp, AliExpress, proxy ------------------------------------------------
NEG_DAYS = 10            # rezultatele NEGATIVE reale se re-verifica dupa N zile; produsele TRIMISE nu expira niciodata
MAX_RUNTIME_MIN = 50     # o rulare se opreste singura dupa atatea minute (task orar: nu se suprapun; watchdog +15)
ALI_PAUSE = 2            # secunde intre cautari (viteza; proxy-urile absorb riscul)
ALI_IMAGES_PER_PRODUCT = 2  # cate poze din galerie se cauta per produs (2 = potriviri mai bune)
ALI_MAX_SEARCHES = 400      # plafon de cautari pe rulare (cu rotatie de IP nu mai e nevoie sa fie mic)
ALI_SEARCHES_PER_IP = 6     # cautari per IP, apoi rotim (AliExpress blocheaza IP-ul dupa ~30-60/ora)
ALI_IP_RETRIES = 4          # cate IP-uri incercam pentru aceeasi poza inainte sa renuntam
PROXIES_FILE = ROOT / "proxies.txt"   # host:port:user:parola, unul pe linie (rotatie pentru AliExpress)
USE_PROXY_ALI = True     # AliExpress prin proxy (IP-ul direct a fost blocat cu pagina anti-bot)
USE_PROXY_META = True    # rotatie din proxies.txt la fiecare cerere Meta (rate limit)
PROXY_FILE = ROOT / "proxy.txt"
META_PROXY_IDX_FILE = ROOT / "out" / "meta_proxy_idx.txt"  # index de rotatie proxy Meta (persistat)


def load_proxy():
    """Citeste proxy.txt (host:port:user:parola) -> dict pentru Playwright/colector, sau None."""
    try:
        line = next(l.strip() for l in PROXY_FILE.read_text(encoding="utf8").splitlines() if l.strip() and not l.startswith("#"))
    except (OSError, StopIteration):
        return None
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, pw = parts
        return {"server": f"http://{host}:{port}", "username": user, "password": pw, "raw": line}
    if len(parts) == 2:
        return {"server": f"http://{parts[0]}:{parts[1]}", "raw": line}
    return None


def load_proxies() -> list[dict]:
    """Lista de proxy pentru rotatie (proxies.txt). Formatul liniei: host:port:utilizator:parola."""
    out = []
    try:
        lines = PROXIES_FILE.read_text(encoding="utf8").splitlines()
    except OSError:
        p = load_proxy()
        return [{k: v for k, v in p.items() if k != "raw"}] if p else []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) == 4:
            out.append({"server": f"http://{parts[0]}:{parts[1]}", "username": parts[2], "password": parts[3]})
        elif len(parts) == 2:
            out.append({"server": f"http://{parts[0]}:{parts[1]}"})
    return out


def profit_at_roas(sell_price: float, ali_price: float) -> dict:
    """Profit pe comanda (1 buc) la ROAS dat. Neplatitor TVA: Disponibil = B - E - F*1.21 - H, unde
    B = pret vanzare, E = marfa AliExpress (+21% TVA import), F = reclama (B/ROAS), H = transport furnizor + curier.
    Platitor TVA: venitul net e B/1.21, iar TVA-ul pe reclama si marfa se recupereaza.
    Intoarce si pretul minim de vanzare pentru pragul MIN_PROFIT_PCT (ex. Ali 5 lei -> 73, 10 -> 85, 20 -> 109 lei)."""
    ship = SHIP_ALI + SHIP_RO
    if PLATITOR_TVA:
        goods, ad_mult, rev_mult = ali_price, 1.0, 1 / (1 + TVA)
    else:
        goods, ad_mult, rev_mult = ali_price * (1 + TVA), 1 + TVA, 1.0
    costs = goods + ship
    ad = sell_price / ROAS * ad_mult
    profit = sell_price * rev_mult - costs - ad
    margin = rev_mult - ad_mult / ROAS - MIN_PROFIT_PCT / 100      # cat din pret ramane pentru marfa+livrare
    return {"profit": profit,
            "pct": (profit / sell_price * 100) if sell_price else 0.0,
            "ad_cost": ad,
            "costs": costs,
            "min_price": costs / margin if margin > 0 else float("inf")}


# --- Shopify: ponturile bune devin automat produse DRAFT (shopify_api.py) -----------------
SHOPIFY_AUTO_DRAFT = True          # False = dezactivat complet
SHOPIFY_MIN_NOTA = 6               # draft doar pentru ponturi cu NOTA >= 6
SHOPIFY_DRAFT_TARGETS = ("prod",)  # doar rularile prod (testele pe "Test grup" nu creeaza drafturi)
SHOPIFY_SHOP = os.environ.get("SHOPIFY_SHOP", "")  # e.g. your-store.myshopify.com
SHOPIFY_ADMIN_STORE = os.environ.get("SHOPIFY_ADMIN_STORE", "")  # https://admin.shopify.com/store/<this>/products/<id>
SHOPIFY_API_VERSION = "2026-10"
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "")  # app client id; the secret stays in shopify_client_secret.txt
SHOPIFY_VENDOR = os.environ.get("SHOPIFY_VENDOR", "My Store")
SHOPIFY_TAG = "ecom-radar"

# --- pagini in romana (poze romanizate + infografice) ---
RO_IMAGES = True                   # poze Ali cu textul tradus in romana (OCR + stergere + text RO); False = pozele originale
RO_IMAGES_MAX = 4                  # cate poze per produs (prima = poza principala)
RO_INFOGRAPHICS = True             # infografice RO 1080x1080: beneficii, cum se foloseste, specificatii
PAGE_STICKY_CTA = False            # buton rosu fix jos in descriere; OPRIT: EasySell (formular ramburs) are deja butonul fix jos
PAGE_CTA_HREF = "#es-popup-button"  # butoanele din descriere duc (derulare) la butonul EasySell "Comanda Acum" din pagina


# --- 19 oct 2026: saturare eMAG, public 30+, produse "in crestere", Gemini -----------------------------------
WA_TARGET = "prod"            # grupul pentru rularile automate (task-ul porneste cu --target prod); "test" = Test grup
WA_MIN_NOTA = 5               # pontul ajunge pe WhatsApp doar cu NOTA >= asta (dupa ajustarile eMAG/30+/crestere)
WA_ENABLED = True             # False = nu mai trimite pe WhatsApp (OpenClaw)
DISCORD_ENABLED = True        # False = nu mai posteaza pe Discord (canalele sunt independente)
SCHEDULE_INTERVAL_MIN = 60    # intervalul task-ului AdsMonitor (aplicat din aplicatia Ecom Monitor)

# eMAG: produsul e saturat daca se gaseste deja ieftin pe eMAG (clientul compara preturile)
EMAG_ENABLED = True
EMAG_CHEAP_PRICE = 60.0       # oferta "ieftina" = sub atatia lei
EMAG_DROP_CHEAP_OFFERS = 5    # >= atatea oferte relevante sub EMAG_CHEAP_PRICE => eliminat (sau nota -3 daca EMAG_DROP=False)
EMAG_SATURATED_OFFERS = 20    # >= atatea oferte relevante => nota -1 (piata plina)
EMAG_DROP = True
EMAG_CACHE_DAYS = 3           # o cautare se refoloseste N zile (out/emag_cache.json)
EMAG_DELAY = 4.0              # secunde intre cautari (+ jitter): politicos, fara blocare
EMAG_MAX_PER_RUN = 20

# Public 30+ (cumparatori seriosi): ponderi pe categorii, adunate la NOTA; negative = penalizare
AUDIENCE_ENABLED = True
AUDIENCE_DROP_AT = -3.0       # pondere <= atat => produsul e eliminat (ex. lenjerie / haine pe marimi)
CATEGORY_WEIGHTS = {
    "pet": 2.0, "casa": 1.5, "bucatarie": 1.5, "gradina": 1.0, "auto": 1.0, "sanatate": 1.0, "diy": 1.0,
    "gadget": -1.5, "adolescenti": -2.0, "fashion": -2.0, "lenjerie": -3.0,
}

# "In crestere": reclame pornite recent + numar de reclame care creste intre rulari (istoric in history.db)
RISING_ENABLED = True
RISING_MIN_DAYS = 3
RISING_MAX_DAYS = 14
RISING_MIN_NEW_ADS = 2        # reclame noi (pornite in ultimele 7 zile) pentru acelasi produs
SATURATED_DAYS = 60           # reclama cea mai veche ruleaza de atatea zile => produs vechi / saturat
SATURATED_ADVERTISERS = 4     # atatea magazine diferite cu aceeasi oferta AliExpress => saturat
HISTORY_DB = ROOT / "history.db"

# Alibaba: sursa de rezerva pentru pretul de cost cand AliExpress blocheaza (anti-bot / proxy expirate)
ALIBABA_FALLBACK = True       # False = fara rezerva (produsul ramane "fara oferta")
ALI_COOLDOWN_MIN = 120        # dupa o blocare AliExpress, atatea minute doar Alibaba (nu mai oprim rularea)
ALIBABA_MAX_PER_RUN = 15
ALIBABA_DELAY = 8.0           # secunde intre cautari Alibaba (+ jitter)
ALIBABA_CACHE_DAYS = 3
USD_RON = 4.6                 # curs aproximativ daca Alibaba afiseaza USD
EUR_RON = 5.0

# AI text: ordinea furnizorilor (Ollama si regulile raman mereu rezerva). Gemini: cheie in gemini_api_key.txt / GEMINI_API_KEY
LLM_PROVIDERS = ["gemini", "ollama", "reguli"]   # 19.10: fara abonament Claude; "claude" se poate adauga din aplicatie
GEMINI_MODEL = "gemini-3.8-flash"   # model stabil curent (ai.google.dev/gemini-api/docs/models, 13.10.2026)
GEMINI_FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-flash-latest", "gemini-2.5-flash"]  # daca primul nu e accesibil
GEMINI_TIMEOUT = 120

# --- suprascrieri din aplicatia Ecom Monitor (settings.json); valorile invalide sunt ignorate ---
try:
    import os as _os
    import settings as _settings
    SETTINGS_IGNORED = [] if _os.environ.get("ECOM_SETTINGS_OFF") == "1" else _settings.apply(globals())
except Exception as _e:  # noqa: BLE001  (o problema in settings.json nu opreste monitorul)
    SETTINGS_IGNORED = [f"settings.json: {_e}"]
