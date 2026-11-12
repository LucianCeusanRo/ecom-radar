"""Erori tipizate: separa esecurile temporare de infrastructura de rezultatele negative reale."""


class InfraError(Exception):
    """Esec temporar (retea, pagina indisponibila, cautare goala, model). Produsul NU se marcheaza ca verificat."""


class VlmError(InfraError):
    """Ollama a picat / a dat timeout / a raspuns invalid. Nu inseamna 'nu e acelasi produs'."""


class AliBlocked(Exception):
    """AliExpress serveste pagina anti-bot (punish/captcha). Oprim faza de cautare, fara sa marcam nimic."""
