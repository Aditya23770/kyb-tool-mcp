# TrustLedger hosted MCP server (Streamable HTTP at /mcp, health at /healthz).
# The sanctions index (OFAC SDN + UN) is built into the image, so rebuilding the image refreshes it.
# No secrets are baked in: each caller sends their own Companies House key in a request header.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.7.19 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    KYB_DATA_DIR=/app/data \
    KYB_AUDIT=false \
    KYB_HTTP_CACHE=false \
    KYB_LOG_FORMAT=json

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"

# Download the public sanctions lists and build the local index (~27 MB).
RUN kyb-tool-mcp update-lists

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec kyb-tool-mcp serve --http --host 0.0.0.0 --port ${PORT:-8080}"]
