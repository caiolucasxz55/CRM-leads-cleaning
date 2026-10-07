"""Cache em SQLite das respostas da API de CNPJ.

Guarda a resposta bruta (status HTTP + JSON) para que mudanças no parser não
exijam consultar a API de novo. Só respostas definitivas devem ser gravadas:
empresa encontrada (200) ou CNPJ inexistente (404/400). Falhas temporárias não.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cnpj_cache (
    provider    TEXT    NOT NULL,
    cnpj        TEXT    NOT NULL,
    status_code INTEGER NOT NULL,
    payload     TEXT    NOT NULL,
    fetched_at  TEXT    NOT NULL,
    PRIMARY KEY (provider, cnpj)
)
"""


@dataclass(frozen=True, slots=True)
class CachedResponse:
    status_code: int
    payload: dict[str, Any]
    fetched_at: datetime


class CnpjCache:
    """Cache persistente das consultas. Use ``":memory:"`` para um cache descartável."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        ttl: timedelta | None = timedelta(days=30),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(_SCHEMA)
        self._ttl = ttl
        self._clock = clock

    def get(self, provider: str, cnpj: str) -> CachedResponse | None:
        row = self._conn.execute(
            "SELECT status_code, payload, fetched_at FROM cnpj_cache "
            "WHERE provider = ? AND cnpj = ?",
            (provider, cnpj),
        ).fetchone()
        if row is None:
            return None
        fetched_at = datetime.fromisoformat(row[2])
        if self._ttl is not None and self._clock() - fetched_at > self._ttl:
            return None
        return CachedResponse(status_code=row[0], payload=json.loads(row[1]), fetched_at=fetched_at)

    def set(self, provider: str, cnpj: str, status_code: int, payload: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cnpj_cache VALUES (?, ?, ?, ?, ?)",
                (
                    provider,
                    cnpj,
                    status_code,
                    json.dumps(payload, ensure_ascii=False),
                    self._clock().isoformat(),
                ),
            )

    def clear(self) -> int:
        """Apaga todas as entradas e devolve quantas foram removidas."""
        with self._conn:
            return self._conn.execute("DELETE FROM cnpj_cache").rowcount

    def __len__(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM cnpj_cache").fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
