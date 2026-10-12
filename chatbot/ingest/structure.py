"""Řádky vytažené ze souboru → bloky dokumentu (název, část, článek, odstavec, písmena, tabulka).

Rozbor nic nepředpokládá o tom, odkud řádky jsou. Pozná české členění předpisů („Čl. 3“, „(1)“, „a)“,
„Příloha č. 1“) a obecné nadpisy z Wordu, Markdownu a HTML. Co nepozná, nechá jako odstavec textu –
dokument se tím neztratí, jen se u něj cituje méně přesně.

Podpisové řádky na konci (jména s tituly, „v. r.“, funkce) se do bloků neberou: nejsou obsahem
předpisu a do odpovědí chatbota nepatří.
"""
from __future__ import annotations

from dataclasses import replace

from chatbot.ingest import markers
from chatbot.ingest.model import Block, Line, SubItem

TITLE_MAX_LINES = 8         # kolik úvodních řádků může tvořit název dokumentu
TITLE_LINE_CHARS = 140      # delší úvodní řádek už není název, ale text
TITLE_HINTED_CHARS = 320    # totéž pro řádek, který je vystředěný nebo má styl nadpisu
HEADING_CHARS = 150         # delší „nadpis“ podle stylu je ve skutečnosti odstavec se špatným stylem
SIGNATURE_WINDOW = 12       # v kolika posledních řádcích oddílu se hledají podpisy
_SENTENCE_END = (".", ":", ";", "!", "?")


def build_blocks(lines: list[Line]) -> tuple[list[Block], list[str]]:
    """Vrátí (bloky s kotvami b0, b1, …; příznaky). Příznaky: „podpisy“, „bez-clanku“."""
    # prázdné řádky a oddělovací čáry („_______“ nad poznámkami pod čarou) obsah nenesou
    lines = [line for line in lines if not line.blank and (line.rows or any(ch.isalnum() for ch in line.text))]
    lines, signed = _drop_signatures(lines)
    builder = _Builder(_paragraph_style(lines))
    index = builder.take_title(lines)
    while index < len(lines):
        index += builder.add(lines, index)
    blocks = builder.blocks
    empty_appendix = _settle_empty_appendices(blocks)
    for number, block in enumerate(blocks):
        block.anchor = f"b{number}"
    flags = ((["podpisy"] if signed else []) + ([] if any(b.kind == "article" for b in blocks) else ["bez-clanku"])
             + (["priloha-bez-textu"] if empty_appendix else []))
    return blocks, flags


def _settle_empty_appendices(blocks: list[Block]) -> bool:
    """Nadpisy příloh, pod kterými nic není. Dva a víc za sebou je výčet příloh na konci textu
    („Příloha č. 1 – Sazebník“, „Příloha č. 2 – Mapa“) – to nadpisy nejsou, zůstanou jako text.
    Jeden osamocený je příloha, jejíž obsah se nedá přečíst (mapa, obrázek). Vrací, zda taková je."""
    found = False
    index = 0
    while index < len(blocks):
        if blocks[index].kind != "priloha":
            index += 1
            continue
        end = index                                 # konec řady nadpisů příloh jdoucích hned za sebou
        while end + 1 < len(blocks) and blocks[end + 1].kind == "priloha":
            end += 1
        at_end = end + 1 == len(blocks)             # za řadou už žádný text není
        if end > index:
            # výčet příloh; poslední z řady zůstává nadpisem jen tehdy, když pod ním něco je
            for block in blocks[index:end + (1 if at_end else 0)]:
                block.kind = "text"
        elif at_end:
            found = True                            # osamocený nadpis přílohy bez textu
        index = end + 1
    return found


def _paragraph_style(lines: list[Line]) -> str | None:
    """Jak dokument čísluje odstavce: „(1)“, „1.“ nebo „1)“. Bere se první číslovaný řádek za prvním
    nadpisem článku (bez článků první v dokumentu). Jiný druh číslování v témže dokumentu jsou pak body."""
    after_article = not any(markers.article(line.text) for line in lines if not line.rows)
    for line in lines:
        if line.rows:
            continue
        if markers.article(line.text):
            after_article = True
            continue
        found = markers.paragraph(line.text)
        if after_article and found and line.level == 0:
            return found[0]
    return None


def _drop_signatures(lines: list[Line]) -> tuple[list[Line], bool]:
    """Vynechá podpisové řádky na konci hlavního textu a na konci každé přílohy, a řadu podpisů s „v. r.“
    kdekoli v dokumentu."""
    ends = [i for i, line in enumerate(lines) if not line.rows and markers.appendix(line.text)] + [len(lines)]
    drop: set[int] = set()
    signed = False
    for end in ends:
        window = range(max(0, end - SIGNATURE_WINDOW), end)
        found: set[int] = set()                     # řádky, které podpisem jistě jsou („v. r.“, jméno s titulem, doložka)
        roles: set[int] = set()                     # samotná funkce („starosta města“)
        names: set[int] = set()                     # samotné jméno bez titulu („Jan Novák“)
        for i in window:
            line = lines[i]
            # jen řádky za posledním „skutečným“ odstavcem: za podpisem už nesmí přijít nadpis článku
            if line.style or any(markers.article(later.text) for later in lines[i + 1:end] if not later.rows):
                continue
            if line.rows:                           # podpisy bývají i v tabulce o dvou sloupcích
                cells = [cell for row in line.rows for cell in row if cell.strip()]
                if cells and all(markers.is_signature(cell) for cell in cells):
                    found.add(i)
            elif markers.is_role_only(line.text):
                roles.add(i)
            elif markers.is_signature(line.text):
                found.add(i)
            elif markers.is_bare_name(line.text):
                names.add(i)
        # Samotná funkce a samotné jméno jsou podpis jen pohromadě: funkce hned u jména nebo u jistého podpisu,
        # jméno hned u funkce. Výčet funkcí („starosta“, „místostarosta“ pod sebou), název článku „Tajemník“
        # ani dvouslovný název obce nad podpisem („Dolní Lhota“) podpis nejsou.
        grew = True
        while grew:
            grew = False
            for i in sorted(roles - found):
                if {i - 1, i + 1} & (found | names):
                    found.add(i)
                    grew = True
            for i in sorted(names - found):
                if {i - 1, i + 1} & (roles & found):
                    found.add(i)
                    grew = True
        drop |= found
        signed = signed or any(lines[i].rows or any(ch.isalpha() for ch in lines[i].text) for i in found)
    # Podpisy uprostřed dokumentu (za nimi ještě ceník, sazebník, druhá část): souvislá řada podpisových
    # řádků, ve které je aspoň jednou „v. r.“ – to se jinde než v podpisu nepíše. Samotný výčet funkcí
    # („starosta“, „místostarosta“ pod sebou) bez „v. r.“ uprostřed textu podpis není.
    run: list[int] = []
    for i in range(len(lines) + 1):
        line = lines[i] if i < len(lines) else None
        if line is not None and not line.rows and (markers.is_signature(line.text)
                                                   or (run and markers.is_bare_name(line.text))):
            run.append(i)
            continue
        if len(run) >= 2 and any(markers.is_signed(lines[j].text) for j in run):
            drop |= set(run)
            signed = True
        run = []
    return [line for i, line in enumerate(lines) if i not in drop], signed


class _Builder:
    def __init__(self, para_style: str | None):
        self.para_style = para_style
        self.blocks: list[Block] = []
        self.container: Block | None = None        # odstavec, ke kterému patří následující písmena a body
        self.open_item: SubItem | None = None       # poslední písmeno – patří k němu vnořené body
        self.last: Block | SubItem | None = None    # kam se připojí pokračování rozdělené věty

    # ------------------------------------------------------------------ název dokumentu
    def take_title(self, lines: list[Line]) -> int:
        """Úvodní řádky (vydavatel, druh, název) spojí do bloku „title“. Vrací, kolik řádků spotřeboval."""
        collected: list[str] = []
        plain = 0                                   # řádky bez nápovědy (vystředění, styl): takové smějí být nejvýš tři
        level: str | None = None                    # úroveň nadpisu, kterou je psán název (další úroveň už je oddíl)
        for line in lines[:TITLE_MAX_LINES]:
            text = line.text.strip()
            # vystředěný nebo nadpisový řádek smí být dlouhý – úřední názvy bývají („…, kterou se zakazuje…“)
            limit = TITLE_HINTED_CHARS if (line.style or line.center) else TITLE_LINE_CHARS
            if line.rows or markers.starts_element(text) or len(text) > limit:
                break
            if line.style:
                if level is not None and line.style != level:
                    break
                level = line.style
            if not (line.style or line.center):
                if plain >= 3 or not _looks_like_heading(text):
                    break
                plain += 1
            collected.append(text)
        if collected:
            self.blocks.append(Block(kind="title", text="\n".join(collected), page=lines[0].page))
        return len(collected)

    # ------------------------------------------------------------------ jeden řádek
    def add(self, lines: list[Line], index: int) -> int:
        """Zpracuje řádek `index` (a případně řádky s názvem nadpisu za ním). Vrací počet spotřebovaných řádků."""
        line = lines[index]
        if line.rows:
            self._heading_reset()
            self.blocks.append(Block(kind="table", text="", rows=line.rows, page=line.page))
            return 1
        text = line.text.strip()
        hinted = bool(line.style or line.center)

        heading = markers.appendix(text) or markers.part(text)
        if heading:
            # „Příloha č. 1“ má název nejspíš na dalším řádku; „Příloha č. 1 k vyhlášce…“ ho už má
            kind = "priloha" if markers.appendix(text) else "part"
            return self._heading(kind, heading, None, lines, index, hinted, has_title=len(heading.split()) > 3)
        found = markers.article(text)
        if found:
            number, title = found
            return self._heading("article", title, number, lines, index, hinted, has_title=bool(title))
        if line.style and len(text) <= HEADING_CHARS and not text.endswith(_SENTENCE_END):
            level = int(line.style[1]) if line.style[1:].isdigit() else 2
            self._heading_reset()
            self.blocks.append(Block(kind="part" if level <= 2 else "article", text=text, page=line.page))
            return 1

        numbered = markers.paragraph(text)
        if numbered:
            kind, number, body = numbered
            if line.level == 0 and kind == (self.para_style or kind):
                self.para_style = self.para_style or kind
                self._paragraph(body, number, line)
            else:
                self._item(f"{number}." if kind == "1." else (f"({number})" if kind == "(1)" else f"{number})"),
                           body, line, nested=self.open_item is not None)
            return 1
        lettered = markers.letter(text)
        if lettered:
            self._item(lettered[0], lettered[1], line, nested=line.level >= 2, opens=True)
            return 1
        bulleted = markers.bullet(text)
        if bulleted:
            self._item(bulleted[0], bulleted[1], line, nested=self.open_item is not None or line.level >= 2)
            return 1

        if markers.footnote(text) and self._list_is_open():
            # poznámka pod čarou na konci stránky PDF uprostřed nedokončeného výčtu: samostatný text,
            # výčet za ní pokračuje (další písmeno patří k témuž odstavci)
            self.blocks.append(Block(kind="text", text=text, page=line.page))
            return 1
        if self._continues(text):
            self.last.text = f"{self.last.text} {text}".strip()
            return 1
        if isinstance(self.last, SubItem) and text[:1].islower():
            # řádek malým písmenem za písmenem ukončeným dvojtečkou („a) za stánek: / do pěti metrů 50 Kč“)
            # je jeho podbod, ne nový odstavec
            child = SubItem(text=text)
            self.last.children.append(child)
            self.last = child
            return 1
        block = Block(kind="text", text=text, page=line.page)
        self.blocks.append(block)
        self.container, self.open_item, self.last = block, None, block
        return 1

    # ------------------------------------------------------------------ nadpisy
    def _heading_reset(self) -> None:
        self.container, self.open_item, self.last = None, None, None

    def _heading(self, kind: str, text: str, number: str | None, lines: list[Line], index: int, hinted: bool,
                 has_title: bool) -> int:
        """Nadpis přílohy, části nebo článku. Název může stát na dalších řádcích („Čl. 3“ / „Účinnost“):
        hledá se tam, když ho nadpis na svém řádku nemá."""
        used = 1
        if not has_title:
            tail, taken = _heading_tail(lines, index + 1, hinted)
            if tail:
                text = f"{text} {tail}".strip()
                used += taken
        self._heading_reset()
        self.blocks.append(Block(kind=kind, text=text, number=number, page=lines[index].page))
        return used

    # ------------------------------------------------------------------ odstavce a body
    def _paragraph(self, body: str, number: str, line: Line) -> None:
        block = Block(kind="para", text=body, number=number, page=line.page)
        self.blocks.append(block)
        self.container, self.open_item, self.last = block, None, block

    def _item(self, marker: str, body: str, line: Line, nested: bool, opens: bool = False) -> None:
        """Písmeno, bod nebo odrážka: patří k poslednímu odstavci; vnořené k poslednímu písmenu."""
        if self.container is None:
            self.container = Block(kind="para", text="", page=line.page)
            self.blocks.append(self.container)
        self.container.kind = "para"                # odstavec bez čísla, ale s pododstavci
        item = SubItem(text=body, marker=marker)
        if nested and self.open_item is not None:
            self.open_item.children.append(item)
        else:
            self.container.items.append(item)
            if opens:
                self.open_item = item
            elif not nested:
                self.open_item = None
        self.last = item

    def _list_is_open(self) -> bool:
        """Je rozdělaný výčet? Buď poslední položka neskončila větou, nebo návětí odstavce končí dvojtečkou
        a položky teprve přijdou (poznámka pod čarou stojí hned za ním)."""
        if isinstance(self.last, SubItem):
            return not self.last.text.rstrip().endswith((".", ":", "!", "?"))
        return self.last is not None and self.last is self.container and self.last.text.rstrip().endswith(":")

    def _continues(self, text: str) -> bool:
        """Je řádek pokračováním věty z předchozího odstavce? (Word: autor uprostřed věty stiskl Enter.)
        Pokračování začíná malým písmenem, odkazem na paragraf („§ 84 odst. 2…“) nebo datem („28. října“).
        Za čárkou pokračuje věta vždy – i když řádek začíná velkým písmenem (výčet ulic)."""
        if self.last is None or not text:
            return False
        previous = self.last.text.rstrip()
        if not previous:
            return False
        if previous.endswith(","):
            return True
        if not (text[0].islower() or text.startswith("§") or markers.starts_with_date(text)):
            return False
        return not previous.endswith(_SENTENCE_END)


def _looks_like_heading(text: str) -> bool:
    """Krátký řádek bez tečky na konci – může být další řádek názvu."""
    return len(text) <= 110 and not text.endswith(_SENTENCE_END)


def _heading_tail(lines: list[Line], start: int, hinted: bool) -> tuple[str, int]:
    """Název nadpisu na řádcích za ním. S nápovědou (vystředění, styl) bere všechny takové řádky,
    bez ní nejvýš jeden krátký řádek bez tečky. Vrací (text, počet řádků)."""
    parts: list[str] = []
    index = start
    while index < len(lines) and len(parts) < 4:
        line = lines[index]
        text = line.text.strip()
        if line.rows or markers.starts_element(text):
            break
        if hinted:
            # vystředěný bývá i krátký článek celý („Čl. 6 / Účinnost / Toto nařízení nabývá účinnosti…“):
            # název nekončí tečkou, věta ano
            if not (line.center or line.style) or len(text) > HEADING_CHARS or text.endswith((".", ";", "!", "?")):
                break
        elif parts or not _looks_like_heading(text):
            break
        parts.append(text)
        index += 1
    return " ".join(parts), index - start


def join_wrapped(lines: list[Line]) -> list[Line]:
    """Složí řádky zalomené uprostřed odstavce (PDF, prostý text, Markdown) zpět do odstavců.

    Nový odstavec začíná za prázdným řádkem, u nadpisu, u značky (článek, „(1)“, „a)“…), při změně
    vystředění, nebo když předchozí řádek skončil tečkou znatelně před pravým okrajem.
    """
    widths = sorted(len(line.text) for line in lines if not line.blank and not line.rows and not line.center)
    full = widths[int(len(widths) * 0.85)] if widths else 0     # šířka běžného plného řádku
    out: list[Line] = []
    current: Line | None = None
    tail = ""                                       # poslední připojený řádek – podle něj se pozná konec odstavce
    last_dot: int | None = None                     # číslo posledního bodu „N.“ – pozná se podle něj další v řadě
    for line in lines:
        if line.blank or line.rows:
            current = None
            if line.rows:
                out.append(line)
            continue
        text = line.text.strip().replace("\u00ad", "")      # měkký spojovník (značka možného dělení slova) pryč
        starts, last_dot = _starts_element(text, current.text if current is not None else None, last_dot)
        ended_early = (current is not None and current.text.endswith(_SENTENCE_END) and text[:1].isupper()
                       and full > 40 and len(tail) < 0.7 * full)
        after_heading = current is not None and _text_after_article_heading(current, text, full)
        if (current is None or line.style or current.style or line.center != current.center or line.level != current.level
                or starts or ended_early or after_heading):
            current = replace(line, text=text)
            out.append(current)
        else:
            current.text = f"{current.text} {text}"
        tail = text
    return out


def _text_after_article_heading(current: Line, text: str, full: int) -> bool:
    """Je řádek za nadpisem článku už textem článku (novým odstavcem), ne pokračováním nadpisu?

    Za samotnou značkou („Čl. 6“, „§ 2“) smí na její řádek jen název: krátký řádek bez tečky. Jakmile nadpis
    název má, další řádek je text – jinak by se celý článek slil s nadpisem („Čl. 2 Účinnost Tato vyhláška…“)
    a nadpis by se ztratil. Vystředěný název smí pokračovat na dalším vystředěném řádku, věta s tečkou ne.
    """
    found = markers.article(current.text)
    if found is None:
        return False
    if found[1]:
        return not current.center or text.endswith((".", ";", "!", "?"))
    if not _looks_like_heading(text):
        return True
    # bez vystředění je názvem jen krátký řádek; řádek přes celou šířku je začátek zalomeného odstavce
    return not current.center and full > 40 and len(text) >= 0.7 * full


def _starts_element(text: str, previous: str | None, last_dot: int | None) -> tuple[bool, int | None]:
    """Začíná zalomený řádek nový prvek? Vrací (ano / ne, číslo posledního bodu „N.“).

    Past: řádek zalomený před „1. května“ nebo „17. listopadu“ vypadá jako číslovaný bod. Bod „N.“ se
    proto uzná jen tehdy, když za číslem následuje velké písmeno, když pokračuje řadu (po 2. přichází 3.),
    nebo když je první za návětím s dvojtečkou.
    """
    if markers.article(text) or markers.part(text) or markers.appendix(text):
        return True, None
    numbered = markers.paragraph(text)
    if numbered and numbered[0] == "1.":
        number = int(numbered[1])
        in_sequence = last_dot is not None and number == last_dot + 1
        opens_list = number == 1 and previous is not None and previous.rstrip().endswith(":")
        if not (numbered[2][:1].isupper() or in_sequence or opens_list):
            return False, last_dot
        return True, number
    return markers.starts_element(text), last_dot
