from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest

from leads.enrich import CnpjEnricher
from leads.export import (
    ExportError,
    HubSpotClient,
    email_domain,
    render_markdown,
    write_leads,
    write_report,
)
from leads.http import RetryPolicy
from leads.models import Col
from leads.pipeline import PipelineResult, run_pipeline
from leads.reader import standardize_columns
from tests.conftest import CNPJ_PETROBRAS
from tests.fakes import CNPJ_SADIA, FakeApi, make_provider


@pytest.fixture
def result() -> PipelineResult:
    raw = standardize_columns(
        pd.DataFrame(
            [
                {"empresa": "Petrobras", "cnpj": CNPJ_PETROBRAS, "email": "ana@petrobras.com.br"},
                {"empresa": "Petrobras", "cnpj": CNPJ_PETROBRAS},
                {"empresa": "Sadia", "cnpj": CNPJ_SADIA},
                {"empresa": "Padaria", "email": "padaria@gmail.com", "telefone": "11 3222-1111"},
            ]
        )
    )
    return run_pipeline(raw, CnpjEnricher(make_provider(FakeApi())))


class TestWriteLeads:
    def test_excel_com_tres_abas(self, result: PipelineResult, tmp_path: Path) -> None:
        path = write_leads(result, tmp_path / "saida" / "limpo.xlsx")

        sheets = pd.read_excel(path, sheet_name=None)
        assert list(sheets) == ["prontos_crm", "todos", "resumo"]
        assert len(sheets["todos"]) == 4
        assert sheets["prontos_crm"][Col.EMPRESA].tolist() == ["Petrobras", "Padaria"]
        resumo = dict(zip(sheets["resumo"]["Indicador"], sheets["resumo"]["Valor"], strict=True))
        assert resumo["Leads recebidos"] == 4
        assert resumo["Ligações evitadas"] == 2

    def test_csv_com_ponto_e_virgula(self, result: PipelineResult, tmp_path: Path) -> None:
        path = write_leads(result, tmp_path / "limpo.csv")

        content = path.read_text(encoding="utf-8-sig")
        assert content.splitlines()[0].startswith("linha;status;detalhe")
        assert len(pd.read_csv(path, sep=";")) == 4

    def test_formato_invalido(self, result: PipelineResult, tmp_path: Path) -> None:
        with pytest.raises(ExportError):
            write_leads(result, tmp_path / "limpo.parquet")


class TestRelatorio:
    def test_markdown(self, result: PipelineResult) -> None:
        md = render_markdown(result.report)

        assert "| Leads recebidos | 4 | 100% |" in md
        assert "| Empresa inativa (`empresa_inativa`) | 1 | 25% |" in md
        assert "**2 ligações evitadas**" in md

    def test_grava_json(self, result: PipelineResult, tmp_path: Path) -> None:
        path = write_report(result.report, tmp_path / "relatorio.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["total"] == 4
        assert data["por_status"]["duplicado"] == 1
        assert data["ligacoes_evitadas"] == 2

    def test_grava_markdown(self, result: PipelineResult, tmp_path: Path) -> None:
        path = write_report(result.report, tmp_path / "relatorio.md")

        assert path.read_text(encoding="utf-8").startswith("# Relatório")

    def test_formato_invalido(self, result: PipelineResult, tmp_path: Path) -> None:
        with pytest.raises(ExportError):
            write_report(result.report, tmp_path / "relatorio.txt")


class FakeHubSpot:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self._responses = list(responses or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        if self._responses:
            return self._responses.pop(0)
        return httpx.Response(201, json={"status": "COMPLETE", "results": body["inputs"]})


def make_hubspot(fake: FakeHubSpot, **kwargs: Any) -> HubSpotClient:
    http = httpx.Client(transport=httpx.MockTransport(fake), base_url=HubSpotClient.BASE_URL)
    return HubSpotClient("token-teste", http=http, sleep=lambda _: None, **kwargs)


class TestHubSpot:
    def test_envia_apenas_leads_ok(self, result: PipelineResult) -> None:
        fake = FakeHubSpot()

        sync = make_hubspot(fake).push_companies(result.leads)

        assert sync.created == 2
        assert sync.failed == 0
        inputs = fake.requests[0]["inputs"]
        assert [i["properties"]["name"] for i in inputs] == [
            "PETROLEO BRASILEIRO S A PETROBRAS",
            "Padaria",
        ]
        assert fake.headers[0]["Authorization"] == "Bearer token-teste"

    def test_mapeia_propriedades(self, result: PipelineResult) -> None:
        client = make_hubspot(FakeHubSpot(), cnpj_property="cnpj")

        props = client.to_properties(result.ready_for_crm.iloc[0])

        assert props == {
            "name": "PETROLEO BRASILEIRO S A PETROBRAS",
            "city": "RIO DE JANEIRO",
            "state": "RJ",
            "domain": "petrobras.com.br",
            "description": "CNAE 0600-0/01 - Extração de petróleo e gás natural",
            "cnpj": "33.000.167/0001-01",
        }

    def test_divide_em_lotes(self, result: PipelineResult) -> None:
        fake = FakeHubSpot()

        make_hubspot(fake, batch_size=1).push_companies(result.leads)

        assert len(fake.requests) == 2

    def test_sucesso_parcial(self, result: PipelineResult) -> None:
        partial = httpx.Response(
            207,
            json={"results": [{"id": "1"}], "errors": [{"message": "Property invalid"}]},
        )

        sync = make_hubspot(FakeHubSpot([partial])).push_companies(result.leads)

        assert (sync.created, sync.failed) == (1, 1)
        assert sync.errors == ["Property invalid"]

    def test_erro_de_autenticacao_nao_derruba(self, result: PipelineResult) -> None:
        fake = FakeHubSpot([httpx.Response(401, text="unauthorized")])

        sync = make_hubspot(fake).push_companies(result.leads)

        assert (sync.created, sync.failed) == (0, 2)
        assert sync.errors[0].startswith("HTTP 401")

    def test_retry_quando_limitado(self, result: PipelineResult) -> None:
        fake = FakeHubSpot([httpx.Response(429), httpx.Response(429)])

        sync = make_hubspot(fake).push_companies(result.leads)

        assert sync.created == 2
        assert len(fake.requests) == 3

    def test_desiste_apos_retries(self, result: PipelineResult) -> None:
        fake = FakeHubSpot([httpx.Response(503)] * 2)

        sync = make_hubspot(fake, retry=RetryPolicy(max_attempts=2)).push_companies(result.leads)

        assert sync.failed == 2
        assert "HTTP 503" in sync.errors[0]

    def test_token_obrigatorio(self) -> None:
        with pytest.raises(ValueError, match="Token"):
            HubSpotClient("")


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("ana@petrobras.com.br", "petrobras.com.br"),
        ("ana@gmail.com", None),
        (None, None),
        ("invalido", None),
    ],
)
def test_email_domain(email: str | None, expected: str | None) -> None:
    assert email_domain(email) == expected
