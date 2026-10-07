"""Junta as etapas: normalização -> deduplicação -> enriquecimento -> saída."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from leads.dedupe import deduplicate
from leads.enrich import CnpjEnricher, enrich_leads
from leads.frame import CNPJ_KEY, append_detail, internal_columns
from leads.models import ENRICHMENT_COLUMNS, Col, LeadStatus
from leads.normalize import normalize_leads

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS: tuple[str, ...] = (
    Col.LINHA,
    Col.STATUS,
    Col.DETALHE,
    Col.DUPLICADO_DE,
    Col.NOME,
    Col.EMPRESA,
    Col.CNPJ,
    Col.EMAIL,
    Col.TELEFONE,
    *ENRICHMENT_COLUMNS,
)

# Leads que o time comercial não precisa ligar.
AVOIDABLE_STATUSES = (LeadStatus.DUPLICADO, LeadStatus.CNPJ_INVALIDO, LeadStatus.EMPRESA_INATIVA)


@dataclass(slots=True)
class PipelineReport:
    total: int
    por_status: dict[str, int]
    sem_cnpj: int
    duracao_segundos: float
    consultas_api: int = 0
    consultas_cache: int = 0
    enriquecimento_ativo: bool = True

    @property
    def ligacoes_evitadas(self) -> int:
        return sum(self.por_status.get(s.value, 0) for s in AVOIDABLE_STATUSES)

    @property
    def prontos_para_crm(self) -> int:
        return self.por_status.get(LeadStatus.OK.value, 0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ligacoes_evitadas"] = self.ligacoes_evitadas
        data["prontos_para_crm"] = self.prontos_para_crm
        return data


@dataclass(frozen=True, slots=True)
class PipelineResult:
    leads: pd.DataFrame
    report: PipelineReport

    @property
    def ready_for_crm(self) -> pd.DataFrame:
        return self.leads[self.leads[Col.STATUS] == LeadStatus.OK.value]


def run_pipeline(
    raw: pd.DataFrame,
    enricher: CnpjEnricher | None = None,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> PipelineResult:
    """Processa a planilha já lida (ver :func:`leads.reader.read_leads`).

    Sem ``enricher`` o pipeline só normaliza e deduplica. Nenhum lead é descartado:
    todos saem com um status.
    """
    started = time.perf_counter()

    df = normalize_leads(raw)
    df = deduplicate(df)
    if enricher is not None:
        df = enrich_leads(df, enricher, on_progress=on_progress)
    df = _finalize(df, enriched=enricher is not None)

    output = _order_columns(df.drop(columns=internal_columns(df)))

    counts = output[Col.STATUS].value_counts()
    report = PipelineReport(
        total=len(output),
        por_status={s.value: int(counts.get(s.value, 0)) for s in LeadStatus},
        sem_cnpj=int(output[Col.CNPJ].isna().sum()),
        duracao_segundos=round(time.perf_counter() - started, 2),
        consultas_api=enricher.stats.api_calls if enricher else 0,
        consultas_cache=enricher.stats.cache_hits if enricher else 0,
        enriquecimento_ativo=enricher is not None,
    )
    logger.info("Pipeline concluído: %s", report.por_status)
    return PipelineResult(leads=output, report=report)


def _finalize(df: pd.DataFrame, *, enriched: bool) -> pd.DataFrame:
    """Leads que passaram por todas as etapas sem problema ficam ``ok``."""
    out = df.copy()
    for column in ENRICHMENT_COLUMNS:
        if column not in out.columns:
            out[column] = None

    pending = out[Col.STATUS].isna()
    no_cnpj = pending & out[CNPJ_KEY].isna()
    append_detail(out, no_cnpj, "sem CNPJ: não enriquecido")
    if not enriched:
        append_detail(out, pending & ~no_cnpj, "enriquecimento desativado")
    out.loc[pending, Col.STATUS] = LeadStatus.OK.value
    return out


def _order_columns(df: pd.DataFrame) -> pd.DataFrame:
    extras = [c for c in df.columns if c not in OUTPUT_COLUMNS]
    return df[[*OUTPUT_COLUMNS, *extras]]
