import math

import pytest

from leads.normalize import (
    clean_cnpj,
    cnpj_check_digits,
    format_cnpj,
    is_valid_cnpj,
    normalize_email,
    normalize_phone,
    to_text,
)


class TestToText:
    @pytest.mark.parametrize("value", [None, math.nan, "", "   ", "nan", "NULL", "-"])
    def test_vazios_viram_none(self, value: object) -> None:
        assert to_text(value) is None

    def test_colapsa_espacos(self) -> None:
        assert to_text("  Acme   Ltda  ") == "Acme Ltda"

    def test_remove_sufixo_de_float_do_excel(self) -> None:
        assert to_text(191.0) == "191"
        assert to_text("11987654321.0") == "11987654321"


class TestCleanCnpj:
    def test_remove_pontuacao(self) -> None:
        assert clean_cnpj("33.000.167/0001-01") == "33000167000101"

    def test_recupera_zeros_a_esquerda(self) -> None:
        assert clean_cnpj(191) == "00000000000191"
        assert clean_cnpj("191.0") == "00000000000191"

    def test_converte_alfanumerico_para_maiusculas(self) -> None:
        assert clean_cnpj("12.abc.345/01de-35") == "12ABC34501DE35"

    def test_vazio(self) -> None:
        assert clean_cnpj(None) is None
        assert clean_cnpj("  ") is None


class TestIsValidCnpj:
    @pytest.mark.parametrize(
        "cnpj",
        [
            "00.000.000/0001-91",  # Banco do Brasil
            "33.000.167/0001-01",  # Petrobras
            "47960950000121",  # Magazine Luiza
            191,  # Banco do Brasil lido como número pelo Excel
            "12.ABC.345/01DE-35",  # exemplo oficial de CNPJ alfanumérico
        ],
    )
    def test_cnpjs_validos(self, cnpj: object) -> None:
        assert is_valid_cnpj(cnpj)

    @pytest.mark.parametrize(
        "cnpj",
        [
            "33.000.167/0001-02",  # dígito verificador errado
            "11.111.111/1111-11",  # todos os dígitos iguais
            "00000000000000",
            "123",  # curto demais (vira 00000000000123, DV inválido)
            "330001670001011",  # 15 dígitos
            "12.ABC.345/01DE-3A",  # DV alfanumérico não é permitido
            "abc",
            None,
        ],
    )
    def test_cnpjs_invalidos(self, cnpj: object) -> None:
        assert not is_valid_cnpj(cnpj)

    def test_calcula_digitos_verificadores(self) -> None:
        assert cnpj_check_digits("330001670001") == "01"
        assert cnpj_check_digits("12ABC34501DE") == "35"


class TestFormatCnpj:
    def test_formata(self) -> None:
        assert format_cnpj("33000167000101") == "33.000.167/0001-01"

    def test_tamanho_errado(self) -> None:
        with pytest.raises(ValueError):
            format_cnpj("123")


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Joao.Silva@Exemplo.COM.br ", "joao.silva@exemplo.com.br"),
            ("mailto:contato@acme.com", "contato@acme.com"),
            ("<vendas@acme.com>;", "vendas@acme.com"),
            ("nome+tag@acme.io", "nome+tag@acme.io"),
        ],
    )
    def test_normaliza(self, raw: str, expected: str) -> None:
        assert normalize_email(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["sem-arroba.com", "a@b", "a@@b.com", "a..b@acme.com", "@acme.com", None, ""]
    )
    def test_invalidos(self, raw: object) -> None:
        assert normalize_email(raw) is None


class TestNormalizePhone:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("(11) 98765-4321", "+5511987654321"),
            ("+55 11 98765-4321", "+5511987654321"),
            ("011 98765 4321", "+5511987654321"),
            ("11 3222-1111", "+551132221111"),
            ("5521 2555-0000", "+552125550000"),
            (11987654321, "+5511987654321"),
            (11987654321.0, "+5511987654321"),
            ("55 99999-1234", "+5555999991234"),  # DDD 55 (RS), celular
        ],
    )
    def test_normaliza(self, raw: object, expected: str) -> None:
        assert normalize_phone(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "98765-4321",  # sem DDD
            "(10) 98765-4321",  # DDD inexistente
            "(11) 88765-4321",  # celular sem o 9
            "(11) 0222-1111",  # fixo começando com 0
            "123",
            "telefone",
            None,
        ],
    )
    def test_invalidos(self, raw: object) -> None:
        assert normalize_phone(raw) is None
