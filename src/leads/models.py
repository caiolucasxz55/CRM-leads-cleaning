"""Tipos compartilhados entre as etapas do pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LeadStatus(StrEnum):
    """O que aconteceu com cada lead ao passar pelo pipeline."""

    OK = "ok"
    DUPLICADO = "duplicado"
    CNPJ_INVALIDO = "cnpj_invalido"
    EMPRESA_INATIVA = "empresa_inativa"
    ERRO_CONSULTA = "erro_consulta"


class Col:
    """Nomes das colunas canônicas usadas no DataFrame de leads."""

    LINHA = "linha"
    NOME = "nome"
    EMPRESA = "empresa"
    CNPJ = "cnpj"
    EMAIL = "email"
    TELEFONE = "telefone"

    RAZAO_SOCIAL = "razao_social"
    NOME_FANTASIA = "nome_fantasia"
    CNAE = "cnae_principal"
    CNAE_DESCRICAO = "cnae_descricao"
    PORTE = "porte"
    CIDADE = "cidade"
    UF = "uf"
    SITUACAO = "situacao_cadastral"

    STATUS = "status"
    DETALHE = "detalhe"
    DUPLICADO_DE = "duplicado_de"


ENRICHMENT_COLUMNS: tuple[str, ...] = (
    Col.RAZAO_SOCIAL,
    Col.NOME_FANTASIA,
    Col.CNAE,
    Col.CNAE_DESCRICAO,
    Col.PORTE,
    Col.CIDADE,
    Col.UF,
    Col.SITUACAO,
)


@dataclass(frozen=True, slots=True)
class CompanyData:
    """Dados públicos de uma empresa retornados pela consulta de CNPJ."""

    cnpj: str
    razao_social: str | None
    nome_fantasia: str | None
    cnae_principal: str | None
    cnae_descricao: str | None
    porte: str | None
    cidade: str | None
    uf: str | None
    situacao_cadastral: str | None
    motivo_situacao: str | None = None

    @property
    def ativa(self) -> bool:
        return (self.situacao_cadastral or "").strip().upper() == "ATIVA"

    def as_columns(self) -> dict[str, str | None]:
        return {
            Col.RAZAO_SOCIAL: self.razao_social,
            Col.NOME_FANTASIA: self.nome_fantasia,
            Col.CNAE: self.cnae_principal,
            Col.CNAE_DESCRICAO: self.cnae_descricao,
            Col.PORTE: self.porte,
            Col.CIDADE: self.cidade,
            Col.UF: self.uf,
            Col.SITUACAO: self.situacao_cadastral,
        }
