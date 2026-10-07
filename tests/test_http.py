import time
from email.utils import formatdate

import pytest

from leads.http import RetryPolicy, parse_retry_after


class TestRetryPolicy:
    def test_espera_exponencial(self) -> None:
        policy = RetryPolicy(base_delay=0.5, factor=3, max_delay=100)

        assert [policy.delay(n) for n in (1, 2, 3)] == [0.5, 1.5, 4.5]

    def test_limita_ao_maximo(self) -> None:
        assert RetryPolicy(base_delay=10, max_delay=15).delay(5) == 15

    def test_retry_after_tem_prioridade_e_nunca_e_negativo(self) -> None:
        policy = RetryPolicy(max_delay=30)

        assert policy.delay(1, retry_after=12) == 12
        assert policy.delay(1, retry_after=120) == 30
        assert policy.delay(1, retry_after=-5) == 0


class TestParseRetryAfter:
    def test_segundos(self) -> None:
        assert parse_retry_after("3") == 3

    def test_data_http(self) -> None:
        value = formatdate(time.time() + 60, usegmt=True)

        assert parse_retry_after(value) == pytest.approx(60, abs=2)

    @pytest.mark.parametrize("value", [None, "", "amanhã"])
    def test_invalido(self, value: str | None) -> None:
        assert parse_retry_after(value) is None
