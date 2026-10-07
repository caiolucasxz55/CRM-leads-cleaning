"""Leitura de planilhas de leads (CSV ou Excel) e padronização dos nomes de colunas."""

from __future__ import annotations

import csv
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

from leads.models import Col

logger = logging.getLogger(__name__)

CSV_EXTENSIONS = frozenset({".csv", ".txt"})
EXCEL_EXTENSIONS = frozenset({".xlsx", ".xlsm", ".xls"})
_CSV_ENCODINGS = ("utf-8-sig", "cp1252")

# Apelidos comuns nas planilhas de vendas -> coluna canônica.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    Col.CNPJ: ("cnpj", "cnpj_empresa", "documento", "cpf_cnpj", "cnpj_cpf", "tax_id"),
    Col.EMAIL: ("email", "e_mail", "mail", "email_contato", "endereco_de_email"),
    Col.TELEFONE: (
        "telefone",
        "fone",
        "tel",
        "celular",
        "whatsapp",
        "phone",
        "telefone_contato",
        "numero",
    ),
    Col.NOME: ("nome", "contato", "nome_contato", "nome_do_contato", "responsavel", "name"),
    Col.EMPRESA: ("empresa", "nome_empresa", "razao_social", "company", "organizacao", "cliente"),
}

IDENTIFIER_COLUMNS = (Col.CNPJ, Col.EMAIL, Col.TELEFONE)


class LeadsFileError(ValueError):
    """A planilha de entrada não pode ser lida ou não tem as colunas mínimas."""


def slugify(name: object) -> str:
    """``"E-mail do Contato "`` -> ``"e_mail_do_contato"``."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def read_leads(path: str | Path) -> pd.DataFrame:
    """Lê a planilha e devolve um DataFrame com colunas canônicas e a coluna ``linha``."""
    path = Path(path)
    if not path.is_file():
        raise LeadsFileError(f"Arquivo não encontrado: {path}")

    suffix = path.suffix.lower()
    if suffix in CSV_EXTENSIONS:
        raw = _read_csv(path)
    elif suffix in EXCEL_EXTENSIONS:
        raw = pd.read_excel(path, dtype=str)
    else:
        raise LeadsFileError(f"Formato não suportado: {suffix!r} (use CSV ou Excel)")

    raw = raw.dropna(how="all")
    if raw.empty:
        raise LeadsFileError(f"A planilha {path.name} não tem nenhum lead")

    df = standardize_columns(raw)
    logger.info("%d leads lidos de %s", len(df), path.name)
    return df


def _read_csv(path: Path) -> pd.DataFrame:
    for encoding in _CSV_ENCODINGS:
        try:
            sample = path.read_text(encoding=encoding)[:4096]
        except UnicodeDecodeError:
            continue
        separator = _sniff_separator(sample)
        return pd.read_csv(path, sep=separator, dtype=str, encoding=encoding)
    raise LeadsFileError(f"Não foi possível identificar a codificação de {path.name}")


def _sniff_separator(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def standardize_columns(raw: pd.DataFrame) -> pd.DataFrame:
    """Renomeia apelidos para as colunas canônicas e garante que todas existam.

    Colunas desconhecidas são preservadas com o nome original. A coluna ``linha``
    guarda a linha do lead na planilha original (o cabeçalho é a linha 1).
    """
    renames: dict[str, str] = {}
    for column in raw.columns:
        slug = slugify(column)
        for canonical, aliases in COLUMN_ALIASES.items():
            if slug in aliases and canonical not in renames.values():
                renames[column] = canonical
                break

    df = raw.rename(columns=renames).reset_index(drop=True)
    if not any(col in df.columns for col in IDENTIFIER_COLUMNS):
        raise LeadsFileError(
            "A planilha precisa de pelo menos uma coluna de CNPJ, e-mail ou telefone. "
            f"Colunas encontradas: {list(raw.columns)}"
        )

    for canonical in COLUMN_ALIASES:
        if canonical not in df.columns:
            df[canonical] = None

    df.insert(0, Col.LINHA, df.index + 2)
    ordered = [Col.LINHA, *COLUMN_ALIASES]
    extras = [c for c in df.columns if c not in ordered]
    return df[ordered + extras]
