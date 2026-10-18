"""Нормализация текста для СРАВНЕНИЯ со справочником и для метрики.

Важно: в ответ парсера всегда идёт оригинальный текст из строки.
Ключи из norm_key() нужны только для поиска и сопоставления.
"""
from __future__ import annotations

import re

# латинские буквы-двойники -> кириллица (для сопоставления "Almaty"-подобных смесей)
_LAT2CYR = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м", "t": "т", "h": "н", "b": "в",
})
# казахские буквы -> русские аналоги
_KK2RU = str.maketrans({
    "ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о",
    "ү": "у", "ұ": "у", "һ": "х", "і": "и", "ё": "е",
})

_ORD = re.compile(r"(\d+)\s*-\s*(?:й|ый|ой|ий|ая|ое|ое|ше|ші|шы|ст|nd|th)(?![^\W\d_])")

KK_LETTERS = set("әғқңөүұһі")
KK_WORDS = {
    "қаласы", "қала", "облысы", "ауданы", "аудан", "көшесі", "көше",
    "даңғылы", "ауылы", "кенті", "шағынауданы",
}


_LAT_DI = (("shch", "щ"), ("sch", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"),
           ("yu", "ю"), ("ya", "я"), ("yo", "е"), ("ye", "е"), ("ay", "ай"), ("ey", "ей"), ("oy", "ой"),
           ("uy", "уй"), ("iy", "ий"))
_LAT_ONE = str.maketrans({
    "a": "а", "b": "б", "c": "с", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "ж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "ы", "z": "з",
})
_HAS_LAT = re.compile(r"[a-z]")
_HAS_CYR = re.compile(r"[а-яё" + "".join(sorted({"ә", "ғ", "қ", "ң", "ө", "ү", "ұ", "һ", "і"})) + "]")


def translit_lat(tok: str) -> str:
    """'almaty' -> 'алматы', 'abaya' -> 'абая': приближённо, только для сопоставления."""
    for a, b in _LAT_DI:
        tok = tok.replace(a, b)
    return tok.translate(_LAT_ONE)


def _fix_script(tok: str) -> str:
    if _HAS_LAT.search(tok) and not _HAS_CYR.search(tok):
        return translit_lat(tok)                  # слово целиком латиницей -> транслит
    return tok.translate(_LAT2CYR)                # смесь: латинские буквы-двойники -> кириллица


def norm_key(s: str) -> str:
    """Ключ для сопоставления: нижний регистр, латиница -> кириллица, kk->ru, без пунктуации,
    дефис = пробел ('Самал-2' == 'самал 2')."""
    s = " ".join(_fix_script(t) for t in s.lower().split(" "))
    s = s.translate(_KK2RU)
    s = _ORD.sub(r"\1", s)                         # «5-й», «2-ой», «60-ші» -> «5», «2», «60»
    s = re.sub(r"[^\w\s/]", " ", s).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def kk_score(s: str) -> int:
    """Сколько в тексте казахских признаков (спец. буквы + служебные слова)."""
    low = s.lower()
    score = sum(1 for ch in low if ch in KK_LETTERS)
    score += sum(1 for w in re.findall(r"[^\W\d_]+", low) if w in KK_WORDS)
    return score


def cmp_norm(s):
    """Как сравнивает жюри: нижний регистр, без пунктуации и лишних пробелов.
    Слэш оставляем (строже жюри): '1/2' не должно сливаться с '12'."""
    if s is None:
        return None
    s = str(s).lower()
    s = re.sub(r"[^\w\s/]", " ", s).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()
