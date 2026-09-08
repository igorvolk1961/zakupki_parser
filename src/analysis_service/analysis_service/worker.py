"""Фоновый воркер RAG-анализа: потребляет задачи из Redis-очереди.

Цикл: ``ZPOPMAX analysis:jobs`` → карточка закупки + активный клиентский профиль
(вопросы и факты BR-03) → обязательные системные проверки (1 LLM-вызов + матчер
по фактам профиля) и вопросы клиента (эмбеддинги, LLM-вердикты) → ``LPUSH
analysis:results`` (транспорт возвращает rag_report в парсер).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from analysis_service.embedder import build_embedder
from analysis_service.llm import LlmClient
from analysis_service.pipeline.prompts import build_geo_address_messages
from analysis_service.pipeline.rag import RagAnalyzer
from analysis_service.settings import Settings
from scoring_common.geo.centers import GeoPoint
from scoring_common.geo.distance import distance_km
from scoring_common.geo.geocoder import Geocoder, build_geocoder
from scoring_common.geo.region_filter import region_too_far
from scoring_common.parser_api import ParserApiClient
from scoring_common.queue import StageQueue
from scoring_common.requirements import extract_requirements
from scoring_common.stage_worker import process_stage_job
from scoring_common.tz import resolve_tz_content_cached

logger = logging.getLogger(__name__)

# Уровень точности для геокодирования центра региона: до города (qc_geo 4).
_CENTER_QUALITY = 4
# Поля-кандидаты «места поставки» в карточке закупки (адрес/населенный пункт).
_DELIVERY_ADDRESS_KEYS = ("delivery_place", "delivery_address", "execution_place", "address")


class AnalysisWorker:
    """Воркер обработки задач RAG-анализа."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._queue = StageQueue(settings)
        self._parser = ParserApiClient(
            settings.parser_api_url, internal_token=settings.parser_internal_token
        )
        self._embedder = build_embedder(settings)
        self._llm = LlmClient(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            temperature=settings.llm_temperature,
            timeout=settings.llm_request_timeout,
            max_tokens=settings.llm_max_tokens,
        )
        self._analyzer = RagAnalyzer(settings, self._embedder, self._llm)
        # Геокодер этапа анализа (место поставки не дальше N км от центра региона).
        self._geocoder: Geocoder | None = build_geocoder(settings.geo_config)

    async def run_forever(self) -> None:
        await self._queue.connect()
        logger.info("Analysis worker started (poll %.1fs)", self._settings.queue_poll_seconds)
        try:
            while True:
                await self._queue.recover_stale()
                await self._process_once()
                await asyncio.sleep(self._settings.queue_poll_seconds)
        finally:
            await self._queue.close()

    async def _resolve_questions(self, profile_id: int) -> list[dict[str, Any]]:
        """Вопросы профиля (из парсера); None при сбое."""
        try:
            client = await self._parser.get_active_client(
                internal_token=self._settings.parser_internal_token, profile_id=profile_id
            )
            questions = (client or {}).get("questions") or []
            return [q for q in questions if isinstance(q, dict)]
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("Не удалось получить вопросы клиента: %s", exc)
            return []

    @staticmethod
    def _delivery_address(record: dict[str, Any]) -> str | None:
        """Адрес места поставки из карточки закупки (best-effort).

        Поля «места поставки» у площадок разные; ищем первый непустой строковый
        кандидат в карточке и в ``detail_json`` (фолбэк — регион закупки).
        """
        for key in _DELIVERY_ADDRESS_KEYS:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        detail = record.get("detail_json") or {}
        if isinstance(detail, dict):
            for key in _DELIVERY_ADDRESS_KEYS:
                value = detail.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            region = detail.get("region")
            if isinstance(region, str) and region.strip():
                return region.strip()
        return None

    async def _resolve_tz_text(self, record: dict[str, Any]) -> str | None:
        """Текст ТЗ закупки (cached, через thread — файл скачивается/конвертируется)."""
        if not (record.get("files_json") or []):
            return None
        try:
            ref, text = await asyncio.to_thread(
                resolve_tz_content_cached,
                record,
                self._settings.tz_download_timeout,
                self._settings.tz_verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001 — сбой не критичен (фолбэк регион)
            logger.warning("Не удалось разрешить ТЗ для гео-адреса: %s", exc)
            return None
        return text if (ref is not None and text) else None

    async def _delivery_address_from_tz(self, record: dict[str, Any]) -> str | None:
        """Адрес места поставки из ТЗ — LLM (структурированный ответ, треб. 3).

        Второй (после полей карточки) источник адреса для геокодирования. ``None`` —
        ТЗ нет/сбой LLM/адрес не найден: используется регион закупки.
        """
        tz_text = await self._resolve_tz_text(record)
        if not tz_text:
            return None
        system, user = build_geo_address_messages(tz_text)
        data = await self._llm.chat_json(system, user)
        if not isinstance(data, dict):
            return None
        address = data.get("address")
        return address.strip() if isinstance(address, str) and address.strip() else None

    async def _profile_centers(self, regions: list[str], profile_id: int) -> list[GeoPoint]:
        """Центры целевых регионов профиля: из кэша БД, либо перегеокодируются.

        Повторное геокодирование профиля — ТОЛЬКО если сохранённый набор регионов
        в кэше отличается от текущего ``regions`` (треб. 2). Иначе — используется
        кэш (перегеокодирование не выполняется).
        """
        assert self._geocoder is not None
        try:
            cache = await self._parser.get_client_geo(profile_id)
        except Exception as exc:  # noqa: BLE001 — кэш недоступен: перегеокодируем
            logger.warning("Кэш регионов профиля %s недоступен: %s", profile_id, exc)
            cache = {}
        if list(cache.get("regions") or []) == list(regions):
            points: list[GeoPoint] = []
            for item in cache.get("centers") or []:
                if item and item.get("lat") is not None and item.get("lon") is not None:
                    points.append(GeoPoint(float(item["lat"]), float(item["lon"])))
            if points:
                return points
        centers: list[dict[str, float]] = []
        points = []
        for region in regions:
            point = await self._geocoder.geocode(str(region).strip(), min_quality=_CENTER_QUALITY)
            if point is None:
                logger.warning("Координаты центра региона «%s» не определены", region)
                return []
            points.append(point)
            centers.append({"lat": point.lat, "lon": point.lon})
        try:
            await self._parser.put_client_geo(profile_id, regions, centers)
        except Exception as exc:  # noqa: BLE001 — кэш сохранить не удалось: не критично
            logger.warning("Не удалось сохранить кэш регионов профиля %s: %s", profile_id, exc)
        return points

    async def _delivery_point(self, record: dict[str, Any], procurement_id: int) -> GeoPoint | None:
        """Координаты места поставки закупки (треб. 1, 3).

        Если координаты уже сохранены в БД — не перегеокодируем. Иначе геокодируем
        адрес из карточки (нет адреса — центр региона закупки) и сохраняем в кэш.
        """
        assert self._geocoder is not None
        try:
            pgeo = await self._parser.get_procurement_geo(procurement_id)
        except Exception as exc:  # noqa: BLE001 — БД-кэш недоступен: геокодируем
            logger.warning("Кэш координат закупки %s недоступен: %s", procurement_id, exc)
            pgeo = {}
        lat, lon = pgeo.get("delivery_lat"), pgeo.get("delivery_lon")
        if lat is not None and lon is not None:
            return GeoPoint(float(lat), float(lon))
        # Адрес из карточки, иначе — из ТЗ (LLM, треб. 3), иначе — регион закупки.
        address = (
            self._delivery_address(record)
            or await self._delivery_address_from_tz(record)
            or record.get("region")
        )
        if not address:
            return None
        point = await self._geocoder.geocode(
            str(address).strip(), min_quality=self._settings.geo_min_result_quality
        )
        if point is None:
            return None
        try:
            await self._parser.put_procurement_geo(procurement_id, point.lat, point.lon)
        except Exception as exc:  # noqa: BLE001 — сохранить кэш не удалось: не критично
            logger.warning("Не удалось сохранить координаты закупки %s: %s", procurement_id, exc)
        return point

    async def _geo_verdict(
        self, record: dict[str, Any], procurement_id: int, profile_id: int
    ) -> dict[str, Any] | None:
        """Вердикт расстояния от центра целевого региона — этап анализа.

        Активен, только если профиль задал ``target_regions`` + ``max_region_distance_km``.
        Fail-open: геокодер/кэш недоступны или данных недостаточно — вердикт не
        пишется (закупка не теряется).
        """
        if self._geocoder is None:
            return None
        try:
            client = await self._parser.get_active_client(
                internal_token=self._settings.parser_internal_token, profile_id=profile_id
            )
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            logger.warning("Профиль %s недоступен для гео-проверки: %s", profile_id, exc)
            return None
        regions = list((client or {}).get("target_regions") or [])
        max_km = (client or {}).get("max_region_distance_km")
        if not regions or max_km is None:
            return None
        centers = await self._profile_centers(regions, profile_id)
        if not centers:
            return None
        delivery = await self._delivery_point(record, procurement_id)
        if delivery is None:
            return None
        nearest = min(distance_km(delivery, center) for center in centers)
        return {
            "too_far": region_too_far(delivery, centers, float(max_km)),
            "distance_km": round(nearest, 1),
            "max_distance_km": float(max_km),
            "region": record.get("region") or "",
            "delivery": {"lat": delivery.lat, "lon": delivery.lon},
            "region_centers": [{"lat": c.lat, "lon": c.lon} for c in centers],
        }

    async def _process_once(self) -> None:
        job = await self._queue.pop_job()
        if job is None:
            return
        procurement_id, profile_id, priority = job
        logger.info(
            "Processing analysis for procurement %s (profile %s, priority=%.2f)",
            procurement_id,
            profile_id,
            priority,
        )

        async def compute(record: dict[str, Any], pid: int, pfd: int) -> dict[str, Any]:
            questions = await self._resolve_questions(pfd)
            report = await self._analyzer.analyze(
                record,
                questions,
                metadata={"procurement_id": pid, "profile_id": pfd},
            )
            # Этап анализа: проверка расстояния от центра целевого региона
            # (особое требование профиля). Вердикт кладётся в rag_report['geo'];
            # недоступность геокодера/БД-кэша — fail-open (закупка не теряется).
            geo = await self._geo_verdict(record, pid, pfd)
            if geo is not None:
                report["geo"] = geo
            # Требования к участнику: извлечение по всем документам (если еще не
            # извлечены) + LLM-заполнение ``data``; персист — per-procurement.
            # Пустой результат ({}) тоже сохраняется — поле перестаёт быть NULL.
            requirements = record.get("requirements_json")
            if requirements is None:
                requirements = await asyncio.to_thread(
                    extract_requirements,
                    record,
                    self._settings.tz_download_timeout,
                    self._settings.tz_verify_ssl,
                )
            try:
                filled = await self._analyzer.fill_requirements_data(requirements or {})
                await self._parser.post_requirements(pid, filled)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось заполнить/сохранить требования закупки %s: %s", pid, exc)
            result = {
                "procurement_id": pid,
                "profile_id": pfd,
                "score": 0.0,
                "score_method": "fit",
                "rag_report": report,
            }
            logger.info(
                "Analysis complete for procurement %s (profile %s): tz_found=%s "
                "file=%r questions=%d%s",
                pid,
                pfd,
                report.get("tz_found"),
                report.get("tz_file"),
                len(report.get("questions") or []),
                f" error={report.get('error')!r}" if report.get("error") else "",
            )
            return result

        try:
            await process_stage_job(
                self._queue,
                self._parser,
                procurement_id,
                profile_id,
                priority,
                retry_backoff_seconds=self._settings.parser_retry_backoff_seconds,
                compute=compute,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Analysis crashed for procurement %s (profile %s): %s",
                procurement_id,
                profile_id,
                exc,
            )


async def run_worker(settings: Settings) -> None:
    worker = AnalysisWorker(settings)
    await worker.run_forever()
