"""Генерация описаний конфигурации (схем форм) из pydantic-моделей.

По дескрипторам фронтенд строит «удобную страницу управления параметрами»
(вкладки «Параметры мониторинга», «Конфигурация», «Управление Логи», «Парсер»).
Описания и типы берутся из полей pydantic-моделей (``Field(description=...)``);
поля-секреты (token/secret/internal_token) в форму не выводятся — они
управляются через env.
"""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

# Поля, управляемые через env (секреты) — в форме не редактируются.
SECRET_FIELD_NAMES = {"secret", "token", "internal_token"}


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Снимает ``X | None`` / ``Optional[X]``: возвращает (X, True)."""
    args = get_args(annotation)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and len(args) == 2:
            return non_none[0], True
    return annotation, False


def _label_from_description(description: str | None, key: str) -> str:
    """Человекочитаемая подпись поля: первое предложение описания или имя."""
    if description:
        first = description.strip()
        for sep in (". ", "\n", "("):
            first = first.split(sep)[0].strip()
        if first:
            return first
    return key.replace("_", " ")


def _default_value(field_info: Any) -> Any:
    if field_info.default is not PydanticUndefined:
        return field_info.default
    return None


def _describe_type(
    annotation: Any,
    options_overrides: dict[str, list[Any]] | None,
    path: str,
) -> tuple[str, dict[str, Any]]:
    """Возвращает (kind, доп. дескриптор) для аннотации поля."""
    annotation, _optional = _unwrap_optional(annotation)
    origin = get_origin(annotation)

    if origin is Literal:
        return "select", {"options": [str(a) for a in get_args(annotation)]}

    if origin is list:
        (item_type,) = get_args(annotation)
        item_type, _ = _unwrap_optional(item_type)
        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
            return "list", {"item": _describe_model(item_type, options_overrides, path)}
        return "tags", {}

    if origin is tuple:
        # Кортежи (например, range задержек браузера) — текстовое представление.
        return "text", {}

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "object", {"fields": _describe_model(annotation, options_overrides, path)}

    if annotation is bool:
        return "bool", {}
    if annotation is int:
        return "int", {}
    if annotation is float:
        return "float", {}
    return "str", {}


def _describe_model(
    model: type[BaseModel],
    options_overrides: dict[str, list[Any]] | None = None,
    path_prefix: str = "",
) -> list[dict[str, Any]]:
    """Дескрипторы полей модели (рекурсивно, без секретов)."""
    fields: list[dict[str, Any]] = []
    for name, finfo in model.model_fields.items():
        if name in SECRET_FIELD_NAMES:
            continue
        path = f"{path_prefix}.{name}" if path_prefix else name
        kind, extra = _describe_type(finfo.annotation, options_overrides, path)
        desc: dict[str, Any] = {
            "key": name,
            "label": _label_from_description(finfo.description, name),
            "kind": kind,
            "description": finfo.description,
            "default": _default_value(finfo),
            "required": finfo.is_required(),
        }
        desc.update(extra)
        # Переопределение выбора значений (например, sites.platform_id — список
        # площадок из dom-конфигов) превращает поле в select.
        if options_overrides and path in options_overrides:
            desc["kind"] = "select"
            desc["options"] = options_overrides[path]
        fields.append(desc)
    return fields


def build_schema(
    model: type[BaseModel],
    options_overrides: dict[str, list[Any]] | None = None,
) -> list[dict[str, Any]]:
    """Схема конфигурации для веб-формы (список дескрипторов корневых полей).

    ``options_overrides`` — словарь «путь поля» → допустимые значения для
    select-полей, не заданных Literal-типом (например, ``sites.platform_id``).
    """
    return _describe_model(model, options_overrides)
