"""Разбор названий из тегов OSM: вынимаем тип улицы и название без типа.

В казахстанском OSM тип зашит в имя и записан по-разному:
  «проспект Абая», «Абай даңғылы», «Б.Майлин улица», «Алматинская улица»,
  «Автомобилист шағын ауданы», «5-й микрорайон», «микрорайон Солнечный».
Функции чистые (без osmium/shapely), поэтому их можно тестировать отдельно.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

# слово -> канонический тип
TYPE_WORDS: Dict[str, str] = {
    # русский
    "улица": "улица", "ул": "улица",
    "проспект": "проспект", "пр-т": "проспект", "пр-кт": "проспект", "просп": "проспект", "пр": "проспект",
    "переулок": "переулок", "пер": "переулок",
    "бульвар": "бульвар", "бул": "бульвар", "б-р": "бульвар",
    "проезд": "проезд", "тупик": "тупик", "шоссе": "шоссе", "тракт": "тракт",
    "площадь": "площадь", "набережная": "набережная",
    "микрорайон": "микрорайон", "мкр": "микрорайон", "мкрн": "микрорайон", "мкр-н": "микрорайон",
    "квартал": "квартал",
    # казахский
    "көшесі": "улица", "көше": "улица",
    "даңғылы": "проспект", "даңғыл": "проспект",
    "тасжолы": "шоссе", "тұйығы": "тупик", "алаңы": "площадь",
    "шағынауданы": "микрорайон",
}
# двухсловные казахские типы (суффикс)
TWO_WORD_SUFFIX: Dict[Tuple[str, str], str] = {
    ("шағын", "ауданы"): "микрорайон",
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def split_type(name: str) -> Tuple[Optional[str], str]:
    """«проспект Абая» -> ('проспект', 'Абая'); «Абай даңғылы» -> ('проспект', 'Абай');
    «Степная» -> (None, 'Степная'). Тип берём только с краёв и только если остаётся название."""
    name = _clean(name)
    toks = name.split(" ")
    if len(toks) < 2:
        return None, name
    low = [t.lower().strip(".,") for t in toks]
    # двухсловный суффикс
    if len(toks) >= 3 and (low[-2], low[-1]) in TWO_WORD_SUFFIX:
        return TWO_WORD_SUFFIX[(low[-2], low[-1])], " ".join(toks[:-2])
    if low[0] in TYPE_WORDS:
        return TYPE_WORDS[low[0]], " ".join(toks[1:])
    if low[-1] in TYPE_WORDS:
        return TYPE_WORDS[low[-1]], " ".join(toks[:-1])
    return None, name


def _split_multi(v: Optional[str]) -> List[str]:
    return [p.strip() for p in (v or "").split(";") if p.strip()]


def name_variants(tags: Dict[str, str], base_key: str = "name") -> List[Tuple[Optional[str], str]]:
    """Все варианты названия объекта: [(тип|None, название_без_типа), ...] без повторов.
    Порядок: name:ru, name, name:kk, alt_name. old_name НЕ берём (старые названия дают ложные совпадения)."""
    out: List[Tuple[Optional[str], str]] = []
    seen = set()
    for k in (f"{base_key}:ru", base_key, f"{base_key}:kk", "alt_name" if base_key == "name" else f"{base_key}:alt"):
        for raw in _split_multi(tags.get(k)):
            t, base = split_type(raw)
            sig = (t, base.lower())
            if base and sig not in seen:
                seen.add(sig)
                out.append((t, base))
    return out


def pick_type(variants: Iterable[Tuple[Optional[str], str]]) -> Optional[str]:
    for t, _ in variants:
        if t:
            return t
    return None


def display_name(variants: List[Tuple[Optional[str], str]]) -> str:
    return variants[0][1] if variants else ""


# ---------------------------------------------------------------- города / районы
_CITY_PREFIX = re.compile(r"^(?:город|г\.|пос\.|посёлок|поселок|село|аул|станция|с\.)\s+(?:имени\s+)?", re.I)
_CITY_SUFFIX = re.compile(r"\s+(?:қаласы|ауылы|кенті|станциясы|ауылдық округі)$", re.I)


def clean_city_name(name: str) -> str:
    n = _clean(name)
    n = _CITY_PREFIX.sub("", n)
    n = _CITY_SUFFIX.sub("", n)
    return n.strip()


_CITY_ADM = re.compile(r"(?:(?<!\w)г\.?\s?а\.?$|(?<!\w)қ\.?\s?ә\.?$|городская администрация)", re.I)
_DISTRICT_WORDS = re.compile(
    r"(?<!\w)(?:район|р-н|ауданы|аудан|городская администрация|ауылдық округі|сельский округ)(?!\w)\.?", re.I)


def clean_district_name(name: str) -> str:
    """«Ауэзовский район» -> «Ауэзовский»; «район Есиль» -> «Есиль»; «Әуезов ауданы» -> «Әуезов»;
    «Актобе Г.А.» -> «Актобе»."""
    n = _CITY_ADM.sub("", _clean(name))
    n = _DISTRICT_WORDS.sub(" ", n)
    return _clean(n)


def is_city_administration(name: str) -> bool:
    """«Актобе Г.А.», «Ақтөбе Қ.Ә.», «Карагандинская городская администрация»."""
    return bool(_CITY_ADM.search(_clean(name)))
