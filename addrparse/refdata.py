"""Справочник адресов: области, города, районы, улицы, микрорайоны.

Parser работает ТОЛЬКО через этот интерфейс. Данные приходят из:
  * reference.sqlite, собранного tools/extract_osm.py из OSM (основной вариант);
  * seed.py, маленького набора для отладки без файла.

Города с одинаковым названием («Новоселовка» в десятке областей) не затирают друг друга:
на один ключ приходится список кандидатов, из которого выбираем по области (если она
известна из строки) и по рангу (город > город малый > село > хутор).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Union

from . import fuzz
from .textutil import norm_key

RANK_HAMLET, RANK_VILLAGE, RANK_TOWN, RANK_CITY = 0, 1, 2, 3


@dataclass(frozen=True)
class City:
    cid: int
    name: str                  # каноническое русское название без «город/пос.» и без казахской половины
    province: str              # «Актюбинская область»; для городов респ. значения совпадает с названием
    republic: bool = False
    rank: int = RANK_TOWN


@dataclass(frozen=True)
class District:
    did: int
    name: str                  # без слова «район»
    parent_cid: Optional[int]  # город, если это район города (Алматы -> Ауэзовский)
    province: Optional[str]    # область, если это район области (Илийский)


@dataclass(frozen=True)
class Hit:
    name: str
    kind: str                  # 'street' | 'place'
    type: Optional[str]
    district: Optional[str]
    score: float
    key: str = ""


# строка справочника: (название_как_в_OSM, вид, тип, did|None, сколько_раз_встретилось)
_Row = Tuple[str, str, Optional[str], Optional[int], int]


class Reference:
    def __init__(self) -> None:
        self.province_ctx: Dict[str, str] = {}
        self.province_adj: Dict[str, str] = {}
        self.cities_by_key: Dict[str, List[City]] = {}
        self.city_by_id: Dict[int, City] = {}
        self._cities_by_name: Dict[str, List[City]] = {}
        self.districts_by_key: Dict[str, List[District]] = {}
        self.district_by_id: Dict[int, District] = {}
        self._items: Dict[int, Dict[str, List[_Row]]] = {}
        self._city_keys_cache: Optional[List[str]] = None
        self._street_index: Optional[Dict[str, Dict[int, int]]] = None
        self._next_cid = 1
        self._next_did = 1

    # ------------------------------------------------------------- наполнение
    def add_province(self, canon: str, ctx_aliases: List[str], adj_aliases: List[str]) -> None:
        for a in ctx_aliases:
            self.province_ctx[norm_key(a)] = canon
        for a in adj_aliases:
            self.province_adj[norm_key(a)] = canon
            self.province_ctx[norm_key(a)] = canon

    def add_city(self, name: str, province: str, aliases: Optional[List[str]] = None, republic: bool = False,
                 rank: Optional[int] = None, cid: Optional[int] = None) -> City:
        if cid is None:
            cid = self._next_cid
        self._next_cid = max(self._next_cid, cid + 1)
        c = City(cid, name, province, republic, rank if rank is not None else (RANK_CITY if republic else RANK_TOWN))
        self.city_by_id[cid] = c
        self._cities_by_name.setdefault(norm_key(name), []).append(c)
        for a in [name] + list(aliases or []):
            k = norm_key(a)
            lst = self.cities_by_key.setdefault(k, [])
            if c not in lst:
                lst.append(c)
        self._city_keys_cache = None
        return c

    def add_district(self, name: str, city: Union[City, str, None] = None, province: Optional[str] = None,
                     aliases: Optional[List[str]] = None, did: Optional[int] = None) -> District:
        parent = None
        if isinstance(city, str):
            cs = self._cities_by_name.get(norm_key(city))
            parent = cs[0] if cs else None
        elif city is not None:
            parent = city
        if did is None:
            did = self._next_did
        self._next_did = max(self._next_did, did + 1)
        d = District(did, name, parent.cid if parent else None, province or (parent.province if parent else None))
        self.district_by_id[did] = d
        for a in [name] + list(aliases or []):
            lst = self.districts_by_key.setdefault(norm_key(a), [])
            if d not in lst:
                lst.append(d)
        return d

    def add_item(self, city: Union[City, str], name: str, kind: str, type_: Optional[str],
                 district: Union[District, str, None] = None, cnt: int = 1, key: Optional[str] = None) -> None:
        if isinstance(city, str):
            cs = self._cities_by_name.get(norm_key(city))
            if not cs:
                return
            city = cs[0]
        did = None
        if isinstance(district, District):
            did = district.did
        elif isinstance(district, str):
            for d in self.districts_by_key.get(norm_key(district), []):
                if d.parent_cid in (None, city.cid):
                    did = d.did
                    break
        self.add_item_raw(city.cid, key or norm_key(name), name, kind, type_, did, cnt)

    def add_item_raw(self, cid: int, key: str, name: str, kind: str, type_: Optional[str],
                     did: Optional[int], cnt: int = 1) -> None:
        self._items.setdefault(cid, {}).setdefault(key, []).append((name, kind, type_, did, cnt))

    # ------------------------------------------------------------- запросы: область
    def match_province(self, key: str, standalone: bool = False) -> Optional[str]:
        return (self.province_adj if standalone else self.province_ctx).get(key)

    # ------------------------------------------------------------- запросы: город
    @staticmethod
    def _choose(cands: List[City], province: Optional[str]) -> City:
        if province:
            pc = [c for c in cands if c.province == province]
            if pc:
                cands = pc
        return sorted(cands, key=lambda c: (-c.rank, c.cid))[0]

    def exact_city(self, key: str, province: Optional[str] = None, min_rank: int = 0) -> Optional[City]:
        cands = [c for c in self.cities_by_key.get(key, []) if c.rank >= min_rank]
        return self._choose(cands, province) if cands else None

    def find_city(self, key: str, province: Optional[str] = None, cutoff: float = 90.0,
                  fuzzy_min_rank: int = RANK_TOWN, exact_min_rank: int = 0) -> Optional[City]:
        """Точное совпадение; иначе нечёткое, но только среди городов ранга >= fuzzy_min_rank
        (села нечётко не ищем: ложных совпадений с названиями улиц слишком много)."""
        if not key:
            return None
        c = self.exact_city(key, province, exact_min_rank)
        if c:
            return c
        if len(key) < 5:
            return None
        if self._city_keys_cache is None:
            self._city_keys_cache = list(self.cities_by_key)
        m = fuzz.best(key, self._city_keys_cache, cutoff)
        if not m:
            return None
        cands = [c for c in self.cities_by_key[m[0]] if c.rank >= fuzzy_min_rank]
        return self._choose(cands, province) if cands else None

    def city_is_ambiguous(self, key: str, province: Optional[str]) -> bool:
        """Несколько городов того же ранга с таким ключом в разных областях, а область неизвестна."""
        if province:
            return False
        cands = self.cities_by_key.get(key, [])
        if len(cands) < 2:
            return False
        top = max(c.rank for c in cands)
        return len({c.province for c in cands if c.rank == top}) > 1

    def city(self, name: Optional[str]) -> Optional[City]:
        if not name:
            return None
        cs = self._cities_by_name.get(norm_key(name))
        return self._choose(cs, None) if cs else None

    # ------------------------------------------------------------- запросы: район
    def find_district(self, key: str, city: Optional[City] = None, province: Optional[str] = None) -> Optional[str]:
        """Название района по ключу, если район однозначен в контексте города/области."""
        cands = self.districts_by_key.get(key, [])
        if not cands:
            return None
        if city is not None:
            cands = [d for d in cands if d.parent_cid == city.cid or (d.parent_cid is None and d.province == city.province)]
        elif province:
            cands = [d for d in cands if d.province == province]
        else:
            if key in self.cities_by_key:     # «Алматы» - и город, и район Астаны: без контекста не гадаем
                return None
        names = {d.name for d in cands}
        return next(iter(names)) if len(names) == 1 else None

    def city_of_district(self, district: str) -> Optional[City]:
        """Город по названию района, если район городской и однозначен («вместо города стоит район»)."""
        parents = {d.parent_cid for d in self.districts_by_key.get(norm_key(district), []) if d.parent_cid}
        if len(parents) == 1:
            return self.city_by_id.get(next(iter(parents)))
        return None

    # ------------------------------------------------------------- запросы: улицы / места
    def has_items(self, city: Optional[City]) -> bool:
        return city is not None and city.cid in self._items

    def lookup(self, city: Optional[City], key: str, cutoff: float = 92.0) -> Optional[Hit]:
        if city is None or not key:
            return None
        d = self._items.get(city.cid)
        if not d:
            return None
        rows = d.get(key)
        score = 100.0
        hit_key = key
        if rows is None:
            m = fuzz.best(key, d.keys(), cutoff)
            if not m:
                return None
            rows, score, hit_key = d[m[0]], m[1], m[0]
        rows = sorted(rows, key=lambda r: (0 if r[1] == "street" else 1, -r[4]))
        name, kind, type_, did, _ = rows[0]
        dn = self.district_by_id[did].name if did in self.district_by_id else None
        return Hit(name, kind, type_, dn, score, hit_key)

    def cities_with_street(self, key: str, limit: int = 5) -> List[Tuple[City, float]]:
        """В каких городах есть улица с таким названием: [(город, доля)], крупные города первыми.
        Это ПОДСКАЗКА для UI («Абайская улица» без города), а не факт: в ответ город не подставляем."""
        if self._street_index is None:
            idx: Dict[str, Dict[int, int]] = {}
            for cid, d in self._items.items():
                for k, rows in d.items():
                    s = sum(r[4] for r in rows if r[1] == "street")
                    if s:
                        idx.setdefault(k, {})[cid] = s
            self._street_index = idx
        per = self._street_index.get(key)
        if not per:
            return []
        total = sum(per.values())
        out = [(self.city_by_id[c], n / total) for c, n in per.items() if c in self.city_by_id]
        out.sort(key=lambda x: (-x[0].rank, -x[1]))
        return out[:limit]

    def is_street_name(self, key: str) -> bool:
        """Известно ли слово как название улицы хотя бы в одном городе (учитывая родительный падеж:
        «абай» -> «абая», «сатпаев» -> «сатпаева»). Нужен, чтобы отличить «abay 45dom» (улица Абая)
        от «осакаровка дом 5» (посёлок, улицы с таким названием нет)."""
        if not key:
            return False
        self.cities_with_street(key)                      # строит индекс при первом вызове
        idx = self._street_index or {}
        forms = [key, key + "а", key + "ы"]
        if key.endswith("й"):
            forms.append(key[:-1] + "я")
            forms.append(key[:-1] + "ая")
        return any(f in idx for f in forms)

    def district_candidates(self, city: Optional[City], key: str) -> List[Tuple[str, float]]:
        """[(район, доля адресов)] для улицы в городе; для UI («предположительно ...»)."""
        if city is None:
            return []
        rows = self._items.get(city.cid, {}).get(key, [])
        tot: Dict[int, int] = {}
        for _, _, _, did, cnt in rows:
            if did in self.district_by_id:
                tot[did] = tot.get(did, 0) + cnt
        s = sum(tot.values())
        if not s:
            return []
        return sorted(((self.district_by_id[i].name, c / s) for i, c in tot.items()), key=lambda x: -x[1])

    def infer_district(self, city: Optional[City], key: str, min_share: float = 0.7) -> Optional[str]:
        cands = self.district_candidates(city, key)
        return cands[0][0] if cands and cands[0][1] >= min_share else None

    # ------------------------------------------------------------- загрузка
    @classmethod
    def from_seed(cls) -> "Reference":
        from . import seed
        ref = cls()
        seed.fill(ref)
        return ref

    @classmethod
    def from_sqlite(cls, path: str) -> "Reference":
        from . import refdb
        return refdb.load(path)

    def stats(self) -> Dict[str, int]:
        return {
            "cities": len(self.city_by_id),
            "city_keys": len(self.cities_by_key),
            "districts": len(self.district_by_id),
            "item_cities": len(self._items),
            "item_rows": sum(len(r) for d in self._items.values() for r in d.values()),
        }


def load_default_reference() -> Reference:
    """reference.sqlite (рядом с проектом или по пути из ADDR_REFERENCE), иначе мини-справочник seed."""
    import os
    path = os.environ.get("ADDR_REFERENCE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "reference.sqlite")
    if os.path.exists(path):
        return Reference.from_sqlite(path)
    return Reference.from_seed()
