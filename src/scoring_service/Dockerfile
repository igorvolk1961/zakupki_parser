FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

# Build context — ../src (содержит scoring_service и scoring_common).
WORKDIR /app/scoring_service

COPY scoring_common ../scoring_common
COPY scoring_service/pyproject.toml scoring_service/uv.lock ./
RUN uv sync --frozen --no-dev

COPY scoring_service/ .

ENV PYTHONPATH=/app/scoring_common

EXPOSE 8100

# По умолчанию — фоновый воркер Redis-очереди.
# Для API: python -m scoring_service serve
CMD ["uv", "run", "python", "-m", "scoring_service", "worker"]
