"""Ochrana proti zneužití: filtr dotazů (před voláním modelu) a kontrola odpovědi (po něm).

Proč dvě vrstvy: filtr je levný a jistý, ale zná jen známé triky; kontrola odpovědi chytí i to, co
filtrem proklouzlo a model se nechal přemluvit. Podrobný popis a meze: docs/ochrana.md.
"""
