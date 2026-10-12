"""Společné heslo (jednoduchá závora, ne účty).

Když je nastavená proměnná `ACCESS_PASSWORD`, odpovídá chatbot jen prohlížeči, který se heslem
přihlásil: dotazy, znění dokumentů i hodnocení vrací bez přihlášení 401. Bez nastaveného hesla
je aplikace otevřená jako dřív (místní vývoj).

Po zadání hesla dostane prohlížeč cookie s podepsanou platností („<konec platnosti>.<podpis>“).
Server si nic nepamatuje – běží-li víc instancí, každá cookie ověří sama. Podpis se
počítá z hesla, takže změna hesla odhlásí všechny naráz. Cookie je HttpOnly (skript na stránce ji
nepřečte) a SameSite=Strict (cizí stránka ji s požadavkem nepošle).

Co heslo neumí: je jedno pro všechny, takže nepozná, kdo se ptal, a kdo ho vynese, pustí dovnitř
kohokoli. Brání náhodným návštěvníkům a robotům, ne cílenému útoku – proto dál platí limity
dotazů i pravidlo, že do veřejného indexu nesmí nic, co by vadilo zveřejnit.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, Response

from chatbot.api.ratelimit import AbuseTracker

COOKIE = "pristup"
LOGIN_REQUIRED = "Přihlaste se prosím heslem."
WRONG_PASSWORD = "Heslo nesouhlasí."
TOO_MANY = "Příliš mnoho pokusů o přihlášení. Zkuste to znovu za {min} minut."


class Access:
    """Ověřuje heslo a cookie. `password` prázdné = závora vypnutá."""

    def __init__(self, password: str, days: int = 30, attempts: int = 5, window_min: int = 10, block_min: int = 5):
        self.password = password.strip()
        self.max_age = days * 86400
        self.block_min = block_min
        # klíč podpisu: otisk hesla (heslo samo se dál nikde nedrží v jiné podobě)
        self._key = hashlib.sha256(("chatbot-pristup|" + self.password).encode("utf-8")).digest()
        # špatná hesla z jedné adresy: po `attempts` pokusech během `window_min` minut pauza
        self._failures = AbuseTracker(attempts, window_min * 60, block_min * 60)

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    # ------------------------------------------------------------------ cookie
    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode("ascii"), hashlib.sha256).hexdigest()

    def token(self, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + self.max_age)
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str | None, now: float | None = None) -> bool:
        expires, _, signature = (token or "").partition(".")
        # jen běžné číslice rozumné délky: „²³“ nebo tisíce číslic by jinak skončily chybou místo odmítnutí;
        # totéž podpis – porovnání ve stálém čase znaky mimo ASCII nepřijme
        if not (expires.isascii() and expires.isdigit() and len(expires) <= 12) or not signature or not signature.isascii():
            return False
        if int(expires) < (time.time() if now is None else now):
            return False
        return hmac.compare_digest(signature, self._sign(int(expires)))

    def allowed(self, request: Request) -> bool:
        """Smí tento prohlížeč dál? Bez nastaveného hesla vždy."""
        return not self.enabled or self.valid(request.cookies.get(COOKIE))

    def require(self, request: Request) -> None:
        if not self.allowed(request):
            raise HTTPException(401, LOGIN_REQUIRED)

    # ------------------------------------------------------------------ přihlášení
    def login(self, password: object, address: str, now: float) -> None:
        """Ověří heslo. Špatné heslo = 401, po sérii špatných pokusů z jedné adresy 429."""
        if self._failures.blocked(address, now):
            raise HTTPException(429, TOO_MANY.format(min=self.block_min))
        given = password if isinstance(password, str) else ""
        # porovnání ve stálém čase: doba odpovědi nesmí prozradit, kolik znaků sedí
        if not hmac.compare_digest(given.encode("utf-8"), self.password.encode("utf-8")):
            self._failures.strike(address, now)
            raise HTTPException(401, WRONG_PASSWORD)

    def set_cookie(self, response: Response, secure: bool) -> None:
        response.set_cookie(COOKIE, self.token(), max_age=self.max_age, httponly=True, samesite="strict",
                            secure=secure, path="/")

    @staticmethod
    def clear_cookie(response: Response) -> None:
        response.delete_cookie(COOKIE, path="/")
