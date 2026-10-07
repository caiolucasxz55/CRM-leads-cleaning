from pathlib import Path

import pandas as pd
import pytest

from leads.models import Col
from leads.reader import LeadsFileError, read_leads, slugify, standardize_columns


def test_slugify_remove_acentos_e_pontuacao() -> None:
    assert slugify(" E-mail do Contato ") == "e_mail_do_contato"
    assert slugify("Razão Social") == "razao_social"


class TestReadCsv:
    def test_le_csv_com_ponto_e_virgula_e_mapeia_colunas(self, tmp_path: Path) -> None:
        path = tmp_path / "leads.csv"
        path.write_text(
            "Nome do Contato;E-mail;Celular;CNPJ;Origem\n"
            "Ana;ana@acme.com;(11) 98765-4321;00.000.000/0001-91;site\n",
            encoding="utf-8",
        )

        df = read_leads(path)

        assert list(df.columns[:6]) == [
            Col.LINHA,
            Col.CNPJ,
            Col.EMAIL,
            Col.TELEFONE,
            Col.NOME,
            Col.EMPRESA,
        ]
        row = df.iloc[0]
        assert row[Col.NOME] == "Ana"
        assert row[Col.CNPJ] == "00.000.000/0001-91"  # lido como texto, sem perder zeros
        assert row["Origem"] == "site"  # coluna desconhecida é preservada
        assert row[Col.LINHA] == 2

    def test_le_csv_em_cp1252(self, tmp_path: Path) -> None:
        path = tmp_path / "leads.csv"
        path.write_bytes("empresa,email\nPadaria São João,contato@padaria.com\n".encode("cp1252"))

        df = read_leads(path)

        assert df.iloc[0][Col.EMPRESA] == "Padaria São João"

    def test_ignora_linhas_totalmente_vazias(self, tmp_path: Path) -> None:
        path = tmp_path / "leads.csv"
        path.write_text("email,telefone\na@acme.com,\n,\nb@acme.com,\n", encoding="utf-8")

        assert len(read_leads(path)) == 2


def test_le_excel(tmp_path: Path) -> None:
    path = tmp_path / "leads.xlsx"
    pd.DataFrame({"CNPJ": ["33000167000101"], "Telefone": ["11 3222-1111"]}).to_excel(
        path, index=False
    )

    df = read_leads(path)

    assert df.iloc[0][Col.CNPJ] == "33000167000101"
    assert df.iloc[0][Col.TELEFONE] == "11 3222-1111"


class TestErros:
    def test_arquivo_inexistente(self, tmp_path: Path) -> None:
        with pytest.raises(LeadsFileError, match="não encontrado"):
            read_leads(tmp_path / "nada.csv")

    def test_extensao_nao_suportada(self, tmp_path: Path) -> None:
        path = tmp_path / "leads.json"
        path.write_text("[]")
        with pytest.raises(LeadsFileError, match="Formato não suportado"):
            read_leads(path)

    def test_planilha_vazia(self, tmp_path: Path) -> None:
        path = tmp_path / "leads.csv"
        path.write_text("cnpj,email\n", encoding="utf-8")
        with pytest.raises(LeadsFileError, match="nenhum lead"):
            read_leads(path)

    def test_sem_coluna_identificadora(self) -> None:
        with pytest.raises(LeadsFileError, match="pelo menos uma coluna"):
            standardize_columns(pd.DataFrame({"nome": ["Ana"]}))


def test_primeira_coluna_com_apelido_vence() -> None:
    df = standardize_columns(pd.DataFrame({"Telefone": ["1"], "Celular": ["2"]}))

    assert df.iloc[0][Col.TELEFONE] == "1"
    assert df.iloc[0]["Celular"] == "2"


def test_cria_colunas_canonicas_ausentes() -> None:
    df = standardize_columns(pd.DataFrame({"email": ["a@acme.com"]}))

    assert df.iloc[0][Col.CNPJ] is None
    assert df.iloc[0][Col.NOME] is None
