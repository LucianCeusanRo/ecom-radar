"""Romana corecta in tot ce iese din pipeline (pagina Shopify, poze, mesajul de pe WhatsApp).

- ş ţ (sedila, gresit) -> ș ț (virgula, corect);
- texte scrise COMPLET fara diacritice (Ollama / sabloane / LLM grabit): cuvinte frecvente, neambigue, primesc
  diacriticele (fara -> fără, si -> și, in -> în, pestele -> peștele ...). Daca textul are deja diacritice,
  nu ghicim nimic (doar sedila -> virgula);
- livrarea nu se promite in X zile (produsele vin de la furnizor): "livrare rapidă 1-3 zile" -> "livrare prin curier".
"""
import re

CEDILLA = str.maketrans({"ş": "ș", "ţ": "ț", "Ş": "Ș", "Ţ": "Ț"})
DIACR = set("ăâîșțĂÂÎȘȚ")
# doar cuvinte fara alt sens in romana fara diacritice (ex. NU "sa", "cat", "lanseta", "peste" = pe+ste / peste)
WORDS = {
    "fara": "fără", "si": "și", "in": "în", "intr": "într", "intre": "între", "pana": "până", "isi": "își", "iti": "îți",
    "esti": "ești", "cand": "când", "dupa": "după", "acasa": "acasă", "usor": "ușor", "usoara": "ușoară",
    "pestele": "peștele", "pestii": "peștii", "pescuitul": "pescuitul", "muscatura": "mușcătura",
    "muscaturi": "mușcături", "muscaturile": "mușcăturile", "lansete": "lansete", "lansetei": "lansetei",
    "comanda": "comanda", "plata": "plata", "livrarea": "livrarea", "rapida": "rapidă", "garantat": "garantat",
    "noapte": "noapte", "noaptea": "noaptea", "functioneaza": "funcționează", "foloseste": "folosește",
    "foloseste-l": "folosește-l", "ramburs": "ramburs", "intrebari": "întrebări", "frecvente": "frecvente",
    "beneficii": "beneficii", "calitate": "calitate", "rezistent": "rezistent", "rezistenta": "rezistentă",
    "siguranta": "siguranța", "timpul": "timpul", "linistit": "liniștit", "liniste": "liniște", "stii": "știi",
    "vrei": "vrei", "poti": "poți", "dai": "dai", "inainte": "înainte", "cumperi": "cumperi", "cadoul": "cadoul",
    "pescarul": "pescarul", "pescar": "pescar", "varful": "vârful", "varf": "vârf", "trezeste": "trezește",
    "clipeste": "clipește", "suna": "sună", "asta": "asta", "asa": "așa", "inca": "încă", "mult": "mult",
    "atat": "atât", "atata": "atâta", "decat": "decât", "niciodata": "niciodată", "astazi": "astăzi",
    "accepti": "accepți", "astepti": "aștepți", "astepta": "aștepta", "verifici": "verifici", "platesti": "plătești", "primesti": "primești", "economisesti": "economisești",
}
CAP = {k: v for k, v in WORDS.items() if k != v}
_IN = r"(?:\s+(?:în|in|doar|numai|de))*"
DELIVERY = [
    (re.compile(r"[Ll]ivrare\s+(?:rapid[ăa]|gratuit[ăa]|express|garantat[ăa])" + _IN + r"\s*\(?\s*(?:24\s*[–-]\s*48\s*(?:h|ore)|1\s*[–-]\s*[23]\s*zile(?:\s+lucr[ăa]toare)?)\)?", re.I), "Livrare prin curier"),
    (re.compile(r"[Ll]ivrare" + _IN + r"\s+(?:24\s*[–-]\s*48\s*(?:h|ore)|1\s*[–-]\s*[23]\s*zile(?:\s+lucr[ăa]toare)?)", re.I), "Livrare prin curier"),
    (re.compile(r"\b1\s*[–-]\s*[23]\s*zile\s+lucr[ăa]toare", re.I), "câteva zile lucrătoare (termenul exact îl primești la confirmarea comenzii)"),
    (re.compile(r"[Tt]ransport\s+rapid" + _IN + r"\s+1\s*[–-]\s*[23]\s*zile"), "Livrare prin curier"),
]


def _word_fix(m: re.Match) -> str:
    w = m.group(0)
    low = w.lower()
    rep = CAP.get(low)
    if not rep:
        return w
    if w.isupper() and len(w) > 1:
        return rep.upper()
    if w[0].isupper():
        return rep[0].upper() + rep[1:]
    return rep


def fix(text, delivery: bool = True):
    """Corecteaza un text (str) sau, recursiv, liste / dict-uri de texte."""
    if isinstance(text, dict):
        return {k: fix(v, delivery) for k, v in text.items()}
    if isinstance(text, list):
        return [fix(v, delivery) for v in text]
    if not isinstance(text, str) or not text:
        return text
    t = text.translate(CEDILLA)
    if not any(c in DIACR for c in t):
        t = re.sub(r"\b([Ss])a-(ti|mi|si|ii|l|o|i|le|ne|va)\b",
                   lambda m: ("Să-" if m.group(1) == "S" else "să-") + {"ti": "ți", "si": "și"}.get(m.group(2), m.group(2)), t)
        t = re.sub(r"\b([Ii])ti\b", lambda m: "Îți" if m.group(1) == "I" else "îți", t)
        t = re.sub(r"[A-Za-z]+", _word_fix, t)
    if delivery:
        for rx, rep in DELIVERY:
            t = rx.sub(lambda m, r=rep: r if m.group(0)[:1].isupper() else r[:1].lower() + r[1:], t)
    return t


def has_english(text: str) -> bool:
    """Semnal simplu: cuvinte englezesti frecvente in text (pentru verificare in selftest)."""
    return bool(re.search(r"\b(the|and|with|for|your|alarm|light|high|fish|free|shipping|buy|now)\b", text or "", re.I))
