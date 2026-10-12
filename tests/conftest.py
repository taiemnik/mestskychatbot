"""Společné nastavení testů: kořen repa na cestě importů a malý index ze zkušebních předpisů smyšleného města."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Aplikace si při startu zkouší, jestli jde zapisovat zpětná vazba, a soubor přitom založí. Při testech
# ať to není ve složce repa (`data/feedback.jsonl`) – testy v ní nemají nic nechávat.
os.environ.setdefault("FEEDBACK_FILE", str(Path(tempfile.gettempdir()) / "chatbot-testy" / "feedback.jsonl"))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AICKOV = FIXTURES / "horni-aickov"            # čtyři předpisy města Horní Áíčkov (smyšlené) + prehled.json


@pytest.fixture(scope="session")
def small_index_dir(tmp_path_factory) -> Path:
    """Malý skutečný index ze zkušebních předpisů (náhradní embeddingy, bez volání modelu)."""
    from chatbot.index.build import build_index
    from chatbot.llm.provider import FakeProvider

    base = tmp_path_factory.mktemp("maly-index")
    report = build_index(AICKOV, FakeProvider(dim=256), base / "index", base / "cache" / "emb.npz", today="2026-10-10")
    assert report.docs == 4 and not report.errors
    return base / "index"
