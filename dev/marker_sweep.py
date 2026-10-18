"""Систематическая проверка маркеров: каждое слово из MARKERS x способы написания (слитно, с точкой, до/после числа).
Запуск: python dev/marker_sweep.py   (код возврата 1, если есть расхождения)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from addrparse import AddressParser                     # noqa: E402
from addrparse.parser import MARKERS, SINGLE_AFTER      # noqa: E402

FIELD = {"apt": "apartment", "ent": "entrance", "floor": "floor", "house": "house"}


def forms(w):
    after = [f"{w} 7", f"{w}7", f"{w}. 7", f"{w}.7"] if w != "э" else []     # «э» бывает только после числа
    before = [f"7 {w}", f"7{w}"]
    return after, (before if w not in SINGLE_AFTER else [])


def street_types(p):
    """Каждое слово-тип улицы x способы написания: ожидаем улицу «Абая» и дом 10."""
    from addrparse.parser import STREET_T
    bad, total = [], 0
    for w in sorted(x for x in STREET_T if not x.endswith(".")):
        for f in (f"{w} Абая 10", f"{w}. Абая 10", f"{w}.Абая 10", f"Абая {w} 10", f"Абая {w}, 10", f"{w} Абая, 10"):
            for pre in ("Алматы, ", ""):
                s = pre + f + ", кв 5"
                total += 1
                r = p.parse(s).values
                wrong = {k: (v, r.get(k)) for k, v in {"street_name": "Абая", "house": "10", "apartment": "5"}.items()
                         if (r.get(k) or "").lower() != v.lower()}
                if wrong:
                    bad.append((w, f, s, wrong))
    return total, bad


def main():
    p = AddressParser()
    bad = []
    total = 0
    t2, bad2 = street_types(p)
    for w, kind in sorted(MARKERS.items()):
        after, before = forms(w)
        for f in after + before:
            for tmpl in ("Алматы, Абая 10, {}", "Алматы, Абая 10 {}", "{}, Алматы, Абая 10"):
                if kind == "house":
                    # дом: «Абая <форма>» (улица перед домом) и «Абая <форма>, кв 5»
                    s = "Алматы, Абая " + f + ", кв 5"
                    exp = {"house": "7", "street_name": "Абая", "apartment": "5"}
                    if tmpl != "Алматы, Абая 10, {}":
                        continue
                else:
                    s = tmpl.format(f)
                    exp = {FIELD[kind]: "7", "house": "10", "street_name": "Абая"}
                    if kind == "apt":
                        exp[FIELD[kind]] = "7"
                total += 1
                r = p.parse(s).values
                wrong = {k: (v, r.get(k)) for k, v in exp.items() if (r.get(k) or "").lower() != v.lower()}
                if wrong:
                    bad.append((w, f, s, wrong))
    print(f"маркеры: проверено {total}, расхождений {len(bad)}; типы улиц: проверено {t2}, расхождений {len(bad2)}")
    bad += bad2
    seen = set()
    for w, f, s, wrong in bad:
        if (w, f) in seen:
            continue
        seen.add((w, f))
        print(f"  [{w}] {s!r} -> {wrong}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
