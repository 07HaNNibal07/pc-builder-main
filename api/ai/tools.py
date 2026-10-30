"""Strict LangChain tools over the existing deterministic selector.

No user text is executed: tool arguments are validated by LangChain/Pydantic and the
only callable functions below are explicit wrappers around ``collector.advisor``.
"""
from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import tool

from collector import advisor, perf


def _catalog() -> dict:
    return advisor.load()


def _json_result(callback, *args, **kwargs) -> str:
    try:
        return json.dumps({"ok": True, "data": callback(_catalog(), *args, **kwargs)}, ensure_ascii=False)
    except KeyError as exc:
        return json.dumps({"ok": False, "error": str(exc.args[0])}, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": "Невозможно обработать параметры подбора"}, ensure_ascii=False)


@tool
def recommend_pc(
    budget: int,
    task: Literal["game", "work", "office"] = "game",
    resolution: Literal["1080p", "1440p", "4K"] = "1440p",
    cpu_vendor: Literal["AMD", "Intel"] | None = None,
    gpu_vendor: Literal["NVIDIA", "AMD", "Intel"] | None = None,
    form_factor: Literal["ITX", "mATX", "ATX"] | None = None,
) -> str:
    """Подобрать реальную совместимую конфигурацию по бюджету, задаче и предпочтениям."""
    if not 15_000 <= budget <= 2_000_000:
        return json.dumps({"ok": False, "error": "Бюджет должен быть от 15 000 до 2 000 000 ₽"}, ensure_ascii=False)
    return _json_result(
        advisor.advise, budget, task, perf.RES.index(resolution),
        cpu_vendor=cpu_vendor, gpu_vendor=gpu_vendor, max_form=form_factor,
    )


@tool
def get_build_details(ids: dict[str, str], ram_qty: int = 1, drives: list[str] | None = None) -> str:
    """Получить цену, совместимость и таблицу FPS для уже известной сборки по id деталей."""
    if not 1 <= ram_qty <= 8:
        return json.dumps({"ok": False, "error": "Количество комплектов памяти должно быть от 1 до 8"}, ensure_ascii=False)
    return _json_result(advisor.build_info, ids, ram_qty, drives or [])


@tool
def find_component_options(
    ids: dict[str, str],
    part: Literal["cpu", "gpu", "mb", "ram", "ssd", "cooler", "case", "psu", "drive"],
    query: str = "",
    limit: int = 20,
    ram_qty: int = 1,
    drives: list[str] | None = None,
) -> str:
    """Найти реальные альтернативные комплектующие, подходящие к существующей сборке."""
    return _json_result(advisor.options, ids, part, query[:100], max(1, min(limit, 80)), ram_qty, drives or [])


@tool
def suggest_upgrade(
    ids: dict[str, str],
    money: int,
    task: Literal["game", "work", "office"] = "game",
    resolution: Literal["1080p", "1440p", "4K"] = "1440p",
    ram_qty: int = 1,
    resale: bool = False,
    drives: list[str] | None = None,
) -> str:
    """Предложить реальное улучшение существующей сборки в пределах доплаты."""
    if not 1_000 <= money <= 1_000_000:
        return json.dumps({"ok": False, "error": "Сумма на улучшение должна быть от 1 000 до 1 000 000 ₽"}, ensure_ascii=False)
    return _json_result(
        advisor.upgrade_build, ids, money, task, perf.RES.index(resolution), ram_qty, resale=resale, drives=drives or [],
    )


TOOLS = [recommend_pc, get_build_details, find_component_options, suggest_upgrade]
