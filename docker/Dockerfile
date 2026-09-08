# Парсер площадок закупок (Playwright + Chromium)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Зависимости системы для запуска Chromium.
# Acquire::ForceIPv4 — на облачных хостах иногда нет IPv6-маршрута, а apt пробует
# IPv6-адрес репозитория и падает с "Unable to connect". Принудительный IPv4 чинит это.
RUN echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4 \
    && apt-get update && apt-get install -y --no-install-recommends \
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

# Конфигурация и руководства ролей (/api/docs/guide/<role> читает docs/guides/<role>.md).
# Копируем только каталог guides (docs/ целиком весит ~78M из-за references) и
# точечно справочник лицензий (читает docs/references/licenze_kind.md).
COPY configs ./configs
COPY docs/guides ./docs/guides
COPY docs/references/licenze_kind.md ./docs/references/licenze_kind.md

# Чанжеты liquibase — иначе миграции в контейнере пропускаются (docker/liquibase/changelog).
COPY docker/liquibase/changelog ./docker/liquibase/changelog

ENV ZAKUPKI_CONFIGS=/app/configs
# Единый процесс парсера: веб-интерфейс + цикл мониторинга (автозапуск по флагу
# auto_start_monitoring в config_ops.yaml). docker-compose переопределяет эту команду
# для сервиса api; отдельный воркер run-service запускается при необходимости.
CMD ["uv", "run", "zp", "--configs", "/app/configs", "serve", "--host", "0.0.0.0", "--port", "8000"]
