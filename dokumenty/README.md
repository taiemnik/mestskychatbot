# Dokumenty

Sem patří dokumenty, ze kterých má chatbot odpovídat. Tento soubor chatbot nečte.

## Co sem dát

- **Předpisy obce** sem stáhne příkaz `python tasks.py sbirka <ID datové schránky>` – do podsložky `sbirka/`.
- **Vlastní dokumenty** nahrajte kamkoli do této složky, klidně do podsložek. Čtou se soubory **PDF, Word (.docx), Markdown (.md), text (.txt) a HTML**.
- Složky a soubory začínající podtržítkem nebo tečkou se nečtou. Co chcete dát stranou, přesuňte třeba do `_stranou/`.

Po každé změně spusťte `python tasks.py build`.

## Údaje o dokumentu (nepovinné)

Bez údajů se dokument jmenuje podle svého nadpisu nebo podle názvu souboru. Lepší citace dostanete, když chatbot zná druh, číslo a účinnost. Zapisují se do souboru `prehled.json` v této složce (u předpisů ze Sbírky vzniká sám):

```json
{
  "dokumenty": {
    "rady/navstevni-rad-knihovny.pdf": {
      "nazev": "Návštěvní řád knihovny",
      "druh": "Řád",
      "cislo": "2/2025",
      "ucinnost_od": "2025-09-01",
      "zdroj": "https://www.horniaickov.example/rady/knihovna.pdf",
      "poznamka": "",
      "prilohy": []
    }
  }
}
```

- `zdroj` je adresa originálu – chatbot na ni u zdroje odkáže.
- `poznamka` se ukáže jako upozornění u zdroje (například „Pozor: změněno předpisem č. 4/2026“).
- `prilohy` jsou soubory, které se mají číst jako součást dokumentu, ne samostatně. Stejně se chová soubor pojmenovaný `<dokument>-priloha-1.pdf`.

U Markdownu stačí údaje napsat na začátek souboru (viz níže).

## Když se soubor nepřečte: přepis do Markdownu

Sestavení vypíše soubory, které nepřečetlo (sken bez textu, poškozené PDF). Oprava: vedle originálu uložte **přepis se stejným názvem a příponou `.md`**. Přepis má při čtení přednost, originál zůstává na místě.

```
dokumenty/sbirka/ozv-2007-03-o-zrizeni-mestske-policie.pdf    ← originál (sken)
dokumenty/sbirka/ozv-2007-03-o-zrizeni-mestske-policie.md     ← přepis, čte se tento
```

Přepis pište doslova. Vzor:

```markdown
---
nazev: o zřízení městské policie
druh: Obecně závazná vyhláška
cislo: 3/2007
ucinnost_od: 2007-08-01
---

# Obecně závazná vyhláška města Horní Áíčkov o zřízení městské policie

Zastupitelstvo města Horní Áíčkov se usneslo vydat tuto obecně závaznou vyhlášku:

### Čl. 1 Úvodní ustanovení

1. Ve městě Horní Áíčkov se zřizuje Městská policie Horní Áíčkov.
2. Každý má právo obracet se na strážníky se žádostí o pomoc.

### Čl. 2 Stejnokroj

Stejnokroj strážníka tvoří:

a) čepice černá,
b) bunda černá se znakem města.

# Příloha č. 1 Vyobrazení stejnokroje

| Součást | Barva |
|---|---|
| čepice | černá |
```

Záhlaví mezi `---` je nepovinné; má stejné údaje jako `prehled.json`. Článek je nadpis `### Čl. N Název`, odstavce číslovaný seznam, písmena `a)`, `b)`. Podpisy na konci nepřepisujte.

## Jak poznat, že se dokument přečetl dobře

`python tasks.py build` vypíše u každého dokumentu, jak se přečetl. Pak pusťte `python tasks.py serve`, zeptejte se na něco z dokumentu a otevřete jeho znění: čísla článků a odstavců mají sedět s originálem. Nesedí-li, pomůže přepis do Markdownu.
