FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

# Build context — ../src (содержит scoring_transport и scoring_common).
WORKDIR /app/scoring_transport

COPY scoring_common ../scoring_common
COPY scoring_transport/pyproject.toml scoring_transport/uv.lock ./
RUN uv sync --frozen --no-dev

COPY scoring_transport/ .

ENV PYTHONPATH=/app/scoring_common

EXPOSE 8200

# По умолчанию — FastAPI (ingest + фоновый consumer результатов).
CMD ["uv", "run", "python", "-m", "scoring_transport", "serve", "--host", "0.0.0.0", "--port", "8200"]
