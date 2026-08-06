FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8100

# По умолчанию — фоновый воркер Redis-очереди.
# Для API: python -m scoring_service serve
CMD ["uv", "run", "python", "-m", "scoring_service", "worker"]
