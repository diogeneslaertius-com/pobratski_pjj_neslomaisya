"""Прогон dev-набора: метрика жюри + список расхождений.

Запуск:  python dev/run_dev.py            (кратко + ошибки)
         python dev/run_dev.py --all      (ещё и все верные записи)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from addrparse import AddressParser          # noqa: E402
from addrparse import fuzz                   # noqa: E402
from addrparse.metric import GROUPS, score   # noqa: E402
from addrparse.textutil import cmp_norm      # noqa: E402

HERE = os.path.dirname(__file__)


def main() -> None:
    show_all = "--all" in sys.argv
    data = json.load(open(os.path.join(HERE, "dev_set.json"), encoding="utf-8"))
    parser = AddressParser()
    recs = [{"id": f"dev_{i:03d}", "raw_address": d["raw_address"]} for i, d in enumerate(data)]
    preds = parser.parse_batch(recs)
    golds = [d["gold"] for d in data]

    total, stats, groups = score(preds, golds)
    print(f"fuzzy backend: {'rapidfuzz' if fuzz.HAVE_RAPIDFUZZ else 'difflib (запасной)'}")
    print(f"ИТОГО по метрике жюри: {total * 100:.1f}%   записей: {len(data)}")
    for name, weight, fields in GROUPS:
        cells = ", ".join(f"{f} {stats[f][0]}/{stats[f][1]}" for f in fields)
        print(f"  {name:<4} ({int(weight * 100)}%): {groups[name] * 100:5.1f}%   [{cells}]")

    print("\nРасхождения:")
    bad = 0
    for d, p in zip(data, preds):
        diffs = []
        for f, gv in d["gold"].items():
            if f in ("street_type", "landmark"):      # не входят в метрику, но смотрим глазами
                tag = "(инфо) "
            else:
                tag = ""
            if cmp_norm(p.get(f)) != cmp_norm(gv):
                diffs.append(f"    {tag}{f}: получено {p.get(f)!r}, ожидалось {gv!r}")
        scored_diff = [x for x in diffs if "(инфо)" not in x]
        if scored_diff:
            bad += 1
        if diffs or show_all:
            flag = "ОШИБКА" if scored_diff else ("инфо" if diffs else "ок")
            note = " [нужен справочник]" if d.get("needs_ref") else ""
            print(f"- [{flag}]{note} {d['raw_address']!r}  conf={p['confidence']}")
            for x in diffs:
                print(x)
    print(f"\nЗаписей с ошибками в метрике: {bad}/{len(data)}")


if __name__ == "__main__":
    main()
