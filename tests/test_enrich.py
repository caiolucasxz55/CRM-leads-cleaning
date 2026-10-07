from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd
import pytest

from leads.cache import CnpjCache
from leads.enrich import (
    BrasilApiProvider,
    CnpjEnricher,
    CnpjLookupError,
    LookupOutcome,
    RetryPolicy,
    enrich_leads,
    format_cnae,
)
from leads.models import Col, LeadStatus
from tests.conftest import CNPJ_PETROBRAS, load_fixture
from tests.fakes import CNPJ_INEXISTENTE, CNPJ_SADIA, FakeApi, make_provider

MakeLeads = Callable[[list[dict[str, Any]]], pd.DataFrame]


class TestBrasilApiProvider:
    def test_parse_da_resposta_real(self) -> None:
        provider = make_provider(FakeApi())

        company = provider.parse(CNPJ_PETROBRAS, load_fixture("brasilapi_ativa.json"))

        assert company.razao_social == "PETROLEO BRASILEIRO S A PETROBRAS"
        assert company.cnae_principal == "0600-0/01"
        assert company.cnae_descricao == "Extração de petróleo e gás natural"
        assert company.cidade == "RIO DE JANEIRO"
        assert company.uf == "RJ"
        assert company.porte == "DEMAIS"
        assert company.ativa

    def test_parse_empresa_baixada(self) -> None:
        provider = make_provider(FakeApi())

        company = provider.parse(CNPJ_SADIA, load_fixture("brasilapi_baixada.json"))

        assert not company.ativa
        assert company.situacao_cadastral == "BAIXADA"
        assert company.motivo_situacao == "INCORPORACAO"
        assert company.nome_fantasia is None  # vem como "" da API

    def test_retry_com_espera_crescente_em_erro_5xx(self) -> None:
        api = FakeApi([httpx.Response(503), httpx.Response(502)])
        sleeps: list[float] = []
        provider = make_provider(api, sleeps, retry=RetryPolicy(base_delay=1, factor=2))

        response = provider.fetch(CNPJ_PETROBRAS)

        assert response.status_code == 200
        assert len(api.calls) == 3
        assert sleeps == [1, 2]

    def test_respeita_retry_after_no_429(self) -> None:
        api = FakeApi([httpx.Response(429, headers={"Retry-After": "7"})])
        sleeps: list[float] = []
        provider = make_provider(api, sleeps)

        provider.fetch(CNPJ_PETROBRAS)

        assert sleeps == [7]

    def test_retry_em_falha_de_rede(self) -> None:
        calls = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectTimeout("timeout", request=request)
            return FakeApi()(request)

        http = httpx.Client(transport=httpx.MockTransport(flaky), base_url="https://x/")
        provider = BrasilApiProvider(http, sleep=lambda _: None)

        assert provider.fetch(CNPJ_PETROBRAS).status_code == 200

    def test_desiste_apos_o_limite_de_tentativas(self) -> None:
        api = FakeApi([httpx.Response(500)] * 10)
        sleeps: list[float] = []
        provider = make_provider(
            api, sleeps, retry=RetryPolicy(max_attempts=3, base_delay=1, max_delay=1.5)
        )

        with pytest.raises(CnpjLookupError, match="HTTP 500 após 3 tentativas"):
            provider.fetch(CNPJ_PETROBRAS)
        assert sleeps == [1, 1.5]  # limitado por max_delay

    def test_erro_nao_recuperavel_nao_faz_retry(self) -> None:
        api = FakeApi([httpx.Response(401)])
        provider = make_provider(api)

        with pytest.raises(CnpjLookupError, match="HTTP 401"):
            provider.fetch(CNPJ_PETROBRAS)
        assert len(api.calls) == 1

    def test_intervalo_minimo_entre_requisicoes(self) -> None:
        # fim da 1ª requisição em 0.0s; a 2ª começa em 0.2s, então espera mais 0.3s
        clock = iter([0.0, 0.2, 0.5])
        sleeps: list[float] = []
        provider = make_provider(FakeApi(), sleeps, min_interval=0.5, monotonic=lambda: next(clock))

        provider.fetch(CNPJ_PETROBRAS)
        provider.fetch(CNPJ_PETROBRAS)

        assert sleeps == [pytest.approx(0.3)]


class TestCnpjEnricher:
    def test_cache_evita_segunda_consulta(self) -> None:
        api = FakeApi()
        enricher = CnpjEnricher(make_provider(api), CnpjCache())

        first = enricher.lookup(CNPJ_PETROBRAS)
        second = enricher.lookup(CNPJ_PETROBRAS)

        assert api.calls == [CNPJ_PETROBRAS]
        assert not first.from_cache
        assert second.from_cache
        assert second.company == first.company
        assert enricher.stats.api_calls == 1
        assert enricher.stats.cache_hits == 1

    def test_nao_encontrado_tambem_e_cacheado(self) -> None:
        api = FakeApi()
        enricher = CnpjEnricher(make_provider(api), CnpjCache())

        enricher.lookup(CNPJ_INEXISTENTE)
        result = enricher.lookup(CNPJ_INEXISTENTE)

        assert result.outcome is LookupOutcome.NOT_FOUND
        assert result.error == "CNPJ 99.999.999/0999-70 não encontrado."
        assert len(api.calls) == 1

    def test_erro_nao_e_cacheado(self) -> None:
        api = FakeApi([httpx.Response(500)] * 4)
        cache = CnpjCache()
        enricher = CnpjEnricher(make_provider(api), cache)

        result = enricher.lookup(CNPJ_PETROBRAS)

        assert result.outcome is LookupOutcome.ERROR
        assert len(cache) == 0
        assert enricher.lookup(CNPJ_PETROBRAS).outcome is LookupOutcome.FOUND


class TestEnrichLeads:
    def test_define_status_por_resultado_da_consulta(self, make_leads: MakeLeads) -> None:
        df = make_leads(
            [
                {"cnpj": CNPJ_PETROBRAS},
                {"cnpj": CNPJ_SADIA},
                {"cnpj": CNPJ_INEXISTENTE},
                {"cnpj": "47960950000121"},  # sem fixture: a FakeApi responde 400
            ]
        )
        enricher = CnpjEnricher(make_provider(FakeApi()))

        out = enrich_leads(df, enricher)

        assert out[Col.STATUS].tolist() == [
            LeadStatus.OK,
            LeadStatus.EMPRESA_INATIVA,
            LeadStatus.CNPJ_INVALIDO,
            LeadStatus.CNPJ_INVALIDO,
        ]
        assert out.iloc[0][Col.RAZAO_SOCIAL] == "PETROLEO BRASILEIRO S A PETROBRAS"
        assert out.iloc[1][Col.DETALHE] == "situação cadastral BAIXADA (INCORPORACAO)"
        assert "não encontrado" in out.iloc[2][Col.DETALHE]

    def test_erro_na_api_vira_erro_consulta(self, make_leads: MakeLeads) -> None:
        df = make_leads([{"cnpj": CNPJ_PETROBRAS, "nome": "Ana"}])
        enricher = CnpjEnricher(make_provider(FakeApi([httpx.Response(503)] * 4)))

        out = enrich_leads(df, enricher)

        assert out.iloc[0][Col.STATUS] == LeadStatus.ERRO_CONSULTA
        assert out.iloc[0][Col.NOME] == "Ana"  # o lead não some
        assert "HTTP 503" in out.iloc[0][Col.DETALHE]

    def test_consulta_cada_cnpj_uma_vez_e_ignora_leads_ja_resolvidos(
        self, make_leads: MakeLeads
    ) -> None:
        df = make_leads(
            [
                {"cnpj": CNPJ_PETROBRAS, "email": "a@petrobras.com.br"},
                {"cnpj": CNPJ_PETROBRAS, "email": "b@petrobras.com.br"},
                {"cnpj": "123"},  # cnpj_invalido: não consulta
                {"email": "sem-cnpj@acme.com"},  # sem CNPJ: não consulta
            ]
        )
        api = FakeApi()
        progress: list[tuple[int, int]] = []

        out = enrich_leads(
            df,
            CnpjEnricher(make_provider(api)),
            on_progress=lambda done, total: progress.append((done, total)),
        )

        assert api.calls == [CNPJ_PETROBRAS]
        assert progress == [(1, 1)]
        assert out.iloc[2][Col.STATUS] == LeadStatus.CNPJ_INVALIDO
        assert out.iloc[3][Col.STATUS] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(600001, "0600-0/01"), ("6422100", "6422-1/00"), (None, None), ("abc", "abc")],
)
def test_format_cnae(raw: object, expected: str | None) -> None:
    assert format_cnae(raw) == expected
