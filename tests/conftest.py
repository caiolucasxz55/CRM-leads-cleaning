from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from leads.normalize import normalize_leads
from leads.reader import standardize_columns

FIXTURES = Path(__file__).parent / "fixtures"

# CNPJs públicos usados nos testes (todos com dígito verificador válido).
CNPJ_BB = "00000000000191"
CNPJ_PETROBRAS = "33000167000101"
CNPJ_MAGALU = "47960950000121"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def make_leads() -> Callable[[list[dict[str, Any]]], pd.DataFrame]:
    """Monta um DataFrame já padronizado e normalizado, como sai da etapa 2."""

    def _make(rows: list[dict[str, Any]]) -> pd.DataFrame:
        return normalize_leads(standardize_columns(pd.DataFrame(rows)))

    return _make
