"""Enriquecimento dos leads com dados públicos do CNPJ.

* :class:`BrasilApiProvider` consulta a BrasilAPI com retry e espera crescente.
* :class:`CnpjEnricher` coloca o cache na frente do provider e conta as consultas.
* :func:`enrich_leads` aplica o resultado no DataFrame e define o status de cada lead.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import httpx
import pandas as pd

from leads.cache import CnpjCache
from leads.frame import CNPJ_KEY, append_detail
from leads.http import RetryExhaustedError, RetryPolicy, send_with_retry
from leads.models import ENRICHMENT_COLUMNS, Col, CompanyData, LeadStatus

__all__ = [
    "BrasilApiProvider",
    "CnpjEnricher",
    "CnpjLookupError",
    "CnpjProvider",
    "LookupOutcome",
    "LookupResult",
    "RetryPolicy",
    "enrich_leads",
    "format_cnae",
]

logger = logging.getLogger(__name__)

DEFINITIVE_STATUS = frozenset({200, 400, 404})


class CnpjLookupError(Exception):
    """A consulta falhou mesmo após as novas tentativas."""


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status_code: int
    payload: dict[str, Any]


class CnpjProvider(Protocol):
    """Fonte de dados de CNPJ. Implemente para usar outra API (ReceitaWS, CNPJá...)."""

    name: str

    def fetch(self, cnpj: str) -> ApiResponse:
        """Retorna uma resposta definitiva (200, 400 ou 404) ou levanta CnpjLookupError."""
        ...

    def parse(self, cnpj: str, payload: dict[str, Any]) -> CompanyData: ...


# ---------------------------------------------------------------------------
# BrasilAPI
# ---------------------------------------------------------------------------


class BrasilApiProvider:
    """Cliente da API de CNPJ da BrasilAPI (https://brasilapi.com.br/docs#tag/CNPJ)."""

    name = "brasilapi"
    BASE_URL = "https://brasilapi.com.br/api/cnpj/v1/"

    def __init__(
        self,
        http: httpx.Client | None = None,
        *,
        retry: RetryPolicy | None = None,
        min_interval: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http or httpx.Client(
            base_url=self.BASE_URL,
            timeout=httpx.Timeout(15.0),
            headers={"User-Agent": "leads-cnpj/0.1 (+https://github.com/caiolucasxz55)"},
        )
        self._retry = retry or RetryPolicy()
        self._min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request: float | None = None

    def close(self) -> None:
        self._http.close()

    def fetch(self, cnpj: str) -> ApiResponse:
        try:
            response = send_with_retry(
                lambda: self._request(cnpj),
                policy=self._retry,
                sleep=self._sleep,
                description=f"Consulta do CNPJ {cnpj}",
            )
        except RetryExhaustedError as exc:
            raise CnpjLookupError(str(exc)) from exc
        if response.status_code not in DEFINITIVE_STATUS:
            raise CnpjLookupError(f"resposta inesperada da API: HTTP {response.status_code}")
        return ApiResponse(response.status_code, _json_or_empty(response))

    def _request(self, cnpj: str) -> httpx.Response:
        if self._min_interval and self._last_request is not None:
            elapsed = self._monotonic() - self._last_request
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
        try:
            return self._http.get(cnpj)
        finally:
            self._last_request = self._monotonic()

    def parse(self, cnpj: str, payload: dict[str, Any]) -> CompanyData:
        return CompanyData(
            cnpj=cnpj,
            razao_social=_text(payload.get("razao_social")),
            nome_fantasia=_text(payload.get("nome_fantasia")),
            cnae_principal=format_cnae(payload.get("cnae_fiscal")),
            cnae_descricao=_text(payload.get("cnae_fiscal_descricao")),
            porte=_text(payload.get("porte")),
            cidade=_text(payload.get("municipio")),
            uf=_text(payload.get("uf")),
            situacao_cadastral=_text(payload.get("descricao_situacao_cadastral")),
            motivo_situacao=_text(payload.get("descricao_motivo_situacao_cadastral")),
        )


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def format_cnae(value: object) -> str | None:
    """``600001`` -> ``"0600-0/01"`` (formato oficial da subclasse CNAE)."""
    text = _text(value)
    if text is None or not text.isdigit():
        return text
    code = text.zfill(7)
    return f"{code[:4]}-{code[4]}/{code[5:]}"


# ---------------------------------------------------------------------------
# Enriquecimento com cache
# ---------------------------------------------------------------------------


class LookupOutcome(Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class LookupResult:
    cnpj: str
    outcome: LookupOutcome
    company: CompanyData | None = None
    error: str | None = None
    from_cache: bool = False


@dataclass(slots=True)
class EnrichmentStats:
    lookups: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    errors: int = 0
    seconds_in_api: float = 0.0


class CnpjEnricher:
    """Consulta CNPJs passando pelo cache. Cada CNPJ é consultado no máximo uma vez."""

    def __init__(self, provider: CnpjProvider, cache: CnpjCache | None = None) -> None:
        self._provider = provider
        self._cache = cache
        self.stats = EnrichmentStats()

    def lookup(self, cnpj: str) -> LookupResult:
        self.stats.lookups += 1
        cached = self._cache.get(self._provider.name, cnpj) if self._cache else None
        if cached is not None:
            self.stats.cache_hits += 1
            return self._to_result(cnpj, ApiResponse(cached.status_code, cached.payload), True)

        self.stats.api_calls += 1
        started = time.perf_counter()
        try:
            response = self._provider.fetch(cnpj)
        except CnpjLookupError as exc:
            self.stats.errors += 1
            logger.error("Não foi possível consultar o CNPJ %s: %s", cnpj, exc)
            return LookupResult(cnpj, LookupOutcome.ERROR, error=str(exc))
        finally:
            self.stats.seconds_in_api += time.perf_counter() - started

        if self._cache is not None:
            self._cache.set(self._provider.name, cnpj, response.status_code, response.payload)
        return self._to_result(cnpj, response, False)

    def _to_result(self, cnpj: str, response: ApiResponse, from_cache: bool) -> LookupResult:
        if response.status_code == 200:
            company = self._provider.parse(cnpj, response.payload)
            return LookupResult(cnpj, LookupOutcome.FOUND, company=company, from_cache=from_cache)
        message = _text(response.payload.get("message")) or f"HTTP {response.status_code}"
        return LookupResult(cnpj, LookupOutcome.NOT_FOUND, error=message, from_cache=from_cache)


def enrich_leads(
    df: pd.DataFrame,
    enricher: CnpjEnricher,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    """Consulta os CNPJs dos leads ainda sem status e define ``ok``, ``empresa_inativa``,
    ``cnpj_invalido`` (não encontrado na Receita) ou ``erro_consulta``."""
    out = df.copy()
    for column in ENRICHMENT_COLUMNS:
        if column not in out.columns:
            out[column] = None
    pending = out[Col.STATUS].isna() & out[CNPJ_KEY].notna()
    unique_cnpjs = list(dict.fromkeys(out.loc[pending, CNPJ_KEY]))

    results: dict[str, LookupResult] = {}
    for done, cnpj in enumerate(unique_cnpjs, start=1):
        results[cnpj] = enricher.lookup(cnpj)
        if on_progress is not None:
            on_progress(done, len(unique_cnpjs))

    details = pd.Series("", index=out.index, dtype=object)
    for idx in out.index[pending]:
        result = results[str(out.at[idx, CNPJ_KEY])]
        status, detail = _status_for(result)
        if result.company is not None:
            for column, value in result.company.as_columns().items():
                out.at[idx, column] = value
        out.at[idx, Col.STATUS] = status.value
        details.at[idx] = detail or ""
    append_detail(out, details != "", details)

    return out


def _status_for(result: LookupResult) -> tuple[LeadStatus, str | None]:
    company = result.company
    if result.outcome is LookupOutcome.NOT_FOUND:
        return LeadStatus.CNPJ_INVALIDO, f"CNPJ não encontrado na Receita ({result.error})"
    if result.outcome is LookupOutcome.ERROR or company is None:
        return LeadStatus.ERRO_CONSULTA, f"erro na consulta do CNPJ: {result.error}"
    if company.ativa:
        return LeadStatus.OK, None
    situacao = company.situacao_cadastral or "desconhecida"
    motivo = company.motivo_situacao
    suffix = f" ({motivo})" if motivo and motivo.upper() != "SEM MOTIVO" else ""
    return LeadStatus.EMPRESA_INATIVA, f"situação cadastral {situacao}{suffix}"
