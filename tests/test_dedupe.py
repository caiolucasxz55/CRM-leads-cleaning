from collections.abc import Callable
from typing import Any

import pandas as pd

from leads.dedupe import deduplicate, group_duplicates
from leads.models import Col, LeadStatus
from tests.conftest import CNPJ_BB, CNPJ_MAGALU, CNPJ_PETROBRAS

MakeLeads = Callable[[list[dict[str, Any]]], pd.DataFrame]


def test_agrupa_pelo_cnpj_mesmo_com_formatacao_diferente(make_leads: MakeLeads) -> None:
    df = make_leads(
        [
            {"cnpj": "33.000.167/0001-01", "nome": "Ana"},
            {"cnpj": CNPJ_MAGALU},
            {"cnpj": "33000167000101", "nome": "Bruno"},
        ]
    )

    assert group_duplicates(df) == [[0, 2]]


def test_sem_cnpj_agrupa_por_email_ou_telefone(make_leads: MakeLeads) -> None:
    df = make_leads(
        [
            {"email": "ana@acme.com"},
            {"email": "ANA@acme.com ", "telefone": "11 98765-4321"},
            {"telefone": "+55 (11) 98765-4321"},
            {"email": "outra@acme.com"},
        ]
    )

    assert group_duplicates(df) == [[0, 1, 2]]


def test_lead_sem_cnpj_se_junta_ao_lead_com_cnpj(make_leads: MakeLeads) -> None:
    df = make_leads([{"email": "ana@acme.com"}, {"cnpj": CNPJ_BB, "email": "ana@acme.com"}])

    assert group_duplicates(df) == [[0, 1]]


def test_nunca_junta_cnpjs_diferentes(make_leads: MakeLeads) -> None:
    # O mesmo escritório de contabilidade atende as duas empresas.
    df = make_leads(
        [
            {"cnpj": CNPJ_BB, "email": "contador@escritorio.com"},
            {"cnpj": CNPJ_PETROBRAS, "email": "contador@escritorio.com"},
        ]
    )

    assert group_duplicates(df) == []


def test_nao_junta_cnpjs_diferentes_por_ponte_transitiva(make_leads: MakeLeads) -> None:
    df = make_leads(
        [
            {"cnpj": CNPJ_BB, "email": "a@x.com"},
            {"email": "a@x.com", "telefone": "11 98765-4321"},
            {"cnpj": CNPJ_PETROBRAS, "telefone": "11 98765-4321"},
        ]
    )

    groups = group_duplicates(df)

    assert groups == [[0, 1]]


def test_leads_sem_identificador_nao_sao_duplicados(make_leads: MakeLeads) -> None:
    df = make_leads([{"email": None, "nome": "Ana"}, {"email": None, "nome": "Ana"}])

    assert group_duplicates(df) == []


class TestDeduplicate:
    def test_marca_duplicados_apontando_para_o_principal(self, make_leads: MakeLeads) -> None:
        df = make_leads([{"cnpj": CNPJ_BB}, {"cnpj": CNPJ_MAGALU}, {"cnpj": CNPJ_BB}])

        out = deduplicate(df)

        assert out[Col.STATUS].tolist() == [None, None, LeadStatus.DUPLICADO]
        assert out.iloc[2][Col.DUPLICADO_DE] == 2  # linha 2 da planilha
        assert out.iloc[2][Col.DETALHE] == "duplicado da linha 2"

    def test_prefere_como_principal_o_lead_com_cnpj(self, make_leads: MakeLeads) -> None:
        df = make_leads([{"email": "ana@acme.com"}, {"cnpj": CNPJ_BB, "email": "ana@acme.com"}])

        out = deduplicate(df)

        assert out.iloc[0][Col.STATUS] == LeadStatus.DUPLICADO
        assert out.iloc[0][Col.DUPLICADO_DE] == 3
        assert out.iloc[1][Col.STATUS] is None

    def test_prefere_o_lead_mais_completo(self, make_leads: MakeLeads) -> None:
        df = make_leads(
            [
                {"cnpj": CNPJ_BB},
                {"cnpj": CNPJ_BB, "nome": "Ana", "email": "ana@bb.com.br"},
            ]
        )

        out = deduplicate(df)

        assert out[Col.STATUS].tolist() == [LeadStatus.DUPLICADO, None]

    def test_preenche_campos_vazios_do_principal(self, make_leads: MakeLeads) -> None:
        df = make_leads(
            [
                {"cnpj": CNPJ_BB, "nome": "Ana", "email": "ana@bb.com.br", "telefone": None},
                {"cnpj": CNPJ_BB, "nome": "Outro nome", "telefone": "61 3493-9002"},
            ]
        )

        master = deduplicate(df).iloc[0]

        assert master[Col.NOME] == "Ana"  # não sobrescreve o que já existe
        assert master[Col.TELEFONE] == "+556134939002"

    def test_duplicado_com_cnpj_invalido_vira_duplicado(self, make_leads: MakeLeads) -> None:
        df = make_leads(
            [
                {"cnpj": CNPJ_BB, "email": "ana@bb.com.br"},
                {"cnpj": "00.000.000/0001-00", "email": "ana@bb.com.br"},
            ]
        )

        out = deduplicate(df)

        assert out.iloc[1][Col.STATUS] == LeadStatus.DUPLICADO
        assert "CNPJ" in out.iloc[1][Col.DETALHE]
        assert "duplicado da linha 2" in out.iloc[1][Col.DETALHE]

    def test_nao_remove_nenhum_lead(self, make_leads: MakeLeads) -> None:
        df = make_leads([{"cnpj": CNPJ_BB}] * 5)

        out = deduplicate(df)

        assert len(out) == 5
        assert (out[Col.STATUS] == LeadStatus.DUPLICADO).sum() == 4
