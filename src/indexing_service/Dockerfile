FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive

# Конвертеры легаси .doc в текст (catdoc/antiword): MarkItDown .doc не понимает
# (см. analysis_service/Dockerfile — тот же extractor scoring_common.tz).
RUN echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4 \
    && apt-get update \
    && apt-get install -y --no-install-recommends catdoc antiword \
    && rm -rf /var/lib/apt/lists/*

# Build context — ../src (содержит indexing_service и scoring_common).
WORKDIR /app/indexing_service

COPY scoring_common ../scoring_common
COPY indexing_service/pyproject.toml indexing_service/uv.lock ./
RUN uv sync --frozen --no-dev

COPY indexing_service/ .

ENV PYTHONPATH=/app/scoring_common

CMD ["uv", "run", "python", "-m", "indexing_service", "worker"]
