"""Генератор грязных адресов с известным эталоном + проверка парсера.

Берёт чистые компоненты (город, улица, дом, квартира...) и собирает строку так, как пишут люди:
латиницей, слитно, без города, с городом в конце, с ориентиром, с невидимыми символами, в разном регистре.
Эталон известен по построению. Запуск:
    python dev/gen_noisy.py                 # 3000 адресов, seed=1, показать 25 ошибок
    python dev/gen_noisy.py -n 5000 -s 7 --show 40
    python dev/gen_noisy.py --save dev/noisy_dev.json     # сохранить набор

ВАЖНО: это проверка устойчивости к шуму, который придумали мы. Рост здесь не равен росту на скрытом
наборе жюри: настоящие строки могут быть грязнее. Лучший тест - реальные raw_address.
"""
import argparse
import json
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from addrparse import AddressParser              # noqa: E402
from addrparse.metric import GROUPS, score       # noqa: E402
from addrparse.textutil import cmp_norm          # noqa: E402

# ---------------------------------------------------------------- компоненты
# (название как пишут по-русски, тип, казахский вариант без типа)
STREETS = [
    ("Абая", "проспект", "Абай"), ("Назарбаева", "проспект", "Назарбаев"), ("Аль-Фараби", "проспект", "Әл-Фараби"),
    ("Достык", "проспект", "Достық"), ("Сейфуллина", "проспект", "Сейфуллин"), ("Рыскулова", "проспект", "Рысқұлов"),
    ("Тимирязева", "улица", "Тимирязев"), ("Сатпаева", "улица", "Сәтбаев"), ("Курильская", "улица", "Курильская"),
    ("Мустафина", "улица", "Мұстафин"), ("Сулейменова", "улица", "Сүлейменов"), ("Жандосова", "улица", "Жандосов"),
    ("Маметовой", "улица", "Маметова"), ("Казыбек би", "улица", "Қазыбек би"), ("Толе би", "улица", "Төле би"),
    ("Байтурсынова", "улица", "Байтұрсынұлы"), ("Ауэзова", "улица", "Әуезов"), ("Розыбакиева", "улица", "Розыбакиев"),
    ("Гагарина", "улица", "Гагарин"), ("Фурманова", "улица", "Фурманов"), ("Желтоксан", "улица", "Желтоқсан"),
    ("Богенбай батыра", "улица", "Бөгенбай батыр"), ("Кулманова", "улица", "Құлманов"), ("Туран", "проспект", "Тұран"),
    ("Кабанбай батыра", "проспект", "Қабанбай батыр"), ("Рыбинский", "переулок", "Рыбинский"),
    ("8 марта", "улица", "8 наурыз"), ("Жуалы", "улица", "Жуалы"), ("Самал-2", "микрорайон", "Самал-2"),
    ("Аксай 3а", "микрорайон", "Ақсай 3а"), ("Аксай 4", "микрорайон", "Ақсай 4"), ("Нурсая", "микрорайон", "Нұрсая"),
    ("15", "микрорайон", "15"), ("12", "микрорайон", "12"), ("66", "квартал", "66"), ("7", "квартал", "7"),
]
# (канон, область, латиница, казахский вариант)
CITIES = [
    ("Алматы", "Алматы", "Almaty", "Алматы қаласы"), ("Астана", "Астана", "Astana", "Астана қаласы"),
    ("Шымкент", "Шымкент", "Shymkent", "Шымкент қаласы"), ("Актобе", "Актюбинская область", "Aktobe", "Ақтөбе қаласы"),
    ("Караганда", "Карагандинская область", "Karaganda", "Қарағанды қаласы"),
    ("Тараз", "Жамбылская область", "Taraz", "Тараз қаласы"), ("Павлодар", "Павлодарская область", "Pavlodar", "Павлодар қаласы"),
    ("Костанай", "Костанайская область", "Kostanay", "Қостанай қаласы"), ("Атырау", "Атырауская область", "Atyrau", "Атырау қаласы"),
    ("Семей", "Абайская область", "Semey", "Семей қаласы"),
]
DISTRICTS = {"Алматы": ["Ауэзовский", "Бостандыкский", "Медеуский", "Алмалинский", "Турксибский", "Наурызбайский"],
             "Астана": ["Есиль", "Сарыарка", "Байконур"], "Шымкент": ["Каратауский", "Енбекшинский"]}
LANDMARKS = ["магазин береке", "Qazpost Солнечный", "рядом с аптекой", "сабина дукен", "кафе Арман", "Magazin Sabina",
             "ТЦ Мега", "напротив школы", "аптека Сункар", "возле мечети", "Kaspi банк", "магазин Дана",
             "маг 24 часа", "magazin vozle ostanovki", "акимат"]

TYPE_FORMS = {
    "улица": ["ул.", "ул", "улица", "Ул .", "ул."], "проспект": ["пр.", "пр", "проспект", "пр-т", "просп."],
    "переулок": ["пер.", "переулок", "пер"], "микрорайон": ["мкр", "мкр.", "микрорайон", "мкрн", "м-н", "Мкр"],
    "квартал": ["квартал", "кв-л"],
}
KK_SUFFIX = {"улица": ["көшесі", "кошеси"], "проспект": ["даңғылы", "дангылы"]}
CYR2LAT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                   ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                    "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def lat(s):
    return "".join(CYR2LAT.get(ch.lower(), ch) for ch in s).capitalize() if s else s


# ---------------------------------------------------------------- генерация одного адреса
def gen(rng):
    g = {}
    parts = []                                   # куски в порядке написания: (вид, текст)

    # --- город / область / район
    city = rng.choice(CITIES)
    has_city = rng.random() < 0.72
    has_prov = (not has_city and rng.random() < 0.08) or (has_city and city[1] != city[0] and rng.random() < 0.2)
    district = None
    if city[0] in DISTRICTS and rng.random() < 0.18:
        district = rng.choice(DISTRICTS[city[0]])
    city_at_end = has_city and rng.random() < 0.10
    if has_city:
        r = rng.random()
        if r < 0.22:
            ctext = city[0]
        elif r < 0.42:
            ctext = "г." + city[0] if rng.random() < .5 else "г. " + city[0]
        elif r < 0.55:
            ctext = "город " + city[0]
        elif r < 0.70:
            ctext = lat(city[0]) if False else city[2].lower() if rng.random() < .6 else city[2]
        elif r < 0.84:
            ctext = f"город {city[0]}/{city[3]}"
        elif r < 0.88:
            ctext = city[0].lower()
        elif r < 0.92:
            ctext = rng.choice(["***город " + city[0], city[2] + " qala", "г." + city[0] + " қаласы"])
        else:
            ctext = f"г.{city[0]}/{city[3]}"
        g["city"] = city[0]
        g["province"] = city[1]
        if not city_at_end:
            if has_prov and city[1] != city[0]:
                parts.append(("prov", city[1]))
            parts.append(("city", ctext))
    elif has_prov and city[1] != city[0]:
        parts.append(("prov", city[1]))
        g["province"] = city[1]
    if district:
        r = rng.random()
        dtext = district + rng.choice([" район", " р-н", " районы"][:2]) if r < 0.6 else "район " + district if r < 0.85 else district
        parts.append(("district", dtext))
        g["district"] = district

    # --- улица
    base, stype, kk = rng.choice(STREETS)
    r = rng.random()
    surface = base if r < 0.72 else kk if r < 0.84 else lat(base)
    is_kk = surface == kk and kk != base
    is_lat = r >= 0.84
    num_name = base[0].isdigit() and " " not in base and stype in ("микрорайон", "квартал")
    # тип
    pos = rng.random()
    if stype in ("микрорайон", "квартал"):
        form = rng.choice(TYPE_FORMS[stype])
        if num_name and pos < 0.4:
            street_txt = f"{surface} {form}" if rng.random() < .6 else f"{surface}{form}"      # «15 мкр», «66квартал»
        else:
            street_txt = f"{form} {surface}"
    elif is_kk and stype in KK_SUFFIX and pos < 0.8:
        street_txt = f"{surface} {rng.choice(KK_SUFFIX[stype])}"
    elif is_lat:
        street_txt = f"{surface}" if pos < 0.5 else f"ul. {surface}" if stype == "улица" else surface
    elif pos < 0.62:
        street_txt = f"{rng.choice(TYPE_FORMS[stype])} {surface}"
        if rng.random() < .1:
            street_txt = street_txt.replace(". ", ".")
    elif pos < 0.82 and stype in ("улица", "проспект"):
        adj_form = {"улица": "улица", "проспект": "проспект"}[stype]
        street_txt = f"{surface} {adj_form}"
    else:
        street_txt = surface
    g["street_name"] = surface

    # --- дом
    house = str(rng.randint(1, 260))
    r = rng.random()
    if r < 0.12:
        house += rng.choice("абвАБ")
    elif r < 0.24:
        house += "/" + str(rng.randint(1, 9))
    elif r < 0.28:
        house += "/" + rng.choice("ABCD")                # «112/A»
    g["house"] = house.lower() if house[-1:] in "абвАБ" else house
    g["house"] = house
    hm = rng.random()
    if hm < 0.45:
        house_txt, glue_to_street = house, False
    elif hm < 0.62:
        house_txt, glue_to_street = f"д.{house}" if rng.random() < .5 else f"д. {house}", False
    elif hm < 0.75:
        house_txt, glue_to_street = f"дом {house}", False
    elif hm < 0.85:
        house_txt, glue_to_street = f"{house} дом", False
    elif hm < 0.88:
        house_txt, glue_to_street = f"{house}дом", False
    elif hm < 0.92:
        house_txt, glue_to_street = rng.choice([f"{house}dom", f"{house} dom", f"dom {house}"] + (
            [f"{house}d", f"{house}h", f"{house}hse", f"d{house}", f"hse {house}"] if house.isdigit() else [])), False
    else:
        house_txt, glue_to_street = f"д{house}", False
    if stype in ("микрорайон", "квартал") and house_txt == house and rng.random() < .3:
        house_txt = f"дом {house}"           # у микрорайона номер дома без маркера путается с номером мкр

    items = [("street", street_txt), ("house", house_txt)]

    # --- квартира / подъезд / этаж
    if rng.random() < 0.75:
        n = str(rng.randint(1, 400))
        g["apartment"] = n
        items.append(("apt", rng.choice([f"кв {n}", f"кв.{n}", f"кв. {n}", f"кв{n}", f"kv{n}", f"kv {n}", f"flat {n}",
                                         f"квартира {n}", f"{n} кв", f"{n}кв", f"кв №{n}", f"{n} квартира", f"кв#{n}", f"kv#{n}"])))
    if rng.random() < 0.30:
        n = str(rng.randint(1, 9))
        g["entrance"] = n
        items.append(("ent", rng.choice([f"подъезд {n}", f"подьезд {n}", f"п.{n}", f"п {n}", f"{n} подъезд", f"{n}подьезд",
                                         f"под. {n}", f"{n} под", f"подъезд{n}"])))
    if rng.random() < 0.22:
        n = str(rng.randint(1, 25))
        g["floor"] = n
        items.append(("floor", rng.choice([f"этаж {n}", f"{n} этаж", f"{n}эт", f"эт.{n}", f"{n}-й этаж", f"{n} эт", f"этаж{n}"])))
    # порядок хвоста: чаще кв, подъезд, этаж, иногда перемешан
    rng.shuffle(items[2:]) if False else None
    tail = items[2:]
    if rng.random() < 0.25:
        rng.shuffle(tail)
    items = items[:2] + tail
    parts.extend(items)

    # --- ориентир
    if rng.random() < 0.28:
        lm = rng.choice(LANDMARKS)
        g["landmark"] = lm
        where = rng.random()
        if where < 0.7 or not parts:
            parts.append(("landmark", lm))
        elif where < 0.9:
            parts.insert(max(1, len([p for p in parts if p[0] in ("prov", "city", "district")])) + 1, ("landmark", lm))
        else:
            parts.insert(0, ("landmark", lm))
    if city_at_end:
        parts.append(("city", city[0] if rng.random() < .6 else city[2]))

    # --- склейка
    seps = [", ", ", ", ", ", " ", ",", " , "]
    out = parts[0][1]
    for (k_prev, _), (k, t) in zip(parts, parts[1:]):
        sep = rng.choice(seps)
        if k_prev == "street" and k == "house" and rng.random() < 0.55:
            sep = " "
        if k in ("apt", "ent", "floor") and rng.random() < 0.7:
            sep = rng.choice([" ", ", "])
        out += sep + t
    # --- шум на уровне символов
    if rng.random() < 0.10:
        out = out.replace(" ", " ", 1)
    if rng.random() < 0.05:
        i = rng.randrange(len(out))
        out = out[:i] + "​" + out[i:]
    if rng.random() < 0.10:
        out = out.replace(" ", "  ", 1)
    if rng.random() < 0.06:
        out = " " + out + " "
    if rng.random() < 0.02:
        out = out.replace(" ", "\t", 1)
    r = rng.random()
    out = out.lower() if r < 0.22 else out.upper() if r < 0.27 else out
    return out, g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=3000)
    ap.add_argument("-s", "--seed", type=int, default=1)
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    data = [gen(rng) for _ in range(args.n)]
    if args.save:
        json.dump([{"id": f"n{i:05d}", "raw_address": r, "gold": g} for i, (r, g) in enumerate(data)],
                  open(args.save, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    parser = AddressParser()
    preds = parser.parse_batch([{"id": f"n{i}", "raw_address": r} for i, (r, _) in enumerate(data)])
    golds = [g for _, g in data]
    total, stats, groups = score(preds, golds)
    print(f"Грязных адресов: {args.n}  seed={args.seed}   ИТОГО по метрике жюри: {total * 100:.1f}%")
    for name, w, fields in GROUPS:
        print(f"  {name:<4} ({int(w * 100)}%): {groups[name] * 100:5.1f}%   " +
              ", ".join(f"{f} {stats[f][0]}/{stats[f][1]}" for f in fields))

    fails = []
    for (raw, g), p in zip(data, preds):
        bad = [f for f in g if f != "landmark" and cmp_norm(p.get(f)) != cmp_norm(g[f])]
        if bad:
            fails.append((raw, g, p, bad))
    by_field = Counter(f for *_, bad in fails for f in bad)
    print(f"\nЗаписей с ошибкой: {len(fails)}/{args.n}; по полям: {dict(by_field.most_common())}")
    rng2 = random.Random(0)
    rng2.shuffle(fails)
    for raw, g, p, bad in fails[:args.show]:
        print(f"\n  {raw!r}")
        for f in bad:
            print(f"      {f}: получено {p.get(f)!r}, ожидалось {g[f]!r}")


if __name__ == "__main__":
    main()
