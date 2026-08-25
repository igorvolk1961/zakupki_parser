"""Резолв кодов ОКПД2 (и регионов) в параметры запроса площадки."""

from __future__ import annotations

import logging
import re

from zakupki_parser.okpd import load_okpd_tree, resolve_codes_to_paths

logger = logging.getLogger(__name__)


def _resolve_paths(codes: list[str], tree_file: str | None, label: str) -> list[str] | None:
    """Резолвит коды (ОКПД2/регион) в пути через маппинг площадки.

    Возвращает None, если маппинг недоступен (коды не применятся).
    """
    if not codes:
        return []
    if not tree_file:
        logger.warning("%s коды заданы, но search-маппинг не указан", label)
        return None
    try:
        tree = load_okpd_tree(tree_file)
        return resolve_codes_to_paths(codes, tree, label=label)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить %s дерево %s: %s", label, tree_file, exc)
        return None


def _digits(code: str) -> str:
    return re.sub(r"\D", "", code)


def _nearest_ancestor_id(code: str, code_to_id: dict[str, str]) -> str | None:
    """id ближайшего предка кода (по цифровому префиксу) или None."""
    digits = _digits(code)
    best_len = 0
    best_id: str | None = None
    for c, cid in code_to_id.items():
        key_digits = _digits(c)
        if key_digits and digits.startswith(key_digits) and len(key_digits) > best_len:
            best_len = len(key_digits)
            best_id = cid
    return best_id


def _resolve_okpd2_eis(codes: list[str], tree_file: str | None) -> dict[str, str] | None:
    """Резолвит коды ОКПД2 для ЕИС в параметр ``okpd2Ids``.

    Возвращает ``{"okpd2Ids": ...}`` — только собственные id выбранных кодов.
    Дочерние узлы подключаются флагом ``okpd2IdsWithNested=on`` (в статических
    query_params), поэтому перечислять всё поддерево не нужно. Для кода без
    собственного id берётся ближайший предок.
    """
    if not codes:
        return None
    if not tree_file:
        logger.warning("ОКПД2 коды заданы, но search-маппинг (ЕИС) не указан")
        return None
    try:
        tree = load_okpd_tree(tree_file)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить дерево ОКПД2 ЕИС %s: %s", tree_file, exc)
        return None

    code_to_id = tree.get("code_to_id") or {}
    ids: list[str] = []
    for code in codes:
        cid = code_to_id.get(code)
        if cid is None:
            cid = _nearest_ancestor_id(code, code_to_id)
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        return None
    return {"okpd2Ids": ",".join(ids)}


def _resolve_okpd2_ids(codes: list[str], tree_file: str | None) -> list[str] | None:
    """Резолвит коды ОКПД2 в внутренние ID площадки для массива ``okpd2[]``.

    Фабрикант фильтрует по ОКПД2 через ``okpd2[]=<opaque-id>`` (не коды):
    id берутся из дерева площадки (``code_to_id``). Код без собственного id
    резолвится в id ближайшего предка (по цифровому префиксу). Если дерево
    не задано (или в нём нет ``code_to_id``) — возвращается None: вызывающий
    передаёт коды как есть (например, etpgpb с префиксным матчингом сервера).
    """
    if not codes:
        return []
    if not tree_file:
        return None
    try:
        tree = load_okpd_tree(tree_file)
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось загрузить дерево ОКПД2 %s: %s", tree_file, exc)
        return None
    code_to_id = tree.get("code_to_id") or {}
    if not code_to_id:
        return None
    ids: list[str] = []
    for code in codes:
        cid = code_to_id.get(code)
        if cid is None:
            cid = _nearest_ancestor_id(code, code_to_id)
        if cid and cid not in ids:
            ids.append(cid)
    return ids
