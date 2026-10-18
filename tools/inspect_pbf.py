"""Разведка OSM PBF: как в файле лежат области, районы, улицы, микрорайоны.

Ничего не строит, только считает и пишет отчёт (stdout + inspect_report.txt).
Нужен pyosmium:   pip install osmium

Запуск:   python tools/inspect_pbf.py kazakhstan-261015.osm.pbf
Отчёт скопируй целиком и пришли мне.
"""
from __future__ import annotations

import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List

SAMPLES = 8
# административные единицы, по которым хотим видеть, на каком admin_level они лежат
WATCH = ("алматы", "астана", "шымкент", "ауэзов", "турксиб", "есиль", "илийск",
         "актобе", "актюбин", "караганд", "боралдай", "жуалы")


def has_cyr(s: str) -> bool:
    return any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in s)


class Stats:
    """Накопитель. Не зависит от osmium: принимает (вид, id, dict тегов)."""

    def __init__(self) -> None:
        self.n_seen = Counter()
        self.admin = defaultdict(list)          # admin_level -> примеры
        self.admin_cnt = Counter()
        self.admin_watch: List[str] = []
        self.place = defaultdict(list)          # (вид, значение place) -> примеры
        self.place_cnt = Counter()
        self.hw_cnt = Counter()
        self.hw_named = 0
        self.hw_lang = Counter()
        self.hw_samples: List[str] = []
        self.hw_typeword: List[str] = []        # имена вида «улица Абая»
        self.hw_digits: List[str] = []          # имена с цифрами (мкр, кварталы)
        self.addr = Counter()
        self.addr_samples: List[str] = []
        self.micro_named: List[str] = []

    @staticmethod
    def _names(t: Dict[str, str]) -> str:
        parts = [f"name={t.get('name')!r}"]
        for k in ("name:ru", "name:kk", "alt_name", "old_name"):
            if k in t:
                parts.append(f"{k}={t[k]!r}")
        return " ".join(parts)

    def feed(self, kind: str, oid: int, t: Dict[str, str]) -> None:
        self.n_seen[kind] += 1
        name = t.get("name", "")

        # --- административные границы
        if t.get("boundary") == "administrative" and kind == "r":
            lvl = t.get("admin_level", "?")
            self.admin_cnt[lvl] += 1
            if len(self.admin[lvl]) < SAMPLES:
                self.admin[lvl].append(f"r{oid} {self._names(t)} place={t.get('place')}")
            low = (name + " " + t.get("name:ru", "")).lower()
            if any(w in low for w in WATCH) and len(self.admin_watch) < 80:
                self.admin_watch.append(f"admin_level={lvl} r{oid} {self._names(t)} place={t.get('place')}")

        # --- place=*
        pl = t.get("place")
        if pl:
            key = (kind, pl)
            self.place_cnt[key] += 1
            if len(self.place[key]) < SAMPLES:
                self.place[key].append(f"{kind}{oid} {self._names(t)}")
            low = (name + " " + t.get("name:ru", "")).lower()
            if ("микрорайон" in low or "мкр" in low) and len(self.micro_named) < 15:
                self.micro_named.append(f"place={pl} {kind}{oid} {self._names(t)}")

        # --- улицы
        hw = t.get("highway")
        if hw and kind == "w":
            self.hw_cnt[hw] += 1
            if name:
                self.hw_named += 1
                for k in ("name:ru", "name:kk", "alt_name", "old_name", "int_name"):
                    if k in t:
                        self.hw_lang[k] += 1
                if has_cyr(name):
                    self.hw_lang["name_cyrillic"] += 1
                if len(self.hw_samples) < 25 and hw in ("residential", "primary", "secondary", "tertiary", "service"):
                    self.hw_samples.append(f"highway={hw} w{oid} {self._names(t)}")
                low = name.lower()
                if len(self.hw_typeword) < 15 and any(
                        w in low.split() for w in ("улица", "проспект", "переулок", "көшесі", "микрорайон")):
                    self.hw_typeword.append(f"highway={hw} w{oid} {self._names(t)}")
                if len(self.hw_digits) < 15 and any(ch.isdigit() for ch in name):
                    self.hw_digits.append(f"highway={hw} w{oid} {self._names(t)}")

        # --- адреса
        if "addr:housenumber" in t:
            self.addr[f"{kind}: addr:housenumber"] += 1
            for k in ("addr:street", "addr:city", "addr:district", "addr:suburb", "addr:place", "addr:postcode"):
                if k in t:
                    self.addr[f"{kind}: +{k}"] += 1
            if len(self.addr_samples) < 15:
                self.addr_samples.append(
                    f"{kind}{oid} " + " ".join(f"{k}={v!r}" for k, v in t.items() if k.startswith("addr:")))

    def report(self) -> str:
        out: List[str] = []
        w = out.append
        w("=== ОБЩЕЕ (после фильтра по ключам) ===")
        w(f"объектов по типам: {dict(self.n_seen)}")
        w("\n=== ADMIN: boundary=administrative, relations, по admin_level ===")
        for lvl in sorted(self.admin_cnt, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0)):
            w(f"\nadmin_level={lvl}: {self.admin_cnt[lvl]} шт.")
            for s in self.admin[lvl]:
                w("   " + s)
        w("\n=== ADMIN: где лежат интересующие нас единицы ===")
        out.extend("   " + s for s in self.admin_watch)
        w("\n=== PLACE=* по видам объекта (n=node, w=way, r=relation) ===")
        for key, c in sorted(self.place_cnt.items(), key=lambda kv: -kv[1]):
            w(f"\n{key[0]} place={key[1]}: {c}")
            for s in self.place[key][:3]:
                w("   " + s)
        w("\n=== МИКРОРАЙОНЫ в place-объектах (по слову в имени) ===")
        out.extend("   " + s for s in self.micro_named)
        w("\n=== УЛИЦЫ: ways с highway=* ===")
        w(f"по значениям highway: {dict(self.hw_cnt.most_common(15))}")
        w(f"из них с name: {self.hw_named}")
        w(f"доля тегов среди именованных: {dict(self.hw_lang)}")
        w("\nпримеры именованных:")
        out.extend("   " + s for s in self.hw_samples)
        w("\nимена со словом-типом внутри («улица Абая»):")
        out.extend("   " + s for s in self.hw_typeword)
        w("\nимена с цифрами (мкр/кварталы):")
        out.extend("   " + s for s in self.hw_digits)
        w("\n=== АДРЕСА (addr:*) ===")
        w(str(dict(self.addr)))
        out.extend("   " + s for s in self.addr_samples)
        return "\n".join(out)


KEYS = ("boundary", "place", "highway", "addr:housenumber")


def run(path: str) -> Stats:
    import osmium  # noqa: WPS433

    st = Stats()
    t0 = time.time()

    if hasattr(osmium, "FileProcessor"):
        fp = osmium.FileProcessor(path).with_filter(osmium.filter.KeyFilter(*KEYS))
        for i, o in enumerate(fp, 1):
            kind = "n" if o.is_node() else "w" if o.is_way() else "r" if o.is_relation() else None
            if kind is None:
                continue
            st.feed(kind, o.id, {t.k: t.v for t in o.tags})
            if i % 500000 == 0:
                print(f"  ...{i} объектов, {time.time() - t0:.0f} с", file=sys.stderr)
    else:   # старый pyosmium (<3.7)
        class H(osmium.SimpleHandler):
            def node(self, o):
                st.feed("n", o.id, {t.k: t.v for t in o.tags}) if o.tags else None

            def way(self, o):
                st.feed("w", o.id, {t.k: t.v for t in o.tags}) if o.tags else None

            def relation(self, o):
                st.feed("r", o.id, {t.k: t.v for t in o.tags}) if o.tags else None

        H().apply_file(path)
    print(f"прочитано за {time.time() - t0:.0f} с", file=sys.stderr)
    return st


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Использование: python tools/inspect_pbf.py <файл.osm.pbf>")
    rep = run(sys.argv[1]).report()
    with open("inspect_report.txt", "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    print("\nОтчёт сохранён в inspect_report.txt", file=sys.stderr)


if __name__ == "__main__":
    main()
