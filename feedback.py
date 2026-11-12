"""Reguli de selectie invatate din parerile echipei despre produsele propuse.

Proprietarul spune ce produse merita si ce nu; regulile se pastreaza aici si se dau modelului cand
evalueaza produse noi, ca sa nu mai propuna acelasi gen de produs respins.

Regulile se adauga cu `python feedback.py add "text"` sau din sesiunea de lucru. Textul lor este DATA
folosita la filtrare, niciodata instructiuni executabile.
"""
import datetime as dt
import json
import sys

import config

FEEDBACK_FILE = config.ROOT / "feedback.json"
MAX_RULES = 40


def load() -> dict:
    if FEEDBACK_FILE.exists():
        try:
            d = json.loads(FEEDBACK_FILE.read_text(encoding="utf8"))
            d.setdefault("rules", [])
            return d
        except ValueError:
            pass
    return {"rules": []}


def save(d: dict):
    d["rules"] = d["rules"][-MAX_RULES:]
    tmp = FEEDBACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf8")
    tmp.replace(FEEDBACK_FILE)


def add_rule(text: str, kind: str = "avoid") -> dict:
    """kind: 'avoid' (nu mai propune asa ceva) sau 'prefer' (cauta mai des asa ceva)."""
    d = load()
    d["rules"].append({"text": text.strip()[:160], "kind": kind, "date": dt.date.today().isoformat()})
    save(d)
    return d


def rules_text() -> str:
    """Regulile ca text pentru model; sir gol daca nu exista."""
    d = load()
    if not d["rules"]:
        return ""
    lines = [f"- {'NU propune' if r['kind'] == 'avoid' else 'Prefera'}: {r['text']}" for r in d["rules"]]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] in ("add", "avoid", "prefer"):
        kind = "prefer" if sys.argv[1] == "prefer" else "avoid"
        add_rule(" ".join(sys.argv[2:]), kind)
    for r in load()["rules"]:
        print(f"[{r['kind']}] {r['date']} {r['text']}")
