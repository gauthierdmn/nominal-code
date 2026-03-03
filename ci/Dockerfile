FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/

COPY app/ /app/
WORKDIR /app

RUN uv sync --no-dev --frozen

COPY ci/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
