"""Метрика по правилам жюри (из ТЗ).

* Сравнение по каждому полю отдельно: нижний регистр, без пунктуации и лишних
  пробелов. Падежи/опечатки не прощаются. Номера сравниваются как строки.
* Поле учитывается только там, где в эталоне оно НЕ null. Если в эталоне null
  (в строке данных нет), запись по этому полю не считается ни верной, ни
  ошибочной, даже если решение подняло значение из справочника.
* Веса: область/город/название улицы/дом = 60%, квартира/подъезд/этаж = 25%,
  район/жилой массив = 15%. street_type в метрику не входит.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .textutil import cmp_norm

GROUPS: List[Tuple[str, float, Tuple[str, ...]]] = [
    ("core", 0.60, ("province", "city", "street_name", "house")),
    ("tail", 0.25, ("apartment", "entrance", "floor")),
    ("area", 0.15, ("district", "living_area")),
]


def field_stats(preds: List[dict], golds: List[dict]) -> Dict[str, Tuple[int, int]]:
    """{поле: (верно, всего_в_эталоне)}"""
    stats: Dict[str, List[int]] = {}
    for _, _, fields in GROUPS:
        for f in fields:
            stats[f] = [0, 0]
    for p, g in zip(preds, golds):
        for f in stats:
            gv = g.get(f)
            if gv is None:
                continue
            stats[f][1] += 1
            if cmp_norm(p.get(f)) == cmp_norm(gv):
                stats[f][0] += 1
    return {k: (v[0], v[1]) for k, v in stats.items()}


def score(preds: List[dict], golds: List[dict]) -> Tuple[float, Dict[str, Tuple[int, int]], Dict[str, float]]:
    st = field_stats(preds, golds)
    total = 0.0
    group_scores: Dict[str, float] = {}
    for name, weight, fields in GROUPS:
        accs = [st[f][0] / st[f][1] for f in fields if st[f][1] > 0]
        g = sum(accs) / len(accs) if accs else 0.0
        group_scores[name] = g
        total += weight * g
    return total, st, group_scores
