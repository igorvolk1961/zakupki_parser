"""Роутеры FastAPI-приложения (эндпоинты, сгруппированные по доменам).

Каждый модуль экспортирует ``build_<domain>_router(ctx) -> APIRouter``; зависимости
и хелперы берутся из контекста ``ApiContext`` (см. ``zakupki_parser.api.app.deps``).
"""
