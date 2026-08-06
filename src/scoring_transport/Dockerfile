FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

EXPOSE 8200

# По умолчанию — FastAPI (ingest + фоновый consumer результатов).
CMD ["uv", "run", "python", "-m", "scoring_transport", "serve", "--host", "0.0.0.0", "--port", "8200"]
