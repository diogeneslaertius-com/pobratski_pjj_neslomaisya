"""OSM PBF -> data/reference.sqlite  (справочник для парсера).

Запуск:
    pip install osmium shapely numpy
    python tools/extract_osm.py kazakhstan-261015.osm.pbf data/reference.sqlite

Нужно ~4 ГБ ОЗУ и 10-25 минут. При нехватке памяти:  --node-cache nodes.cache
(кэш координат узлов пойдёт на диск, медленнее, но памяти почти не нужно).

Что делает (по результатам inspect_report.txt):
  * в файле есть соседние страны -> области берём по словарю (seed.PROVINCES) + Алматы/Астана/Шымкент
    (admin_level=4); всё, что вне казахстанских областей, отбрасываем по геометрии;
  * районы: admin_level 6 и 7 внутри казахстанских областей; «Актобе Г.А.»/«Қ.Ә.» - это границы
    города, а не районы;
  * города/сёла: place=city|town|village|hamlet (узлы и контуры);
  * улицы: highway=* с name (+ addr:street у зданий); микрорайоны: place=neighbourhood|suburb|quarter
    и addr:suburb/addr:place вида «микрорайон ...»;
  * каждой улице считаем город и район (по полигонам), а у района - долю адресов.
Чтение PBF и геометрию я локально проверить не мог: при ошибке пришли traceback целиком.
"""
from __future__ import annotations

import argparse
import array
import os
import re
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    import numpy as np
    import osmium
    import shapely
    from shapely import STRtree
    from shapely.geometry import LineString
    from shapely.ops import polygonize
except ImportError as e:   # pragma: no cover
    sys.exit(f"Не хватает библиотеки ({e}). Установи:  pip install osmium shapely numpy")

from addrparse import osmnames, refdb, seed                                   # noqa: E402
from addrparse.refdata import RANK_CITY, RANK_HAMLET, RANK_TOWN, RANK_VILLAGE, Reference  # noqa: E402
from addrparse.textutil import norm_key                                       # noqa: E402

T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:6.0f} с] {msg}", file=sys.stderr, flush=True)


NAME_KEYS = ("name", "name:ru", "name:kk", "alt_name")
HW_SKIP = {"footway", "path", "steps", "track", "cycleway", "bridleway", "corridor", "proposed",
           "construction", "elevator", "platform", "raceway", "bus_guideway", "escape"}
PLACE_RANK = {"city": RANK_CITY, "town": RANK_TOWN, "village": RANK_VILLAGE, "hamlet": RANK_HAMLET}
PLACE_LIVING = {"neighbourhood", "suburb", "quarter", "city_block", "subdistrict"}
REPUBLIC = ("Алматы", "Астана", "Шымкент")
MICRO_RE = re.compile(r"микрорайон|мкр|шағын\s*аудан|жилой\s+массив|жилмассив|тұрғын\s+алап|квартал|\bжм\b|\bжк\b", re.I)
DESIGNATION_RE = re.compile(r"^[a-zа-я]{1,2}[- ]?\d{1,3}$", re.I)        # А-18, M-32: номера трасс, не названия
EXPECTED_PROVINCES = [p[0] for p in seed.PROVINCES] + list(REPUBLIC)


# ----------------------------------------------------------------------------- области
def make_province_matcher():
    ref = Reference()
    seed.fill_provinces(ref)
    republic = {norm_key(n): n for n in REPUBLIC}

    def match(tags: Dict[str, str]) -> Optional[Tuple[str, bool]]:
        names = [tags.get(k) for k in ("name:ru", "name", "name:kk")]
        for nm in names:
            if nm and re.search(r"област|облыс", nm, re.I):
                key = norm_key(re.sub(r"(?i)\b(область|облысы|обл\.?)\b", " ", nm))
                p = ref.province_ctx.get(key)
                if p:
                    return p, False
        for nm in names:
            if nm and norm_key(nm) in republic:
                return republic[norm_key(nm)], True
        return None
    return match


# ----------------------------------------------------------------------------- проход 1: отношения
class RelPass(osmium.SimpleHandler):
    def __init__(self, match):
        super().__init__()
        self.match = match
        self.rels: Dict[int, dict] = {}

    def relation(self, r):
        t = r.tags
        if t.get("boundary") != "administrative":
            return
        lvl = t.get("admin_level")
        if lvl not in ("4", "6", "7"):
            return
        tags = {k: t.get(k) for k in NAME_KEYS if t.get(k)}
        info: dict = {"level": int(lvl), "tags": tags}
        if lvl == "4":
            pm = self.match(tags)
            if not pm:
                return
            info["prov"], info["republic"] = pm
        info["ways"] = [m.ref for m in r.members if m.type == "w" and m.role in ("outer", "")]
        self.rels[r.id] = info


# ----------------------------------------------------------------------------- проход 2: узлы и линии
class MainPass(osmium.SimpleHandler):
    def __init__(self, needed_ways: set):
        super().__init__()
        self.needed = needed_ways
        self.bw: Dict[int, array.array] = {}          # контуры границ: id линии -> [lon,lat,lon,lat,...]
        # записи для улиц/мест: вид, варианты названия, подсказка города (addr:city), координаты
        self.kind: List[str] = []
        self.var: List[tuple] = []
        self.hint: List[Optional[str]] = []
        self.X = array.array("d")
        self.Y = array.array("d")
        # населённые пункты и «жилые» места (узлы и контуры)
        self.places: List[tuple] = []                  # (place, tags_dict, lon, lat)
        self._vcache: Dict[tuple, tuple] = {}
        self.n_way = self.n_node = 0

    # -- вспомогательное
    def _variants(self, t, base: str) -> tuple:
        key = (t.get(base), t.get(base + ":ru"), t.get(base + ":kk"), t.get("alt_name") if base == "name" else None)
        v = self._vcache.get(key)
        if v is None:
            d = {k: x for k, x in zip((base, base + ":ru", base + ":kk", "alt_name"), key) if x}
            v = tuple(osmnames.name_variants(d, base))
            self._vcache[key] = v
        return v

    def _add(self, kind, var, hint, x, y):
        self.kind.append(kind)
        self.var.append(var)
        self.hint.append(hint)
        self.X.append(x)
        self.Y.append(y)

    def _add_addr(self, t, x, y):
        city = t.get("addr:city")
        if t.get("addr:street"):
            v = self._variants(t, "addr:street")
            if v:
                self._add("street", v, city, x, y)
        for k in ("addr:suburb", "addr:place"):
            val = t.get(k)
            if val and MICRO_RE.search(val):
                v = self._vcache.get((k, val))
                if v is None:
                    tp, base = osmnames.split_type(val)
                    v = ((tp or "микрорайон", base),)
                    self._vcache[(k, val)] = v
                self._add("place", v, city, x, y)

    # -- линии
    def way(self, w):
        self.n_way += 1
        wid = w.id
        if wid in self.needed:
            try:
                arr = array.array("d")
                for n in w.nodes:
                    arr.append(n.lon)
                    arr.append(n.lat)
                self.bw[wid] = arr
            except Exception:          # узлы за границей выгрузки
                pass
        t = w.tags
        if not len(t):
            return
        hw = t.get("highway")
        place = t.get("place")
        has_addr = t.get("addr:housenumber") is not None
        if not (hw or place or has_addr):
            return
        try:
            n0 = w.nodes[len(w.nodes) // 2]
            x, y = n0.lon, n0.lat
        except Exception:
            return
        if hw and hw not in HW_SKIP and t.get("name"):
            v = self._variants(t, "name")
            if v:
                self._add("street", v, None, x, y)
        if place and t.get("name"):
            self.places.append((place, {k: t.get(k) for k in NAME_KEYS if t.get(k)}, x, y))
        if has_addr:
            self._add_addr(t, x, y)

    # -- узлы
    def node(self, n):
        t = n.tags
        if not len(t):
            return
        place = t.get("place")
        if place and t.get("name"):
            loc = n.location
            self.places.append((place, {k: t.get(k) for k in NAME_KEYS if t.get(k)}, loc.lon, loc.lat))
        if t.get("addr:housenumber") is not None:
            loc = n.location
            self._add_addr(t, loc.lon, loc.lat)


# ----------------------------------------------------------------------------- геометрия
def assemble(way_ids: List[int], bw: Dict[int, array.array], simplify: float):
    lines = []
    for wid in way_ids:
        a = bw.get(wid)
        if a is not None and len(a) >= 4:
            lines.append(LineString(list(zip(a[0::2], a[1::2]))))
    if not lines:
        return None
    merged = shapely.unary_union(lines)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    polys = list(polygonize(parts))
    if not polys:
        return None
    g = shapely.unary_union(polys)
    if not g.is_valid:
        g = g.buffer(0)
    if simplify:
        g = g.simplify(simplify, preserve_topology=True)
    return g if not g.is_empty else None


class PolyIndex:
    """Для каждой точки находит САМЫЙ МАЛЕНЬКИЙ полигон, в который она попала (-1 если ни в какой)."""

    def __init__(self, geoms: list):
        self.geoms = np.empty(len(geoms), dtype=object)
        for i, g in enumerate(geoms):
            self.geoms[i] = g
        self.area = np.array([g.area for g in geoms]) if geoms else np.zeros(0)
        for g in geoms:
            shapely.prepare(g)
        self.tree = STRtree(self.geoms) if geoms else None

    def smallest(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        out = np.full(len(xs), -1, dtype=np.int64)
        if self.tree is None or not len(xs):
            return out
        pi, gi = self.tree.query(shapely.points(xs, ys))
        for g in np.argsort(-self.area, kind="stable"):          # от крупных к мелким: мелкие перезапишут
            sel = pi[gi == g]
            if not len(sel):
                continue
            inside = shapely.contains_xy(self.geoms[g], xs[sel], ys[sel])
            out[sel[inside]] = g
        return out


# ----------------------------------------------------------------------------- основной сбор
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("out")
    ap.add_argument("--node-cache", default=None, help="файл-кэш координат узлов на диске (если мало ОЗУ)")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    match = make_province_matcher()

    # ---- проход 1
    log("проход 1/2: границы (отношения)")
    rp = RelPass(match)
    rp.apply_file(args.pbf)
    rels = rp.rels
    needed = {w for r in rels.values() for w in r["ways"]}
    log(f"  отношений границ: {len(rels)}, линий в них: {len(needed)}")

    # ---- проход 2
    log("проход 2/2: узлы и линии (самый долгий этап, 5-20 минут)")
    mp = MainPass(needed)
    idx = f"sparse_file_array,{args.node_cache}" if args.node_cache else "flex_mem"
    mp.apply_file(args.pbf, locations=True, idx=idx)
    log(f"  линий: {mp.n_way}, записей улиц/мест: {len(mp.X)}, мест (place): {len(mp.places)}")

    # ---- области
    log("собираю полигоны областей")
    prov_geoms: Dict[str, list] = defaultdict(list)
    prov_rep: Dict[str, bool] = {}
    prov_tags: Dict[str, dict] = {}
    for rid, r in rels.items():
        if r["level"] != 4:
            continue
        g = assemble(r["ways"], mp.bw, 0.002)
        if g is None:
            log(f"  ! граница не собралась: r{rid} {r['tags'].get('name')}")
            continue
        prov_geoms[r["prov"]].append(g)
        prov_rep[r["prov"]] = r["republic"]
        prov_tags[r["prov"]] = r["tags"]
    provs = []                               # (имя, republic, геометрия)
    for name, gs in prov_geoms.items():
        provs.append((name, prov_rep[name], shapely.unary_union(gs)))
    missing = [p for p in EXPECTED_PROVINCES if p not in prov_geoms]
    log(f"  найдено областей/городов респ. значения: {len(provs)} из {len(EXPECTED_PROVINCES)}"
        + (f"; НЕ найдены: {missing}" if missing else ""))
    prov_idx = PolyIndex([g for _, _, g in provs])

    # ---- города
    cities: List[dict] = []                 # cid, name, province, rank, republic, lon, lat, keys
    city_by_prov_name: Dict[str, int] = {}
    seed_alias: Dict[str, Tuple[str, list]] = {}      # исторические названия: Алма-Ата, Нур-Султан, Чимкент...
    for n, p, al, rep in seed.CITIES:
        seed_alias[norm_key(n)] = (p, al)

    def keys_for(tags: Dict[str, str], cleaner) -> set:
        ks = set()
        for k in NAME_KEYS:
            for raw in (tags.get(k) or "").split(";"):
                c = cleaner(raw)
                if c:
                    ks.add(norm_key(c))
        return ks

    for name, rep, g in provs:
        if not rep:
            continue
        tags = prov_tags[name]
        keys = keys_for(tags, osmnames.clean_city_name)
        keys.add(norm_key(name))
        for a in seed_alias.get(norm_key(name), (None, []))[1]:
            keys.add(norm_key(a))
        c = g.representative_point()
        cities.append({"cid": len(cities) + 1, "name": name, "province": name, "rank": RANK_CITY, "republic": 1,
                       "lon": c.x, "lat": c.y, "keys": keys})
        city_by_prov_name[name] = cities[-1]["cid"]

    prov_names = [p[0] for p in provs]
    seen_places = set()
    pl_x, pl_y, pl_cid = [], [], []
    px = np.array([p[2] for p in mp.places])
    py = np.array([p[3] for p in mp.places])
    place_prov = prov_idx.smallest(px, py) if len(px) else np.zeros(0, dtype=np.int64)
    n_foreign = 0
    for (place, tags, x, y), pi in zip(mp.places, place_prov):
        rank = PLACE_RANK.get(place)
        if rank is None:
            continue
        if pi < 0:
            n_foreign += 1
            continue
        pname, prep, _ = provs[pi]
        if prep:                                   # точка внутри Алматы/Астаны/Шымкента: сам город уже есть
            continue
        canon = osmnames.clean_city_name(tags.get("name:ru") or tags.get("name") or "")
        if not canon:
            continue
        sig = (norm_key(canon), pname, int(x * 10), int(y * 10))
        if sig in seen_places:
            continue
        seen_places.add(sig)
        keys = keys_for(tags, osmnames.clean_city_name)
        sa = seed_alias.get(norm_key(canon))
        if sa and sa[0] == pname:
            keys.update(norm_key(a) for a in sa[1])
        cities.append({"cid": len(cities) + 1, "name": canon, "province": pname, "rank": rank, "republic": 0,
                       "lon": x, "lat": y, "keys": keys})
        pl_x.append(x)
        pl_y.append(y)
        pl_cid.append(len(cities))
    log(f"  городов/сёл: {len(cities)} (отброшено вне Казахстана: {n_foreign})")

    # ---- районы и границы городов (городские администрации)
    log("собираю полигоны районов")
    districts: List[dict] = []              # did, name, parent_cid, province, keys, area
    dist_geoms: list = []
    adm_geoms: list = []
    adm_info: List[dict] = []               # province, cid
    for rid, r in rels.items():
        if r["level"] not in (6, 7):
            continue
        tags = r["tags"]
        g = assemble(r["ways"], mp.bw, 0.0003)
        if g is None:
            continue
        rp_pt = g.representative_point()
        pi = int(prov_idx.smallest(np.array([rp_pt.x]), np.array([rp_pt.y]))[0])
        if pi < 0:
            continue                                       # район чужой страны
        pname, prep, _ = provs[pi]
        label = tags.get("name:ru") or tags.get("name") or ""
        if osmnames.is_city_administration(label) or osmnames.is_city_administration(tags.get("name", "")):
            adm_geoms.append(g)
            adm_info.append({"province": pname, "tags": tags, "cid": None})
            continue
        canon = osmnames.clean_district_name(tags.get("name:ru") or tags.get("name") or "")
        if not canon:
            continue
        keys = keys_for(tags, osmnames.clean_district_name)
        parent = city_by_prov_name.get(pname) if prep else None
        districts.append({"did": len(districts) + 1, "name": canon, "parent_cid": parent, "province": pname,
                          "keys": keys, "area": g.area})
        dist_geoms.append(g)
    dist_idx = PolyIndex(dist_geoms)
    adm_idx = PolyIndex(adm_geoms)
    log(f"  районов: {len(districts)}, границ городов (Г.А.): {len(adm_geoms)}")

    # город для каждой границы Г.А.: самое крупное поселение внутри, лучше с совпадающим именем
    if adm_geoms and pl_x:
        pts_tree = STRtree(shapely.points(np.array(pl_x), np.array(pl_y)))
        for g, info in zip(adm_geoms, adm_info):
            inside = pts_tree.query(g, predicate="contains")
            if not len(inside):
                continue
            adm_keys = keys_for(info["tags"], osmnames.clean_district_name)
            best = max(inside, key=lambda i: (
                bool(adm_keys & cities[pl_cid[i] - 1]["keys"]), cities[pl_cid[i] - 1]["rank"]))
            info["cid"] = pl_cid[best]

    # ---- привязка улиц и мест к городу и району
    log("привязываю улицы к городам и районам")
    xs = np.frombuffer(mp.X, dtype=np.float64) if len(mp.X) else np.zeros(0)
    ys = np.frombuffer(mp.Y, dtype=np.float64) if len(mp.Y) else np.zeros(0)
    ri = prov_idx.smallest(xs, ys)
    ai = adm_idx.smallest(xs, ys)
    di = dist_idx.smallest(xs, ys)

    # сёла: ближайшее поселение (для точек вне Алматы/Астаны/Шымкента и вне Г.А.)
    rural = np.array([i for i in range(len(xs)) if ri[i] >= 0 and not provs[ri[i]][1]
                      and not (ai[i] >= 0 and adm_info[ai[i]]["cid"])], dtype=np.int64)
    nearest = np.full(len(xs), -1, dtype=np.int64)
    if len(rural) and pl_x:
        tree = STRtree(shapely.points(np.array(pl_x), np.array(pl_y)))
        pairs = tree.query_nearest(shapely.points(xs[rural], ys[rural]), max_distance=0.25, all_matches=False)
        nearest[rural[pairs[0]]] = pairs[1]

    key_to_cities: Dict[str, List[dict]] = defaultdict(list)
    for c in cities:
        for k in c["keys"]:
            key_to_cities[k].append(c)

    agg: Dict[tuple, list] = {}             # (cid,key,kind,type,did) -> [name, cnt]
    n_ok = n_nocity = 0
    for i in range(len(xs)):
        pi = ri[i]
        if pi < 0:
            continue
        pname, prep, _ = provs[pi]
        cid = did = None
        if prep:
            cid = city_by_prov_name[pname]
            d = int(di[i])
            if d >= 0 and districts[d]["parent_cid"] == cid:
                did = districts[d]["did"]
        else:
            adm = int(ai[i])
            if adm >= 0 and adm_info[adm]["cid"] and adm_info[adm]["province"] == pname:
                cid = adm_info[adm]["cid"]
                d = int(di[i])
                if d >= 0 and districts[d]["parent_cid"] is None and districts[d]["province"] == pname \
                        and dist_idx.area[d] < adm_idx.area[adm] * 0.99:
                    did = districts[d]["did"]
            else:
                h = mp.hint[i]
                if h:
                    cs = [c for c in key_to_cities.get(norm_key(osmnames.clean_city_name(h)), [])
                          if c["province"] == pname]
                    if cs:
                        cid = max(cs, key=lambda c: c["rank"])["cid"]
                if cid is None and nearest[i] >= 0:
                    cid = pl_cid[int(nearest[i])]
                d = int(di[i])
                if d >= 0 and districts[d]["parent_cid"] is None and districts[d]["province"] == pname:
                    did = districts[d]["did"]
        if cid is None:
            n_nocity += 1
            continue
        n_ok += 1
        variants = mp.var[i]
        tp = osmnames.pick_type(variants) or (None)
        kind = mp.kind[i]
        city_key = cities[cid - 1]["keys"]
        for _, base in variants:
            key = norm_key(base)
            if not key or len(base) > 60 or DESIGNATION_RE.match(base.strip()):
                continue
            if key in city_key and kind == "place":       # «Алматы» как микрорайон города Алматы: не нужно
                continue
            k = (cid, key, kind, tp, did)
            row = agg.get(k)
            if row is None:
                agg[k] = [base, 1]
            else:
                row[1] += 1
    log(f"  записей привязано к городу: {n_ok}, без города: {n_nocity}")

    # живые места (place=neighbourhood/suburb/...) - узлы и контуры
    living = [p for p in mp.places if p[0] in PLACE_LIVING]
    if living:
        lx = np.array([p[2] for p in living])
        ly = np.array([p[3] for p in living])
        l_prov = prov_idx.smallest(lx, ly)
        l_dist = dist_idx.smallest(lx, ly)
        l_near = np.full(len(living), -1, dtype=np.int64)
        if pl_x:
            tree2 = STRtree(shapely.points(np.array(pl_x), np.array(pl_y)))
            pr = tree2.query_nearest(shapely.points(lx, ly), max_distance=0.3, all_matches=False)
            l_near[pr[0]] = pr[1]
        n_living = 0
        for j, (place, tags, x, y) in enumerate(living):
            if l_prov[j] < 0:
                continue
            pname, prep, _ = provs[l_prov[j]]
            did = None
            if prep:
                cid = city_by_prov_name[pname]
                d = int(l_dist[j])
                if d >= 0 and districts[d]["parent_cid"] == cid:
                    did = districts[d]["did"]
            else:
                if l_near[j] < 0:
                    continue
                cid = pl_cid[int(l_near[j])]
                if cities[cid - 1]["province"] != pname:
                    continue
            dtags = {k: tags[k] for k in NAME_KEYS if k in tags}
            for tp, base in osmnames.name_variants(dtags, "name"):
                key = norm_key(base)
                if not key or len(base) > 60 or key in cities[cid - 1]["keys"]:
                    continue
                k = (cid, key, "place", tp or "микрорайон", did)
                row = agg.get(k)
                if row is None:
                    agg[k] = [base, 1]
                    n_living += 1
                else:
                    row[1] += 1
        log(f"  мест place=neighbourhood/suburb/quarter добавлено: {n_living}")

    # ---- запись
    log("записываю SQLite")
    city_rows = [(c["cid"], c["name"], c["province"], c["rank"], c["republic"], c["lon"], c["lat"]) for c in cities]
    city_key_rows = [(k, c["cid"]) for c in cities for k in sorted(c["keys"]) if k]
    dist_rows = [(d["did"], d["name"], d["parent_cid"], d["province"]) for d in districts]
    dist_key_rows = [(k, d["did"]) for d in districts for k in sorted(d["keys"]) if k]
    item_rows = [(cid, key, name, kind, tp, did, cnt) for (cid, key, kind, tp, did), (name, cnt) in agg.items()]
    refdb.write(args.out, city_rows, city_key_rows, dist_rows, dist_key_rows, item_rows,
                {"source": os.path.basename(args.pbf), "built_s": int(time.time() - T0)})
    log(f"готово: {args.out}  ({os.path.getsize(args.out) / 1e6:.1f} МБ)")

    # ---- самопроверка и отчёт
    print("\n=== ОТЧЁТ СБОРКИ ===")
    print(f"области/города респ. значения: {len(provs)} из {len(EXPECTED_PROVINCES)}; не найдены: {missing or 'нет'}")
    print(f"городов и сёл: {len(cities)}; районов: {len(districts)}; границ городов (Г.А.): {len(adm_geoms)}")
    print(f"строк улиц/мест: {len(item_rows)}; привязано {n_ok}, без города {n_nocity}")
    by_prov = defaultdict(int)
    for d in districts:
        by_prov[d["province"]] += 1
    print("районов по областям:", dict(sorted(by_prov.items())))
    try:
        ref = Reference.from_sqlite(args.out)
        print("загрузка парсером:", ref.stats())
        for cname, street in (("Алматы", "Аксай 3а"), ("Алматы", "Абая"), ("Алматы", "Самал 2"),
                              ("Астана", "Туран"), ("Актобе", "Курильская"), ("Шымкент", "Тауке хана")):
            c = ref.city(cname)
            if c is None:
                print(f"  {cname}: города нет в справочнике!")
                continue
            h = ref.lookup(c, norm_key(street), cutoff=100.0)
            print(f"  {cname} / {street}: {'нет' if h is None else (h.kind, h.type)}; районы:",
                  [(n, round(s, 2)) for n, s in ref.district_candidates(c, norm_key(street))[:3]])
    except Exception as e:     # pragma: no cover
        print("самопроверка не удалась:", e)


if __name__ == "__main__":
    main()
