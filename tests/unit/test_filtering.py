"""Тесты клиентской пост-фильтрации закупок по ключевым словам (R9)."""

from __future__ import annotations

from zakupki_parser.parser.filtering import (
    exclusions_present,
    keywords_match,
    region_match,
)


def _record(subject: str) -> dict[str, str]:
    return {"number": "N-1", "subject": subject}


def test_keywords_match_plain_word() -> None:
    assert keywords_match(_record("Разработка ИИ-ассистента"), ["ИИ"])
    assert not keywords_match(_record("Ремонт помещения"), ["ИИ"])
    # Пустой список слов — фильтра нет.
    assert keywords_match(_record("Любая закупка"), [])


def test_keywords_match_stem() -> None:
    # «разработ*» ловит «разработка», «разработке», «разработку».
    assert keywords_match(_record("Разработка программного обеспечения"), ["разработ*"])
    assert keywords_match(_record("в разработке систем"), ["разработ*"])
    assert not keywords_match(_record("Поставка готового ПО"), ["разработ*"])


def test_keywords_match_multiword_phrase() -> None:
    # Все токены фразы (со стеб-префиксами) присутствуют.
    assert keywords_match(
        _record("Внедрение информационных систем на предприятии"),
        ["внедрен* информацион* систем*"],
    )
    assert not keywords_match(_record("Внедрение CRM"), ["внедрен* информацион* систем*"])


def test_keywords_match_proximity() -> None:
    # (автоматизир* систем* учет*)~2 — не более 2 слов между токенами.
    expr = "(автоматизир* систем* учет*)~2"
    assert keywords_match(_record("Автоматизированная система бухгалтерского учета"), [expr])
    assert not keywords_match(_record("Учет закупок в старой автоматизированной системе"), [expr])


def test_proximity_window_examples() -> None:
    """~N = не более N слов МЕЖДУ токенами (как в Lucene-slack)."""
    cases = [
        ("(систем* учет*)~0", "система учета", True),
        ("(систем* учет*)~0", "система коммерческого учета", False),
        ("(систем* учет*)~1", "система учета", True),
        ("(систем* учет*)~1", "система коммерческого учета", True),
        ("(систем* учет*)~1", "система автоматизированного коммерческого учета", False),
        ("(систем* учет*)~2", "система автоматизированного коммерческого учета", True),
    ]
    for expr, subject, expected in cases:
        assert keywords_match(_record(subject), [expr]) is expected, (expr, subject)


def test_keywords_match_exact_phrase() -> None:
    assert keywords_match(_record("поставка 1С Документооборот"), ["1С Документооборот"])
    assert not keywords_match(_record("поставка 1С Зарплата"), ["1С Документооборот"])


def test_exclusions_present() -> None:
    assert exclusions_present(_record("Ремонт и модернизация"), ["ремонт"])
    assert exclusions_present(_record("Ремонтные работы"), ["ремонт*"])
    assert not exclusions_present(_record("Разработка ИИ"), ["ремонт"])
    # Пустой список — ничего не исключается.
    assert not exclusions_present(_record("Любая закупка"), [])


def test_empty_subject() -> None:
    # Пустое описание: позитивные слова не совпадают (закупка отбрасывается),
    # слова-исключения — не срабатывают.
    assert not keywords_match({"number": "N", "subject": ""}, ["ИИ"])
    assert not exclusions_present({"number": "N", "subject": ""}, ["ремонт"])


def test_matched_keywords_returns_hits() -> None:
    from zakupki_parser.parser.filtering import matched_keywords

    record = {"number": "N", "subject": "Разработка программного обеспечения и внедрение ИИ"}
    assert matched_keywords(record, ["разработк* программ*", "внедрен* ИИ", "ремонт"]) == [
        "разработк* программ*",
        "внедрен* ИИ",
    ]


def test_matched_keywords_empty_for_no_hits() -> None:
    from zakupki_parser.parser.filtering import matched_keywords

    record = {"number": "N", "subject": "Оказание услуг по уборке"}
    assert matched_keywords(record, ["разработк* программ*", "ИИ"]) == []


def test_matched_keywords_empty_subject() -> None:
    from zakupki_parser.parser.filtering import matched_keywords

    assert matched_keywords({"number": "N", "subject": ""}, ["ИИ"]) == []


def _region_record(region: str) -> dict[str, str]:
    return {"number": "N-1", "region": region}


def test_region_match_empty_targets() -> None:
    # Пустой список целевых регионов — фильтра нет (как пустые ключевые слова).
    assert region_match(_region_record("Москва"), [])
    assert region_match({"number": "N", "region": ""}, [])


def test_region_match_template_stem() -> None:
    # Шаблон-стем как у ключевых слов: «московск*» ловит «Московская», но не «Москва».
    assert region_match(_region_record("Московская область"), ["московск*"])
    assert region_match(_region_record("город Московской области"), ["московск*"])
    assert not region_match(_region_record("Москва"), ["московск*"])
    assert not region_match(_region_record("Санкт-Петербург"), ["московск*"])
    # Регистронезависимость шаблона.
    assert region_match(_region_record("МОСКОВСКАЯ ОБЛАСТЬ"), ["Московск*"])


def test_region_match_template_phrase() -> None:
    # Фраза-шаблон из нескольких стемов: оба токена присутствуют в регионе.
    assert region_match(_region_record("Московская область"), ["Московск* обл*"])
    assert region_match(_region_record("Область московская"), ["Московск* обл*"])
    assert not region_match(_region_record("Новосибирская область"), ["Московск* обл*"])
    assert not region_match(_region_record("Московская губерния"), ["Московск* обл*"])


def test_region_match_template_any_of_multiple() -> None:
    # Множественные целевые регионы — достаточно совпадения хотя бы одного.
    targets = ["Московск* обл*", "санкт-петербург"]
    assert region_match(_region_record("Санкт-Петербург"), targets)
    assert region_match(_region_record("Московская область"), targets)
    assert not region_match(_region_record("Новосибирская область"), targets)


def test_region_match_plain_and_template_mix() -> None:
    # Литеральное значение и шаблон в одном списке.
    targets = ["Казань", "московск* обл*"]
    assert region_match(_region_record("Республика Татарстан, г. Казань"), targets)
    assert region_match(_region_record("Московская область"), targets)
    assert not region_match(_region_record("Екатеринбург"), targets)


def test_region_match_exact_ignores_case() -> None:
    assert region_match(_region_record("Москва"), ["москва"])
    assert region_match(_region_record("  Москва  "), ["москва"])
    assert region_match(_region_record("МОСКВА"), ["Москва"])


def test_region_match_contains_either_direction() -> None:
    # Целевой регион может быть как точным названием субъекта, так и его частью.
    assert region_match(_region_record("Московская область"), ["область"])
    assert region_match(_region_record("г. Санкт-Петербург"), ["санкт-петербург"])
    assert region_match(_region_record("Область"), ["Московская область"])


def test_region_match_hyphen_spaces_in_delivery_address() -> None:
    # Адрес поставки b2b-center: «г. Санкт - Петербург, …» — пробелы вокруг дефиса
    # игнорируются для литерального целевого региона.
    address = "Россия, г. Санкт - Петербург, 196643, поселок Понтонный, ул. Фанерная, д. 5"
    assert region_match({"number": "N", "region": address}, ["Санкт-Петербург"])
    assert region_match({"number": "N", "region": address}, ["Санкт"])
    assert region_match({"number": "N", "region": address}, ["Понтонный"])
    assert not region_match({"number": "N", "region": address}, ["Москва"])
    # Шаблон-фраза по-прежнему работает по отдельным словам значения.
    assert region_match({"number": "N", "region": address}, ["санкт* петербург*"])


def test_region_match_no_match() -> None:
    assert not region_match(_region_record("Москва"), ["Санкт-Петербург"])
    assert not region_match(_region_record("Новосибирская область"), ["Москва"])


def test_region_match_empty_record_region() -> None:
    # Регион закупки неизвестен — целевым регионам не соответствует.
    assert not region_match(_region_record(""), ["Москва"])
    assert not region_match({"number": "N"}, ["Москва"])


def test_region_match_prefers_record_region_over_detail() -> None:
    record = {"number": "N", "region": "Москва", "detail_json": {"region": "Казань"}}
    assert region_match(record, ["Москва"])
    assert not region_match(record, ["Казань"])


def test_region_match_falls_back_to_detail_json() -> None:
    assert region_match({"number": "N", "detail_json": {"region": "Москва"}}, ["москва"])
    assert not region_match({"number": "N", "detail_json": {"region": "Москва"}}, ["Казань"])
