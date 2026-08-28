FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

# Конвертеры легаси .doc в текст (catdoc/antiword): MarkItDown .doc не понимает,
# а RAG-экстрактор ТЗ падает на них без внешнего конвертера.
RUN apt-get update \
    && apt-get install -y --no-install-recommends catdoc antiword \
    && rm -rf /var/lib/apt/lists/*

# Build context — ../src (содержит analysis_service и scoring_common).
WORKDIR /app/analysis_service

COPY scoring_common ../scoring_common
COPY analysis_service/pyproject.toml analysis_service/uv.lock ./
RUN uv sync --frozen --no-dev

COPY analysis_service/ .

ENV PYTHONPATH=/app/scoring_common

# По умолчанию — фоновый воркер Redis-очереди RAG-анализа.
# Разовый анализ по JSON-карточке: uv run python -m analysis_service.cli analyze <card.json>
CMD ["uv", "run", "python", "-m", "analysis_service.cli", "worker"]
