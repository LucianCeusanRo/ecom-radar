"""Trimite mesaje pe WhatsApp prin OpenClaw (gateway-ul detine sesiunea; nu porni alt client)."""
import json
import re
import os
import shutil
import subprocess
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

import config


def side_by_side(left: str, right: str, out: Path, h: int = 520) -> str:
    """Poza din reclama (stanga) langa poza de pe AliExpress (dreapta), ca sa judece oricine potrivirea."""
    imgs = []
    for p in (left, right):
        im = Image.open(p).convert("RGB")
        imgs.append(im.resize((int(im.width * h / im.height), h)))
    w = sum(i.width for i in imgs) + 30
    canvas = Image.new("RGB", (w, h + 40), "white")
    x = 0
    for i, im in enumerate(imgs):
        canvas.paste(im, (x, 40))
        ImageDraw.Draw(canvas).text((x + 10, 12), "Reclama / magazin RO" if i == 0 else "AliExpress", fill="black")
        x += im.width + 30
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "JPEG", quality=90)
    return str(out)


def _t(s: str, n: int = 110) -> str:
    """Text sigur pentru WhatsApp: scoate caracterele de formatare (* _ ~ `) din titluri si taie la n caractere."""
    return " ".join(str(s).replace("*", " ").replace("_", " ").replace("~", " ").replace("`", " ").split())[:n]


COUNTRY_NAMES = {"PL": "Polonia", "HU": "Ungaria"}


def build_message(m: dict) -> str:
    try:  # romana corecta in pont (unghiuri, hook-uri, CTA, motivul notei), indiferent de motorul care le-a scris
        import ro_text
        m = {**m, "angles": ro_text.fix(m.get("angles") or []), "card": ro_text.fix(m.get("card") or {}),
             "bf_reason": ro_text.fix(m.get("bf_reason") or "")}
    except ImportError:
        pass
    card0 = m.get("card") or {}
    lines = ([f"⭐ *NOTA {card0['nota']}/10*" + (f" – {_t(card0.get('nota_reason'), 110)}" if card0.get("nota_reason") else "")]
             if card0.get("nota") else []) + [
        "🐾 *Produs PET găsit*" if m.get("pet") else "🔥 *Produs găsit*",
        f"📦 {_t(m['title'])}",
        "",
        f"🛒 *AliExpress: {m['ali_price']:.2f} lei* (+ transport)",
        f"🔗 Link AliExpress: {m['ali_url']}",
    ] if m.get("price_source") != "alibaba" else [
        f"🛒 *Alibaba: {m['ali_price']:.2f} lei/buc* (angro, + transport)",
        f"🔗 Link Alibaba: {m['ali_url']}",
        f"⚠️ *Sursă preț: Alibaba (angro, MOQ {m.get('moq') or '?'} buc) – verifică pe AliExpress înainte de comandă*",
    ]
    econ = m.get("econ") or {}
    if m.get("sell_price"):
        lines.append(f"💰 Vândut în RO cu *{m['sell_price']:.0f} lei*")
        if econ:
            lines.append(f"📈 Profit la ROAS {config.ROAS:.0f}: *{econ['profit']:.0f} lei/comandă ({econ['pct']:.0f}%)*"
                         f"  · reclamă {econ['ad_cost']:.0f} lei{'' if config.PLATITOR_TVA else ' (cu TVA)'} + marfă și livrare {econ['costs']:.0f} lei")
            lines.append(f"🧮 Preț minim pentru {config.MIN_PROFIT_PCT:.0f}% profit la ROAS {config.ROAS:.0f}: *{econ['min_price']:.0f} lei*")
    elif econ:
        lines.append(f"📈 Ca să iasă {config.MIN_PROFIT_PCT:.0f}% profit la ROAS {config.ROAS:.0f}, "
                     f"vinde-l cu minim *{econ['min_price']:.0f} lei*")
    if m.get("source") == "magazin":
        shop_line = f"🏪 {_t(m['page_name'], 50)} · produs nou în magazin din {m['started']} ({m['days_running']} zile)"
        ad_line = f"📢 Reclamele active ale magazinului: {m['ad_url']}"
    else:
        shop_line = f"🏪 {_t(m['page_name'], 50)} · reclamă pornită {m['started']} ({m['days_running']} zile)"
        ad_line = f"📢 Link reclamă: {m['ad_url']}"
    if m.get("country") and m["country"] != config.COUNTRY:
        lines.append(f"🌍 Reclamă din {COUNTRY_NAMES.get(m['country'], m['country'])} – încă rar în RO, poți fi printre primii")
    mk = m.get("market") or {}
    if mk.get("rising"):
        lines.append("📈 *În creștere*" + (f" – {_t('; '.join(mk.get('rising_notes') or []), 110)}" if mk.get("rising_notes") else ""))
    elif mk.get("rising_notes"):
        lines.append(f"⏳ Reclame: {_t('; '.join(mk['rising_notes']), 110)}")
    if mk.get("emag"):
        lines.append(f"🏬 {_t(mk['emag'], 90)}" + (f" ({mk['emag_url']})" if mk.get("emag_url") else ""))
    if mk.get("audience"):
        w = mk.get("aud_w") or 0
        lines.append(f"👥 Public 30+: {_t(mk['audience'], 50)} ({w:+g})")
    if m.get("black_friday"):
        lines.append(f"🎁 *Potrivit pentru Black Friday / Crăciun*" + (f" – {_t(m.get('bf_reason'), 80)}" if m.get("bf_reason") else ""))
    lines += [
        "",
        shop_line,
        ad_line,
        f"🌐 Link magazin: {m['link_url'].split('?')[0]}",
        "",
    ]
    card = m.get("card") or {}
    sc = [(k, card[k]) for k in ("wow", "problema", "nesaturat", "upsell", "compact") if k in card]
    if sc:
        names = {"wow": "WOW vizual", "problema": "problemă clară", "nesaturat": "nesaturat", "upsell": "upsell/cantitate",
                 "compact": "compact"}
        tot = sum(v for _, v in sc)
        lines.append(f"📊 *Scor produs: {tot}/{5 * len(sc)}* · " + " · ".join(f"{names[k]} {v}/5" for k, v in sc))
        if card.get("ajustari"):
            lines.append(f"🧾 NOTA {card.get('nota_base')} → {card.get('nota')}: " + "; ".join(_t(x, 70) for x in card["ajustari"]))
        lines.append("")
    if m.get("angles"):
        lines.append("🎯 *Unghiuri de marketing (hook-uri pentru primele 3 secunde):*")
        for i, a in enumerate(m["angles"][:3], 1):
            hooks = a.get("hooks") or [a.get("hook", "")]
            lines.append(f"{i}. *{_t(a['name'], 40)}*")
            lines += [f"   – „{_t(h)}”" for h in hooks[:3] if h]
        if card.get("cta"):
            lines.append(f"📣 CTA final: „{_t(card['cta'], 140)}”")
        lines.append("🎬 Reclamă 20-40s: Hook 3s → Body ~80% (ce face, avantaje, problema rezolvată; voiceover = imagini) → CTA 5-10s")
        lines.append("")
    if m.get("price_source") == "alibaba":
        lines.append(f"🔎 Găsit pe Alibaba după cuvinte cheie ({_t(m['ali_title'], 60)}), fără verificare vizuală")
    elif m.get("no_ai"):
        lines.append(f"⚠️ Verificat doar vizual, fără AI ({_t(m['ali_title'], 60)}) – uită-te la poza comparativă")
    else:
        lines.append(f"🤖 Verificat automat: același tip de produs ({_t(m['ali_title'], 60)})")
    return "\n".join(lines)


def _openclaw_cmd() -> list[str]:
    """Node + openclaw.mjs direct (evita shim-ul .cmd, care strica ghilimelele si liniile noi din JSON)."""
    mjs = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "openclaw" / "openclaw.mjs"
    node = shutil.which("node")
    if node and mjs.exists():
        return [node, str(mjs)]
    return [shutil.which("openclaw.cmd") or "openclaw"]


# Directorul dedicat OpenClaw pentru poze trimise. E permis de gateway chiar si cand uneltele agentului sunt blocate
# pe grup (protectia pusa pe "Ponturi Ecom"), deci pozele se trimit fara sa slabim acea protectie.
OUTBOUND_DIR = Path(os.environ.get("OPENCLAW_CONFIG_DIR") or (Path.home() / ".openclaw")) / "media" / "outbound" / "ads-monitor"


def _stage_media(media: str) -> Path:
    OUTBOUND_DIR.mkdir(parents=True, exist_ok=True)
    dst = OUTBOUND_DIR / Path(media).name
    shutil.copyfile(media, dst)
    return dst


def cleanup_outbound(max_age_min: int = 30) -> int:
    """Sterge pozele copiate pentru trimitere, mai vechi de max_age_min (gateway-ul le-a citit deja)."""
    n = 0
    if OUTBOUND_DIR.exists():
        for f in OUTBOUND_DIR.glob("*"):
            try:
                if time.time() - f.stat().st_mtime > max_age_min * 60:
                    f.unlink()
                    n += 1
            except OSError:
                pass
    return n


def send(target_jid: str, text: str, media: str | None = None, retries: int = 2) -> bool:
    """Trimite prin gateway-ul OpenClaw (RPC `send`). Cheia de idempotenta face reincercarile sigure: fara dubluri.
    Daca gateway-ul refuza poza, trimite macar textul (cu toate linkurile), ca produsul sa ajunga in grup."""
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{target_jid}|{text}|{media or ''}"))
    params = {"to": target_jid, "channel": "whatsapp", "message": text, "idempotencyKey": key}
    if media:
        params["mediaUrl"] = str(_stage_media(media))
    for attempt in range(1, retries + 2):
        cmd = _openclaw_cmd() + ["gateway", "call", "send", "--params", json.dumps(params, ensure_ascii=False),
                                 "--timeout", "90000", "--json"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf8", timeout=150)
            if r.returncode == 0:
                return True
            err = " ".join((r.stderr or r.stdout).split())[:200]
        except subprocess.TimeoutExpired:
            err = "timeout"
        print(f"[notify] incercarea {attempt} esuata: {err}", flush=True)
        if "LocalMediaAccessError" in err and "mediaUrl" in params:
            print("[notify] gateway-ul refuza poza; trimit mesajul fara poza", flush=True)
            params.pop("mediaUrl")
            params["idempotencyKey"] = key + "-text"
            continue
        time.sleep(5)
    return False

def _wa_bold_to_discord(text: str) -> str:
    """WhatsApp *bold* -> Discord **bold** (perechi simple de *...*)."""
    return re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"**\1**", text)


def send_discord(text: str, media: str | None = None, files: list[str] | None = None) -> bool:
    """Posteaza pe Discord webhook. Fara URL configurat => skip (False). Nu logheaza URL-ul."""
    url = getattr(config, "DISCORD_WEBHOOK_URL", "") or ""
    if not url:
        print("[notify] discord skip: webhook neconfigurat", flush=True)
        return False
    full = _wa_bold_to_discord(text or "")
    chunks, cur = [], ""
    for line in full.split("\n"):  # Discord: max 2000 caractere/mesaj => impartim pe linii
        if len(cur) + len(line) + 1 > 1900 and cur:
            chunks.append(cur)
            cur = ""
        cur = (cur + "\n" + line) if cur else line[:1900]
    chunks.append(cur)
    if len(chunks) > 1:
        ok = True
        for ch in chunks[:-1]:
            ok = send_discord(ch) and ok
            time.sleep(1)
        return send_discord(chunks[-1], media, files) and ok
    content = chunks[0]
    paths = [Path(p) for p in ([media] if media else []) + list(files or []) if p and Path(p).is_file()]
    media_path = paths[0] if paths else None

    def _post_json() -> tuple[int, str]:
        body = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ads-monitor"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, (resp.read() or b"")[:200].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"")[:200].decode("utf-8", "replace")

    def _post_multipart() -> tuple[int, str]:
        boundary = "----adsmonitor" + uuid.uuid4().hex
        payload = json.dumps({"content": content}, ensure_ascii=False)
        parts = [(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
        ).encode("utf-8")]
        for i, fp in enumerate(paths):
            filename = fp.name
            low = filename.lower()
            ct = ("image/jpeg" if low.endswith((".jpg", ".jpeg")) else "image/png" if low.endswith(".png")
                  else "text/html; charset=utf-8" if low.endswith(".html")
                  else "text/plain; charset=utf-8" if low.endswith(".txt") else "application/octet-stream")
            parts.append((
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[{i}]\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {ct}\r\n\r\n"
            ).encode("utf-8") + fp.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "ads-monitor",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status, (resp.read() or b"")[:200].decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"")[:200].decode("utf-8", "replace")

    for attempt in range(1, 4):
        try:
            status, snippet = _post_multipart() if media_path else _post_json()
        except Exception as e:  # noqa: BLE001
            print(f"[notify] discord attempt {attempt} error: {type(e).__name__}: {e}", flush=True)
            time.sleep(1.5 * attempt)
            continue
        if status in (200, 204):
            print(f"[notify] discord OK status={status}", flush=True)
            return True
        if status == 429 or status >= 500:
            print(f"[notify] discord retry status={status} attempt={attempt}", flush=True)
            time.sleep(2 * attempt)
            continue
        print(f"[notify] discord fail status={status} body={snippet[:120]}", flush=True)
        return False
    print("[notify] discord fail after retries", flush=True)
    return False
