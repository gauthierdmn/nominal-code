FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/

WORKDIR /app

ARG EXTRAS=all
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

COPY app/pyproject.toml app/uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project --extra ${EXTRAS}

COPY app/ ./
RUN uv sync --no-dev --frozen --no-editable --extra ${EXTRAS}

COPY ci/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN groupadd --gid 1000 nominal && \
    useradd --uid 1000 --gid nominal --create-home nominal && \
    mkdir -p /workspace /tmp/nominal && \
    chown -R nominal:nominal /app /workspace /tmp/nominal

ENTRYPOINT ["/entrypoint.sh"]
