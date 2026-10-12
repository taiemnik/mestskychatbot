/* Městský chatbot – orientační kontrola osobních údajů v dotazu.
 *
 * Běží v prohlížeči PŘED odesláním dotazu: když text vypadá, že obsahuje osobní nebo citlivý údaj,
 * stránka (app.js) zobrazí varování s volbou „Zpět k úpravě“ / „Přesto odeslat“. Nic se nikam
 * neposílá ani neukládá – funkce jen vrátí názvy druhů údajů, které našla.
 *
 * Je to síto, ne záruka: pozná běžné tvary (rodné číslo, datum narození, telefon, e-mail, číslo
 * dokladu, spisová značka, účet, jméno s titulem nebo oslovením, dvě slova s velkým písmenem za
 * sebou). Jméno psané malými písmeny nebo samotné příjmení nenajde, a naopak občas zareaguje
 * na název (proto jen varuje a odeslání nezakazuje).
 *
 * Úprava: každé pravidlo je jeden řádek v seznamu RULES (název pro uživatele + výraz); obecná slova,
 * která se za jméno považovat nemají, jsou v NOT_NAMES. Názvy z dokumentů (ulice, části obce, instituce)
 * se berou z nastaveni.js – sestavení indexu je tam zapíše samo, aby dotaz na „ulici Boženy Němcové“
 * varování nevyvolal. Po změně pusťte testy (tests/test_osobni_udaje.py).
 */
(function (root) {
  "use strict";

  const UP = "A-ZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ";
  const LOW = "a-záčďéěíňóřšťúůýž";
  const DATE = "\\d{1,2}\\.\\s?\\d{1,2}\\.\\s?(?:19|20)\\d{2}";

  // Pořadí je důležité: co pravidlo najde, to se z textu vymaže, aby totéž číslo nenahlásilo i další pravidlo
  // (rodné číslo by jinak vypadalo i jako číslo účtu nebo telefon).
  const RULES = [
    ["e-mailová adresa", /[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g],
    // rodné číslo: RRMMDD/XXXX s platným měsícem (ženy +50) a dnem; bez lomítka jen celých deset číslic
    ["rodné číslo", /(^|[^\d/])\d{2}(?:0[1-9]|1[0-2]|5[1-9]|6[0-2])(?:0[1-9]|[12]\d|3[01])(?:\s?\/\s?\d{3,4}|\d{4})(?!\d)/g],
    // datum narození: datum uvedené slovem „nar.“ / „narozen“, nebo datum s rokem do 2010 (v dotazu na předpis nemá co dělat)
    ["datum narození", new RegExp(`(?:nar\\.|narozen\\w*|datum\\w*\\s+narození)\\s*:?\\s*(?:dne\\s+)?${DATE}`, "gi")],
    ["datum narození", /(^|[^\d.])\d{1,2}\.\s?\d{1,2}\.\s?(?:19[2-9]\d|200\d|2010)(?!\d)/g],
    // spisová značka / číslo jednací: uvedená zkratkou, nebo číslo (aspoň tři číslice) lomené rokem – „Směrnice č. 4/2024“ sem nespadne
    ["číslo spisu nebo číslo jednací", /(?:č\.\s?j\.|čj\.|sp\.\s?zn\.|spisov\w+\s+značk\w+|čísl\w+\s+jednací\w*)\s*:?\s*\S*\d[\w/.-]*/gi],
    // (číslo zákona „č. 251/2016 Sb.“ vypadá stejně – pozná se podle „Sb.“ za ním)
    ["číslo spisu nebo číslo jednací", /(^|[^\d/])\d{3,}\/(?:19|20)\d{2}(?:\/[\w-]+)?(?![\d/])(?!\s*Sb\b)/g],
    ["číslo účtu", /(^|[^\d/-])(?:\d{2,6}-)?\d{6,10}\/\d{4}(?!\d)/g],
    // doklad: číslo uvedené slovem (OP, pas, průkaz…), nebo samostatných devět číslic (občanský průkaz)
    ["číslo dokladu", /(?:\bOP\b|občansk\w+\s+průkaz\w*|řidičsk\w+\s+průkaz\w*|cestovní\w*\s+(?:pas\w*|doklad\w*)|\bpas\w*|čísl\w+\s+dokladu|č\.\s?dokladu)\s*(?:č\.|číslo)?\s*:?\s*[A-Z]{0,3}\s?\d{5,9}/gi],
    // mobil (začíná 6 nebo 7), s předvolbou i bez; částky v Kč se nepočítají
    ["telefonní číslo", /(^|[^\d/])(?:\+420\s?)?[67]\d{2}\s?\d{3}\s?\d{3}(?!\d)(?!\s?(?:Kč|,-|tis|mil))/g],
    ["číslo dokladu", /(^|[^\d/])\d{9}(?!\d)(?!\s?(?:Kč|,-|tis|mil))/g],
    ["jméno a příjmení", new RegExp(`(?:Ing|Mgr|Bc|JUDr|PhDr|MUDr|RNDr|PaedDr|MVDr|DiS|MBA|Ph\\.D)\\.(?:\\s*(?:et|arch\\.))?\\s+[${UP}][${LOW}]+`, "g")],
    ["jméno a příjmení", new RegExp(`(?:^|[^${UP}${LOW}])(?:pan|paní|pana|panu|panem|kolega|kolegy|kolegyně|kolegyni)\\s+[${UP}][${LOW}]{2,}`, "g")],
  ];

  // slova s velkým písmenem, která jméno nejsou (úřad, město, druh předpisu…)
  const NOT_NAMES = new Set(("Městský Městského Městskému Městském Městským Městská Městské Městskou Obecní Obecního Obecnímu Obecním "
    + "Úřad Úřadu Úřadem Rada Rady Radě Radu Radou Zastupitelstvo Zastupitelstva Zastupitelstvu Zastupitelstvem Město Města Městu Městem "
    + "Obec Obce Obci Obcí Městys Městyse Česká České Českou Republika Republiky Republice Kraj Kraje Krajský Krajského Organizační "
    + "Řád Řádu Směrnice Směrnici Vyhláška Vyhlášky Vyhlášce Vyhlášku Nařízení Pokyn Pokynu Příkaz Příkazu Zákon Zákona Zákoník Zákoníku "
    + "Práce Sociální Fond Fondu Portál Portálu Policie Policii Evropská Evropské Unie Sbírka Sbírky Sbírce").split(" "));
  // názvy z dokumentů a z nastavení (zkrácené tvary bez diakritiky – stejně je zkracuje chatbot/web.py)
  const KNOWN = new Set((root.NASTAVENI && Array.isArray(root.NASTAVENI.znama_slova)) ? root.NASTAVENI.znama_slova : []);
  const stem = (word) => word.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").slice(0, 5);
  const notName = (word) => NOT_NAMES.has(word) || KNOWN.has(stem(word));
  // slova, kterými dotaz běžně začíná – velké písmeno mají jen proto, že stojí na začátku věty
  const STARTERS = new Set(("Kdo Kdy Kde Kam Jak Jaký Jaká Jaké Jakou Který Která Které Kolik Proč Komu Koho Čeho Může Mohu Můžu Můžeme Musí "
    + "Musím Musíme Smí Smím Lze Jsou Jsem Bude Budou Byla Bylo Byl Platí Mám Máme Mají Potřebuji Potřebuju Chci Chceme Prosím Dobrý Děkuji "
    + "Díky Ahoj Existuje Existují Vztahuje Náleží Dostane Dostanu Jde Když Pokud Zda Jestli Podle Kdyby Může-li").split(" "));

  /** Dvě slova s velkým písmenem hned za sebou („Jan Novák“) – nejběžnější podoba jména bez titulu. */
  function nameLike(text) {
    const word = new RegExp(`[${UP}${LOW}]+`, "g");
    const capital = new RegExp(`^[${UP}][${LOW}]{2,}$`);
    let previous = null;
    for (let m = word.exec(text); m; m = word.exec(text)) {
      const current = { text: m[0], start: m.index, end: m.index + m[0].length };
      const adjacent = previous && /^\s+$/.test(text.slice(previous.end, current.start));
      if (adjacent && capital.test(previous.text) && capital.test(current.text)
          && !notName(previous.text) && !notName(current.text) && !STARTERS.has(previous.text)) {
        return true;
      }
      previous = current;
    }
    return false;
  }

  /** Vrátí názvy druhů údajů, které text nejspíš obsahuje (prázdné pole = nic podezřelého). */
  function najdi(text) {
    let rest = String(text || "");
    const found = [];
    const add = (label) => { if (!found.includes(label)) found.push(label); };
    RULES.forEach(([label, pattern]) => {
      pattern.lastIndex = 0;
      if (!pattern.test(rest)) return;
      add(label);
      pattern.lastIndex = 0;
      rest = rest.replace(pattern, " ");
    });
    if (nameLike(rest)) add("jméno a příjmení");
    return found;
  }

  const api = { najdi };
  if (typeof module !== "undefined" && module.exports) module.exports = api;   // testy v Node
  else root.osobniUdaje = api;
})(typeof window !== "undefined" ? window : globalThis);
