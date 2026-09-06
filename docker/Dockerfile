# Парсер площадок закупок (Playwright + Chromium)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Зависимости системы для запуска Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fonts-liberation \
    libasound2t64 \
    libatk-bridge2.0-0t64 \
    libatk1.0-0t64 \
    libcups2t64 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0t64 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# uv для установки зависимостей
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Устанавливаем зависимости и браузер
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev && \
    uv run playwright install --with-deps chromium

# Конфигурация
COPY configs ./configs

ENV ZAKUPKI_CONFIGS=/app/configs
# Единый процесс парсера: веб-интерфейс + цикл мониторинга (автозапуск по флагу
# auto_start_monitoring в config_ops.yaml). docker-compose переопределяет эту команду
# для сервиса api; отдельный воркер run-service запускается при необходимости.
CMD ["uv", "run", "zp", "--configs", "/app/configs", "serve", "--host", "0.0.0.0", "--port", "8000"]
