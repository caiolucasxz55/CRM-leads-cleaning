"""Interface de linha de comando: ``leads run entrada.csv --saida limpo.xlsx``."""

from __future__ import annotations

import logging
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table

from leads import __version__
from leads.cache import CnpjCache
from leads.enrich import BrasilApiProvider, CnpjEnricher
from leads.export import (
    STATUS_LABELS,
    ExportError,
    HubSpotClient,
    write_leads,
    write_report,
)
from leads.models import LeadStatus
from leads.pipeline import PipelineReport, run_pipeline
from leads.reader import LeadsFileError, read_leads

app = typer.Typer(
    name="leads",
    help="Limpa, deduplica e enriquece planilhas de leads com dados públicos do CNPJ.",
    no_args_is_help=True,
    add_completion=False,
)
cache_app = typer.Typer(help="Gerencia o cache das consultas de CNPJ.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")

console = Console()
err_console = Console(stderr=True)

DEFAULT_CACHE = Path(".cache/cnpj.sqlite")

CacheOption = Annotated[
    Path,
    typer.Option(
        "--cache",
        envvar="LEADS_CACHE_PATH",
        help="Arquivo SQLite do cache das consultas.",
    ),
]


def _version(value: bool) -> None:
    if value:
        console.print(f"leads-cnpj {__version__}")
        raise typer.Exit


@app.callback()
def main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Mostra logs detalhados.")
    ] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Mostra a versão."),
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def run(
    entrada: Annotated[
        Path, typer.Argument(help="Planilha de leads (.csv, .xlsx ou .xls).", show_default=False)
    ],
    saida: Annotated[
        Path | None,
        typer.Option(
            "--saida", "-o", help="Planilha limpa (.xlsx ou .csv). [padrão: <entrada>_limpo.xlsx]"
        ),
    ] = None,
    relatorio: Annotated[
        Path | None,
        typer.Option("--relatorio", "-r", help="Grava o relatório em .md ou .json."),
    ] = None,
    cache: CacheOption = DEFAULT_CACHE,
    sem_cache: Annotated[
        bool, typer.Option("--sem-cache", help="Não lê nem grava o cache.")
    ] = False,
    cache_dias: Annotated[
        int,
        typer.Option("--cache-dias", min=0, help="Validade do cache em dias (0 = sem validade)."),
    ] = 30,
    sem_enriquecimento: Annotated[
        bool,
        typer.Option("--sem-enriquecimento", help="Só normaliza e deduplica, sem consultar a API."),
    ] = False,
    intervalo: Annotated[
        float,
        typer.Option("--intervalo", min=0.0, help="Segundos mínimos entre consultas à API."),
    ] = 0.0,
    hubspot: Annotated[
        bool,
        typer.Option("--hubspot", help="Envia os leads ok para o HubSpot como empresas."),
    ] = False,
    hubspot_token: Annotated[
        str | None,
        typer.Option(envvar="HUBSPOT_TOKEN", help="Token de Private App do HubSpot."),
    ] = None,
    hubspot_cnpj_prop: Annotated[
        str | None,
        typer.Option(help="Propriedade personalizada do HubSpot que recebe o CNPJ."),
    ] = None,
) -> None:
    """Executa o pipeline completo sobre a planilha de entrada."""
    if hubspot and not hubspot_token:
        _fail("Para usar --hubspot defina HUBSPOT_TOKEN ou passe --hubspot-token.")

    saida = saida or entrada.with_name(f"{entrada.stem}_limpo.xlsx")

    try:
        raw = read_leads(entrada)
    except LeadsFileError as exc:
        _fail(str(exc))

    with ExitStack() as stack:
        enricher = None
        if not sem_enriquecimento:
            ttl = timedelta(days=cache_dias) if cache_dias else None
            store = None if sem_cache else stack.enter_context(CnpjCache(cache, ttl=ttl))
            provider = BrasilApiProvider(min_interval=intervalo)
            stack.callback(provider.close)
            enricher = CnpjEnricher(provider, store)

        with _progress() as progress:
            task = progress.add_task("Consultando CNPJs", total=None)
            result = run_pipeline(
                raw,
                enricher,
                on_progress=lambda done, total: progress.update(task, completed=done, total=total),
            )

    try:
        write_leads(result, saida)
        if relatorio:
            write_report(result.report, relatorio)
    except ExportError as exc:
        _fail(str(exc))

    _print_report(result.report)
    console.print(f"\n[green]Planilha limpa:[/] {saida}")
    if relatorio:
        console.print(f"[green]Relatório:[/] {relatorio}")

    if hubspot and hubspot_token:
        client = HubSpotClient(hubspot_token, cnpj_property=hubspot_cnpj_prop)
        try:
            sync = client.push_companies(result.leads)
        finally:
            client.close()
        console.print(f"[green]HubSpot:[/] {sync.created} empresas criadas, {sync.failed} falhas")
        for error in sync.errors[:5]:
            err_console.print(f"  [red]•[/] {error}")
        if sync.failed:
            raise typer.Exit(code=2)


@cache_app.command("limpar")
def cache_clear(cache: CacheOption = DEFAULT_CACHE) -> None:
    """Apaga todas as consultas guardadas no cache."""
    if not cache.exists():
        console.print("Nenhum cache encontrado.")
        return
    with CnpjCache(cache) as store:
        removed = store.clear()
    console.print(f"{removed} consultas removidas do cache {cache}.")


@cache_app.command("info")
def cache_info(cache: CacheOption = DEFAULT_CACHE) -> None:
    """Mostra quantos CNPJs estão no cache."""
    if not cache.exists():
        console.print("Nenhum cache encontrado.")
        return
    with CnpjCache(cache) as store:
        console.print(f"{len(store)} CNPJs no cache {cache}.")


def _progress() -> Progress:
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    )


def _print_report(report: PipelineReport) -> None:
    table = Table(title="Resultado", show_header=True, header_style="bold")
    table.add_column("Status")
    table.add_column("Leads", justify="right")
    table.add_row("Recebidos", str(report.total), style="bold")
    colors = {
        LeadStatus.OK: "green",
        LeadStatus.DUPLICADO: "yellow",
        LeadStatus.CNPJ_INVALIDO: "red",
        LeadStatus.EMPRESA_INATIVA: "red",
        LeadStatus.ERRO_CONSULTA: "magenta",
    }
    for status in LeadStatus:
        count = report.por_status.get(status.value, 0)
        table.add_row(f"[{colors[status]}]{STATUS_LABELS[status]}[/]", str(count))
    console.print(table)
    console.print(
        f"[bold]{report.ligacoes_evitadas}[/] ligações evitadas · "
        f"{report.consultas_api} consultas à API · {report.consultas_cache} do cache · "
        f"{report.duracao_segundos:.2f}s"
    )


def _fail(message: str) -> NoReturn:
    err_console.print(f"[red]Erro:[/] {message}")
    raise typer.Exit(code=1)
