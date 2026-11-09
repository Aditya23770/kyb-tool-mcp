# Optional container for running the MCP server in isolation (stdio).
#   docker build -t kyb-tool-mcp .
#   docker run --rm -i -e COMPANIES_HOUSE_API_KEY=... -v kyb-data:/data kyb-tool-mcp update-lists
#   docker run --rm -i -e COMPANIES_HOUSE_API_KEY=... -v kyb-data:/data kyb-tool-mcp
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 kyb && mkdir /data && chown kyb /data
COPY --from=build --chown=kyb /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" KYB_DATA_DIR=/data PYTHONUNBUFFERED=1
USER kyb
VOLUME /data
ENTRYPOINT ["kyb-tool-mcp"]
