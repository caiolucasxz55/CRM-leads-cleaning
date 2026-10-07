"""Normalização de CNPJ, e-mail, telefone e texto livre.

Todas as funções aceitam qualquer valor vindo de uma célula de planilha (str, int,
float, NaN, None) e devolvem ``None`` quando não há nada aproveitável.
"""

from __future__ import annotations

import re

import pandas as pd

# Pesos oficiais do cálculo do dígito verificador do CNPJ.
_CNPJ_WEIGHTS_DV1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_CNPJ_WEIGHTS_DV2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)

# Desde jul/2026 o CNPJ pode ser alfanumérico: 12 posições [0-9A-Z] + 2 dígitos verificadores.
_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_CNPJ_SEPARATORS_RE = re.compile(r"[\s./\-]")
_FLOAT_SUFFIX_RE = re.compile(r"^(\d+)\.0+$")

_EMAIL_RE = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$"
)

# DDDs em uso no Brasil (Anatel).
_VALID_DDDS = frozenset(
    {
        11, 12, 13, 14, 15, 16, 17, 18, 19,
        21, 22, 24, 27, 28,
        31, 32, 33, 34, 35, 37, 38,
        41, 42, 43, 44, 45, 46, 47, 48, 49,
        51, 53, 54, 55,
        61, 62, 63, 64, 65, 66, 67, 68, 69,
        71, 73, 74, 75, 77, 79,
        81, 82, 83, 84, 85, 86, 87, 88, 89,
        91, 92, 93, 94, 95, 96, 97, 98, 99,
    }
)  # fmt: skip

_COUNTRY_CODE = "55"


def to_text(value: object) -> str | None:
    """Converte o valor de uma célula em texto sem espaços extras, ou ``None`` se vazio."""
    if value is None:
        return None
    if not isinstance(value, str) and pd.isna(value):  # type: ignore[call-overload]
        return None
    text = " ".join(str(value).split())
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None
    match = _FLOAT_SUFFIX_RE.match(text)
    return match.group(1) if match else text


# ---------------------------------------------------------------------------
# CNPJ
# ---------------------------------------------------------------------------


def clean_cnpj(value: object) -> str | None:
    """Remove pontuação do CNPJ e recupera zeros à esquerda perdidos pelo Excel.

    Não valida: um CNPJ "limpo" ainda pode ser inválido. Use :func:`is_valid_cnpj`.
    """
    text = to_text(value)
    if text is None:
        return None
    cleaned = _CNPJ_SEPARATORS_RE.sub("", text).upper()
    if cleaned.isdigit() and len(cleaned) < 14:
        cleaned = cleaned.zfill(14)
    return cleaned or None


def _char_value(char: str) -> int:
    # Regra da Receita para o CNPJ alfanumérico: valor = código ASCII - 48.
    # Para dígitos isso coincide com o valor numérico ('0' -> 0, 'A' -> 17).
    return ord(char) - 48


def _check_digit(base: str, weights: tuple[int, ...]) -> str:
    total = sum(_char_value(c) * w for c, w in zip(base, weights, strict=True))
    remainder = total % 11
    return "0" if remainder < 2 else str(11 - remainder)


def cnpj_check_digits(base12: str) -> str:
    """Calcula os dois dígitos verificadores para as 12 primeiras posições do CNPJ."""
    dv1 = _check_digit(base12, _CNPJ_WEIGHTS_DV1)
    dv2 = _check_digit(base12 + dv1, _CNPJ_WEIGHTS_DV2)
    return dv1 + dv2


def is_valid_cnpj(value: object) -> bool:
    """Valida formato e dígitos verificadores (aceita CNPJ numérico e alfanumérico)."""
    cnpj = clean_cnpj(value)
    if cnpj is None or not _CNPJ_RE.match(cnpj):
        return False
    if len(set(cnpj)) == 1:  # 00000000000000, 11111111111111...
        return False
    return cnpj[12:] == cnpj_check_digits(cnpj[:12])


def format_cnpj(cnpj: str) -> str:
    """Formata 14 caracteres como ``XX.XXX.XXX/XXXX-XX``."""
    if len(cnpj) != 14:
        raise ValueError(f"CNPJ deve ter 14 caracteres, recebido: {cnpj!r}")
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------


def normalize_email(value: object) -> str | None:
    """Padroniza o e-mail em minúsculas; retorna ``None`` se for inválido."""
    text = to_text(value)
    if text is None:
        return None
    email = text.strip().lower().removeprefix("mailto:").replace(" ", "")
    email = email.strip("<>;,")
    return email if _EMAIL_RE.match(email) else None


# ---------------------------------------------------------------------------
# Telefone
# ---------------------------------------------------------------------------


def normalize_phone(value: object) -> str | None:
    """Padroniza telefones brasileiros no formato E.164 (``+5511987654321``).

    Aceita fixo (10 dígitos) e celular (11 dígitos, começando com 9), com ou sem
    código do país e zero de operadora. Retorna ``None`` se não for um número válido.
    """
    text = to_text(value)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if digits.startswith(_COUNTRY_CODE) and len(digits) in (12, 13):
        digits = digits[len(_COUNTRY_CODE) :]
    digits = digits.lstrip("0")

    if len(digits) not in (10, 11):
        return None
    if int(digits[:2]) not in _VALID_DDDS:
        return None
    subscriber = digits[2:]
    if len(subscriber) == 9 and subscriber[0] != "9":
        return None
    if len(subscriber) == 8 and subscriber[0] in "01":
        return None
    return f"+{_COUNTRY_CODE}{digits}"
