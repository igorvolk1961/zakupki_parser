"""Фоновая индексация закупок+документов по ОКПД2 (мгновенный «горячий» пересбор).

Стадия ``index`` вне каскада Fit/P(win)/Margin: не профильная, нет score. Обрабатывает
закупки, сохранённые системным «индексным» профилем (``zakupki_parser.scheduler``,
``IndexingConfig``) — те, у кого парсер уже дозагрузил ``files_json`` (см.
``processing.py``, п.1 плана индексации). Скачивает и извлекает текст документов
(``scoring_common.tz``, тот же модуль, что и ``analysis_service``/просмотр ТЗ в карточке)
и возвращает результат в парсер через ``scoring_transport`` (``POST
/api/procurements/{id}/index-result``), который пишет строку ``procurement_search_index``.
"""
