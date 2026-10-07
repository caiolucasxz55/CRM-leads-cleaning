from datetime import UTC, datetime, timedelta
from pathlib import Path

from leads.cache import CnpjCache

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def test_guarda_e_recupera_resposta() -> None:
    with CnpjCache(clock=lambda: NOW) as cache:
        cache.set("brasilapi", "00000000000191", 200, {"razao_social": "BANCO DO BRASIL SA"})

        hit = cache.get("brasilapi", "00000000000191")

    assert hit is not None
    assert hit.status_code == 200
    assert hit.payload == {"razao_social": "BANCO DO BRASIL SA"}


def test_miss_para_cnpj_ou_provider_desconhecido() -> None:
    with CnpjCache() as cache:
        cache.set("brasilapi", "00000000000191", 200, {})

        assert cache.get("brasilapi", "33000167000101") is None
        assert cache.get("outra_api", "00000000000191") is None


def test_entrada_expirada_e_ignorada() -> None:
    now = [NOW]
    with CnpjCache(ttl=timedelta(days=30), clock=lambda: now[0]) as cache:
        cache.set("brasilapi", "00000000000191", 200, {})

        now[0] = NOW + timedelta(days=29)
        assert cache.get("brasilapi", "00000000000191") is not None

        now[0] = NOW + timedelta(days=31)
        assert cache.get("brasilapi", "00000000000191") is None


def test_sem_ttl_nunca_expira() -> None:
    now = [NOW]
    with CnpjCache(ttl=None, clock=lambda: now[0]) as cache:
        cache.set("brasilapi", "00000000000191", 200, {})
        now[0] = NOW + timedelta(days=3650)

        assert cache.get("brasilapi", "00000000000191") is not None


def test_persiste_em_disco_entre_execucoes(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "cache.sqlite"
    with CnpjCache(path) as cache:
        cache.set("brasilapi", "00000000000191", 404, {"message": "não encontrado"})

    with CnpjCache(path) as cache:
        hit = cache.get("brasilapi", "00000000000191")
        assert hit is not None
        assert hit.payload == {"message": "não encontrado"}
        assert len(cache) == 1


def test_clear() -> None:
    with CnpjCache() as cache:
        cache.set("brasilapi", "a", 200, {})
        cache.set("brasilapi", "b", 200, {})

        assert cache.clear() == 2
        assert len(cache) == 0
