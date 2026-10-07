"""Deduplicação de leads.

Leads são agrupados pelo CNPJ e, quando ele falta, por e-mail ou telefone. Dois
leads com CNPJs válidos diferentes nunca são juntados, mesmo que compartilhem um
e-mail ou telefone (é comum o mesmo contador ou agência atender várias empresas).

Em cada grupo, um lead é eleito o principal e os demais recebem o status
``duplicado`` apontando para a linha dele. Campos vazios do principal são
preenchidos com os valores dos duplicados, então nenhuma informação se perde.
"""

from __future__ import annotations

import logging

import pandas as pd

from leads.frame import CNPJ_KEY, append_detail, internal_columns
from leads.models import Col, LeadStatus

logger = logging.getLogger(__name__)

_MATCH_COLUMNS = (CNPJ_KEY, Col.EMAIL, Col.TELEFONE)
_PROTECTED_COLUMNS = frozenset({Col.LINHA, Col.STATUS, Col.DETALHE, Col.DUPLICADO_DE})


class _UnionFind:
    """Union-find que se recusa a juntar grupos com CNPJs diferentes."""

    def __init__(self, cnpjs: list[str | None]) -> None:
        self._parent = list(range(len(cnpjs)))
        self._cnpj = list(cnpjs)

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:  # compressão de caminho
            self._parent[i], i = root, self._parent[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        ca, cb = self._cnpj[ra], self._cnpj[rb]
        if ca is not None and cb is not None and ca != cb:
            return
        self._parent[rb] = ra
        self._cnpj[ra] = ca or cb


def group_duplicates(df: pd.DataFrame) -> list[list[int]]:
    """Retorna os grupos de posições (``iloc``) que representam o mesmo lead.

    Só devolve grupos com mais de um lead, na ordem em que aparecem na planilha.
    """
    uf = _UnionFind([v if isinstance(v, str) else None for v in df[CNPJ_KEY]])
    first_seen: dict[tuple[str, str], int] = {}

    for column in _MATCH_COLUMNS:
        for pos, value in enumerate(df[column]):
            if not isinstance(value, str):
                continue
            key = (column, value)
            if key in first_seen:
                uf.union(first_seen[key], pos)
            else:
                first_seen[key] = pos

    groups: dict[int, list[int]] = {}
    for pos in range(len(df)):
        groups.setdefault(uf.find(pos), []).append(pos)
    return [members for members in groups.values() if len(members) > 1]


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Marca os duplicados e consolida os dados no lead principal de cada grupo."""
    out = df.reset_index(drop=True).copy()
    groups = group_duplicates(out)
    mergeable = [c for c in out.columns if c not in _PROTECTED_COLUMNS]

    for members in groups:
        master = _pick_master(out, members)
        others = [m for m in members if m != master]

        for column in mergeable:
            if pd.isna(out.at[master, column]):
                filled = out.loc[others, column].dropna()
                if not filled.empty:
                    out.at[master, column] = filled.iloc[0]

        out.loc[others, Col.STATUS] = LeadStatus.DUPLICADO.value
        out.loc[others, Col.DUPLICADO_DE] = out.at[master, Col.LINHA]
        dup_mask = out.index.isin(others)
        append_detail(
            out,
            pd.Series(dup_mask, index=out.index),
            f"duplicado da linha {out.at[master, Col.LINHA]}",
        )

    logger.info(
        "%d grupos de duplicados, %d leads marcados como duplicado",
        len(groups),
        sum(len(g) - 1 for g in groups),
    )
    return out


def _pick_master(df: pd.DataFrame, members: list[int]) -> int:
    """Prefere quem tem CNPJ válido, depois quem tem mais dados, depois a primeira linha."""
    data_columns = [c for c in df.columns if c not in _PROTECTED_COLUMNS]
    data_columns = [c for c in data_columns if c not in internal_columns(df)]
    subset = df.iloc[members]
    has_cnpj: list[bool] = subset[CNPJ_KEY].notna().tolist()
    filled: list[int] = subset[data_columns].notna().sum(axis=1).tolist()
    scores = {
        pos: (int(cnpj), count, -pos)
        for pos, cnpj, count in zip(members, has_cnpj, filled, strict=True)
    }
    return max(members, key=scores.__getitem__)
