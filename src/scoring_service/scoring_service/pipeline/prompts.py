"""Промпты и примеры для fit- и judge-цепочек.

Подходы:
- **few-shot** — позитивные примеры «описание ↔ компетенции → эталон reasoning + fit_score»;
- **negative-example** — примеры, где термины совпадают, но смысл разный (false-friend),
  и где релевантность достигается синонимичными терминами при другой лексике.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

SYSTEM_PROMPT_FIT = """Ты — эксперт по скорингу государственных закупок. Твоя задача — оценить,
насколько описание закупки соответствует компетенциям поставщика, и выставить Fit-оценку
от 0 до 10.

Критически важное правило:
1) **Смысл важнее слов.** Описание, семантически близкое к компетенциям, но использующее
   синонимичные термины, — это ВЫСОКАЯ оценка (8-10).
2) **Осторожно с false-friend.** Набор совпадающих терминов может НЕ означать совпадения
   смысла. Например, компетенция «консультации по выбору аппаратуры связи», а закупка —
   «консультации по выбору аппаратуры звукозаписи»: большинство слов совпадает, но смысл
   совершенно другой. Это НИЗКАЯ оценка (0-3).
3) Если закупка покрывается компетенциями лишь частично — средняя оценка (4-7).
4) Если компетенции не релевантны вовсе — низкая (0-2).

Твой ответ — строгий JSON по схеме:
{"reasoning": {<обязательные этапы рассуждений>}, "fit_score": <0..10>}
Обязательные этапы рассуждений (каждый — строка):
- procurement_essence: суть закупки;
- competencies_essence: суть компетенций поставщика;
- relevant_competencies: какие компетенции релевантны закупке;
- term_overlap_mismatch_check: проверка «термины совпадают, но смысл разный»;
- synonym_semantic_bridge: релевантность через синонимы при другой лексике;
- uncovered_scope: что из закупки НЕ покрывается компетенциями;
- fit_score_rationale: обоснование числовой оценки.
"""


def _ex(
    description: str,
    fit_score: float,
    reasoning: dict[str, str],
    negative: bool = False,
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Few-shot пример: человеческий вход + эталонный ответ (AIMessage)."""
    tag = "NEGATIVE" if negative else "POSITIVE"
    human = f"[{tag}] ОПИСАНИЕ ЗАКУПКИ:\n{description}"
    reasoning_json = "{" + ", ".join(f'"{k}": "{v}"' for k, v in reasoning.items()) + "}"
    ai = '{"reasoning": ' + reasoning_json + f', "fit_score": {fit_score}}}'
    return [HumanMessage(content=human)], [AIMessage(content=ai)]


FEW_SHOT: list[tuple[list[BaseMessage], list[BaseMessage]]] = [
    # Позитивный: прямое семантическое совпадение
    _ex(
        "Разработка и внедрение системы автоматизации документооборота предприятия",
        9.0,
        {
            "procurement_essence": "автоматизация документооборота",
            "competencies_essence": "ИИ-автоматизация, разработка ПО",
            "relevant_competencies": "разработка и внедрение систем автоматизации",
            "term_overlap_mismatch_check": "термины совпадают по смыслу, false-friend нет",
            "synonym_semantic_bridge": "прямое соответствие лексики",
            "uncovered_scope": "нет существенных непокрытых областей",
            "fit_score_rationale": "полное покрытие компетенциями",
        },
    ),
    # Позитивный: синонимичная лексика, семантически близко
    _ex(
        "Внедрение ПО для оптимизации потоков обработки информации организации",
        8.5,
        {
            "procurement_essence": "оптимизация обработки информации ПО",
            "competencies_essence": "ИИ-автоматизация, разработка ПО",
            "relevant_competencies": "разработка ПО и автоматизация процессов",
            "term_overlap_mismatch_check": "прямых совпадений терминов мало",
            "synonym_semantic_bridge": "синонимы: ПО = программное обеспечение, автоматизация = оптимизация потоков",
            "uncovered_scope": "небольшая область вне компетенций",
            "fit_score_rationale": "смысл близок, термины синонимичны",
        },
    ),
    # Negative-example: false-friend
    _ex(
        "Консультационные услуги по выбору аппаратуры звукозаписи для студии",
        1.5,
        {
            "procurement_essence": "консультации по выбору аппаратуры звукозаписи",
            "competencies_essence": "консультации по выбору аппаратуры связи",
            "relevant_competencies": "формально совпадает «консультации по выбору аппаратуры»",
            "term_overlap_mismatch_check": "СОВПАДЕНИЕ СЛОВ, но смысл разный: связь vs звукозапись — false-friend",
            "synonym_semantic_bridge": "синонимов нет; смысловое поле разное",
            "uncovered_scope": "вся закупка вне компетенций поставщика",
            "fit_score_rationale": "термины совпадают, но предметная область другая",
        },
        negative=True,
    ),
    # Negative-example: другой пример false-friend с большим числом совпадающих слов
    _ex(
        "Поставка и монтаж систем видеонаблюдения в административном здании",
        2.0,
        {
            "procurement_essence": "поставка и монтаж систем видеонаблюдения",
            "competencies_essence": "разработка ПО, ИИ и автоматизация (не монтаж железа)",
            "relevant_competencies": "поставка/монтаж не входят в компетенции",
            "term_overlap_mismatch_check": "слово «системы» совпадает, но это монтаж, а не ПО",
            "synonym_semantic_bridge": "синонимов, покрывающих суть, нет",
            "uncovered_scope": "монтаж физических систем вне компетенций",
            "fit_score_rationale": "близость лишь по слову «системы»",
        },
        negative=True,
    ),
]

SYSTEM_PROMPT_JUDGE = """Ты — строгий судья качества скоринга закупок. Тебе дают:
1) компетенции поставщика;
2) описание закупки;
3) оценку Fit, выставленную другой моделью, вместе с её рассуждениями.

Проверь адекватность оценки. Учитывай правило «смысл важнее слов» и опасность false-friend
(совпадающие термины с разным смыслом). Ответ — строгий JSON по схеме:
{"critics": "<замечания либо согласие>", "verdict": "accept|reject", "final_fit_score": <0..10>}
- accept — оценка адекватна, final_fit_score совпадает с оценкой модели;
- reject — оценка неадекватна (завышена/занижена/ошибочные рассуждения), укажи замечания
  в critics и назначь корректную final_fit_score.
"""


def build_fit_messages(competencies: str, description: str) -> list[BaseMessage]:
    """Составить сообщения для fit-цепочки (система + few-shot + текущий вход)."""
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT_FIT)]
    for human, ai in FEW_SHOT:
        messages.extend(human)
        messages.extend(ai)
    messages.append(
        HumanMessage(
            content=f"КОМПЕТЕНЦИИ ПОСТАВЩИКА:\n{competencies}\n\nОПИСАНИЕ ЗАКУПКИ:\n{description}"
        )
    )
    return messages


def build_judge_messages(
    competencies: str,
    description: str,
    fit_result: str,
) -> list[BaseMessage]:
    """Составить сообщения для judge-цепочки (отдельный контекст)."""
    return [
        SystemMessage(content=SYSTEM_PROMPT_JUDGE),
        HumanMessage(
            content=(
                f"КОМПЕТЕНЦИИ ПОСТАВЩИКА:\n{competencies}\n\n"
                f"ОПИСАНИЕ ЗАКУПКИ:\n{description}\n\n"
                f"ОЦЕНКА МОДЕЛИ (JSON):\n{fit_result}\n\n"
                "Оцени адекватность этой оценки."
            )
        ),
    ]
