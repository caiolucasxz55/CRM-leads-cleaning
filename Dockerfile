# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# build: gera o wheel do projeto e das dependências
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --wheel-dir /wheels .

# ---------------------------------------------------------------------------
# test: roda lint, tipagem e testes  ->  docker build --target test .
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS test

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[dev]"
COPY tests ./tests
CMD ["sh", "-c", "ruff check . && ruff format --check . && mypy && pytest --cov"]

# ---------------------------------------------------------------------------
# runtime: imagem final, só com o necessário e sem root
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="leads-cnpj" \
      org.opencontainers.image.description="Limpeza, deduplicação e enriquecimento de leads por CNPJ"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LEADS_CACHE_PATH=/data/.cache/cnpj.sqlite

RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install /wheels/*.whl \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown app:app /data

USER app
WORKDIR /data
VOLUME ["/data"]

ENTRYPOINT ["leads"]
CMD ["--help"]
