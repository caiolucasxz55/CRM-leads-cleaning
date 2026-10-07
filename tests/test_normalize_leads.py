from collections.abc import Callable
from typing import Any

import pandas as pd

from leads.frame import CNPJ_KEY
from leads.models import Col, LeadStatus
from leads.normalize import normalize_leads
from leads.reader import standardize_columns

MakeLeads = Callable[[list[dict[str, Any]]], pd.DataFrame]


def test_formata_cnpj_valido_e_guarda_chave(make_leads: MakeLeads) -> None:
    df = make_leads([{"cnpj": "33000167000101"}])

    row = df.iloc[0]
    assert row[Col.CNPJ] == "33.000.167/0001-01"
    assert row[CNPJ_KEY] == "33000167000101"
    assert row[Col.STATUS] is None
    assert row[Col.DETALHE] == ""


def test_marca_cnpj_invalido_mantendo_valor_original(make_leads: MakeLeads) -> None:
    df = make_leads([{"cnpj": "33.000.167/0001-02"}])

    row = df.iloc[0]
    assert row[Col.STATUS] == LeadStatus.CNPJ_INVALIDO
    assert row[Col.CNPJ] == "33.000.167/0001-02"
    assert pd.isna(row[CNPJ_KEY])
    assert "CNPJ" in row[Col.DETALHE]


def test_lead_sem_cnpj_nao_e_invalido(make_leads: MakeLeads) -> None:
    df = make_leads([{"cnpj": None, "email": "a@acme.com"}])

    assert df.iloc[0][Col.STATUS] is None


def test_descarta_email_e_telefone_invalidos_anotando_no_detalhe(make_leads: MakeLeads) -> None:
    df = make_leads([{"email": "nao-e-email", "telefone": "123"}])

    row = df.iloc[0]
    assert pd.isna(row[Col.EMAIL])
    assert pd.isna(row[Col.TELEFONE])
    assert row[Col.DETALHE] == (
        "e-mail inválido descartado: nao-e-email; telefone inválido descartado: 123"
    )


def test_padroniza_email_telefone_e_texto(make_leads: MakeLeads) -> None:
    df = make_leads(
        [{"email": " Ana@ACME.com ", "telefone": "(11) 98765-4321", "empresa": "  Acme   SA "}]
    )

    row = df.iloc[0]
    assert row[Col.EMAIL] == "ana@acme.com"
    assert row[Col.TELEFONE] == "+5511987654321"
    assert row[Col.EMPRESA] == "Acme SA"


def test_nao_altera_o_dataframe_original() -> None:
    original = standardize_columns(pd.DataFrame([{"email": " A@B.COM "}]))
    normalize_leads(original)

    assert original.iloc[0][Col.EMAIL] == " A@B.COM "
