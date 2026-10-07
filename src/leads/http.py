"""Retry com espera crescente para chamadas HTTP (API de CNPJ e CRM)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryExhaustedError(Exception):
    """Todas as tentativas falharam com erro temporário."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Espera exponencial: ``base_delay * factor ** (tentativa - 1)``, limitada a ``max_delay``."""

    max_attempts: int = 5
    base_delay: float = 2.0
    factor: float = 2.0
    max_delay: float = 60.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        return min(self.base_delay * self.factor ** (attempt - 1), self.max_delay)


def parse_retry_after(value: str | None) -> float | None:
    """Interpreta o cabeçalho ``Retry-After`` (segundos ou data HTTP)."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).timestamp() - time.time()
    except (TypeError, ValueError):
        return None


def send_with_retry(
    send: Callable[[], httpx.Response],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    description: str = "requisição",
) -> httpx.Response:
    """Executa ``send`` até obter uma resposta que não seja um erro temporário.

    Falhas de rede e os status em :data:`RETRYABLE_STATUS` disparam nova tentativa;
    qualquer outra resposta (inclusive 4xx) é devolvida para quem chamou decidir.
    """
    last_error = "erro desconhecido"
    for attempt in range(1, policy.max_attempts + 1):
        retry_after: float | None = None
        try:
            response = send()
        except httpx.TransportError as exc:
            last_error = f"falha de rede: {exc.__class__.__name__}"
        else:
            if response.status_code not in RETRYABLE_STATUS:
                return response
            last_error = f"HTTP {response.status_code}"
            retry_after = parse_retry_after(response.headers.get("Retry-After"))

        if attempt < policy.max_attempts:
            wait = policy.delay(attempt, retry_after)
            logger.warning("%s falhou (%s), nova tentativa em %.1fs", description, last_error, wait)
            sleep(wait)

    raise RetryExhaustedError(f"{last_error} após {policy.max_attempts} tentativas")
