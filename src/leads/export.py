"""Saída do pipeline: planilha limpa, relatório e envio para o CRM (HubSpot)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from leads.http import RetryExhaustedError, RetryPolicy, send_with_retry
from leads.models import Col, LeadStatus
from leads.pipeline import PipelineReport, PipelineResult

logger = logging.getLogger(__name__)

STATUS_LABELS: dict[str, str] = {
    LeadStatus.OK: "Prontos para o CRM",
    LeadStatus.DUPLICADO: "Duplicados",
    LeadStatus.CNPJ_INVALIDO: "CNPJ inválido ou inexistente",
    LeadStatus.EMPRESA_INATIVA: "Empresa inativa",
    LeadStatus.ERRO_CONSULTA: "Erro na consulta (reprocessar)",
}


class ExportError(ValueError):
    """Formato de saída não suportado."""


# ---------------------------------------------------------------------------
# Planilha
# ---------------------------------------------------------------------------


def write_leads(result: PipelineResult, path: str | Path) -> Path:
    """Grava a planilha limpa.

    * ``.xlsx``: abas ``prontos_crm`` (status ok), ``todos`` (todos os leads com status)
      e ``resumo``.
    * ``.csv``: todos os leads, separados por ``;`` e em UTF-8 com BOM (abre direto no
      Excel em português).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        result.leads.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    elif suffix == ".xlsx":
        _write_excel(result, path)
    else:
        raise ExportError(f"Formato de saída não suportado: {suffix!r} (use .xlsx ou .csv)")

    logger.info("Planilha gravada em %s", path)
    return path


def _write_excel(result: PipelineResult, path: Path) -> None:
    summary = pd.DataFrame(report_rows(result.report), columns=["Indicador", "Valor"])
    sheets = {
        "prontos_crm": result.ready_for_crm,
        "todos": result.leads,
        "resumo": summary,
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            _autofit(sheet, frame)


def _autofit(sheet: Any, frame: pd.DataFrame, max_width: int = 60) -> None:
    from openpyxl.utils import get_column_letter

    for i, column in enumerate(frame.columns, start=1):
        values = frame[column].dropna().astype(str)
        longest = max([len(str(column)), *values.str.len().tolist()])
        sheet.column_dimensions[get_column_letter(i)].width = min(longest + 2, max_width)


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------


def report_rows(report: PipelineReport) -> list[tuple[str, int | float | str]]:
    rows: list[tuple[str, int | float | str]] = [("Leads recebidos", report.total)]
    rows += [(STATUS_LABELS[s], report.por_status.get(s.value, 0)) for s in LeadStatus]
    rows += [
        ("Leads sem CNPJ", report.sem_cnpj),
        ("Ligações evitadas", report.ligacoes_evitadas),
        ("Consultas à API", report.consultas_api),
        ("Consultas atendidas pelo cache", report.consultas_cache),
        ("Tempo total (s)", report.duracao_segundos),
    ]
    if not report.enriquecimento_ativo:
        rows.append(("Enriquecimento", "desativado"))
    return rows


def render_markdown(report: PipelineReport) -> str:
    total = report.total or 1
    lines = [
        "# Relatório de limpeza de leads",
        "",
        "| Indicador | Quantidade | % |",
        "|---|---:|---:|",
        f"| Leads recebidos | {report.total} | 100% |",
    ]
    for status in LeadStatus:
        count = report.por_status.get(status.value, 0)
        lines.append(
            f"| {STATUS_LABELS[status]} (`{status.value}`) | {count} | {count / total:.0%} |"
        )
    lines += [
        f"| Leads sem CNPJ | {report.sem_cnpj} | {report.sem_cnpj / total:.0%} |",
        "",
        f"**{report.ligacoes_evitadas} ligações evitadas** para leads duplicados, "
        "com CNPJ inválido ou de empresas inativas "
        f"({report.ligacoes_evitadas / total:.0%} da base).",
        "",
        f"Processado em {report.duracao_segundos:.2f}s, com {report.consultas_api} consultas "
        f"à API e {report.consultas_cache} respostas vindas do cache.",
    ]
    if not report.enriquecimento_ativo:
        lines += ["", "> Enriquecimento desativado: empresas inativas não foram verificadas."]
    return "\n".join(lines) + "\n"


def write_report(report: PipelineReport, path: str | Path) -> Path:
    """Grava o relatório em Markdown (``.md``) ou JSON (``.json``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".md":
        path.write_text(render_markdown(report), encoding="utf-8")
    elif suffix == ".json":
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), "utf-8")
    else:
        raise ExportError(f"Formato de relatório não suportado: {suffix!r} (use .md ou .json)")
    return path


# ---------------------------------------------------------------------------
# HubSpot
# ---------------------------------------------------------------------------

FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "yahoo.com",
        "yahoo.com.br",
        "bol.com.br",
        "uol.com.br",
        "terra.com.br",
        "icloud.com",
        "ig.com.br",
    }
)


@dataclass(slots=True)
class HubSpotSyncResult:
    created: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class HubSpotClient:
    """Cria empresas no HubSpot pela API v3 (endpoint batch), em lotes de até 100.

    Requer um token de Private App com o escopo ``crm.objects.companies.write``.
    O CNPJ só é enviado se ``cnpj_property`` indicar uma propriedade personalizada
    já criada no portal.
    """

    BASE_URL = "https://api.hubapi.com"
    BATCH_ENDPOINT = "/crm/v3/objects/companies/batch/create"
    MAX_BATCH = 100

    def __init__(
        self,
        token: str,
        *,
        http: httpx.Client | None = None,
        cnpj_property: str | None = None,
        batch_size: int = MAX_BATCH,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise ValueError("Token do HubSpot não informado")
        self._http = http or httpx.Client(base_url=self.BASE_URL, timeout=30.0)
        self._headers = {"Authorization": f"Bearer {token}"}
        self._cnpj_property = cnpj_property
        self._batch_size = min(batch_size, self.MAX_BATCH)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def push_companies(self, leads: pd.DataFrame) -> HubSpotSyncResult:
        """Envia os leads com status ``ok``. Falhas de um lote não interrompem os demais."""
        ready = leads[leads[Col.STATUS] == LeadStatus.OK.value]
        result = HubSpotSyncResult()
        for batch in _chunks(
            [self.to_properties(row) for _, row in ready.iterrows()], self._batch_size
        ):
            self._send_batch(batch, result)
        logger.info("HubSpot: %d empresas criadas, %d falhas", result.created, result.failed)
        return result

    def _send_batch(self, batch: list[dict[str, str]], result: HubSpotSyncResult) -> None:
        body = {"inputs": [{"properties": props} for props in batch]}
        try:
            response = send_with_retry(
                lambda: self._http.post(self.BATCH_ENDPOINT, json=body, headers=self._headers),
                policy=self._retry,
                sleep=self._sleep,
                description="Envio de lote ao HubSpot",
            )
        except RetryExhaustedError as exc:
            result.failed += len(batch)
            result.errors.append(str(exc))
            return

        if response.status_code in (200, 201):
            result.created += len(batch)
        elif response.status_code == 207:  # sucesso parcial
            payload = response.json()
            created = len(payload.get("results", []))
            result.created += created
            result.failed += len(batch) - created
            result.errors += [str(e.get("message", e)) for e in payload.get("errors", [])]
        else:
            result.failed += len(batch)
            result.errors.append(f"HTTP {response.status_code}: {response.text[:300]}")

    def to_properties(self, row: pd.Series) -> dict[str, str]:
        """Converte um lead nas propriedades padrão de empresa do HubSpot."""
        cnae = " - ".join(v for v in (_get(row, Col.CNAE), _get(row, Col.CNAE_DESCRICAO)) if v)
        candidates = {
            "name": _get(row, Col.RAZAO_SOCIAL) or _get(row, Col.EMPRESA),
            "phone": _get(row, Col.TELEFONE),
            "city": _get(row, Col.CIDADE),
            "state": _get(row, Col.UF),
            "domain": email_domain(_get(row, Col.EMAIL)),
            "description": f"CNAE {cnae}" if cnae else None,
        }
        if self._cnpj_property:
            candidates[self._cnpj_property] = _get(row, Col.CNPJ)
        return {k: v for k, v in candidates.items() if v}


def email_domain(email: str | None) -> str | None:
    """Domínio corporativo do e-mail (ignora provedores gratuitos como gmail.com)."""
    if not email or "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].lower()
    return None if domain in FREE_EMAIL_DOMAINS else domain


def _get(row: pd.Series, column: str) -> str | None:
    value = row.get(column)
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return str(value)


def _chunks(items: list[dict[str, str]], size: int) -> Iterator[list[dict[str, str]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
