"""Парсер казахстанских адресов: правила + словарь.

Два слоя (см. ТЗ):
  extract - берёт ТОЛЬКО куски исходной строки (это и есть метрика);
  enrich  - добивает пустые поля из справочника, никогда не перезаписывая
            то, что нашлось в строке (оценивается жюри на защите).
Для каждого поля хранится источник: 'string' | 'reference'.
"""
from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import fuzz
from .refdata import City, RANK_HAMLET, RANK_TOWN, RANK_CITY, Reference, load_default_reference
from .textutil import kk_score, norm_key

FIELDS = [
    "province", "city", "district", "living_area", "street_type", "street_name",
    "house", "apartment", "entrance", "floor", "landmark",
]

# ------------------------------------------------------------------ словари
TOKEN = re.compile(r"[^\s,;]+|[,;]")
GLUE = "\u2062"      # невидимый разделитель: «11дом» -> «11» GLUE «дом» (помним, что слова были слитно)

NOISE_WORDS = {"тел", "телефон", "whatsapp", "wa", "ватсап", "вотсап", "звонить", "код", "домофон", "дмф", "номер", "tel",
               "phone", "индекс", "index", "қоңырау", "званда", "звонда", "маяк"}
NUM_RE = re.compile(r"^\d+(?:[/-]?[^\W\d_])?(?:/\d+(?:[/-]?[^\W\d_])?)*$")     # 12, 12а, 55/6, 33/1, 3а, 112/A
APT_RE = re.compile(r"^\d+[^\W\d_]?$")
INT_RE = re.compile(r"^\d+$")
ORD_RE = re.compile(r"^(\d+)-(?:й|ый|ой|ая|ое|го|ого|я|е)$", re.I)  # 5-й этаж

MARKERS: Dict[str, str] = {}
for _w in ("кв", "квартира", "квартиры", "квартиру", "кварт", "flat", "kv", "kvartira", "apt", "apartment",
           "оф", "офис", "office", "ofis", "пәтер", "патер", "pater", "pəter"):
    MARKERS[_w] = "apt"
for _w in ("подъезд", "подьезд", "подезд", "подъезда", "подьезда", "подъезде", "под", "подъ",
           "пд", "п", "pod", "podezd", "podyezd", "entrance", "p"):
    MARKERS[_w] = "ent"
for _w in ("этаж", "этажа", "эт", "floor", "etazh", "etaj", "fl", "қабат", "кабат", "qabat", "kabat", "э"):
    MARKERS[_w] = "floor"
for _w in ("дом", "дома", "д", "house", "dom", "үй", "uy", "уй", "d", "hse", "hs"):
    MARKERS[_w] = "house"
SINGLE_AFTER = {"д", "п", "p", "d"}          # короткие маркеры: число только ПОСЛЕ
KEEP_IF_UNBOUND = {"д", "под", "п", "дом", "дома", "dom"}   # "d"/"hse" без числа - шум, их не оставляем            # не удаляем, если число не нашли (может быть частью названия)

STREET_T = {
    "улица": "улица", "ул": "улица", "көшесі": "улица", "көше": "улица", "к-сі": "улица","к-сы": "улица" ,"ксі": "улица","ксы": "улица" ,"кошесы": "улица","кошесі": "улица",
    "кошеси": "улица", "коше": "улица", "koshesi": "улица","ksi": "улица", "кшси": "улица", "кшсі": "улица", "кшесі": "улица", "кшс": "улица", "koshe": "улица", "ulitsa": "улица",
    "street": "улица", "ulica": "улица", "ul": "улица",
    "проспект": "проспект", "пр": "проспект", "пр-т": "проспект", "пр-кт": "проспект",
    "просп": "проспект", "даңғылы": "проспект", "дангылы": "проспект", "даныглы": "проспект",
    "dangyly": "проспект", "danygyly": "проспект", "danygly": "проспект", "dly": "проспект", "prospekt": "проспект",
    "переулок": "переулок", "пер": "переулок", "пер-к": "переулок",
    "бульвар": "бульвар", "бул": "бульвар", "б-р": "бульвар",
    "проезд": "проезд", "пр-д": "проезд", "тупик": "тупик", "шоссе": "шоссе",
    "тракт": "тракт", "площадь": "площадь", "пл": "площадь",
    "набережная": "набережная", "наб": "набережная",
    "per": "переулок", "pereulok": "переулок", "pr": "проспект","ps": "проспект", "pr-t": "проспект", "pr-kt": "проспект",
    "prosp": "проспект", "prospect": "проспект", "prst": "проспект", "prsp": "проспект", "prosp.": "проспект", "d-ly": "проспект", "dang": "проспект",
    "bulvar": "бульвар", "bul": "бульвар", "kosh": "улица", "kosh.": "улица",
}
MICRO_T = {"микрорайон": "микрорайон", "мкр": "микрорайон", "мкрн": "микрорайон",
           "мкр-н": "микрорайон", "м-н": "микрорайон", "мр-н": "микрорайон"}
QUARTER_T = {"квартал": "квартал", "кв-л": "квартал"}
LIVING_T = {"жк", "жм", "жилмассив"}
ABBR = {"ul", "per", "pr", "pr-t", "pr-kt", "prosp", "d-ly", "dang", "kosh", "ул", "пр", "пр-т", "пр-кт", "просп", "мкр", "мкрн", "мкр-н", "м-н", "мр-н", "пер", "пер-к",
        "бул", "б-р", "пр-д", "пл", "наб", "кв-л", "жк", "жм"}
TYPE_WORDS = set(STREET_T) | set(MICRO_T) | set(QUARTER_T) | LIVING_T | {"жилой"}

OBL_WORDS = {"область", "обл", "облысы", "oblysy", "oblast"}
DIST_WORDS = {"район", "р-н", "р-он", "рн", "ауданы", "аудан", "audany"}
VILLAGE_POST = {"ауылы", "ауыл", "кенті", "селосы", "auyly", "kenti"}     # «Мәртөк ауылы»: слово ПЕРЕД типом
CITY_PREFIX = {"город", "г", "гор", "пос", "посёлок", "поселок", "село", "аул", "ауыл", "g", "gor", "gorod"}
CITY_FALLBACK_PREFIX = {"город", "г", "пос", "посёлок", "поселок", "аул", "ауыл", "g", "gorod"}
KK_NOISE = {"қаласы", "қала", "қ", "qala", "qalasy", "к", "r-sy"}
LANDMARK_KW = {
    "магазин", "кафе", "аптека", "школа", "тц", "трц", "рынок", "базар", "база", "точка", "тошкы", "больница", "поликлиника",
    "банк", "отделение", "ресторан", "детсад", "садик", "мечеть", "остановка", "рядом", "возле",
    "около", "zhanynda", "напротив", "karsy", "argy", "вход", "ориентир", "жанында", "qazpost", "kaspi", "halyk", "почта", "супермаркет",
    "столовая", "кинотеатр", "университет", "вуз", "шарага", "колледж", "гостиница", "обшага", "общага", "вокзал", "вокзалы", "парковка", "паркинг", "parking", "заправка", "азс", "касында",
    "акимат", "әкімдік", "akimat", "маг", "ostanovka", "ostanovki", "дукен", "дүкен", "мектеп", "дәріхана", "дарихана", "аурухана", "павильон", "пав", "магнум", "small",
    "magazin", "dukan", "dukon", "market", "apteka", "kafe", "cafe", "shkola", "mektep", "bank", "magnum", "arbat", "арбат", "меломан", "meloman"
}

# слова, которые могут стоять слитно с числом («11дом», «7flat», «66квартал»): все слова-маркеры + типы микрорайонов
_GLUE_EXTRA = ("квартал", "кв-л", "корпус", "корп", "мкр-н", "мкрн", "мкр", "микрорайон", "м-н", "мр-н", "kvartal")
_GLUE_WORDS = "(?:" + "|".join(re.escape(w) for w in sorted(
    {w for w in MARKERS if len(w) >= 2 or w == "э"} | set(_GLUE_EXTRA), key=lambda x: (-len(x), x))) + ")"

DATE_WORDS = {"января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря", "лет", "летия", "наурыз", "мамыр", "қаңтар", "желтоксан",
              "marta", "maya", "yanvarya", "fevralya", "aprelya", "iyunya", "iyulya", "avgusta", "sentyabrya",
              "oktyabrya", "noyabrya", "dekabrya"}
PRE_KW = {   # слово + название после него: «магазин береке», «аптека Сункар», «ТЦ Мега»
    "магазин", "кафе", "аптека", "школа", "тц", "трц", "рынок", "базар", "больница", "поликлиника", "ресторан",
    "гостиница", "мечеть", "остановка", "отделение", "университет", "колледж", "супермаркет", "столовая",
    "кинотеатр", "вокзал", "заправка", "азс", "детсад", "садик", "qazpost", "kaspi", "halyk", "почта",
    "magazin", "market", "apteka", "kafe", "cafe", "shkola", "магнум", "magnum", "банк", "bank",
}
POST_KW = {"вокзалы", "дукен", "дүкен", "dukan", "dukon", "мектеп", "mektep", "дәріхана", "дарихана", "аурухана"}   # название + слово
PREP_KW = {"рядом", "возле", "около", "напротив", "near", "ориентир", "вход", "внутри", "vnutri", "vozle", "ryadom",
           "okolo", "naprotiv", "ishinde", "syrt", "syrtta", "kasy", "karsy", "karama", "karamakarsy", "qaramaqarsy", "карамакарсы", "қарамақарсы","карсы","сырт", "сыртында",
            "иши", "иште", "іште", "іші", "ішінде", "сыртта", "жанында", "жаны", "қасында"}
DOM_PHRASE = {"культуры", "быта", "торговли", "офицеров", "печати", "творчества", "мод", "книги", "кино", "мәдениет"}
HOURS_WORDS = {"часа", "часов", "час", "часам", "сағат", "saghat", "hours", "hour"}
HOURS_SHORT = {"с", "s", "ч", "h"}            # только после «24»: «маг 24 с», но не «дом 5 с 2»
_BI = re.compile(
    r"(?<![^\s,;])([^\s,;/]*[^\W\d_])\s*/\s*([^\W\d_][^\s,;/]*)"
    r"((?:\s+(?:қаласы|қала|облысы|ауданы|аудан|ауылы|кенті|округі))?)(?![^\s,;])")


_FUZZY_TYPES = [("улица", "улица", "street"), ("проспект", "проспект", "street"), ("переулок", "переулок", "street"),
                ("бульвар", "бульвар", "street"), ("микрорайон", "микрорайон", "micro"),
                ("квартал", "квартал", "quarter"), ("prospekt", "проспект", "street"), ("ulitsa", "улица", "street")]
_FUZZY_CACHE: Dict[str, Optional[Tuple[str, str]]] = {}


# слово-тип, слитно с точкой и названием: «prosp.Абая»
_TYPE_DOT = re.compile(r"(?i)(?<![^\W\d_-])(" + "|".join(re.escape(w.rstrip(".")) for w in sorted(
    set(STREET_T) | set(MICRO_T) | set(QUARTER_T) | LIVING_T, key=lambda x: -len(x))) + r")\.(?=[^\W\d_])")


# ------------------------------------------------------------------ результат
@dataclass
class Result:
    values: Dict[str, Optional[str]] = field(default_factory=lambda: {f: None for f in FIELDS})
    sources: Dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    flags: List[str] = field(default_factory=list)
    suggestions: Dict[str, list] = field(default_factory=dict)   # только для UI/explain, в ответ не попадает

    def get(self, name: str) -> Optional[str]:
        return self.values.get(name)


@dataclass
class Tok:
    i: int
    text: str
    low: str
    used: bool = False
    lm: bool = False       # токен входит в ориентир

    @property
    def comma(self) -> bool:
        return self.text in (",", ";")


# ------------------------------------------------------------------ очистка
def clean(raw: str) -> Tuple[str, bool]:
    """Чистит строку. Возвращает (строка, были_ли_склейки)."""
    s = raw.replace("№", " ")
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        out.append(" " if cat in ("Cc", "Zs", "Zl", "Zp") else ch)
    s = "".join(out)
    for ch in "()«»\"'`*#~_=|\\":
        s = s.replace(ch, " ")
    # «Ул .Курильская» -> «Ул. Курильская»
    s = _TYPE_DOT.sub(lambda m: m.group(1) + ". ", s)                       # «bul.Абая», «көшесі.Абая» -> «bul. Абая»
    s = re.sub(r"(?<=[^\W\d_])\.(?=\d)", ". ", s)              # «kv.7», «дом.7» -> «kv. 7»
    s = re.sub(r"(?i)(?<=\d)\s*/\s*(?=[^\W\d_]{2,})(?![^\W\d_](?:дом|dom)(?![^\W\d_]))", " ", s)        # «81/кв 19» -> «81 кв 19»
    s = re.sub(r"(?<=[^\W\d_]{2})\s*/\s*(?=\d)", " ", s)          # «кв/19» -> «кв 19»
    s = re.sub(r"(?i)\b(ул|пр|просп|бул|пл|наб|мкр|пер|г|д|кв|пос|обл|эт|стр|корп|kv|ul|per|pr)\s+\.", r"\1.", s)
    s = re.sub(r"(?i)\b(ул|пр|просп|бул|пл|наб|мкр|пер|г|д|кв|пос|обл|эт|стр|корп|р-н|пр-т|мкр-н|kv|ul|per|pr)\.(?=\S)", r"\1. ", s)
    glued = 0
    # «Назарбаева8», «kv12», «Аксай3а» (минимум 2 буквы перед цифрой, «3а» не трогаем)
    s, n1 = re.subn(r"([^\W\d_]{2,})(?=\d)", "\\1" + GLUE, s)
    s, n3 = re.subn(r"(?i)(?<![^\W\d_])([пдpd])\.?(?=\d)", "\\1" + GLUE, s)    # «п19», «д81»
    n1 += n3
    # «7d», «7h», «7hse» - ленивое «дом» латиницей; «24h» - часы работы, а не дом. «112/D», «7a» не трогаем
    s, n5 = re.subn(r"(?i)(?<![\d/.,-])(\d+)(d|h|hse|hs|dm)(?![^\W\d_])",
                    lambda m: m.group(1) + GLUE + ("h" if m.group(2).lower() == "h" and m.group(1) == "24" else "dom"), s)
    # «11дом», «66квартал», «4подьезд», «9этаж»
    s, n2 = re.subn(r"(?i)(?<=\d)(?=" + _GLUE_WORDS + r"(?![^\W\d_]))", GLUE, s)
    s, n4 = re.subn(r"(?i)(\d+(?:/\d+)*[/-]?[^\W\d_])(?=(?:дом|dom)(?![^\W\d_]))", "\\1" + GLUE, s)   # «126Бдом», «88/Adom»
    glued = n1 + n2 + n4 + n5
    return s, glued > 0


def resolve_bilingual(s: str) -> Tuple[str, bool]:
    """«город Алматы/Алматы қаласы» -> «город Алматы». Заменяем только пару слов вокруг слэша
    (и казахское служебное слово после неё), остальная строка сохраняется - даже без запятых."""
    def repl(m):
        left, right, marker = m.group(1), m.group(2), m.group(3)
        sl, sr = kk_score(left), kk_score(right + marker)
        if sl == 0 and sr == 0 and norm_key(left) != norm_key(right):
            a, b = norm_key(left), norm_key(right)
            if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a) or fuzz.ratio(a, b) >= 80):
                return left if len(a) > len(b) else right        # «петропавл/петропавловск»: берём полное
            return m.group(0)                    # не двуязычие (например «А/Б»)
        return left if sl <= sr else right
    out, n = _BI.subn(repl, s)
    return out, n > 0


# ------------------------------------------------------------------ один разбор
class _Run:
    def __init__(self, ref: Reference, text: str, flags: set):
        self.ref = ref
        self.toks: List[Tok] = []
        self.glued = set()                       # пары соседних токенов, которые были написаны слитно
        for m in TOKEN.finditer(text):
            parts = m.group().split(GLUE)
            for pi, part in enumerate(parts):
                if not part:
                    continue
                if pi > 0 and parts[pi - 1] and self.toks:
                    self.glued.add((len(self.toks) - 1, len(self.toks)))
                self.toks.append(Tok(len(self.toks), part, part.lower().strip(".:")))
        self.n = len(self.toks)
        self.house_pos: Optional[int] = None
        self.house_toks: set = set()             # токены, съеденные маркером дома («45dom»)
        self.suggest: Dict[str, list] = {}
        self.v: Dict[str, Optional[str]] = {f: None for f in FIELDS}
        self.src: Dict[str, str] = {}
        self.flags = flags
        self.extra: List[str] = []   # лишние группы -> в ориентир
        self.house_marked = False
        self.city_obj: Optional[City] = None

    # ---------- предикаты
    def free(self, i: int) -> bool:
        return 0 <= i < self.n and not self.toks[i].used and not self.toks[i].comma

    def is_alpha(self, i: int) -> bool:
        return bool(re.search(r"[^\W\d_]", self.toks[i].text))

    def bare_num(self, i: int) -> bool:
        if not self.free(i):
            return False
        t = self.toks[i].text
        return bool(NUM_RE.match(t)) and not ORD_RE.match(t)

    def put(self, name: str, val: Optional[str], src: str = "string") -> None:
        self.v[name] = val
        self.src[name] = src

    def use(self, idx) -> None:
        for i in idx:
            self.toks[i].used = True

    def join(self, idx) -> str:
        return " ".join(self.toks[i].text.strip(".") if self.toks[i].text.endswith(".") and len(self.toks[i].text) > 2
                        else self.toks[i].text for i in sorted(idx)).strip(" .")

    # ---------- 0. шум
    def mask_noise(self) -> None:
        for t in self.toks:
            if t.low in KK_NOISE or not re.search(r"\w", t.text):
                if not t.comma:
                    t.used = True
        # почтовый индекс, телефон, код домофона: не адрес и не ориентир - выбрасываем вместе с цифрами
        i = 0
        while i < self.n:
            t = self.toks[i]
            if t.used or t.comma:
                i += 1
                continue
            word = t.low in NOISE_WORDS or t.text.startswith("+")
            if word or re.fullmatch(r"\d{6}", t.text) or re.fullmatch(r"\d{7,}", t.text):
                t.used = True
                j = i + 1
                if word:
                    while j < self.n and not self.toks[j].comma and re.fullmatch(r"[\d\-()]+", self.toks[j].text):
                        self.toks[j].used = True
                        j += 1
                i = j
                continue
            i += 1
        # «маг 24 часа», «24/7»: число - часть ориентира, а не дом/квартира
        for t in self.toks:
            if t.used or t.comma:
                continue
            nxt = self.toks[t.i + 1].low if t.i + 1 < self.n else ""
            if INT_RE.match(t.text) and (nxt in HOURS_WORDS or (nxt in HOURS_SHORT and t.text == "24")):
                t.used = True
                t.lm = True
                if t.i + 1 < self.n:
                    self.toks[t.i + 1].used = True
                    self.toks[t.i + 1].lm = True

    def dash_pairs(self) -> None:
        """«Абая 81-19» -> дом 81, квартира 19 (только если маркеров дома/квартиры не было)."""
        if self.v["house"] is not None or self.v["apartment"] is not None:
            return
        for t in self.toks:
            if t.used or t.comma:
                continue
            m = re.fullmatch(r"(\d+[^\W\d_]?)-(\d+)", t.text)
            if m:
                self.put("house", m.group(1))
                self.put("apartment", m.group(2))
                self.use([t.i])
                self.house_pos = t.i
                self.flags.add("dash_pair")
                return

    # ---------- 1. маркеры кв / подъезд / этаж / дом
    def marker_kind(self, t: Tok) -> Optional[Tuple[str, bool]]:
        low = t.low
        if low in TYPE_WORDS:
            return None
        k = MARKERS.get(low)
        if k:
            return k, low not in SINGLE_AFTER
        if len(low) >= 6 and re.fullmatch(r"[^\W\d_]+", low):
            if fuzz.ratio(low, "подъезд") >= 82:
                return "ent", True
            if len(low) >= 7 and fuzz.ratio(low, "квартира") >= 88:
                return "apt", True
        return None

    def num_ok(self, i: int, kind: str) -> bool:
        if not self.free(i):
            return False
        t = self.toks[i].text
        if kind == "house":
            return bool(NUM_RE.match(t)) and not ORD_RE.match(t)
        if kind == "apt":
            return bool(APT_RE.match(t))
        return bool(INT_RE.match(t) or ORD_RE.match(t))

    def markers(self) -> None:
        ms = []
        for t in self.toks:
            if t.used:
                continue
            mk = self.marker_kind(t)
            if mk:
                ms.append((t.i, mk[0], mk[1]))
        if not ms:
            return
        options = []
        for idx, kind, allow_before in ms:
            opts: List[Tuple[Optional[str], Optional[int]]] = [(None, None)]
            if self.num_ok(idx + 1, kind):
                opts.append(("after", idx + 1))
            if allow_before and self.num_ok(idx - 1, kind):
                opts.append(("before", idx - 1))
            options.append(opts)

        prior = {("house", "after"): 1.5, ("house", "before"): 0.5}       # «дом 5» чаще, чем «5 дом»
        free_nums = [k for k in range(self.n) if self.bare_num(k)]
        has_house_marker = any(kind == "house" for _, kind, _ in ms)
        first_marker = min(idx for idx, _, _ in ms)
        best_score, best = -1.0, None
        for combo in itertools.product(*options):
            nums = [c[1] for c in combo if c[1] is not None]
            if len(nums) != len(set(nums)):
                continue
            n_after = sum(1 for c in combo if c[0] == "after")
            n_before = sum(1 for c in combo if c[0] == "before")
            score = 0.3 * max(n_after, n_before)       # единообразие порядка («5 кв, 3 подъезд») - слабый плюс
            for (idx, kind, _), (side, num_i) in zip(ms, combo):
                if num_i is None:
                    continue
                score += 10.0 + prior.get((kind, side), 1.0)
                if (min(idx, num_i), max(idx, num_i)) in self.glued:
                    score += 4.0                       # «11дом», «kv19»: число слитно с маркером
            if not has_house_marker:                   # дом без маркера стоит ПЕРЕД квартирой/подъездом
                left = [k for k in free_nums if k < first_marker and k not in nums]
                if left:
                    prev = first_marker - 1
                    while prev >= 0 and self.toks[prev].comma:
                        prev -= 1
                    score += 6.0 if prev in left else 3.0
            if score > best_score:
                best_score, best = score, combo
        assert best is not None
        for (idx, kind, _), (side, num_i) in zip(ms, best):
            if num_i is None:
                if self.toks[idx].low not in KEEP_IF_UNBOUND:
                    self.toks[idx].used = True
                continue
            txt = self.toks[num_i].text
            if kind in ("ent", "floor"):
                val = re.match(r"\d+", txt).group()
            else:
                val = txt
            name = {"apt": "apartment", "ent": "entrance", "floor": "floor", "house": "house"}[kind]
            self.put(name, val)
            if kind == "house":
                self.house_marked = True
                self.house_pos = min(idx, num_i)
                self.house_toks.update((idx, num_i))
            self.use([idx, num_i])

    # ---------- 1б. ориентиры: «магазин береке», «аптека Сункар», «сабина дукен», «рядом с аптекой»
    def landmarks(self) -> None:
        def ok_name(j: int) -> bool:
            # «Дом» без номера (его markers() не взял) - часть названия: «рядом Дом Культуры»
            unbound_dom = self.free(j) and self.toks[j].low in ("дом", "dom")
            return (self.free(j) and self.is_alpha(j) and not self.bare_num(j) and self.type_info(j) is None
                    and (self.marker_kind(self.toks[j]) is None or unbound_dom) and self.toks[j].low not in LANDMARK_KW | PREP_KW
                    and self.ref.exact_city(norm_key(self.toks[j].text), None, RANK_TOWN) is None)

        i = 0
        while i < self.n:
            t = self.toks[i]
            if t.used or t.comma:
                i += 1
                continue
            low, start, end = t.low, i, i + 1
            if low in ("дом", "dom") and self.free(i + 1) and self.toks[i + 1].low in DOM_PHRASE:
                for k in (i, i + 1):                       # «Дом Культуры», «Дом быта»
                    self.toks[k].used = True
                    self.toks[k].lm = True
                i += 2
                continue
            if low in PREP_KW:
                if self.free(end) and self.toks[end].low in ("с", "со", "от", "у", "возле", "около"):
                    end += 1
                for _ in range(2):
                    if ok_name(end) or (self.free(end) and self.toks[end].low in LANDMARK_KW):
                        end += 1
            elif low in POST_KW:
                if i > 0 and ok_name(i - 1):
                    start = i - 1
            elif low in PRE_KW or low in LANDMARK_KW:
                if ok_name(end):
                    end += 1
                if low in ("банк", "bank") and i > 0 and self.free(i - 1) and self.toks[i - 1].low in PRE_KW:
                    start = i - 1
            else:
                i += 1
                continue
            for k in range(start, end):
                if not self.toks[k].used:
                    self.toks[k].used = True
                    self.toks[k].lm = True
            i = end

    # ---------- 2. область / город / район
    def province(self) -> None:
        for t in self.toks:
            if t.used or t.low not in OBL_WORDS:
                continue
            for j, nxt in ((t.i - 1, False), (t.i + 1, True)):
                if self.free(j) and self.is_alpha(j):
                    p = self.ref.match_province(norm_key(self.toks[j].text))
                    if p:
                        self.put("province", p)
                        self.use([t.i, j])
                        return
            t.used = True   # «область» без распознанного имени
        # прилагательное само по себе («Алматинская, Алматы») - только когда это весь сегмент:
        # «Абайская улица 81» - это улица, а не область
        for seg in self.segments():
            idx = [i for i in seg if self.free(i)]
            if len(idx) == 1 and self.is_alpha(idx[0]):
                p = self.ref.match_province(norm_key(self.toks[idx[0]].text), standalone=True)
                if p:
                    self.put("province", p)
                    self.use(idx)
                    return

    def segments(self) -> List[List[int]]:
        segs, cur = [], []
        for t in self.toks:
            if t.comma:
                segs.append(cur)
                cur = []
            else:
                cur.append(t.i)
        segs.append(cur)
        return segs

    def near_district_word(self, idx) -> bool:
        """Слово стоит вплотную к «район/audany»: это район, а не город («auyezov audany»)."""
        for i in idx:
            for j in (i - 1, i + 1):
                if 0 <= j < self.n and not self.toks[j].comma and self.toks[j].low in DIST_WORDS:
                    if j > i:
                        k = j + 1             # «Шымкент район Каратауский»: после «район» идёт название - слово слева это город
                        if self.free(k) and self.is_alpha(k) and self.type_info(k) is None \
                                and self.marker_kind(self.toks[k]) is None and not self.bare_num(k):
                            continue
                    return True
        return False

    def house_in_seg(self, seg) -> bool:
        """В сегменте был дом («abay 45dom»): слово + дом - улица, а не город (кроме крупных городов)."""
        return any(i in self.house_toks for i in seg)

    def city(self) -> None:
        ref = self.ref
        found = None
        found_key = ""
        prov = self.v["province"]

        # 0) казахский порядок: «Мәртөк ауылы» - название ПЕРЕД словом «ауылы»
        for t in self.toks:
            if t.used or t.low not in VILLAGE_POST:
                continue
            j = t.i - 1
            if self.free(j) and self.is_alpha(j) and self.toks[j].low not in TYPE_WORDS:
                c = ref.find_city(norm_key(self.toks[j].text), prov, cutoff=88.0, fuzzy_min_rank=RANK_HAMLET)
                if c:
                    found, found_key = c, norm_key(self.toks[j].text)
                else:
                    self.put("city", self.toks[j].text)
                self.use([t.i, j])
                break
            t.used = True
        if self.v["city"] and not found:
            return

        # 1) после слова «город / г. / пос.»
        for t in self.toks:
            if found or t.used or t.low not in CITY_PREFIX:
                continue
            run = []
            j = t.i + 1
            while len(run) < 3 and self.free(j) and self.is_alpha(j) and self.toks[j].low not in TYPE_WORDS:
                run.append(j)
                j += 1
            for n in (len(run), 2, 1):
                if n < 1 or n > len(run):
                    continue
                ck = norm_key(" ".join(self.toks[k].text for k in run[:n]))
                # явное слово «город/пос.»: можно искать нечётко и среди сёл
                c = ref.find_city(ck, self.v["province"], cutoff=88.0, fuzzy_min_rank=RANK_HAMLET)
                if c:
                    found, found_key = c, ck
                    self.use([t.i] + run[:n])
                    break
            if found:
                break
            if run and t.low in CITY_FALLBACK_PREFIX:     # города нет в справочнике: берём как написано
                self.put("city", self.toks[run[0]].text)
                self.use([t.i, run[0]])
                return

        # 2) весь сегмент между запятыми = город
        if not found:
            best = None
            for si, seg in enumerate(self.segments()):
                idx = [i for i in seg if self.free(i)]
                if not idx or any(not self.is_alpha(i) or self.toks[i].low in TYPE_WORDS for i in idx):
                    continue
                if self.near_district_word(idx):
                    continue
                ck = norm_key(" ".join(self.toks[i].text for i in idx))
                # нечётко только в первых сегментах и только среди городов: иначе улица
                # «Курильская» превратится в село «Курильск»
                c = ref.find_city(ck, prov, cutoff=90.0, fuzzy_min_rank=RANK_TOWN) if si < 2 \
                    else ref.exact_city(ck, prov)
                # если в строке и город, и одноимённое село («астана» и «ауэзов»), побеждает крупный
                if c and c.rank < RANK_CITY and self.house_in_seg(seg) and ref.is_street_name(ck):
                    c = None                  # «abay 45dom»: слово + дом, и такая улица есть - это улица
                if c and (best is None or c.rank > best[0].rank):
                    best = (c, ck, idx)
            if best:
                found, found_key = best[0], best[1]
                self.use(best[2])

        # 3) город в самом начале сегмента без запятой: «Алматы Абая 10» (только точное совпадение)
        if not found:
            for si, seg in enumerate(self.segments()[:3]):
                idx = [i for i in seg if self.free(i)]
                for n in (2, 1):
                    if len(idx) <= n:
                        continue
                    head = idx[:n]
                    if not all(self.toks[x].used for x in seg if x < idx[0]) or not all(self.is_alpha(i) for i in head):
                        continue
                    ck = norm_key(" ".join(self.toks[i].text for i in head))
                    c = ref.exact_city(ck, prov, min_rank=RANK_TOWN)
                    if c and self.near_district_word(head):
                        continue
                    nxt = idx[n]
                    ok_next = self.is_alpha(nxt) and self.toks[nxt].low not in KK_NOISE
                    if not ok_next and self.bare_num(nxt) and nxt + 1 < self.n and self.free(nxt + 1):
                        w = self.toks[nxt + 1].low           # «Караганда 12 м-н», «тараз 8 марта»
                        ok_next = w in MICRO_T or w in QUARTER_T or w in DATE_WORDS
                    if c and ok_next:
                        found, found_key = c, ck
                        self.use(head)
                        break
                if found:
                    break

        # 4) город в самом конце строки: «Абая 81, сабина дукен алматы» (только точное совпадение, город/малый город)
        if not found:
            j = self.n - 1
            while j >= 0 and (self.toks[j].used or self.toks[j].comma):
                j -= 1
            for n in (2, 1):
                idx = list(range(j - n + 1, j + 1))
                if idx[0] < 0 or not all(self.free(i) and self.is_alpha(i) for i in idx):
                    continue
                before = idx[0] - 1
                if before >= 0 and not self.toks[before].comma and (
                        self.toks[before].low in TYPE_WORDS or self.toks[before].low in CITY_PREFIX):
                    continue                    # «ул. Абай»: это улица
                ck = norm_key(" ".join(self.toks[i].text for i in idx))
                c = ref.exact_city(ck, prov, min_rank=RANK_TOWN)
                if c and c.rank < RANK_CITY and ref.is_street_name(ck) and any(
                        self.house_in_seg(sg) for sg in self.segments() if idx[0] in sg):
                    continue                  # «abay 45dom»: единственное слово рядом с домом - улица
                if c and not self.near_district_word(idx):
                    found, found_key = c, ck
                    self.use(idx)
                    break

        if found:
            self.city_obj = found
            self.put("city", found.name)
            if ref.city_is_ambiguous(found_key, prov):
                self.flags.add("ambiguous_city")
            # дубль города: «Алматы, Алматы»
            key = norm_key(found.name)
            for seg in self.segments():
                idx = [i for i in seg if self.free(i)]
                if idx and norm_key(" ".join(self.toks[i].text for i in idx)) == key:
                    self.use(idx)

    def district(self) -> None:
        for t in self.toks:
            if t.used or t.low not in DIST_WORDS:
                continue
            if self.free(t.i - 1) and self.is_alpha(t.i - 1):
                self.put("district", self.toks[t.i - 1].text)
                self.use([t.i, t.i - 1])
                return
            if self.free(t.i + 1) and self.is_alpha(t.i + 1):
                self.put("district", self.toks[t.i + 1].text)
                self.use([t.i, t.i + 1])
                return
            t.used = True
        # район в начале сегмента без слова «район»: «Енбекшинский Курильская д. 30, Шымкент»
        for seg in self.segments():
            idx = [i for i in seg if self.free(i)]
            if len(idx) < 2 or not all(self.toks[x].used for x in seg if x < idx[0]) \
                    or not self.is_alpha(idx[0]) or self.toks[idx[0]].low in TYPE_WORDS:
                continue
            nxt = idx[1]
            if not self.is_alpha(nxt) or self.bare_num(nxt) or self.marker_kind(self.toks[nxt]) is not None:
                continue                                  # «Туран 142»: после слова число - это улица, а не район
            ti = self.type_info(nxt)
            if ti is not None:
                after = nxt + ti[2]
                if not (self.free(after) and self.is_alpha(after) and not self.bare_num(after)) and not (
                        ti[1] in ("micro", "quarter") and self.bare_num(after)):
                    continue                              # «Тұран даңғылы»: тип после слова - это название улицы
            d = self.bare_district(idx[0])
            if d:
                self.put("district", self.toks[idx[0]].text)
                self.use([idx[0]])
                return

    def bare_district(self, i: int) -> Optional[str]:
        """Слово без «район» как район. «Туран» - и район Шымкента, и проспект Астаны, поэтому
        справочник не должен знать такую улицу в этом городе; остальное решает порядок вызовов."""
        key = norm_key(self.toks[i].text)
        d = self.ref.find_district(key, self.city_obj, self.v["province"])
        if not d:
            return None
        if self.city_obj is not None and self.ref.lookup(self.city_obj, key, cutoff=100.0) is not None:
            return None
        return d

    def district_late(self) -> None:
        """Одиночное слово-район («Турксибский, ...») берём ПОСЛЕ улицы: если улицы нет, слово - это улица."""
        if self.v["district"] or not self.v["street_name"]:
            return
        for seg in self.segments():
            idx = [i for i in seg if self.free(i)]
            if len(idx) == 1 and self.is_alpha(idx[0]) and self.toks[idx[0]].low not in TYPE_WORDS:
                d = self.bare_district(idx[0])
                if d:
                    self.put("district", self.toks[idx[0]].text)
                    self.use(idx)
                    return

    # ---------- 3. улица / микрорайон / ЖК
    def fuzzy_type(self, low: str) -> Optional[Tuple[str, str]]:
        """Опечатки в полных словах-типах: «проспек», «улиица», «пероулок», «микрорайн»."""
        if len(low) < 5 or not re.fullmatch(r"[^\W\d_]+", low):
            return None
        if low in _FUZZY_CACHE:
            return _FUZZY_CACHE[low]
        res = None
        for word, canon, kind in _FUZZY_TYPES:
            if abs(len(word) - len(low)) <= 2 and fuzz.ratio(low, word) >= 86:
                res = (canon, kind)
                break
        _FUZZY_CACHE[low] = res
        return res

    def type_info(self, i: int) -> Optional[Tuple[str, str, int]]:
        if not self.free(i):
            return None
        low = self.toks[i].low
        if low not in TYPE_WORDS and low not in MARKERS and low not in LANDMARK_KW and low not in DATE_WORDS:
            ft = self.fuzzy_type(low)
            if ft:
                return (ft[0], ft[1], 1)
        if low == "жилой" and self.free(i + 1) and self.toks[i + 1].low in ("комплекс", "массив"):
            return ("living", "living", 2)
        if low in LIVING_T:
            return ("living", "living", 1)
        if low in STREET_T:
            return (STREET_T[low], "street", 1)
        if low in MICRO_T:
            return (MICRO_T[low], "micro", 1)
        if low in QUARTER_T:
            return (QUARTER_T[low], "quarter", 1)
        return None

    def is_stop(self, j: int) -> bool:
        return (not self.free(j)) or self.type_info(j) is not None or self.toks[j].low in LANDMARK_KW

    def count_bare_from(self, j: int) -> int:
        return sum(1 for k in range(j, self.n) if self.bare_num(k))

    def cap_after(self, last: int, kind: str) -> List[int]:
        name: List[int] = []
        j = last + 1
        while j < self.n and len(name) < 5:
            if self.is_stop(j):
                break
            if self.bare_num(j):
                # «ул. 8 марта 10»: число в начале названия, если дальше слово
                if kind == "street" and not name and self.free(j + 1) and self.is_alpha(j + 1) \
                        and not self.is_stop(j + 1) and self.marker_kind(self.toks[j + 1]) is None:
                    name.append(j)
                    j += 1
                    continue
                break
            name.append(j)
            j += 1
        return name

    def cap_before(self, first: int, kind: str) -> List[int]:
        name: List[int] = []
        j = first - 1
        while j >= 0 and len(name) < 5:
            if self.is_stop(j):
                break
            if self.bare_num(j) and kind not in ("micro", "quarter"):
                # «8 марта улица»: число в начале названия, если дальше слово-дата
                if kind == "street" and name and self.toks[name[0]].low in DATE_WORDS and self.toks[j].text.isdigit():
                    name.insert(0, j)
                break
            name.insert(0, j)
            j -= 1
        return name

    def streets(self) -> None:
        groups = []
        i = 0
        while i < self.n:
            ti = self.type_info(i)
            if not ti:
                i += 1
                continue
            canon, kind, span = ti
            t_idx = list(range(i, i + span))
            abbreviated = self.toks[i].low in ABBR
            a = self.cap_after(i + span - 1, kind)
            b = self.cap_before(i, kind)
            if not a and not b and kind == "street" and i + span < self.n and self.toks[i + span].comma:
                a = self.cap_after(i + span, kind)        # «prst, satpayev 55»: тип отдельно от названия
                if a:
                    self.flags.add("type_comma_name")
            a_has_alpha = any(self.is_alpha(k) for k in a)

            # микрорайон/квартал: число сразу после названия - часть названия или дом?
            nxt = (a[-1] + 1) if a else (i + span)
            if kind in ("micro", "quarter") and self.bare_num(nxt) and not (not a_has_alpha and b):
                if not a:
                    a = [nxt]
                else:
                    key = norm_key(" ".join(self.toks[k].text for k in a + [nxt]))
                    ref_hit = self.ref.lookup(self.city_obj, key, cutoff=100.0) is not None
                    if self.house_marked or self.count_bare_from(nxt) >= 2 or ref_hit:
                        a = a + [nxt]
                        if not (self.house_marked or ref_hit):
                            self.flags.add("ambiguous_number")
                a_has_alpha = any(self.is_alpha(k) for k in a)

            if kind in ("micro", "quarter"):
                use_after = bool(a) and (a_has_alpha or not b)
            else:
                use_after = a_has_alpha
                if use_after and b and not abbreviated:
                    last = self.toks[b[-1]].low
                    if last.endswith(("ский", "ская", "ское", "ый", "ой", "ий", "ая", "ое")):
                        use_after = False
            name = a if use_after else b
            if not name:
                self.use(t_idx)
                i += span
                continue
            self.use(t_idx + name)
            groups.append((canon, kind, name))
            i += span

        street = living = None
        sg = [g for g in groups if g[1] == "street"]
        mg = [g for g in groups if g[1] in ("micro", "quarter")]
        lg = [g for g in groups if g[1] == "living"]
        if sg:
            street = sg[0]
            rest_micro = mg
            living = (lg + rest_micro or [None])[0]
            used_groups = [street, living]
        elif mg:
            street = mg[0]
            living = lg[0] if lg else None
            used_groups = [street, living]
        else:
            living = lg[0] if lg else None
            used_groups = [living]
        if street:
            self.put("street_type", street[0])
            self.put("street_name", self.join(street[2]))
        if living:
            self.put("living_area", self.join(living[2]))
        for g in groups:
            if g not in used_groups:
                self.extra.append(self.join(g[2]))

    def alpha_run_before(self, k: int) -> List[int]:
        """Свободные слова непосредственно перед позицией k (можно перепрыгнуть одну запятую)."""
        run: List[int] = []
        j = k - 1
        while j >= 0 and self.toks[j].lm:              # «Жандосова Magazin Sabina 32»: ориентир прозрачен
            j -= 1
        while j >= 0 and self.free(j) and self.is_alpha(j) and self.toks[j].low not in LANDMARK_KW:
            run.insert(0, j)
            j -= 1
        if not run and j >= 0 and self.toks[j].comma:
            j -= 1                                     # «Курильская, 9»
            while j >= 0 and self.toks[j].lm:
                j -= 1
            while j >= 0 and self.free(j) and self.is_alpha(j) and self.toks[j].low not in LANDMARK_KW:
                run.insert(0, j)
                j -= 1
            if run:
                self.flags.add("street_comma_house")
        if run and self.toks[run[0]].low in DATE_WORDS and run[0] > 0 and self.free(run[0] - 1) \
                and self.toks[run[0] - 1].text.isdigit():
            run.insert(0, run[0] - 1)                  # «8 марта 180»: число - часть названия
        return run

    def untyped_street(self) -> None:
        """Название улицы без типа: слова перед домом («Кулманова 133», «жуалы 19», «Желтоксан д.66»)."""
        if self.v["street_name"]:
            return
        # 1) перед домом, найденным по маркеру («Желтоксан д.66», «кулманова,200дом»)
        if self.house_pos is not None:
            run = self.alpha_run_before(self.house_pos)
            if run:
                self.use(run)
                self.put("street_name", self.join(run))
                return
        # 2) перед первым голым номером
        for k in range(self.n):
            if not self.bare_num(k):
                continue
            run = self.alpha_run_before(k)
            if not run:
                continue
            key = norm_key(" ".join(self.toks[x].text for x in run + [k]))
            if self.ref.lookup(self.city_obj, key, cutoff=100.0) is not None:
                run.append(k)                      # «Аксай 3а» - место из справочника
                self.flags.add("name_with_number_from_ref")
            self.use(run)
            self.put("street_name", self.join(run))
            return
        # 3) нет номера: одиночное слово, которое справочник знает как улицу
        for seg in self.segments():
            idx = [i for i in seg if self.free(i) and self.is_alpha(i)]
            if idx:
                key = norm_key(" ".join(self.toks[i].text for i in idx))
                if self.ref.lookup(self.city_obj, key, cutoff=100.0):
                    self.use(idx)
                    self.put("street_name", self.join(idx))
                    return
        # 3б) «8 марта» отдельным сегментом: число + слово-дата - это название улицы
        for seg in self.segments():
            idx = [i for i in seg if self.free(i)]
            if len(idx) == 2 and self.toks[idx[0]].text.isdigit() and self.toks[idx[1]].low in DATE_WORDS:
                self.use(idx)
                self.put("street_name", self.join(idx))
                return
        # 4) дом есть, а улицы нет: первый оставшийся сегмент из слов («Астана, фурманова, ..., дом 195»)
        if self.house_pos is not None or any(self.bare_num(k) for k in range(self.n)):
            for seg in self.segments():
                idx = [i for i in seg if self.free(i)]
                if idx and len(idx) <= 4 and all(self.is_alpha(i) for i in idx):
                    self.use(idx)
                    self.put("street_name", self.join(idx))
                    self.flags.add("street_by_elimination")
                    return

    # ---------- 4. голые номера
    def numbers(self) -> None:
        cand, near = [], []
        for k in range(self.n):
            if not self.bare_num(k):
                continue
            if any(self.toks[x].lm for x in range(max(0, k - 2), k)):
                near.append(k)                     # «магазин береке 5»: число, скорее всего, к ориентиру
            else:
                cand.append(k)
        cand += near                               # но если больше номеров нет - это дом
        for k in cand:
            if self.v["house"] is None:
                self.put("house", self.toks[k].text)
                self.use([k])
            elif self.v["apartment"] is None:
                self.put("apartment", self.toks[k].text)
                self.use([k])
                self.flags.add("bare_apartment")

    # ---------- 5. ориентир
    def landmark(self) -> None:
        words = [t.text.strip(".") for t in self.toks
                 if (t.lm or not t.used) and not t.comma and re.search(r"\w", t.text)]
        words = [w for w in words if w]
        parts = self.extra + ([" ".join(words)] if words else [])
        text = ", ".join(p for p in parts if p).strip()
        if text:
            self.put("landmark", text)

    # ---------- 6. enrich из справочника
    def enrich(self) -> None:
        ref, v = self.ref, self.v
        # город по району, если города в строке нет («вместо города стоит район»)
        if not v["city"] and v["district"]:
            c = ref.city_of_district(v["district"])
            if c:
                self.city_obj = c
                self.put("city", c.name, "reference")
        # город: канонический вид (Ақтөбе -> Актобе) - значение из строки сопоставлено со справочником
        c = self.city_obj
        if c and v["city"] and norm_key(v["city"]) != norm_key(c.name):
            v["city"] = c.name
        if c and not v["province"]:
            self.put("province", c.province, "string" if self.src.get("city") == "string" else "reference")
        if v["street_name"] and c:
            key = norm_key(v["street_name"])
            hit = ref.lookup(c, key)
            if hit:
                if not v["street_type"] and hit.type:
                    self.put("street_type", hit.type, "reference")
                if not v["district"] and hit.district:
                    d = ref.infer_district(c, hit.key)
                    if d:
                        self.put("district", d, "reference")
                    else:                       # улица идёт через несколько районов: в ответ не пишем, показываем в UI
                        dc = ref.district_candidates(c, hit.key)
                        if dc:
                            self.suggest["district"] = [{"district": n, "share": round(sh, 2)} for n, sh in dc[:3]]
                            self.flags.add("district_suggested")
                self.flags.add("street_in_reference")
            else:
                self.flags.add("street_not_in_reference")
        # города в строке нет, а улица есть в справочнике: подсказка, где она может быть (поле не заполняем)
        if not v["city"] and not v["district"] and v["street_name"]:
            cands = ref.cities_with_street(norm_key(v["street_name"]))
            if cands:
                self.suggest["city"] = [{"city": c.name, "province": c.province, "share": round(sh, 2)}
                                        for c, sh in cands[:3]]
                self.flags.add("city_suggested")

    # ---------- 7. confidence
    def confidence(self, bilingual: bool, glued: bool) -> float:
        v = self.v
        if not any(v[f] for f in ("province", "city", "district", "living_area", "street_name", "house",
                                  "apartment", "entrance", "floor")):
            return 0.0
        has_street = bool(v["street_name"] or v["living_area"])
        has_house = bool(v["house"])
        if not has_street and not has_house:
            return 0.2
        if not has_street or not has_house:
            return 0.5
        c = 1.0
        if bilingual or glued:
            c = 0.8
        if "ambiguous_number" in self.flags or "bare_apartment" in self.flags or "ambiguous_city" in self.flags \
                or "dash_pair" in self.flags:
            c -= 0.1
        if not (v["city"] or v["district"]):
            c -= 0.1
        if "street_not_in_reference" in self.flags and self.ref.has_items(self.city_obj):
            c -= 0.05
        return round(max(0.55, c), 2)


# ------------------------------------------------------------------ публичный API
class AddressParser:
    def __init__(self, ref: Optional[Reference] = None):
        self.ref = ref or load_default_reference()

    def parse(self, raw: str) -> Result:
        text, glued = clean(raw)
        text, bilingual = resolve_bilingual(text)
        flags: set = set()
        run = _Run(self.ref, text, flags)
        run.mask_noise()
        run.markers()
        run.dash_pairs()
        run.landmarks()
        run.province()
        run.city()
        run.district()
        run.streets()
        run.untyped_street()
        run.district_late()
        run.numbers()
        run.landmark()
        run.enrich()
        conf = run.confidence(bilingual, glued)
        if bilingual:
            flags.add("bilingual")
        if glued:
            flags.add("glued")
        return Result(values=run.v, sources=run.src, confidence=conf, flags=sorted(flags),
                      suggestions=run.suggest)

    def parse_record(self, rec: dict) -> dict:
        raw = rec.get("raw_address") or ""
        try:
            res = self.parse(raw)
            values, conf = res.values, res.confidence
        except Exception:   # на каждую запись обязан быть ответ
            values, conf = {f: None for f in FIELDS}, 0.0
        out = {"id": rec.get("id"), "raw_address": raw}
        for f in FIELDS:
            out[f] = values.get(f)
        out["confidence"] = conf
        return out

    def parse_batch(self, recs: List[dict]) -> List[dict]:
        return [self.parse_record(r) for r in recs]
