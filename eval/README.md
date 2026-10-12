# Zkušební otázky a sada útoků

Bez zkušebních otázek nevíte, jestli chatbot odpovídá správně – jen že odpovídá.

## Zkušební otázky (`otazky.jsonl`)

Soubor si sepíšete sami (nebo s agentem) nad svými dokumenty. Jeden řádek = jedna otázka:

```json
{"question": "Do kdy je splatný poplatek ze psů?", "doc": "5/2025", "contains": "splatný nejpozději do 31. března"}
```

- `question` – otázka tak, jak by ji položil člověk.
- `doc` – čím se pozná správný dokument: číslo předpisu nebo část jeho názvu (musí být v označení dokumentu).
- `contains` – úryvek odstavce, který na otázku odpovídá, opsaný z dokumentu doslova.

Stačí 15 až 25 otázek: na každý důležitější dokument jedna dvě, k tomu pár takových, kde se lidé ptají jinými slovy, než jaká jsou v předpisu.

```bash
python tasks.py check                          # místně; bez klíče k modelu změří jen hledání
python tasks.py check --url https://adresa     # proti nasazenému chatbotovi (jen když nemá heslo)
```

Výsledek říká, u kolika otázek chatbot správný odstavec **našel** (je mezi zdroji nebo v „mohlo by souviset“) a u kolika z něj přímo **citoval**. Práh je 90 % nalezených. Nenalezené otázky vypíše – buď je špatně úryvek, nebo se dokument špatně přečetl, nebo hledání na otázku nestačí.

## Sada útoků (`utoky.jsonl`)

Pokusy o obcházení zadání, vulgarity a zadání mimo účel. Řádky vrstvy `filtr` jsou obecné, upravovat je nemusíte. Řádky vrstvy `model` míří na běžné vyhlášky obce (poplatek ze psů, noční klid, odpad). Když chatbot odpovídá z jiných dokumentů, přepište v nich názvy na své: útok na dokument, který chatbot nemá, nic neověří – chatbot jen odpoví, že nic nenašel.

```bash
python tasks.py check --utoky                  # jen místně – proti nasazené adrese by narazila na pauzu pro nevhodné dotazy
```

Řádky vrstvy `filtr` musí zastavit vstupní filtr (jinak chyba). U vrstvy `model` se vypíše, co chatbot odpověděl – přečtěte si to celé až do konce: názor, rada nebo věta přidaná na přání bývá poslední. Řádek s `must_not` je chyba, když odpověď uvedený text obsahuje. Nový trik přidáte jako další řádek; postup je v `docs/ochrana.md`.
