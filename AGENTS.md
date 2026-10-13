# Postup pro agenta

Tento soubor čte programovací agent (Claude Code, Codex, Cursor a další). Člověk, který tě spustil, chce chatbota nad svými dokumenty – nejčastěji nad vyhláškami a nařízeními své obce. Proveď ho kroky níže. Mluv česky, srozumitelně a bez žargonu: nemusí to být programátor.

## Pravidla, která platí po celou dobu

1. **Soubor `.env` nečti, nevypisuj a neposílej nikam.** Klíč k modelu a heslo do něj zapisuje člověk sám. Jestli je klíč vyplněný, poznáš jen z toho, že `python tasks.py build` nespadne na hlášce „Chybí LLM_API_KEY“. Když ti člověk pošle klíč do chatu, řekni mu, ať ho tam nedává, a ať si vytvoří nový.
2. **Před každým krokem ven se zeptej a počkej na výslovné ano:** založení účtu nebo projektu na hostingu, nasazení, nahrání na GitHub, cokoli, co něco zveřejní nebo stojí peníze. Souhlas s jedním krokem neplatí pro další.
3. **Do dokumentů nic nedomýšlej.** Když přepisuješ sken do Markdownu, přepisuj doslova. Co nepřečteš jistě, označ `[nečitelné]` a řekni to. Originály ve složce `dokumenty/` nemaž ani neupravuj.
4. **Nemaž.** Co má zmizet, přesuň do složky začínající podtržítkem (například `dokumenty/_stranou/`) – takové složky chatbot nečte.
5. **Neříkej „hotovo“, dokud jsi to neviděl běžet:** testy prošly, stránka se načetla, zkušební otázky vyšly.
6. **Kód měň jen tehdy, když o to člověk stojí.** Na většinu úprav stačí `chatbot.toml`.

## Krok 0 – co zjistit od člověka

Zeptej se najednou, jen na to, co nevíš:

- Čí chatbot to bude (název obce nebo organizace)?
- Z čeho má odpovídat: jen z vyhlášek a nařízení ze Sbírky právních předpisů, nebo i z vlastních dokumentů?
- Kdo ho bude používat: veřejnost, nebo jen lidé s heslem?
- Kam se má člověk obrátit, když chatbot odpověď nezná (web, e-mail nebo telefon), a má organizace svou barvu? Obojí půjde do nastavení v kroku 3.

## Krok 1 – prostředí

```bash
python --version                                                  # potřeba 3.12 nebo novější
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # Mac a Linux: .venv/bin/python -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q                             # musí projít celé
```

- **Všechny další příkazy pouštěj Pythonem z `.venv`** (`.venv\Scripts\python.exe tasks.py …`, na Macu a Linuxu `.venv/bin/python tasks.py …`). Kde návod píše jen `python`, myslí tím tenhle. Na „aktivaci“ prostředí se nespoléhej: mezi jednotlivými příkazy agenta se neudrží a balíčky by se nainstalovaly jinam.
- **Krátká cesta.** Složku s repem měj blízko kořene disku (například `C:\chatbot`). Ve složce zanořené hluboko selže na Windows už založení prostředí nebo testy (cesty delší než 260 znaků).
- **Synchronizovaný disk** (OneDrive, Google Disk): virtuální prostředí založ mimo něj – tisíce drobných souborů synchronizaci zahltí.

## Krok 2 – dokumenty

**Předpisy obce ze Sbírky právních předpisů.** Každá obec má na `https://sbirkapp.gov.cz/vydavatel/<ID datové schránky>` seznam svých předpisů. ID (7 znaků) zjistíš podle názvu obce:

```bash
python tasks.py sbirka --hledej "název obce"     # vypíše ID, kraj a IČO; obcí stejného jména bývá víc
python tasks.py sbirka <ID>
```

Které ID je správné, potvrď s člověkem podle kraje a IČO. První řádek výpisu ze stažení pak říká, čí předpisy to jsou.

Stáhnou se předpisy, které dnes platí, do `dokumenty/sbirka/`, a zapíše se `dokumenty/prehled.json` (názvy, čísla, účinnost, odkaz na originál, novelizace). ID zapiš do `chatbot.toml`, oddíl `[sbirka]` – příště stačí `python tasks.py sbirka`.

Řádky „K ruční kontrole“ ve výpisu člověku přečti a vyřeš s ním. Nejčastější:

- **Více platných předpisů se stejným názvem.** Sbírka někdy vede jako platný i předpis, který nový předpis nahradil. Chatbot by pak odpovídal z obou (třeba dvěma různými sazbami). Co s tím, rozhodne člověk z obce – neplatný předpis přesuň do `dokumenty/sbirka/_neplatne/`. Další stažení ho tam nechá a ve výpisu to připomene.
- **Předpis účinný až od budoucího data.** Zůstává; chatbot u něj sám upozorní, že ještě neplatí.
- **Soubor se nestáhl nebo ho neumím číst** (starý Word `.doc`, RTF). Musí ho dodat člověk jako PDF nebo `.docx` – a uložit pod názvem, který výpis uvádí (`dokumenty/sbirka/<název>.docx`). Pod jiným názvem by ho další stažení nepoznalo.
- **Soubory, které seznam ve Sbírce nezná.** Stažení je nechává být: seznam mohl přijít neúplný (výpadek). Zopakuj stažení. Chybí-li předpis v seznamu i pak a člověk potvrdí, že neplatí, přesuň soubor do `dokumenty/sbirka/_neplatne/`. Samo stažení tam odkládá jen předpisy, které Sbírka výslovně vede jako zrušené.

**Vlastní dokumenty.** Soubory PDF, DOCX, Markdown, text nebo HTML patří do `dokumenty/` (klidně do podsložek). Jak doplnit název, číslo a účinnost, je v `dokumenty/README.md`.

## Krok 3 – nastavení

Uprav `chatbot.toml`: `nazev`, `organizace`, `popis` (z čeho chatbot odpovídá), `odkaz` (kde jsou předpisy zveřejněné – u Sbírky adresa stránky obce), `kontakt`, `nenalezeno` (co chatbot řekne, když odpověď v dokumentech není: z čeho odpovídá a kam jít jinam – lidé se „chatbota města“ ptají i na starostu a úřední hodiny), `barva`. Návrhy otázek (`[otazky] navrhy`) doplníš v kroku 6, až uvidíš, co v dokumentech je.

## Krok 4 – klíč

Řekni člověku, ať zkopíruje `env.vzor.txt` jako `.env` a vloží klíč za `LLM_API_KEY=`. Připomeň mu strop útraty u poskytovatele modelu: na jednotlivý klíč nastavit nejde, na projekt ano (u OpenAI vlastní projekt pro chatbota a v něm *Limits → Spend → Enforce a hard limit*, podrobně `docs/nasazeni.md`). Ty soubor `.env` neotvírej.

Dvě věci, na kterých se to kazí: klíč patří do kopie, ne do `env.vzor.txt` (ten je v repu – `python tasks.py kontrola` by vyplněný klíč ohlásila jako chybu). A Průzkumník Windows skrývá přípony, takže soubor se může ve skutečnosti jmenovat `.env.txt` – chatbot pak klíč nenajde a sestavení dál hlásí „Chybí LLM_API_KEY“.

Bez klíče jde pokračovat nanečisto: `python tasks.py build --fake` (zkušební index bez modelu: hledání funguje, odpověď se nesestavuje).

## Krok 5 – sestavení

```bash
python tasks.py build
```

Výpis projdi s člověkem celý:

- **Nepřečtené soubory.** Sken bez textu přepiš do Markdownu: otevři PDF, přepiš ho doslova a ulož vedle originálu se stejným názvem a příponou `.md` (vzor zápisu je v `dokumenty/README.md`). Přepis má při čtení přednost před originálem. Starý formát Wordu (`.doc`) musí člověk uložit jako `.docx`.
- **Jak se dokumenty přečetly.** „Text není členěn na články“ u předpisu znamená, že se nadpisy článků nepoznaly – podívej se do souboru, jak jsou zapsané. Když nepomůže nic jiného, udělej přepis do Markdownu.
- **Osobní údaje.** Výpis říká, kolik nálezů v dokumentu je a kde (článek, příloha) – hodnoty nevypisuje. Podívej se na to místo: často je to služební kontakt úřadu nebo zbytek podpisu. U každého nálezu se zeptej, jestli dokument smí ven. Když ne, přesuň ho do `dokumenty/_stranou/` (stažení ze Sbírky ho pak znovu nestáhne) nebo nastav v `chatbot.toml` `[osobni_udaje] rezim = "vynechat"`.
- **Dokumenty s upozorněním** (novelizace, předpis zrušený k budoucímu datu, předpis, který ještě nenabyl účinnosti) jen přečti – chatbot upozornění ukáže u zdroje sám.
- **Tabulky v PDF** se čtou jako souvislý text, sloupce se slijí. Když na tabulce záleží (sazebník, zóny), přepiš ji do Markdownu stejně jako sken.
- **Mapy a výkresy.** Z mapy v PDF jdou vytáhnout jen popisky (čísla parcel, kóty) – jako text by mátly, proto se strana „jen s čísly a značkami“ nečte a z přílohy zůstane nadpis. Řekni člověku, že vymezení míst z mapy chatbot nezná; u odpovědi z takového předpisu na to chatbot sám upozorní („má i mapu nebo jinou grafiku… zkontrolujte si ji v originálu“). Je-li na takové straně ve skutečnosti seznam (třeba čísla parcel pod sebou), přepiš ho do Markdownu.

Namátkou ověř dva tři předpisy: `python tasks.py serve`, otevři stránku, zeptej se a porovnej zobrazené znění s originálem (čísla článků a odstavců, začátek a konec textu).

## Krok 6 – zkušební otázky a návrhy na stránku

Sepiš aspoň 60 otázek do `eval/otazky.jsonl` (formát v `eval/README.md`): polovinu tak, jak se lidé opravdu ptají (běžnými slovy, heslovitě, bez diakritiky), k tomu otázky podle textu předpisů, pár s odkazem na číslo předpisu a článek a aspoň pět **mimo dokumenty**, na které chatbot odpovědět nemá. U otázek s jednoznačným údajem doplň `fakta` (částka, lhůta). Úryvek `contains` opiš z textu dokumentu doslova. Otázky dej člověku zkontrolovat – on ví, na co se lidé ptají. Sada psaná jen slovy předpisu vyjde skoro vždy a neřekne nic.

```bash
python tasks.py check             # prahy: nalezeno 90 %, správně zodpovězeno 80 %, mimo dokumenty bez odpovědi 90 %
python tasks.py check --hledani   # kde se správný odstavec ztrácí: podle slov, podle významu, po sloučení
python tasks.py check --utoky     # pokusy o obcházení; vypsané odpovědi přečti
```

U otázek, které nevyšly, výpis říká proč. „Zahodila kontrola odpovědi“ znamená, že model odpověděl a server odpověď nevydal – přečti si kód důvodu v `docs/ochrana.md`, než začneš cokoli povolovat. Výsledek se mezi běhy liší o jednu až tři otázky; nelaď podle jednoho běhu.

Sada útoků má ve vrstvě `model` dotazy na běžné vyhlášky (poplatek ze psů, noční klid, odpad). Když je chatbot nad jinými dokumenty, přepiš v těch řádcích názvy na dokumenty, které v něm jsou – útok na dokument, který chatbot nemá, nic neověří. Vypsané odpovědi přečti celé, až do konce, a ukaž člověku ty, ve kterých chatbot hodnotí, radí nebo píše něco, co v dokumentech není.

**Bez klíče** (po `build --fake`) příkazy běží taky, jen měří míň: `check` vyhodnotí hledání podle slov (práh 90 % ber jako orientační, citace a správnost odpovědí ověříš až s klíčem), `check --utoky` jen vstupní filtr. Výpis to sám řekne.

Do `chatbot.toml`, `[otazky] navrhy`, vyber 6 až 20 otázek ze sady, které prošly, a spusť znovu `python tasks.py build`. Nikdy tam nedávej otázku, na kterou chatbot neodpoví – je to první věc, kterou návštěvník zkusí.

## Krok 7 – kontrola před zveřejněním

```bash
python tasks.py kontrola
```

S řádkem `CHYBA` se ven nejde. Řádky `POZOR` projdi s člověkem jeden po druhém.

## Krok 8 – zveřejnění

Kde chatbot poběží, rozhoduje člověk – repo žádný hosting nepředepisuje. Zeptej se ho: vlastní server, hosting pro webové aplikace v Pythonu, nebo jen vnitřní síť? Co chatbot k běhu potřebuje a na co si dát pozor, je v `docs/nasazeni.md`: projdi ho s ním a dál se drž dokumentace toho, co si vybral. Hosting sám nevybírej ani nezakládej.

Před každým krokem, který něco založí nebo zveřejní, se zeptej. Na GitHub nahrávej jen příkazy `git` z návodu, nikdy přetažením souborů do prohlížeče: tam neplatí `.gitignore` a soubor `.env` s klíčem by šel ven. Klíč a případné heslo zadává člověk sám (soubor `.env` na serveru, proměnné prostředí služby nebo hostingu); ty je nevidíš.

Stojí-li před chatbotem proxy nebo hosting, nastav s člověkem `TRUSTED_PROXIES` podle oddílu „Adresa tazatele a proxy“ v `docs/nasazeni.md` a po nasazení projdi obě zkoušky, které tam jsou: že limit nejde obejít podvrženou hlavičkou a že se o něj nedělí všichni návštěvníci (druhou musí udělat člověk z jiného připojení). Spouští-li chatbota něco jiného než `tasks.py serve`, platí i oddíl „Když chatbota nespouští `tasks.py serve`“ – hlavně že se nečte `.env`.

Po nasazení ověř živou adresu:

```bash
python tasks.py check --url https://<adresa>
```

a otevři ji v prohlížeči, i na šířku telefonu. Příkaz `check --url` funguje jen u chatbota bez hesla (neumí se přihlásit) – s heslem ověř živou adresu jen v prohlížeči. Sadu útoků (`--utoky`) proti nasazené adrese nepouštěj: po pár pokusech narazí na pauzu pro nevhodné dotazy.

Chce-li člověk chatbota jen ve vlastní síti, je to odstavec „U sebe na síti“ v `README.md`.

## Na co si dát pozor

- **Git Bash na Windows** mění v některých zápisech zpětná lomítka. Soubory zapisuj nástrojem pro zápis souborů, ne přes `echo` a heredoc.
- **Index se nasazuje s kódem.** Po `build` musí jít do nasazení i složka `data/index/` a soubory `public/nastaveni.js`, `public/vzhled.css`, `public/logo.svg`, `public/otazky.json`.
- **Změna modelu pro embeddingy** = sestavit index znovu; aplikace to pozná a řekne.
- **Zkušební index** (`build --fake`) se nenasazuje. Kontrola před zveřejněním ho odmítne.

## Kde co je

| Chceš změnit | Soubor |
|---|---|
| název, texty, barvu, návrhy otázek | `chatbot.toml` |
| limity, model, heslo | `.env` (vzor `env.vzor.txt`) – upravuje člověk |
| jak se poznává článek, odstavec, podpis | `chatbot/ingest/markers.py` |
| čtení souborů (PDF, Word, Markdown) | `chatbot/ingest/extract.py` |
| zadání pro model a pro kontrolora odpovědí | `chatbot/answer/prompt.py` |
| co server udělá s odpovědí (čísla, opora, ověření vět) | `chatbot/guard/answer.py`, `numbers.py`, `verify.py`, `chatbot/answer/service.py` |
| vzory pokusů o obcházení | `chatbot/guard/patterns.py` + řádek do `eval/utoky.jsonl` |
| vzhled stránky | `public/styles.css` (barvy jen přes `chatbot.toml`) |

Po každé změně kódu: `python -m pytest -q`.
