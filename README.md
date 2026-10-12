# Městský chatbot

Chatbot, který odpovídá z předpisů vaší obce – z obecně závazných vyhlášek a nařízení – a u každé odpovědi ukáže, **kde přesně to v předpisu stojí**. Postavíte si ho sami: předpisy si stáhne ze Sbírky právních předpisů, vy dodáte klíč k jazykovému modelu a rozhodnete, kde poběží.

Funguje i nad jinými dokumenty (řády, směrnice, návody, smlouvy) a pro kohokoli, nejen pro obce. Na předpisy je ale vyladěný nejlépe: zná členění na články a odstavce a cituje podle něj.

## Co potřebujete

- **Počítač s Pythonem 3.12 nebo novějším.**
- **Programovacího agenta** – Claude Code, Codex nebo Cursor. Provede vás celým postupem a udělá většinu práce. Jde to i bez něj, pak vás čeká pár příkazů v terminálu (níže).
- **Klíč k jazykovému modelu** (API klíč). Výchozí nastavení počítá s OpenAI; jde použít kterýkoli model s rozhraním kompatibilním s OpenAI, i lokální.
- **Pro zveřejnění místo, kde chatbot poběží** – vlastní server, nebo hosting pro webové aplikace v Pythonu. To si musíte vyřešit sami; co chatbot k běhu potřebuje, je v [docs/nasazeni.md](docs/nasazeni.md).

Kdo to zvládne: informatik obce, nebo kdokoli, kdo už s agentem někdy pracoval. Počítejte s odpolednem, než chatbot poběží na vašem počítači, a s dalším dnem, než mu budete věřit natolik, abyste ho pustili ven.

## Jak na to s agentem

1. **Stáhněte si tohle repo** (zelené tlačítko *Code* → *Download ZIP*, nebo `git clone`) a rozbalte ho.
2. **Otevřete složku v agentovi** a napište mu:
   > Postav mi chatbota podle souboru AGENTS.md. Jsme obec … (doplňte název).
3. **Předpisy.** Agent najde vaši obec ve [Sbírce právních předpisů](https://sbirkapp.gov.cz) a stáhne její platné vyhlášky a nařízení. Chcete přidat vlastní dokumenty? Nahrajte je do složky `dokumenty/` (PDF, Word, Markdown, text).
4. **Klíč.** Zkopírujte soubor `env.vzor.txt` jako `.env` a za `LLM_API_KEY=` vložte svůj klíč. U poskytovatele modelu si nastavte strop útraty – stačí pár korun na vyzkoušení.
5. **Hotovo.** Agent sestaví databázi, pustí chatbota na vašem počítači, sepíše s vámi zkušební otázky, zkontroluje, co půjde ven, a pomůže vám chatbota nasadit tam, kde jste se rozhodli ho provozovat.

Klíč do chatu agentovi nepište. Patří jen do souboru `.env`; agent má v pravidlech, že ho nečte.

## Totéž ručně

```bash
python -m venv .venv
.venv\Scripts\activate            # Git Bash: source .venv/Scripts/activate · Mac a Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

python tasks.py sbirka --hledej "Horní Áíčkov"   # najde ID datové schránky obce podle názvu
python tasks.py sbirka abc1234    # stáhne platné předpisy obce do dokumenty/
python tasks.py build             # z dokumentů sestaví index (potřebuje klíč v .env)
python tasks.py serve             # chatbot běží na http://localhost:8770
python tasks.py check             # zkušební otázky z eval/otazky.jsonl
python tasks.py kontrola          # co zkontrolovat, než půjde ven
```

ID datové schránky je i na konci adresy stránky obce ve Sbírce právních předpisů (`sbirkapp.gov.cz/vydavatel/<ID>`). Po stažení si přečtěte řádky „K ruční kontrole“ – třeba když Sbírka vede jako platné dva předpisy téhož názvu. Bez klíče jde chatbota aspoň vyzkoušet: `python tasks.py build --fake` sestaví zkušební index a stránka pak ukazuje místa, která našlo hledání, bez sestavené odpovědi.

Název, texty na stránce, barvu a návrhy otázek nastavíte v souboru [chatbot.toml](chatbot.toml). Po změně spusťte znovu `python tasks.py build`.

## Co chatbot umí a co ne

| Umí | Neumí |
|---|---|
| odpovědět z vašich dokumentů a ukázat zdroj: předpis, článek, odstavec | vykládat právo nebo radit nad rámec toho, co v dokumentu stojí |
| otevřít celé znění se zvýrazněným odstavcem a odkazem na originál | zákony a předpisy, které jste mu nedali |
| říct „nenašel jsem“, když odpověď v dokumentech není | číst skeny bez textu, obrázky a mapy (sken přepíše agent, viz `dokumenty/README.md`) |
| upozornit, že předpis změnila novela, že bude zrušen nebo že ještě nenabyl účinnosti | zaručit bezchybnost – závazné je vždy znění předpisu |
| upozornit u odpovědi, že citovaný předpis má i mapu nebo jinou grafiku, kterou nevidí | říct, kde přesně něco platí, když je to jen v mapě |
| vynechat podpisy na konci předpisů | číst tabulky v PDF po buňkách – slije je do textu (pomůže přepis do Markdownu) |
| odmítnout pokusy o obcházení a vulgarity | nahradit úředníka |
| říct, že od názorů a osobních rad tu není („Co si myslíš o starostovi?“) | hodnotit lidi, úřad nebo politiku a rozhodovat za vás |

Odpověď, která se neopírá o nalezené odstavce, se uživateli vůbec neukáže – o vydání odpovědi rozhoduje server, ne model. Jak přesně, popisuje [docs/ochrana.md](docs/ochrana.md).

## Kolik to stojí

Platíte jen poskytovateli modelu, podle spotřeby. Sestavení databáze z desítek předpisů stojí haléře. Dotaz, který filtr odmítne, nestojí nic.

Změřeno 11. 10. 2026 na chatbotovi nad 23 vyhláškami a nařízeními jednoho města: jeden dotaz spotřeboval průměrně 3 350 vstupních a 100 výstupních tokenů (u dokumentů s dlouhými odstavci to bude víc). Podle ceníku OpenAI a kurzu 21,75 Kč za dolar to dělá:

| Model | 10 dotazů | 1 000 dotazů | Jak odpovídal na 25 zkušebních otázek |
|---|---|---|---|
| `gpt-4.1-mini` (výchozí) | 0,33 Kč | 33 Kč | odpověděl na 24 |
| `gpt-6-luna` s `REASONING_EFFORT=none` | 0,08 Kč | 8 Kč | odpověděl na 24 |
| `gpt-5-nano` s `REASONING_EFFORT=minimal` | 0,04 Kč | 4 Kč | odpověděl na 20, z toho jednou chybně |

Ceny jsou bez slevy, kterou poskytovatel dává za opakovaný začátek zadání; při častém provozu vyjdou o něco níž.

Nejlevnější model se nevyplatil: pětinu otázek nechal bez odpovědi, jednou odpověděl špatně a v sadě útoků jako jediný poslechl pokyn vložit do odpovědi urážku. Levnější model proto vždycky nejdřív změřte (`python tasks.py check` a `check --utoky`) a odpovědi si přečtěte. Modely, které „uvažují“ (gpt-5…, gpt-6…), potřebují nastavit `REASONING_EFFORT`: bez něj byl `gpt-5-nano` v jiné zkoušce sedmkrát pomalejší (10 sekund na dotaz), čtyřikrát dražší a odpověděl jen na čtyři otázky z deseti.

Strop útraty nastavte u poskytovatele modelu. Je to jediná tvrdá pojistka: limity v chatbotovi (dotazy za minutu, za hodinu, volitelně za den z jedné adresy) provoz brzdí, ale peníze nehlídají. U OpenAI se strop nedává na klíč, ale na projekt: založte pro chatbota vlastní projekt a v jeho nastavení zapněte tvrdý měsíční limit (*Limits → Spend → Enforce a hard limit*; postup je v [docs/nasazeni.md](docs/nasazeni.md)). Když kredit dojde nebo strop zabere, chatbot nespadne – ukáže místa, která našlo hledání, a řekne, že model není dostupný.

## Co smí ven

- **Vyhlášky a nařízení jsou veřejné** – zveřejnit nad nimi chatbota je v pořádku.
- **Vnitřní dokumenty** zveřejňujte jen po rozhodnutí toho, kdo za ně odpovídá. Chatbota jde zamknout společným heslem (`ACCESS_PASSWORD`) nebo provozovat jen ve vlastní síti. Platí jednoduché pravidlo: do chatbota na internetu nedávejte nic, co by vadilo zveřejnit – heslo se dá vynést.
- **Osobní údaje.** Sestavení v dokumentech hledá e-maily, telefony, rodná čísla a jména s tituly a vypíše počty nálezů. Podpisy na konci předpisů vynechává samo. Je to síto, ne záruka – rozhodnutí je na vás.
- **Dotazy lidí** odcházejí k poskytovateli modelu. Stránka na to upozorňuje a před odesláním varuje, když dotaz vypadá na osobní údaj. Chatbot text dotazů neukládá ani neloguje; výjimkou je hodnocení odpovědi, které člověk sám odešle palcem (dotaz, odpověď a hodnocení, bez jména a adresy).

Před zveřejněním spusťte `python tasks.py kontrola`.

## Nasazení

Kde chatbot poběží, je na vás: na vlastním serveru, na hostingu pro webové aplikace v Pythonu, nebo jen ve vnitřní síti. Co k běhu potřebuje, co se nasazuje a na co si dát pozor (https, proxy, limity), popisuje [docs/nasazeni.md](docs/nasazeni.md).

**U sebe na síti.** Tentýž kód běží jako obyčejná služba: `python tasks.py serve --port 8770` na serveru ve vaší síti a před ním běžná proxy s https (nastavení proxy a adresy tazatele je v [docs/nasazeni.md](docs/nasazeni.md)). Model může být vnější (klíč v `.env`), nebo lokální – změňte `LLM_BASE_URL`, `CHAT_MODEL` a `EMBED_MODEL` na adresu a názvy vašeho serveru modelů, nastavte `EMBED_DIM=0` a do `LLM_API_KEY` napište libovolný text (bez klíče chatbot model nevolá). Dotazy pak síť neopustí.

## Když se předpis změní

```bash
python tasks.py sbirka            # stáhne nové, odloží zrušené (do dokumenty/sbirka/_neplatne/); přečtěte si řádky „K ruční kontrole“
python tasks.py build             # přepočítá jen změněné odstavce a vypíše, co je nové, změněné a odebrané
python tasks.py check             # zkušební otázky
```

Potom nasaďte znovu. Chatbot odpovídá jen z platných předpisů – zrušené se nestahují.

## Jak to funguje

```
dokumenty/ ─► čtení (PDF, Word, Markdown) ─► články a odstavce ─► index v souborech   (python tasks.py build)
dotaz ─► filtr ─► hledání slovy i významem ─► model ─► kontrola citací a obsahu ─► odpověď se zdroji
```

Žádná databáze: index jsou čtyři soubory ve složce `data/index/`, které se nasazují spolu s kódem. Podrobněji v [ARCHITECTURE.md](ARCHITECTURE.md).

## Co k tomu nedostanete

Je to základ, který si máte upravit – ne hotová služba. Licence [MIT](LICENSE): použijte, upravte, šiřte. Je to tak, jak to stojí a leží: bez záruky a bez podpory. Chybu nebo vylepšení můžete poslat přes Issues a Pull requests na GitHubu.
