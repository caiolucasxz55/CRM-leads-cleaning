from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

from leads import cli
from leads.cache import CnpjCache
from leads.enrich import BrasilApiProvider
from leads.export import HubSpotSyncResult
from tests.conftest import CNPJ_PETROBRAS
from tests.fakes import CNPJ_SADIA, FakeApi, make_provider

runner = CliRunner()


@pytest.fixture
def entrada(tmp_path: Path) -> Path:
    path = tmp_path / "leads.csv"
    path.write_text(
        "empresa;cnpj;email\n"
        f"Petrobras;{CNPJ_PETROBRAS};ana@petrobras.com.br\n"
        f"Petrobras;33.000.167/0001-01;\n"
        f"Sadia;{CNPJ_SADIA};\n"
        "Errado;12.345.678/0001-00;\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = FakeApi()

    def factory(**kwargs: Any) -> BrasilApiProvider:
        return make_provider(api, **kwargs)

    monkeypatch.setattr(cli, "BrasilApiProvider", factory)
    return api


def test_run_completo(entrada: Path, tmp_path: Path, fake_api: FakeApi) -> None:
    saida = tmp_path / "limpo.xlsx"
    relatorio = tmp_path / "relatorio.json"
    cache = tmp_path / "cache.sqlite"

    result = runner.invoke(
        cli.app,
        ["run", str(entrada), "-o", str(saida), "-r", str(relatorio), "--cache", str(cache)],
    )

    assert result.exit_code == 0, result.output
    assert "ligações evitadas" in result.output
    todos = pd.read_excel(saida, sheet_name="todos")
    assert todos["status"].tolist() == ["ok", "duplicado", "empresa_inativa", "cnpj_invalido"]
    report = json.loads(relatorio.read_text(encoding="utf-8"))
    assert report["ligacoes_evitadas"] == 3
    with CnpjCache(cache) as store:
        assert len(store) == 2


def test_saida_padrao_ao_lado_da_entrada(entrada: Path) -> None:
    result = runner.invoke(cli.app, ["run", str(entrada), "--sem-enriquecimento"])

    assert result.exit_code == 0, result.output
    assert (entrada.parent / "leads_limpo.xlsx").exists()


def test_sem_cache_nao_cria_arquivo(entrada: Path, tmp_path: Path, fake_api: FakeApi) -> None:
    cache = tmp_path / "cache.sqlite"

    result = runner.invoke(
        cli.app,
        ["run", str(entrada), "-o", str(tmp_path / "x.csv"), "--cache", str(cache), "--sem-cache"],
    )

    assert result.exit_code == 0, result.output
    assert not cache.exists()


def test_arquivo_inexistente(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["run", str(tmp_path / "nada.csv")])

    assert result.exit_code == 1
    assert "não encontrado" in result.output


def test_formato_de_saida_invalido(entrada: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", str(entrada), "--sem-enriquecimento", "-o", str(tmp_path / "x.pdf")]
    )

    assert result.exit_code == 1
    assert "não suportado" in result.output


def test_hubspot_exige_token(entrada: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUBSPOT_TOKEN", raising=False)

    result = runner.invoke(cli.app, ["run", str(entrada), "--hubspot"])

    assert result.exit_code == 1
    assert "HUBSPOT_TOKEN" in result.output


def test_cache_info_e_limpar(tmp_path: Path) -> None:
    cache = tmp_path / "cache.sqlite"
    with CnpjCache(cache) as store:
        store.set("brasilapi", CNPJ_PETROBRAS, 200, {})

    info = runner.invoke(cli.app, ["cache", "info", "--cache", str(cache)])
    limpar = runner.invoke(cli.app, ["cache", "limpar", "--cache", str(cache)])

    assert "1 CNPJs no cache" in info.output
    assert "1 consultas removidas" in limpar.output


def test_cache_inexistente(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["cache", "info", "--cache", str(tmp_path / "x.sqlite")])

    assert "Nenhum cache" in result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "leads-cnpj" in result.output


def test_hubspot_envia_leads_ok(
    entrada: Path, tmp_path: Path, fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[pd.DataFrame] = []

    class FakeClient:
        def __init__(self, token: str, **_: Any) -> None:
            assert token == "tok"

        def push_companies(self, leads: pd.DataFrame) -> HubSpotSyncResult:
            sent.append(leads)
            return HubSpotSyncResult(created=1, failed=1, errors=["Property invalid"])

        def close(self) -> None: ...

    monkeypatch.setattr(cli, "HubSpotClient", FakeClient)
    monkeypatch.setenv("HUBSPOT_TOKEN", "tok")

    result = runner.invoke(
        cli.app, ["run", str(entrada), "-o", str(tmp_path / "x.csv"), "--sem-cache", "--hubspot"]
    )

    assert result.exit_code == 2  # falha parcial no CRM sinaliza com código 2
    assert "1 empresas criadas, 1 falhas" in result.output
    assert len(sent) == 1
