# Note de lucru (RO) — istoric și detalii

Rulează local, gratuit (fără Apify): Meta Ad Library (`meta-ads-collector`), căutare AliExpress după poză
(Playwright + Edge), verificare „același produs?" cu modelul de vedere din Ollama (`qwen3.8:27b`),
trimitere pe WhatsApp prin OpenClaw.

## Rulare
```
.venv\Scripts\python.exe run.py --target none --limit 5      # doar afișează (nu trimite)
.venv\Scripts\python.exe run.py --target test --max-send 3   # trimite pe "Test grup"
.venv\Scripts\python.exe run.py --target prod                # trimite pe "Ponturi Ecom"
```
Zilnic automat: `powershell -ExecutionPolicy Bypass -File register_task.ps1` (șterge cu `-Remove`).

## Setări
Toate în `config.py`: frazele căutate, ultimele N zile, intervalul de cost (1–10 lei), grupurile WhatsApp, modelul Ollama.

## Fișiere
- `run.py` orchestrare, `collect.py` reclame, `page.py` pagina produsului + galerie, `aliexpress.py` căutare după poză,
  `vlm.py` verificator, `notify.py` mesaj + imagine, `state.json` produse deja verificate (14 zile), `out/` loguri și imagini.

## Cerințe
- Gateway-ul OpenClaw pornit și WhatsApp conectat (`openclaw channels status`), Ollama pornit cu modelul din `config.py`.
- Nu porni un al doilea client WhatsApp pe același număr; trimiterea trece doar prin `openclaw message send`.
- Dacă AliExpress schimbă pagina sau cere captcha, rularea se oprește după 3 erori la rând și nu trimite nimic.

## Upgrade 1 oct 2026
- `metapool.py`: Meta pe IP direct (proxy-urile erau toate limitate), cooldown automat per ruta, page-size 30, buget de cereri.
- `shopfeed.py`: rezerva cand Meta e limitat — produse noi (2-21 zile) din magazinele Shopify care fac reclame.
- Pagini moarte 404/403: cooldown per produs/magazin, nu mai opresc rularea.
- `llm.py`: Claude Sonnet prin Cursor Agent CLI (judecata pe lot, unghiuri + hook-uri, scor, Black Friday); rezerva Ollama.
- `content.py`: pagina Shopify (titlu, pret, beneficii, descriere HTML, FAQ) in `out/content/`.
- `mockup.py`: mockup de telefon 1080x2340 (STORE_NAME) = imaginea pontului pe WhatsApp/Discord, `out/mockup_<id>.png`.
- Profit: 20% la ROAS 4, neplatitor TVA (PLATITOR_TVA in config.py). Cautari si in PL/HU.
- Teste: `--target test` NU posteaza pe Discord (`--discord on` pentru a forta). `--feed-only`, `--no-feed`, `--no-content`.

## Shopify magazinul propriu (2 oct 2026)
- `shopify_api.py`: ponturile trimise cu NOTA >= `SHOPIFY_MIN_NOTA` (6) devin produse **DRAFT** (nepublicate) pe magazinul propriu,
  variante Pachet 1/2/4 buc, poza Ali + mockup, tag `ecom-radar` + `ali:<id>` (fara duplicate). Link admin pe Discord, rand scurt pe WhatsApp.
- Token client_credentials (24h) in `.shopify_token.json`, reinnoit automat sub 1h; secretul in `shopify_client_secret.txt` (nu se afiseaza).
- `python shopify_api.py --test` | `--from-content out/content/content_<id>.json` | `--verify <aliId>`. Oprire: `SHOPIFY_AUTO_DRAFT = False`.
