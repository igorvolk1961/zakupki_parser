"""Автоматическое применение Liquibase-миграций перед стартом сервиса.

Запускает ``liquibase update`` через CLI/подпроцесс. Способ запуска:
1. исполняемый файл ``liquibase`` из PATH, если есть;
2. иначе — образ ``liquibase/liquibase`` через docker (``--network host``,
   подходит для локальной разработки на Linux).

Если ни Liquibase, ни docker недоступны (или каталог чанжетов не найден) —
миграции пропускаются с предупреждением, старт не блокируется. Применение
идемпотентно (Liquibase ведёт таблицу ``DATABASECHANGELOG``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from zakupki_parser.config.models import DbConfig

logger = logging.getLogger(__name__)

CHANGELOG_FILE = "db.changelog-master.yaml"
LIQUIBASE_IMAGE = "liquibase/liquibase:4.30"


def jdbc_from_dsn(dsn: str) -> tuple[str, str, str]:
    """Преобразует SQLAlchemy-строку ``postgresql+asyncpg://user:pass@host/db``
    в (jdbc-URL, user, password)."""
    scheme, _, rest = dsn.partition("://")
    scheme = scheme.split("+", 1)[0]  # postgresql+asyncpg -> postgresql
    userinfo, sep, hostport_db = rest.rpartition("@")
    if not sep:
        return f"jdbc:{scheme}://{rest}", "", ""
    user, _, password = userinfo.partition(":")
    return f"jdbc:{scheme}://{hostport_db}", user, password


def changelog_dir(configs_dir: str | Path) -> Path:
    """Каталог чанжетов (docker/liquibase/changelog) относительно корня проекта."""
    base = Path(configs_dir).expanduser().resolve()
    return base.parent / "docker" / "liquibase" / "changelog"


def _liquibase_command(changelog_dir: Path, db_env: dict[str, str]) -> list[str] | None:
    """Команда запуска liquibase (CLI или docker). None — недоступно.

    Для CLI параметры передаются переменными окружения (``db_env``); для docker —
    флагами ``-e`` (docker не наследует env родительского процесса).
    """
    if shutil.which("liquibase"):
        return [
            "liquibase",
            "--search-path",
            str(changelog_dir),
            "--changelog-file",
            CHANGELOG_FILE,
            "update",
        ]
    if shutil.which("docker"):
        args = [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "-v",
            f"{changelog_dir}:/liquibase/changelog",
        ]
        for key, value in db_env.items():
            args += ["-e", f"{key}={value}"]
        args += [
            LIQUIBASE_IMAGE,
            "--search-path=/liquibase/changelog",
            f"--changelog-file={CHANGELOG_FILE}",
            "update",
        ]
        return args
    return None


def run_migrations(configs_dir: str | Path, db: DbConfig) -> bool:
    """Применяет Liquibase-миграции к БД. True — применены/уже актуальны.

    Не блокирует запуск: при невозможности выполнить миграции логирует
    предупреждение и возвращает False.
    """
    if not db.enabled:
        return False
    chdir = changelog_dir(configs_dir)
    if not chdir.is_dir():
        logger.warning("Каталог миграций не найден: %s — пропуск", chdir)
        return False

    jdbc, user, password = jdbc_from_dsn(db.dsn)
    db_env = {
        "LIQUIBASE_COMMAND_URL": jdbc,
        "LIQUIBASE_COMMAND_USERNAME": user,
        "LIQUIBASE_COMMAND_PASSWORD": password,
        "LIQUIBASE_COMMAND_CHANGELOG_FILE": CHANGELOG_FILE,
    }
    cmd = _liquibase_command(chdir, db_env)
    if cmd is None:
        logger.warning("Не найдены Liquibase CLI и docker — пропуск миграций")
        return False
    env = os.environ.copy()
    # Для CLI лишние переменные env не мешают; docker получает их через -e.
    env.update(db_env)
    logger.info("Применяю Liquibase-миграции к %s …", jdbc)
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        logger.warning("Таймаут Liquibase: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "Liquibase завершился с кодом %s:\n%s",
            result.returncode,
            (result.stdout + result.stderr).strip(),
        )
        return False
    logger.info("Liquibase-миграции применены (%s)", chdir)
    return True
