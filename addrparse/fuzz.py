"""Тонкая обёртка над RapidFuzz.

В Docker ставится настоящий rapidfuzz (быстрый). Если его нет (например, в
песочнице без интернета), используется запасной вариант на difflib, чтобы
парсер и тесты работали везде. Интерфейс одинаковый.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

try:  # pragma: no cover - зависит от окружения
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process

    HAVE_RAPIDFUZZ = True

    def ratio(a: str, b: str) -> float:
        return float(_rf_fuzz.ratio(a, b))

    def best(query: str, choices: Iterable[str], cutoff: float = 0.0) -> Optional[Tuple[str, float]]:
        res = _rf_process.extractOne(query, list(choices), scorer=_rf_fuzz.ratio, score_cutoff=cutoff)
        if res is None:
            return None
        return res[0], float(res[1])

except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    HAVE_RAPIDFUZZ = False

    def ratio(a: str, b: str) -> float:
        if not a and not b:
            return 100.0
        return 100.0 * SequenceMatcher(None, a, b).ratio()

    def best(query: str, choices: Iterable[str], cutoff: float = 0.0) -> Optional[Tuple[str, float]]:
        top: Optional[Tuple[str, float]] = None
        for c in choices:
            # быстрый отсев по длине, чтобы не гонять SequenceMatcher впустую
            if abs(len(c) - len(query)) > max(3, len(query) // 2):
                continue
            sc = ratio(query, c)
            if sc >= cutoff and (top is None or sc > top[1]):
                top = (c, sc)
        return top
