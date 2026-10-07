"""Dublês de teste para a BrasilAPI, baseados nas respostas reais salvas em fixtures/."""

from __future__ import annotations

from typing import Any

import httpx

from leads.enrich import BrasilApiProvider
from tests.conftest import CNPJ_PETROBRAS, load_fixture

CNPJ_SADIA = "20730099000194"  # baixada por incorporação à BRF
CNPJ_INEXISTENTE = "99999999099970"

FIXTURE_ROUTES: dict[str, tuple[int, str]] = {
    CNPJ_PETROBRAS: (200, "brasilapi_ativa.json"),
    CNPJ_SADIA: (200, "brasilapi_baixada.json"),
    CNPJ_INEXISTENTE: (404, "brasilapi_404.json"),
}


class FakeApi:
    """Servidor falso: responde com as fixtures e, se configurado, com respostas roteirizadas."""

    def __init__(self, scripted: list[httpx.Response] | None = None) -> None:
        self.calls: list[str] = []
        self._scripted = list(scripted or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cnpj = request.url.path.rsplit("/", 1)[-1]
        self.calls.append(cnpj)
        if self._scripted:
            return self._scripted.pop(0)
        if cnpj in FIXTURE_ROUTES:
            status, fixture = FIXTURE_ROUTES[cnpj]
            return httpx.Response(status, json=load_fixture(fixture))
        return httpx.Response(400, json=load_fixture("brasilapi_400.json"))


def make_provider(
    api: FakeApi, sleeps: list[float] | None = None, **kwargs: Any
) -> BrasilApiProvider:
    http = httpx.Client(transport=httpx.MockTransport(api), base_url=BrasilApiProvider.BASE_URL)
    recorder = sleeps if sleeps is not None else []
    return BrasilApiProvider(http, sleep=recorder.append, **kwargs)
