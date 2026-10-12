# Nasazení

Kde chatbot poběží, si musíte vyřešit sami. Repo žádný hosting nepředepisuje ani nedoporučuje: chatbot je běžná webová aplikace v Pythonu a běží na vlastním serveru stejně jako na hostingu, který takové aplikace umí. Tady je, co k běhu potřebuje, co se nasazuje a na co si dát pozor.

## Než začnete

1. `python tasks.py build` proběhl **bez `--fake`** – index je ostrý.
2. `python tasks.py check` vyšel (správný odstavec nalezen aspoň u 90 % zkušebních otázek).
3. `python tasks.py kontrola` neukázal žádný řádek `CHYBA` a řádky `POZOR` jste prošli.
4. U poskytovatele modelu máte nastavený **strop útraty**. Na jednotlivý klíč ho nastavit nejde, na projekt ano. U OpenAI: založte pro chatbota vlastní projekt, klíč vytvořte v něm a v nastavení projektu zvolte *Limits → Spend → Edit spend limit*, zadejte měsíční částku a zapněte *Enforce a hard limit*. Po vyčerpání začne model vracet chybu a chatbot dál ukazuje nalezená místa, jen nesestavuje odpovědi. Strop nezabírá okamžitě, útrata ho může o něco přesáhnout. Druhá možnost je předplacený kredit bez automatického dobíjení.

## Co chatbot k běhu potřebuje

- **Python 3.12 nebo novější**, knihovny z `requirements.txt` a server pro aplikace ASGI (například uvicorn). Spouští se příkazem `python tasks.py serve`. Jiný server nebo hosting spouští přímo aplikaci `chatbot.api.app:app` – pak čtěte i oddíl „Když chatbota nespouští `tasks.py serve`“.
- **Soubory:** balík `chatbot/`, stránka `public/` (včetně souborů `nastaveni.js`, `vzhled.css`, `logo.svg` a `otazky.json`, které vznikly při sestavení), index `data/index/`, `chatbot.toml`, `requirements.txt` a `tasks.py`. Originály dokumentů (`dokumenty/`) chatbot k běhu nepotřebuje a nikomu je nepodává. Při nasazení z repa ale jdou na hosting spolu s ním – u neveřejných dokumentů s tím počítejte.
- **Klíč a heslo mimo kód:** v souboru `.env` vedle `tasks.py`, nebo v proměnných prostředí služby či hostingu. Soubor `.env` čte jen `tasks.py` a do repa nepatří nikdy.
- **Spojení ven** na adresu poskytovatele modelu (`LLM_BASE_URL`). S lokálním modelem dotazy vaši síť neopustí.
- **https.** Chatbot sám mluví jen http; šifrování zajišťuje proxy nebo hosting před ním.
- **Zápis na disk nepotřebuje.** `tasks.py serve` při startu obnovuje soubory stránky v `public/`; když do složky zapisovat nesmí, řekne to a poběží s těmi, které v ní jsou. Zpětná vazba se zapisuje do `data/feedback.jsonl`; kde se psát nedá, jde jen řádek do logu, bez textů.

Index se načítá při startu. Po výměně souborů v `data/index/` a `public/` proto službu restartujte nebo nasaďte znovu.

## Kde může běžet

- **Vlastní server** – fyzický nebo virtuální, na internetu nebo jen ve vnitřní síti. Postup je níže.
- **Hosting pro webové aplikace v Pythonu** – cokoli, co umí spustit aplikaci ASGI (FastAPI) a nastavit jí proměnné prostředí. Postup se u každého liší; držte se jeho dokumentace a seznamu „Co chatbot k běhu potřebuje“.

Než se rozhodnete, ujasněte si:

- **Kdo to bude provozovat.** Někdo musí po změně předpisů sestavit index znovu a nasadit ho, hlídat útratu a číst zpětnou vazbu.
- **Podmínky a cenu.** U bezplatných plánů si přečtěte, pro koho jsou – některé nedovolují použití v organizaci.
- **Kudy jdou dotazy.** Text dotazu jde z prohlížeče na váš server nebo hosting a odtud k poskytovateli modelu. Kde ty servery stojí, kdo je spravuje a co si zapisují do logů, patří do rozhodnutí.
- **Kolik instancí poběží.** Limity dotazů se počítají v paměti každé instance zvlášť (viz „Co nastavit pro veřejný provoz“).

## Vlastní server (i jen ve vnitřní síti)

```bash
pip install -r requirements.txt uvicorn
python tasks.py serve --port 8770
```

- Na server stačí zkopírovat soubory ze seznamu výše. Sestavovat index (a mít knihovny z `requirements-dev.txt`) můžete na svém počítači.
- Před službu dejte běžnou proxy s https (nginx, Caddy, IIS) a nastavte `TRUSTED_PROXIES=1` – viz „Adresa tazatele a proxy“.
- **Kdo se k chatbotovi dostane.** Bez `--host` naslouchá chatbot jen na adrese `127.0.0.1`, tedy jen proxy na stejném stroji – tak to nechte. `--host 0.0.0.0` použijte, jen když proxy běží na jiném stroji nebo chatbot běží bez proxy ve vnitřní síti; port pak na firewallu otevřete jen těm, kdo se k němu dostat mají.
- Klíč a heslo patří do souboru `.env` vedle `tasks.py` nebo do proměnných prostředí služby.
- **Lokální model:** nastavte `LLM_BASE_URL` na adresu svého serveru modelů (rozhraní kompatibilní s OpenAI), `CHAT_MODEL` a `EMBED_MODEL` na názvy modelů, `EMBED_DIM=0` (rozměr určí model) a do `LLM_API_KEY` libovolný text – lokální servery klíč většinou nevyžadují, ale bez něj chatbot model nevolá. Po změně modelu pro embeddingy sestavte index znovu. Dotazy pak neopustí vaši síť.
- Menší modely hůř drží předepsaný tvar odpovědi. Poznáte to na zkušebních otázkách: `python tasks.py check`.

## Když chatbota nespouští `tasks.py serve`

Hosting nebo vlastní příkaz (například `uvicorn chatbot.api.app:app`) spustí aplikaci přímo. Tři věci, které jinak zařídí `tasks.py serve`, pak musíte zařídit sami:

- **Soubor `.env` se nečte.** Klíč, heslo i ostatní nastavení zadejte jako proměnné prostředí. Jinak chatbot poběží bez hesla a bez modelu a nic vám neřekne. Ověřte na adrese `/api/status`: `"mode": "ostry"` a u chatbota s heslem `"password": true`.
- **Adresy v logu.** Chatbot sám adresy tazatelů ani texty dotazů nezapisuje, ale server ASGI mívá zapnutý přístupový log s adresami. Vypněte ho (u uvicornu `--no-access-log`). Proxy a hosting si logy vedou podle svého nastavení.
- **Adresa spojení.** Server ASGI umí adresu spojení přepsat podle hlavičky `X-Forwarded-For` sám. Vypněte to (u uvicornu `--no-proxy-headers`) a použijte `TRUSTED_PROXIES`. Nikdy nenastavujte `FORWARDED_ALLOW_IPS=*`: server by pak věřil adrese, kterou si napsal klient.

## Adresa tazatele a proxy

Limit dotazů za minutu, denní limit i pauza po špatných heslech se počítají na adresu tazatele. Chatbot bere adresu spojení. Hlavičce `X-Forwarded-For` sám nevěří – klient si do ní napíše, co chce, a limity by tak obešel.

Stojí-li před chatbotem proxy nebo hosting, vidí chatbot jako tazatele je a o limity se dělí všichni návštěvníci. Kolik proxy před chatbotem stojí, mu proto musíte říct:

| Kde chatbot běží | Nastavení |
|---|---|
| bez proxy (vnitřní síť, zkoušení) | nic (`TRUSTED_PROXIES=0`) |
| za jednou proxy – na stejném stroji i jinde – nebo na hostingu, který má před aplikací vlastní proxy | `TRUSTED_PROXIES=1` |
| za více proxy za sebou (například CDN a za ní vaše proxy) | `TRUSTED_PROXIES` = jejich počet |

S `TRUSTED_PROXIES=1` bere chatbot z hlavičky poslední adresu – tu, kterou připsala proxy. Co stojí před ní, napsal klient a nepočítá se. Aby to platilo, musí sedět tři věci:

- **Proxy adresu do hlavičky sama zapisuje** – připisuje ji na konec, nebo hlavičku přepisuje (v nginx `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`). Proxy, která hlavičku od klienta jen přeposílá, nestačí.
- **K chatbotovi se nedá dostat jinudy než přes proxy.** Kdo by se připojil přímo na jeho port, napsal by si hlavičku celou sám. U proxy na stejném stroji to zajistí výchozí `127.0.0.1`, jinak firewall.
- **Číslo není vyšší než počet proxy.** S vyšším číslem by chatbot věřil adrese od klienta.

Připisuje-li proxy adresu i s portem, chatbot si port odmyslí. Co v hlavičce není adresa, nebere a použije adresu spojení.

### Ověření po nasazení

Zkoušejte z jiného počítače, než na kterém chatbot běží, a přes veřejnou adresu. Příkazy jsou pro bash (Linux, Mac, na Windows Git Bash).

1. **Limit nejde obejít podvrženou hlavičkou.** Dvanáct prázdných dotazů, každý s jinou vymyšlenou adresou:

   ```bash
   for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code} " -X POST https://<adresa>/api/ask -H "Content-Type: application/json" -H "X-Forwarded-For: 203.0.113.$i" -d '{}'; done
   ```

   Správně přijde desetkrát `400` a potom `429` (počet odpovědí `400` je `RATE_LIMIT_PER_MIN`; s vyšším limitem pošlete dotazů víc). Rozhoduje, že `429` přijde. Samé `400` znamená, že chatbot věří adrese od klienta: `TRUSTED_PROXIES` je vyšší než počet proxy, proxy hlavičku jen přeposílá, nebo adresu přepisuje server ASGI. Stejně to dopadne, když běží víc instancí a dotazy se mezi ně rozdělí – pak zkoušku zopakujte s víc dotazy.

2. **O limit se nedělí všichni.** Hned po první zkoušce, dokud váš počítač dostává `429`, otevřete chatbota z jiného připojení (telefon na mobilních datech) a zeptejte se. Musí odpovědět. Když i tam přijde „Příliš mnoho dotazů“, vidí chatbot všechny návštěvníky pod adresou proxy: `TRUSTED_PROXIES` chybí nebo je nižší než počet proxy. V tom stavu jeden člověk dvanácti dotazy za minutu vypne chatbota všem.

3. **U chatbota s heslem** první zkouška vrací `401`. Totéž proto ověřte na přihlášení – sedm pokusů se špatným heslem:

   ```bash
   for i in $(seq 1 7); do curl -s -o /dev/null -w "%{http_code} " -X POST https://<adresa>/api/login -H "Content-Type: application/json" -H "X-Forwarded-For: 203.0.113.$i" -d '{"password":"zkouska"}'; done
   ```

   Správně přijde pětkrát `401` a potom `429`. Z vaší adresy se pak pět minut nepřihlásíte; z jiného připojení to jít musí.

Po zkouškách počkejte minutu, než pustíte `python tasks.py check --url` – jinak narazí na limit, který jste právě vyčerpali.

## Co nastavit pro veřejný provoz

| Proměnná | Výchozí | Kdy změnit |
|---|---|---|
| `RATE_LIMIT_PER_MIN` | 10 | dotazů z jedné adresy za minutu; zvyšte, když chatbota používá hodně lidí z jedné sítě (úřad, škola) |
| `MAX_ASKS_PER_HOUR` | 300 | placených dotazů za hodinu celkem; snižte, když chcete útratu brzdit víc |
| `DAILY_ASKS_PER_ADDRESS` | prázdné | placených dotazů za 24 hodin z jedné adresy (například `10` pro veřejnou ukázku); prázdné = bez omezení. Lidé za jednou adresou (úřad, škola) se o limit dělí |
| `TRUSTED_PROXIES` | 0 | počet proxy před chatbotem, viz „Adresa tazatele a proxy“ |
| `ACCESS_PASSWORD` | prázdné | společné heslo; prázdné = otevřeno všem |
| `CHAT_MODEL` | `gpt-4.1-mini` | jiný model pro odpovědi; levnější modely odpovídají hůř – změřte je zkušebními otázkami (`python tasks.py check`) |
| `REASONING_EFFORT` | prázdné | jen u modelů, které „uvažují“ (gpt-5…, gpt-6…): nejnižší míra je u gpt-5 `minimal`, u gpt-6 `none`. Bez ní jsou pomalé, dražší a část odpovědí přijde prázdná |
| `FEEDBACK_FILE` | `data/feedback.jsonl` | kam se zapisuje zpětná vazba; změňte, když se složka aplikace při novém nasazení přepisuje |

Limity běží v paměti jedné instance. Na serveru s jedním procesem platí přesně. Kde běží víc instancí (víc procesů, hosting, který aplikaci spouští podle provozu), počítá každá zvlášť, a když instance skončí, počítadla se vynulují – denní limit na adresu tam platí jen přibližně. Jsou to brzdy, ne záruka; záruka je strop u poskytovatele modelu.

Zpětná vazba (palce pod odpovědí) se zapisuje do souboru. Kde se na disk psát nedá, jde jen řádek do logu, bez textů. Disk, na který se psát dá, ale při restartu nebo novém nasazení se maže, chatbot nepozná – záznamy by se tam ztrácely. Soubor proto mějte na trvalém místě.

## Když kód a index ukládáte do repa

Řada hostingů nasazuje z repa na GitHubu a repo se hodí i jako záloha. Obsahuje-li chatbot jen vyhlášky a nařízení, může být veřejné; s jakýmkoli neveřejným dokumentem musí být soukromé – do repa jdou originály ze složky `dokumenty/` i jejich text v indexu. Na github.com si založte nové prázdné repo (bez README a licence) a ve složce projektu:

```bash
git init -b main
git config user.name "Jméno Příjmení"
git config user.email "<adresa>"     # bude u commitu vidět; GitHub nabízí neveřejnou adresu …@users.noreply.github.com
git check-ignore -v .env           # musí vypsat řádek ze souboru .gitignore – pak .env do repa nepůjde
git add .
git status                         # v seznamu NESMÍ být soubor .env
git commit -m "Chatbot: první verze"
git remote add origin https://github.com/<účet>/<repo>.git
git push -u origin main
```

Stáhli jste repo příkazem `git clone`? Pak místo `git init` a `git remote add` použijte `git remote set-url origin https://github.com/<účet>/<repo>.git`. Jméno a adresu nastavte i tak – bez nich první commit skončí chybou, a adresa z commitu je ve veřejném repu vidět (neveřejnou adresu najdete na GitHubu v Settings → Emails).

**Soubory nenahrávejte přetažením do prohlížeče.** Na webu GitHubu neplatí `.gitignore` – soubor `.env` s klíčem by šel do repa. Přes `git` do repa nepůjde `.env` ani složky začínající podtržítkem ve složce `dokumenty/` (odložené dokumenty); musí tam naopak být `data/index/` a vygenerované soubory v `public/`.

## Po nasazení

Otevřete adresu chatbota, zeptejte se a rozklikněte zdroj. Pak z počítače:

```bash
python tasks.py check --url https://<adresa>
```

- Se zapnutým heslem tenhle příkaz nefunguje (neumí se přihlásit) – ověřte chatbota v prohlížeči.
- Sadu útoků (`check --utoky`) pouštějte jen na svém počítači: proti nasazené adrese by po pár pokusech narazila na pauzu pro nevhodné dotazy.
- Ověřte adresu tazatele (oddíl „Adresa tazatele a proxy“, „Ověření po nasazení“).
- **Bezpečnostní hlavičky.** Chatbot je posílá sám u všeho, co podává – u odpovědí i u stránky. Podává-li stránku ze složky `public/` něco jiného (statický hosting, CDN), nastavte je tam: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy: camera=(), microphone=(), geolocation=()` a `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Adresy začínající `/api/` musí vést na chatbota.

## Aktualizace

Po `python tasks.py sbirka` a `build` nasaďte znovu: do nasazení musí jít nový obsah `data/index/` a `public/`. Z repa to znamená:

```bash
git add .
git status                         # zkontrolujte, co odchází
git commit -m "Aktualizace předpisů"
git push
```

Výpis ze stažení si přečtěte – řádky „K ruční kontrole“ říkají, co se nestáhlo a co zůstalo ve složce, i když to seznam ve Sbírce nezná. Tři věci, které je dobré vědět:

- Soubor, který ve složce už je, se znovu nestahuje. Opravenou verzi téhož předpisu získáte tak, že starý soubor přesunete **mimo složku `dokumenty/`**.
- Předpis, který nechcete (souběžně platný starší předpis, dokument s osobními údaji), přesuňte do složky začínající podtržítkem – `dokumenty/sbirka/_neplatne/` nebo `dokumenty/_stranou/`. Další stažení ho tam nechá a ve výpisu to připomene.
- Do `_neplatne/` se samy odkládají jen předpisy, které Sbírka výslovně vede jako zrušené. Co v seznamu úplně chybí, zůstává na místě.
