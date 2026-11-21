"""Shared helpers for Dialpad adapter tests."""

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())
