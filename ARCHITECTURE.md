# Architektura

## Vrstvy

```
dokumenty/ (PDF, Word, Markdown, text, HTML; prehled.json s údaji)
  → chatbot/ingest/     sbirka.py   stažení předpisů obce ze Sbírky právních předpisů
                        extract.py  soubor → řádky textu (číslování Wordu, zalomené řádky PDF)
                        markers.py  jak vypadá článek, odstavec, písmeno, příloha, podpis
                        structure.py řádky → bloky (název, část, článek, odstavec, tabulka)
                        folder.py   složka + prehled.json → dokumenty; přílohy; přepis v Markdownu má přednost
  → chatbot/index/      úseky + embeddingy → soubory v data/index/; výpis změn; kontrola osobních údajů
  → chatbot/search/     hledání podle slov (BM25) a podle významu, sloučení pořadí (v paměti)
  → chatbot/answer/     zadání pro model, ověření citací, „nenašel jsem“, měkký pád bez modelu
  → chatbot/api/        FastAPI: /api/ask, /api/feedback, /api/doc/{id}, /api/status, heslo, omezovače
  → public/             stránka s chatem (historie a poznámky v prohlížeči, zdroje, znění dokumentu)
chatbot/guard/          filtr dotazu (před hledáním) a kontrola odpovědi (po modelu) – docs/ochrana.md
chatbot/llm/            jediné místo, kde se volá model (embeddingy i odpověď)
chatbot/feedback/       zpětná vazba (palce): soubor JSONL, jinak jen log
chatbot/config.py       Settings (prostředí, .env) a Instance (chatbot.toml)
chatbot/web.py          z chatbot.toml vyrobí public/nastaveni.js, vzhled.css, logo.svg, otazky.json
chatbot/precheck.py     kontrola před zveřejněním
chatbot/evalcheck.py    zkušební otázky a sada útoků
tasks.py                příkazy: sbirka, build, serve, check, kontrola
```

## Dvě cesty kódem

- **Sestavení** (`tasks.py sbirka`, `build`; jen na počítači s dokumenty): `ingest → index (+ llm pro embeddingy) → web`. Potřebuje knihovny z `requirements-dev.txt` (pypdf, python-docx, beautifulsoup4).
- **Běh** (server nebo hosting): `api → guard → search → answer → llm → guard`. Čte jen hotový index. Balík `chatbot.ingest` ani knihovny pro čtení dokumentů se při běhu nenačítají (hlídá test) – na serveru nemusí být nainstalované a běžící chatbot se k originálům dokumentů nedostane.

## Pravidla

- Šipky vedou jen dolů: `api` nezná čtení dokumentů, `ingest` nezná model, `search` nečte nic než index.
- Model se volá jen přes `chatbot/llm/` – nikde jinde žádný klíč, adresa ani knihovna dodavatele.
- `ingest/extract.py` a `structure.py` jsou čisté funkce (soubor dovnitř, bloky ven). Pravidla, jak se pozná článek nebo podpis, jsou na jednom místě: `markers.py`.
- Index je odvozený: jde kdykoli smazat a sestavit znovu z dokumentů. Nic, co nejde obnovit, se do něj neukládá.
- O vydání odpovědi rozhoduje server, ne model: bez platných citací nalezených úseků a bez prošlé kontroly obsahu se text modelu uživateli neukáže.
- Stránka vkládá vše ze serveru jako prostý text; žádné inline skripty ani styly (hlavička Content-Security-Policy).
- Historie dotazů a poznámky uživatele žijí jen v jeho prohlížeči (localStorage) – server o nich neví.
- Žádná databáze. Zpětná vazba jde do souboru přes rozhraní `FeedbackStore`; kde se na disk psát nedá, jen do logu.
- Tajemství jsou jen v `.env` nebo v proměnných hostingu. `chatbot.toml` a vše v `public/` je veřejné.

## Index (data/index/)

| Soubor | Obsah |
|---|---|
| `manifest.json` | kdy a čím byl index sestaven, počty, rozměr embeddingů |
| `documents.json` | dokumenty: označení, údaje, bloky textu s kotvami (pro zobrazení znění) |
| `chunks.json` | úseky pro hledání: text, cesta (dokument › článek › odstavec), kotva |
| `embeddings.bin` | embeddingy úseků ve stejném pořadí jako `chunks.json` (float16) |

Úsek = odstavec i se svými písmeny, tabulka nebo odstavec volného textu. Číslo dokumentu je otisk cesty souboru – nezmění se, dokud se soubor nepřejmenuje, takže odkazy na odstavce vydrží nové sestavení.

## Režimy běhu

| Stav | Chování |
|---|---|
| ostrý index + klíč | hledání slovy i významem, odpověď modelu se zdroji |
| ostrý index + klíč, model neodpovídá (výpadek, kredit) | hledání podle slov, nalezená místa a věta, že model není dostupný |
| ostrý index, bez klíče | zkušební režim: hledání jen podle slov, odpověď se nesestavuje |
| zkušební index (`build --fake`), bez klíče | zkušební režim s náhradními embeddingy |
| index sestavený jiným modelem, než je nastaven | `/api/status` hlásí chybu, `/api/ask` vrací 503 |
| index chybí nebo si neodpovídají soubory | totéž |
