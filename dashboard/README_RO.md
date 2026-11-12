# Ecom Radar (dashboard produse ads-monitor)

- Online: ruta `/ecom` din serverul FastAPI (`ecom_routes.py`), doar pentru contul admin
- Offline: deschide `index.html` din acest folder (date locale din `data/ecom_products.js`)
- Panoul desktop (Tkinter) -> tab **Ecom** (`ecom_panel_tab.py`)

## Cum se actualizeaza datele
`build_data.py` citeste (doar citire) folderul ads-monitor (`ADS_MONITOR_DIR`, implicit folderul parinte):
`out/run_*.json`, mesajele din `*.log` / `out/*.log`, `out/examples/*`, `out/content/content_<id>.*`,
`out/mockup_<id>.png`, `out/match_<id>.jpg`, `state.json` (sent_items). Pozele lipsa se iau o singura data din
og:image-ul paginii magazinului concurent. Scrie `data/ecom_products.json`, `data/ecom_products.js`, `data/img/`, `index.html`.
Task Windows **EcomRadarRefresh** il ruleaza la 15 minute (pythonw, fara fereastra). Manual: `python build_data.py`
sau butonul "Reconstruieste datele" (site -> Setari, sau tab-ul Ecom din panou).

## Fisiere
- `app.html` - interfata (aceeasi pentru site si offline). `index.html` e generat din ea.
- `build_data.py` - exportul de date. `ecom_panel_tab.py` - tab-ul din panoul desktop. `shot.py` - capturi Playwright.
- Pe server: `ecom_routes.py` se monteaza intr-o aplicatie FastAPI existenta.

## Formula profit (neplatitor TVA, editabila in Setari)
B = 0.8·A, E = 20·N + 0.5·N, reclame = A/ROAS·1.21, H = 0.8·cost·N, profit% = (B − E − reclame − H)/B. Tinta 20% la ROAS 4.
