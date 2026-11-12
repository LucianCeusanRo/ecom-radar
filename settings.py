"""Setari editabile din aplicatia Ecom Monitor (settings.json langa config.py).

config.py are valorile implicite; la final cheama apply(globals()) si suprascrie doar cheile din SCHEMA,
dupa validare. O valoare invalida in settings.json e ignorata (ramane cea din config.py), nu opreste rularea.
Cheile cu punct ("CATEGORY_WEIGHTS.pet", "GROUPS.prod") modifica o intrare dintr-un dict din config.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
SETTINGS_FILE = ROOT / "settings.json"
GEMINI_KEY_FILE = ROOT / "gemini_api_key.txt"

PROVIDERS = ("gemini", "claude", "ollama", "reguli")

# (cheie, tip, eticheta RO, grup, extra). Tipuri: int, float, bool, str, list (lista de texte), choice, jid, order.
SCHEMA: list[dict] = [
    # --- General / rulare
    {"key": "SCHEDULE_INTERVAL_MIN", "type": "int", "label": "Rulare automată la fiecare (minute)", "group": "Rulare",
     "min": 15, "max": 720, "help": "Se aplică task-ului AdsMonitor când salvezi."},
    {"key": "MAX_SEND_PER_RUN", "type": "int", "label": "Ponturi maxime pe rulare", "group": "Rulare", "min": 0, "max": 50},
    {"key": "MAX_CANDIDATES", "type": "int", "label": "Produse verificate pe rulare", "group": "Rulare", "min": 1, "max": 200},
    {"key": "MAX_RUNTIME_MIN", "type": "int", "label": "Durata maximă a unei rulări (minute)", "group": "Rulare", "min": 10, "max": 170},
    {"key": "WA_TARGET", "type": "choice", "label": "Grup WhatsApp pentru rulările automate", "group": "Rulare",
     "choices": ["prod", "test"], "help": "prod = Ponturi Ecom, test = Test grup (fără Discord și fără drafturi)."},
    {"key": "GROUPS.prod", "type": "jid", "label": "WhatsApp prod (ID grup)", "group": "Rulare"},
    {"key": "GROUPS.test", "type": "jid", "label": "WhatsApp test (ID grup)", "group": "Rulare"},
    {"key": "WA_ENABLED", "type": "bool", "label": "Canal WhatsApp (OpenClaw)", "group": "Rulare"},
    {"key": "DISCORD_ENABLED", "type": "bool", "label": "Canal Discord", "group": "Rulare",
     "help": "Canalele sunt independente: pontul contează trimis dacă a ajuns pe cel puțin unul."},
    {"key": "SHOPIFY_AUTO_DRAFT", "type": "bool", "label": "Creează draft Shopify automat", "group": "Rulare"},
    {"key": "RO_IMAGES", "type": "bool", "label": "Poze traduse în română", "group": "Rulare"},
    {"key": "RO_INFOGRAPHICS", "type": "bool", "label": "Infografice în română", "group": "Rulare"},
    # --- Pret si nota
    {"key": "COST_MAX", "type": "float", "label": "Preț maxim AliExpress (lei)", "group": "Preț și notă", "min": 1, "max": 100},
    {"key": "COST_MIN", "type": "float", "label": "Preț minim AliExpress (lei)", "group": "Preț și notă", "min": 0, "max": 50},
    {"key": "WA_MIN_NOTA", "type": "int", "label": "NOTA minimă pentru WhatsApp", "group": "Preț și notă", "min": 0, "max": 10},
    {"key": "SHOPIFY_MIN_NOTA", "type": "int", "label": "NOTA minimă pentru draft Shopify", "group": "Preț și notă", "min": 0, "max": 10},
    {"key": "MIN_PROFIT_PCT", "type": "float", "label": "Profit minim (%)", "group": "Preț și notă", "min": 0, "max": 80},
    {"key": "ROAS", "type": "float", "label": "ROAS de calcul", "group": "Preț și notă", "min": 1, "max": 20},
    {"key": "ALIBABA_FALLBACK", "type": "bool", "label": "Rezervă Alibaba când AliExpress blochează", "group": "Preț și notă",
     "help": "Preț angro (MOQ); fără draft Shopify pentru aceste ponturi."},
    {"key": "ALI_COOLDOWN_MIN", "type": "int", "label": "Pauză AliExpress după blocare (minute)", "group": "Preț și notă", "min": 10, "max": 1440},
    {"key": "ALIBABA_MAX_PER_RUN", "type": "int", "label": "Căutări Alibaba maxime pe rulare", "group": "Preț și notă", "min": 0, "max": 100},
    # --- Cautari
    {"key": "QUERIES", "type": "list", "label": "Cuvinte cheie RO (Meta)", "group": "Căutări"},
    {"key": "PET_QUERIES", "type": "list", "label": "Cuvinte cheie RO pentru animale", "group": "Căutări"},
    {"key": "FOREIGN_QUERIES.PL", "type": "list", "label": "Cuvinte cheie PL", "group": "Căutări"},
    {"key": "FOREIGN_QUERIES.HU", "type": "list", "label": "Cuvinte cheie HU", "group": "Căutări"},
    {"key": "QUERIES_PER_RUN", "type": "int", "label": "Căutări RO pe rulare", "group": "Căutări", "min": 0, "max": 30},
    {"key": "FOREIGN_PER_RUN", "type": "int", "label": "Căutări PL/HU pe rulare", "group": "Căutări", "min": 0, "max": 10},
    {"key": "MIN_DAYS", "type": "int", "label": "Reclamă pornită de minim (zile)", "group": "Căutări", "min": 0, "max": 60},
    {"key": "DAYS", "type": "int", "label": "Reclamă pornită de maxim (zile)", "group": "Căutări", "min": 1, "max": 365},
    # --- Public 30+
    {"key": "AUDIENCE_ENABLED", "type": "bool", "label": "Prioritate public 30+", "group": "Public 30+"},
    {"key": "AUDIENCE_DROP_AT", "type": "float", "label": "Elimină produsele cu pondere ≤", "group": "Public 30+", "min": -10, "max": 0,
     "help": "Ex. -3 elimină lenjeria / produsele pe mărimi."},
    *[{"key": f"CATEGORY_WEIGHTS.{k}", "type": "float", "label": f"Pondere: {lbl}", "group": "Public 30+", "min": -5, "max": 5}
      for k, lbl in (("pet", "animale de companie"), ("casa", "casă și curățenie"), ("bucatarie", "bucătărie"),
                     ("gradina", "grădină"), ("auto", "auto"), ("sanatate", "sănătate și confort"), ("diy", "bricolaj / DIY"),
                     ("gadget", "gadgeturi la modă"), ("adolescenti", "adolescenți"), ("fashion", "modă / bijuterii"),
                     ("lenjerie", "lenjerie / mărimi"))],
    # --- eMAG
    {"key": "EMAG_ENABLED", "type": "bool", "label": "Verificare saturare pe eMAG", "group": "eMAG"},
    {"key": "EMAG_CHEAP_PRICE", "type": "float", "label": "Ofertă „ieftină” sub (lei)", "group": "eMAG", "min": 10, "max": 500},
    {"key": "EMAG_DROP_CHEAP_OFFERS", "type": "int", "label": "Elimină dacă are ≥ atâtea oferte ieftine", "group": "eMAG", "min": 1, "max": 100},
    {"key": "EMAG_SATURATED_OFFERS", "type": "int", "label": "Saturat de la (oferte relevante)", "group": "eMAG", "min": 1, "max": 200},
    {"key": "EMAG_DROP", "type": "bool", "label": "Elimină (nu doar scade nota)", "group": "eMAG"},
    {"key": "EMAG_CACHE_DAYS", "type": "int", "label": "Cache căutări (zile)", "group": "eMAG", "min": 0, "max": 30},
    {"key": "EMAG_DELAY", "type": "float", "label": "Pauză între căutări (secunde)", "group": "eMAG", "min": 1, "max": 60},
    {"key": "EMAG_MAX_PER_RUN", "type": "int", "label": "Căutări eMAG maxime pe rulare", "group": "eMAG", "min": 0, "max": 100},
    # --- In crestere
    {"key": "RISING_ENABLED", "type": "bool", "label": "Detectare produse în creștere", "group": "În creștere"},
    {"key": "RISING_MIN_DAYS", "type": "int", "label": "Reclame pornite de minim (zile)", "group": "În creștere", "min": 0, "max": 60},
    {"key": "RISING_MAX_DAYS", "type": "int", "label": "Reclame pornite de maxim (zile)", "group": "În creștere", "min": 1, "max": 90},
    {"key": "RISING_MIN_NEW_ADS", "type": "int", "label": "Reclame noi (7 zile) pentru „în creștere”", "group": "În creștere", "min": 1, "max": 50},
    {"key": "SATURATED_DAYS", "type": "int", "label": "Saturat dacă rulează de (zile)", "group": "În creștere", "min": 14, "max": 730},
    {"key": "SATURATED_ADVERTISERS", "type": "int", "label": "Saturat de la (magazine cu același produs)", "group": "În creștere", "min": 2, "max": 50},
    # --- AI
    {"key": "LLM_PROVIDERS", "type": "order", "label": "Ordinea AI (text)", "group": "AI", "choices": list(PROVIDERS),
     "help": "Ex. gemini, claude, ollama, reguli. Ollama și reguli rămân mereu rezervă la final."},
    {"key": "GEMINI_MODEL", "type": "str", "label": "Model Gemini", "group": "AI"},
    {"key": "LLM_MODEL", "type": "str", "label": "Model Claude (Cursor CLI)", "group": "AI"},
    {"key": "LLM_MAX_CALLS_PER_RUN", "type": "int", "label": "Apeluri AI maxime pe rulare", "group": "AI", "min": 0, "max": 100},
    {"key": "VLM_MODEL", "type": "str", "label": "Model Ollama (vedere)", "group": "AI"},
]
BY_KEY = {f["key"]: f for f in SCHEMA}


def validate(field: dict, value):
    """Intoarce valoarea convertita sau ridica ValueError cu mesaj in romana."""
    t = field["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("1", "true", "da", "on"):
            return True
        if str(value).strip().lower() in ("0", "false", "nu", "off"):
            return False
        raise ValueError("trebuie DA sau NU")
    if t in ("int", "float"):
        try:
            v = float(str(value).replace(",", "."))
        except ValueError:
            raise ValueError("trebuie să fie un număr") from None
        if t == "int":
            if v != int(v):
                raise ValueError("trebuie să fie un număr întreg")
            v = int(v)
        if "min" in field and v < field["min"]:
            raise ValueError(f"minim {field['min']}")
        if "max" in field and v > field["max"]:
            raise ValueError(f"maxim {field['max']}")
        return v
    if t == "choice":
        if value not in field["choices"]:
            raise ValueError("alege una din: " + ", ".join(field["choices"]))
        return value
    if t in ("list", "order"):
        items = value if isinstance(value, list) else re.split(r"[\n,;]+", str(value))
        items = [str(x).strip() for x in items if str(x).strip()]
        if t == "order":
            items = [x.lower() for x in items]
            bad = [x for x in items if x not in field["choices"]]
            if bad:
                raise ValueError("necunoscut: " + ", ".join(bad))
            if len(set(items)) != len(items):
                raise ValueError("fără dubluri")
            if not items:
                raise ValueError("cel puțin unul")
        return items
    if t == "jid":
        v = str(value).strip()
        if not re.fullmatch(r"\d{6,}(-\d+)?@g\.us", v):
            raise ValueError("format: 1203...@g.us")
        return v
    v = str(value).strip()
    if not v:
        raise ValueError("nu poate fi gol")
    return v


def load() -> dict:
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(values: dict) -> None:
    clean = {}
    for k, v in values.items():
        if k in BY_KEY:
            clean[k] = validate(BY_KEY[k], v)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def get(ns: dict, key: str):
    if "." in key:
        a, b = key.split(".", 1)
        return (ns.get(a) or {}).get(b)
    return ns.get(key)


def _set(ns: dict, key: str, value):
    if "." in key:
        a, b = key.split(".", 1)
        d = dict(ns.get(a) or {})
        d[b] = value
        ns[a] = d
    else:
        ns[key] = value


def apply(ns: dict) -> list[str]:
    """Aplica settings.json peste config (globals()). Intoarce lista de chei ignorate (invalide)."""
    bad = []
    for k, v in load().items():
        f = BY_KEY.get(k)
        if not f:
            continue
        try:
            _set(ns, k, validate(f, v))
        except ValueError:
            bad.append(k)
    if ns.get("COST_MIN", 0) > ns.get("COST_MAX", 10):
        ns["COST_MIN"] = 1.0
    return bad


def gemini_key() -> str:
    """Cheia Gemini: variabila de mediu sau gemini_api_key.txt. Nu se afiseaza niciodata."""
    import os
    for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(n, "").strip():
            return os.environ[n].strip()
    try:
        return GEMINI_KEY_FILE.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def save_gemini_key(key: str) -> None:
    key = key.strip()
    if not key:
        GEMINI_KEY_FILE.unlink(missing_ok=True)
        return
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]{20,200}", key):
        raise ValueError("cheia Gemini pare invalidă")
    GEMINI_KEY_FILE.write_text(key + "\n", encoding="utf-8")
    restrict_acl(GEMINI_KEY_FILE)


def restrict_acl(path) -> bool:
    """Fisier secret: acces doar pentru utilizatorul curent (+SYSTEM), fara mostenire (icacls). Windows only."""
    import os
    import subprocess
    if os.name != "nt" or not Path(path).exists():
        return False
    user = os.environ.get("USERNAME", "")
    r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(F)", "/grant:r", "SYSTEM:(F)"],
                       capture_output=True, text=True, creationflags=0x08000000)
    return r.returncode == 0
