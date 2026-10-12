"""Filtr dotazu – běží dřív než hledání a model, takže odmítnutý dotaz nic nestojí.

Odmítá čtyři věci: pokus o obcházení zadání, vulgarity, zadání mimo účel (básně, vtipy) a nesmysly.
Navíc pozná pozdrav nebo poděkování bez dotazu („Dobrý den“, „Díky“) – odpoví zdvořile a model se nevolá –
a žádost o názor („Co si myslíš o…“): na tu řekne, že od názorů tu není.
Odpovědí je vždy pevná věta z `MESSAGES`; text dotazu se do ní nikdy neopisuje. Tón vět: zdvořilý,
lidský, s nápovědou, co zkusit – stránka u nich neukazuje žádné zdroje.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from chatbot.guard.patterns import INJECTION, OFFTOPIC, OPINION, ROLE_MARKERS, VULGAR

MESSAGES = {
    "obchazeni": ("Tohle neumím – odpovídám jen z dokumentů, které mám k dispozici. "
                  "Zeptejte se prosím na to, co v nich potřebujete najít."),
    "vulgarni": ("Na takový dotaz odpovídat nebudu. "
                 "Zeptejte se prosím věcně na to, co potřebujete najít."),
    "mimo": ("S tímhle bohužel nepomohu – odpovídám jen z dokumentů, které mám k dispozici. "
             "Zkuste se zeptat na něco, co v nich může být."),
    "nesmysl": ("Tomu úplně nerozumím – nejspíš se vloudil překlep nebo se odeslalo něco omylem. "
                "Zkuste dotaz napsat celou větou."),
    # od názorů a osobních rozhodnutí chatbot není; stejnou větu vydá server, když žádost pozná až model
    "nazor": ("Od názorů a osobních rad tu nejsem. Jsem vyhledávač a rádce k předpisům a dokumentům, které mám "
              "k dispozici: zeptejte se, co k věci říkají, a ukážu vám, kde to stojí."),
    "pozdrav": "Dobrý den! Na co se chcete zeptat?",
    "podekovani": "Rádo se stalo. Kdykoli se zeptejte znovu – a nezapomeňte, že závazné je vždy znění předpisu.",
}

# Zdvořilosti bez dotazu. Porovnává se očištěný text (malá písmena bez diakritiky) zbavený výplňových slov;
# musí sedět celý, takže „Dobrý den, kdo schvaluje dovolenou?“ normálně projde k hledání.
_GREETINGS = frozenset((
    "ahoj", "cau", "cus", "nazdar", "zdravim", "zdar", "dobry den", "dobre rano", "dobry vecer", "dobre odpoledne",
    "hezky den", "hello", "hi", "hey", "jak se mas", "jak se mate", "jak se dari", "jak je", "jak to jde",
    "co je noveho", "co je novyho", "jsi tu", "jsi tam", "jste tu", "jste tam",
))
# poděkování a potvrzení: dotaz složený jen z těchto slov („Díky moc“, „Super, děkuji za odpověď“)
_THANK_WORDS = frozenset((
    "dekuji", "dekuju", "diky", "dik", "dikec", "dikes", "super", "parada", "skvele", "jasne", "rozumim", "ok", "dobre",
    "nashledanou", "na", "shledanou", "mej", "mejte", "se", "hezky", "den", "thanks", "thank", "you", "thx",
    "za", "pomoc", "odpoved", "vysvetleni", "info", "informaci", "to", "staci",
))
_FILLER = frozenset(("moc", "mockrat", "pekne", "krasne", "velmi", "prosim", "vam", "ti", "tobe", "vsem", "asistente",
                     "chatbote", "robote", "tedy", "no"))
_NOT_LETTERS = re.compile(r"[^a-z0-9 ]+")
MAX_SMALLTALK_WORDS = 6

# neviditelné znaky, kterými se dá rozdělit slovo, aby ho filtr nepoznal
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# hláskování „i g n o r u j“: pět a víc jednotlivých písmen oddělených mezerou
_SPELLED = re.compile(r"\b(?:\w ){4,}\w\b")
# písmena azbuky a řečtiny, která vypadají jako latinka
_LOOKALIKES = str.maketrans("аеорсухіјѕԁӏαοερτυ", "aeopcyxijsdlaoeptu")
MIN_LETTERS = 3             # kratší „dotaz“ není dotaz
# slovo ze 40 a více písmen, číslic a znaků + / = (bez tečky a dvojtečky) je zakódovaný text, např. base64
_ENCODED = re.compile(r"[a-z0-9+/=]{40,}")
MIN_VOWELS = 0.10           # podíl samohlásek; pod ním jde o bouchání do klávesnice (zkratky „GDPR DPH“ projdou)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None       # obchazeni | vulgarni | mimo | nazor | nesmysl | pozdrav | podekovani
    message: str | None = None      # pevná věta pro uživatele


def normalize(text: str) -> str:
    """Očistí dotaz: jednotná podoba znaků, bez neviditelných a řídicích znaků, mezery sjednocené.

    Tahle podoba jde dál do hledání a k modelu.
    """
    text = unicodedata.normalize("NFKC", text).replace("\r", "\n").replace("\t", " ")
    text = _INVISIBLE.sub("", text)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    lines = (re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def plain(text: str) -> str:
    """Podoba pro porovnání se vzory: malá písmena bez diakritiky, podobná cizí písmena převedená,
    hláskovaná slova spojená. Slouží jen filtru – do hledání nejde."""
    lowered = normalize(text).lower().translate(_LOOKALIKES)
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPELLED.sub(lambda m: m.group(0).replace(" ", ""), stripped)


def _is_nonsense(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < MIN_LETTERS:
        return True
    if re.search(r"([^\s.\-_\d])\1{6,}", text):                     # „aaaaaaa“, „???????“ (tečky, pomlčky a nuly v částce nevadí)
        return True
    if any(_ENCODED.fullmatch(token) for token in text.split()):    # base64 a podobné; dlouhý odkaz na stránku nevadí
        return True
    meaningful = sum(ch.isalnum() or ch.isspace() for ch in text)
    if len(text) >= 10 and meaningful / len(text) < 0.6:            # většina znaků jsou symboly
        return True
    vowels = sum(ch in "aeiouy" for ch in letters)
    return len(letters) >= 10 and vowels / len(letters) < MIN_VOWELS


def _smalltalk(candidate: str) -> str | None:
    """„pozdrav“ nebo „podekovani“, když je celý dotaz jen zdvořilost; jinak None."""
    words = [w for w in _NOT_LETTERS.sub(" ", candidate).split() if w not in _FILLER]
    if not words or len(words) > MAX_SMALLTALK_WORDS:
        return None
    phrase = " ".join(words)
    if phrase in _GREETINGS:
        return "pozdrav"
    # „Ahoj, jak se máš?“ – pozdrav a za ním zdvořilá otázka
    if any(" ".join(words[:i]) in _GREETINGS and " ".join(words[i:]) in _GREETINGS for i in range(1, len(words))):
        return "pozdrav"
    if all(w in _THANK_WORDS for w in words) and not phrase.startswith("hezky den"):
        return "podekovani"
    return None


def _reject(reason: str) -> Verdict:
    return Verdict(ok=False, reason=reason, message=MESSAGES[reason])


def check_question(text: str) -> Verdict:
    """Rozhodne, zda dotaz smí k hledání a modelu.
    Pořadí: zdvořilost → nesmysl → obcházení → vulgarita → mimo účel → názor."""
    candidate = plain(text)
    smalltalk = _smalltalk(candidate)
    if smalltalk:
        return _reject(smalltalk)
    if _is_nonsense(candidate):
        return _reject("nesmysl")
    # značky rolí se hledají v textu s diakritikou: „Systém: …“ je česká věta, „system: …“ podvrh konverzace
    marked = normalize(text).lower().translate(_LOOKALIKES)
    if any(p.search(candidate) for p in INJECTION) or any(p.search(marked) for p in ROLE_MARKERS):
        return _reject("obchazeni")
    if VULGAR.search(candidate):
        return _reject("vulgarni")
    if OFFTOPIC.search(candidate):
        return _reject("mimo")
    if OPINION.search(candidate):
        return _reject("nazor")
    return Verdict(ok=True)
