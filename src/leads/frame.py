"""Utilitários para manipular as colunas de controle do DataFrame de leads."""

from __future__ import annotations

import pandas as pd

from leads.models import Col

# Colunas internas (prefixo "_") circulam entre as etapas e não vão para a saída.
CNPJ_KEY = "_cnpj"

DETAIL_SEPARATOR = "; "


def append_detail(df: pd.DataFrame, mask: pd.Series, message: str | pd.Series) -> None:
    """Acrescenta uma observação à coluna ``detalhe`` das linhas selecionadas (in-place)."""
    if not mask.any():
        return
    new = message[mask] if isinstance(message, pd.Series) else message
    current = df.loc[mask, Col.DETALHE].fillna("")
    df.loc[mask, Col.DETALHE] = current.where(current == "", current + DETAIL_SEPARATOR) + new


def internal_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if str(c).startswith("_")]
