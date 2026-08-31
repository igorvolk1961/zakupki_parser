"""Работа со страницей списка закупок (URL-запрос + навигация/пагинация)."""

from zakupki_parser.parser.lister.page import (
    SETTLE_MS,
    _increment_url_page,
    extract_total_results,
    goto_next_page,
    iter_container_records,
    list_containers,
    next_page_exists,
    open_list_page,
    setup_sort_and_filters,
)
from zakupki_parser.parser.lister.query import (
    MSK,
    _resolve_okpd2_eis,
    _resolve_okpd2_ids,
    build_list_url,
    build_query,
    keyword_batches,
    keyword_search_string,
)

__all__ = [
    "SETTLE_MS",
    "MSK",
    "build_query",
    "build_list_url",
    "keyword_batches",
    "keyword_search_string",
    "open_list_page",
    "setup_sort_and_filters",
    "list_containers",
    "extract_total_results",
    "next_page_exists",
    "goto_next_page",
    "iter_container_records",
    "_increment_url_page",
    "_resolve_okpd2_eis",
    "_resolve_okpd2_ids",
]
