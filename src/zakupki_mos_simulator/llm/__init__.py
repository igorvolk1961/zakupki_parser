"""LLM-клиент, промпты и пайплайн генерации/валидации тестовой выборки."""

from zakupki_mos_simulator.llm.client import LLMClient, LLMError
from zakupki_mos_simulator.llm.generate import (
    assign_metadata,
    build_customers,
    build_demo_dataset,
    generate_dataset,
    generate_with_llm,
    read_competencies,
)
from zakupki_mos_simulator.llm.validate import (
    POSITIVE_CATEGORIES,
    Metrics,
    evaluate,
    validate_cli,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "Metrics",
    "POSITIVE_CATEGORIES",
    "assign_metadata",
    "build_customers",
    "build_demo_dataset",
    "evaluate",
    "generate_dataset",
    "generate_with_llm",
    "read_competencies",
    "validate_cli",
]
