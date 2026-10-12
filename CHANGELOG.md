# Změny

## 0.2.0 – 12. 10. 2026

- Repo je nezávislé na hostingu. Kde chatbot poběží, si každý řeší sám: `docs/nasazeni.md` říká, co k běhu potřebuje, co se nasazuje a na co si dát pozor. Soubory a postup pro jeden konkrétní hosting v repu nejsou. Aplikace je `chatbot.api.app:app` a stránku z `public/` podává vždy sama.
- **Změna chování – adresa tazatele.** Hlavičce `X-Forwarded-For` se už nevěří, dokud to provozovatel nezapne (`TRUSTED_PROXIES`, výchozí 0); limity se řídí adresou spojení. Dřív se bral začátek hlavičky vždy, a kde ji proxy nepřepisovala, šel limit dotazů i pauza po špatných heslech obejít podvrženou hlavičkou. Se zapnutým nastavením se adresa bere od konce hlavičky – tu část píše proxy, ne klient – a případný port se z ní odstraní. `tasks.py serve` zároveň vypíná přepisování adresy spojení v uvicornu, takže o důvěře rozhoduje jediné nastavení. **Kdo má před chatbotem proxy nebo hosting s vlastní proxy, musí nastavit `TRUSTED_PROXIES=1`**, jinak se o limity dělí všichni návštěvníci. Návod má zkoušku, která obojí ověří.
- `tasks.py serve` běží i pod účtem, který do složky aplikace nesmí zapisovat (soubory stránky vezme tak, jak jsou).
- Návod říká, co je potřeba zařídit, když aplikaci nespouští `tasks.py serve`: soubor `.env` se pak nečte, přístupový log serveru zapisuje adresy a server umí adresu spojení přepisovat sám.

## 0.1.1 – 11. 10. 2026

- Volitelný denní limit placených dotazů z jedné adresy (`DAILY_ASKS_PER_ADDRESS`, výchozí vypnuto) – pro veřejnou ukázku s malým rozpočtem. Dotaz, který zastaví filtr, se nepočítá. Kde instance po chvíli bez provozu končí, počítadlo se vynuluje; tvrdou pojistkou zůstává strop u poskytovatele modelu.
- Návod říká, jak nastavit tvrdý měsíční strop útraty u OpenAI: na projekt, ne na klíč.

## 0.1.0 – 11. 10. 2026

První verze.

- Stažení platných vyhlášek a nařízení obce ze Sbírky právních předpisů (`python tasks.py sbirka`), včetně příloh, odkazů na originál a upozornění na novelizace. Hledání obce podle názvu (`sbirka --hledej`).
- Chyba při stahování nesmí vypadat jako zrušení předpisu: stranou se odkládá jen předpis, který seznam ve Sbírce výslovně vede jako zrušený nebo vyřazený. Co v seznamu chybí nebo se nenačetlo, zůstává ve složce i v přehledu a hlásí se k ruční kontrole – stejně jako souběžně platné předpisy stejného názvu a předpisy s budoucí účinností. Předpis, který člověk sám odložil do složky s podtržítkem, se znovu nestahuje.
- Upozornění u zdroje, když předpis změnila novela, když je zrušen k budoucímu datu (ode dne zrušení „už neplatí“) nebo když ještě nenabyl účinnosti.
- Čtení dokumentů ze složky: PDF, Word (.docx, včetně automatického číslování odstavců), Markdown, text, HTML. Členění na články nebo paragrafy, odstavce a písmena; podpisy a doložky o vyvěšení se vynechávají; sken bez textu se ohlásí jménem souboru.
- Mapy a výkresy v PDF se nečtou: strana jen s čísly a značkami (popisky parcel, kóty) by jako text mátla. Z přílohy zůstane nadpis a sestavení na ni upozorní.
- Čtení je vyzkoušené na předpisech dvou měst (PDF i Word). Co neumí: tabulky v PDF čte jako souvislý text; nadpis článku pozná podle značky („Čl. 3“, „§ 3“), jinak zapsané členění ne.
- Index v souborech (bez databáze), hledání podle slov i významu.
- Odpověď jen s citacemi ověřenými serverem; zobrazení celého znění se zvýrazněným odstavcem.
- Ochrana proti obcházení zadání a zneužití, sada 58 pokusů o útok (17 z nich míří na model: osobní rada, hodnocení, věta přidaná na přání nad běžnými vyhláškami). Dotazy se přijímají jen ve formátu JSON, takže je nejde posílat formulářem z cizí stránky.
- Kontrola odpovědi posuzuje i jednotlivé věty: věta za poslední značkou zdroje nebo bez jediného slova z citovaných odstavců se nevydá, zbytek odpovědi ano. Co kontrola neumí (názor složený ze slov předpisu), popisuje `docs/ochrana.md`.
- Od názorů chatbot není: na „Co si myslíš o…“ odpoví pevnou větou, že je vyhledávač a rádce k předpisům (model se nevolá, do pauzy za nevhodné dotazy se to nepočítá). Žádost o osobní rozhodnutí („mám dát výpověď?“) označí model a větu napíše server.
- U odpovědi z předpisu, který má mapu nebo jinou grafiku, chatbot připíše, že ji nevidí a že je potřeba podívat se do originálu.
- Levné modely, které „uvažují“ (gpt-5-nano, gpt-6-luna): nastavení `REASONING_EFFORT`. Bez něj jsou pomalé a část odpovědí přijde prázdná.
- Když model není dostupný, chatbot ukáže nalezená místa místo chyby.
- Nastavení v `chatbot.toml` (název, texty, barva, návrhy otázek); volitelné společné heslo.
- Kontrola před zveřejněním (`python tasks.py kontrola`), včetně klíče omylem vyplněného do vzoru nastavení. Výpis ze sestavení říká, kde v dokumentu je nález osobních údajů (místo, ne hodnotu).
- Zkušební otázky a sada útoků jdou pustit i bez klíče k modelu (měří se jen hledání a filtr).
- Postup pro agenta (`AGENTS.md`) a návod na nasazení (`docs/nasazeni.md`).
