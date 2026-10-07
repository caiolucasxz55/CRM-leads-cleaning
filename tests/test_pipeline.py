import pandas as pd

from leads.cache import CnpjCache
from leads.enrich import CnpjEnricher
from leads.models import Col, LeadStatus
from leads.pipeline import OUTPUT_COLUMNS, run_pipeline
from leads.reader import standardize_columns
from tests.conftest import CNPJ_PETROBRAS
from tests.fakes import CNPJ_INEXISTENTE, CNPJ_SADIA, FakeApi, make_provider


def planilha() -> pd.DataFrame:
    return standardize_columns(
        pd.DataFrame(
            [
                {"Empresa": "Petrobras", "CNPJ": "33.000.167/0001-01", "Origem": "feira"},
                {"Empresa": "Petrobras", "CNPJ": CNPJ_PETROBRAS, "E-mail": "x@petrobras.com.br"},
                {"Empresa": "Sadia", "CNPJ": CNPJ_SADIA},
                {"Empresa": "Fantasma", "CNPJ": CNPJ_INEXISTENTE},
                {"Empresa": "Digitou errado", "CNPJ": "33.000.167/0001-99"},
                {"Empresa": "Sem CNPJ", "E-mail": "contato@padaria.com"},
            ]
        )
    )


def test_pipeline_completo() -> None:
    api = FakeApi()
    enricher = CnpjEnricher(make_provider(api), CnpjCache())

    result = run_pipeline(planilha(), enricher)

    assert result.leads[Col.STATUS].tolist() == [
        LeadStatus.OK,
        LeadStatus.DUPLICADO,
        LeadStatus.EMPRESA_INATIVA,
        LeadStatus.CNPJ_INVALIDO,
        LeadStatus.CNPJ_INVALIDO,
        LeadStatus.OK,
    ]
    # empate de completude: fica a primeira linha, que herda o e-mail do duplicado
    assert result.leads.iloc[0][Col.EMAIL] == "x@petrobras.com.br"
    assert result.leads.iloc[1][Col.DUPLICADO_DE] == 2
    assert result.leads.iloc[5][Col.DETALHE] == "sem CNPJ: não enriquecido"
    assert sorted(api.calls) == sorted([CNPJ_PETROBRAS, CNPJ_SADIA, CNPJ_INEXISTENTE])


def test_relatorio() -> None:
    enricher = CnpjEnricher(make_provider(FakeApi()), CnpjCache())

    report = run_pipeline(planilha(), enricher).report

    assert report.total == 6
    assert report.por_status == {
        "ok": 2,
        "duplicado": 1,
        "cnpj_invalido": 2,
        "empresa_inativa": 1,
        "erro_consulta": 0,
    }
    assert report.ligacoes_evitadas == 4
    assert report.prontos_para_crm == 2
    assert report.sem_cnpj == 1
    assert report.consultas_api == 3
    assert report.to_dict()["ligacoes_evitadas"] == 4


def test_segunda_execucao_usa_somente_o_cache() -> None:
    api = FakeApi()
    cache = CnpjCache()

    run_pipeline(planilha(), CnpjEnricher(make_provider(api), cache))
    report = run_pipeline(planilha(), CnpjEnricher(make_provider(api), cache)).report

    assert len(api.calls) == 3  # nenhuma chamada nova
    assert report.consultas_api == 0
    assert report.consultas_cache == 3


def test_sem_enriquecimento_apenas_limpa_e_deduplica() -> None:
    result = run_pipeline(planilha(), enricher=None)

    assert result.leads[Col.STATUS].tolist() == [
        LeadStatus.OK,
        LeadStatus.DUPLICADO,
        LeadStatus.OK,
        LeadStatus.OK,
        LeadStatus.CNPJ_INVALIDO,
        LeadStatus.OK,
    ]
    assert result.leads.iloc[2][Col.DETALHE] == "enriquecimento desativado"
    assert not result.report.enriquecimento_ativo


def test_nenhum_lead_some_e_colunas_internas_nao_vazam() -> None:
    result = run_pipeline(planilha(), CnpjEnricher(make_provider(FakeApi())))

    assert len(result.leads) == 6
    assert list(result.leads.columns[: len(OUTPUT_COLUMNS)]) == list(OUTPUT_COLUMNS)
    assert not any(str(c).startswith("_") for c in result.leads.columns)
    assert result.leads[Col.STATUS].notna().all()


def test_ready_for_crm_filtra_apenas_ok() -> None:
    result = run_pipeline(planilha(), CnpjEnricher(make_provider(FakeApi())))

    assert result.ready_for_crm[Col.EMPRESA].tolist() == ["Petrobras", "Sem CNPJ"]
