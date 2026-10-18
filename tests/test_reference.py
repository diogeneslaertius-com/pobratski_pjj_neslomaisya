"""Проверки справочника и парсера на синтетической базе. Запуск: python tests/test_reference.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from addrparse import AddressParser, refdb                     # noqa: E402
from addrparse.refdata import RANK_CITY, RANK_TOWN, RANK_VILLAGE, Reference  # noqa: E402
from addrparse.textutil import norm_key                        # noqa: E402


def build_sqlite(path):
    cities = [
        (1, "Алматы", "Алматы", RANK_CITY, 1, 76.9, 43.2),
        (2, "Актобе", "Актюбинская область", RANK_CITY, 0, 57.2, 50.3),
        (3, "Новоселовка", "Акмолинская область", RANK_VILLAGE, 0, 70, 51),
        (4, "Новоселовка", "Костанайская область", RANK_VILLAGE, 0, 63, 52),
        (5, "Туран", "Туркестанская область", RANK_VILLAGE, 0, 68, 43),       # село Туран...
        (6, "Курильск", "Сахалинская область", RANK_VILLAGE, 0, 70, 45),      # ...и «Курильск», похожий на «Курильская»
        (7, "Абай", "Карагандинская область", RANK_TOWN, 0, 72, 49),          # город Абай и улица Абая
        (8, "Ауэзов", "Абайская область", RANK_TOWN, 0, 80, 50),              # город Ауэзов и «Ауэзовский район»
        (9, "Каскелен", "Алматинская область", RANK_TOWN, 0, 76, 43),
        (10, "Темиртау", "Карагандинская область", RANK_CITY, 0, 72, 50),
        (11, "Осакаровка", "Карагандинская область", RANK_VILLAGE, 0, 72, 50),   # посёлок, улицы с таким именем нет
    ]
    ck = [(norm_key(n), cid) for cid, n, *_ in cities] + [(norm_key("Ақтөбе"), 2)]
    districts = [(1, "Ауэзовский", 1, "Алматы"), (2, "Турксибский", 1, "Алматы"), (3, "Илийский", None, "Алматинская область")]
    dk = [(norm_key("Ауэзовский"), 1), (norm_key("Әуезов"), 1), (norm_key("Турксибский"), 2), (norm_key("Илийский"), 3)]
    items = [
        (1, norm_key("Аксай 3а"), "Аксай 3а", "place", "микрорайон", 1, 40),
        (1, norm_key("Абая"), "Абая", "street", "проспект", 1, 70),
        (1, norm_key("Абая"), "Абая", "street", "проспект", 2, 30),          # улица идёт через два района: 70/30
        (1, norm_key("Рыбинский"), "Рыбинский", "street", "переулок", 2, 12),
        (1, norm_key("Сатпаева"), "Сатпаева", "street", "улица", 1, 10),      # улица поровну в двух районах
        (1, norm_key("Сатпаева"), "Сатпаева", "street", "улица", 2, 10),
        (2, norm_key("Курильская"), "Курильская", "street", "улица", None, 5),
        (2, norm_key("Абая"), "Абая", "street", "проспект", None, 20),
        (2, norm_key("Абайская"), "Абайская", "street", "улица", None, 8),
        (1, norm_key("Абайская"), "Абайская", "street", "улица", 1, 60),
        (4, norm_key("Абайская"), "Абайская", "street", "улица", None, 3),
    ]
    refdb.write(path, cities, ck, districts, dk, items, {"built": "test"})


def main():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "ref.sqlite")
        build_sqlite(path)
        ref = Reference.from_sqlite(path)
        print("stats:", ref.stats())
        assert ref.stats()["cities"] == 11

        # --- одноимённые города: область из строки выбирает нужный
        c = ref.find_city(norm_key("Новоселовка"), "Костанайская область")
        assert c.cid == 4, c
        assert ref.city_is_ambiguous(norm_key("Новоселовка"), None)
        assert not ref.city_is_ambiguous(norm_key("Новоселовка"), "Костанайская область")
        # --- вероятность района по улице
        c1 = ref.city_by_id[1]
        assert ref.district_candidates(c1, norm_key("Абая")) == [("Ауэзовский", 0.7), ("Турксибский", 0.3)] or \
            [(n, round(s, 2)) for n, s in ref.district_candidates(c1, norm_key("Абая"))] == [("Ауэзовский", 0.7), ("Турксибский", 0.3)]
        assert ref.infer_district(c1, norm_key("Абая"), 0.8) is None          # 70% < порога: не гадаем
        assert ref.infer_district(c1, norm_key("Рыбинский")) == "Турксибский"
        assert ref.infer_district(c1, norm_key("Аксай 3а")) == "Ауэзовский"

        p = AddressParser(ref)

        def run(raw):
            return p.parse_record({"id": "t", "raw_address": raw})

        # --- пример из ТЗ: район подтянут из справочника
        r = run("город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70")
        assert (r["province"], r["city"], r["district"], r["street_type"], r["street_name"], r["house"], r["apartment"]) == \
            ("Алматы", "Алматы", "Ауэзовский", "микрорайон", "Аксай 3а", "59", "70"), r

        # --- село «Туран» не должно съедать улицу «Туран 55/6»
        r = run("Туран 55/6 магазин береке 4подьезд 9этаж")
        assert r["street_name"] == "Туран" and r["house"] == "55/6" and r["city"] is None, r

        # --- улица «Курильская» не становится селом «Курильск»
        r = run("Курильская, 9, кв 1")
        assert r["street_name"] == "Курильская" and r["house"] == "9" and r["apartment"] == "1" and r["city"] is None, r
        r = run("Актобе, Ул .Курильская 9 кв 1")
        assert r["city"] == "Актобе" and r["street_name"] == "Курильская" and r["street_type"] == "улица", r
        assert r["province"] == "Актюбинская область", r

        # --- область в строке выбирает одноимённое село
        r = run("Костанайская область, село Новоселовка, ул. Мира 5")
        assert r["city"] == "Новоселовка" and r["province"] == "Костанайская область", r

        # --- район вместо города
        r = run("Турксибский район, Рыбинский переулок, дом 4")
        assert r["district"] == "Турксибский" and r["street_name"] == "Рыбинский" and r["house"] == "4", r
        assert r["city"] == "Алматы" and r["province"] == "Алматы", r

        # --- «Абайская улица 81» без города: поле пустое, подсказка - крупнейший город по числу адресов
        r = p.parse("Абайская улица 81")
        assert r.values["city"] is None and r.values["street_name"] == "Абайская" and r.values["house"] == "81", r
        assert r.values["province"] is None, r.values                       # «Абайская» - не область
        sug = r.suggestions["city"]
        assert sug[0]["city"] == "Алматы" and sug[0]["share"] > 0.5, sug
        assert "city_suggested" in r.flags

        # --- «abay 45dom»: слово + дом - это улица, даже если есть одноимённый город
        r = run("abay 45dom, vnutri torgovi dom")
        assert r["city"] is None and r["street_name"] == "abay" and r["house"] == "45", r
        assert r["landmark"] == "vnutri torgovi dom", r
        # --- «auyezov audany»: слово рядом с «audany» - район, а не город Ауэзов
        r = run("auyezov audany, rybinsky per, 4")
        assert r["city"] != "Ауэзов" and r["district"] == "auyezov" and r["street_name"] == "rybinsky" \
            and r["street_type"] == "переулок" and r["house"] == "4", r
        # --- латиница: kv.15, d-ly, ofis + qabat
        r = run("qasqelen, abai d-ly 2, kv.15, magazin zhan")
        assert r["city"] == "Каскелен" and r["street_name"] == "abai" and r["house"] == "2" \
            and r["apartment"] == "15" and r["landmark"] == "magazin zhan", r
        r = run("aktobe tauke han 18 ofis 301 3 qabat")
        assert r["street_name"] == "tauke han" and r["house"] == "18" and r["apartment"] == "301" \
            and r["floor"] == "3" and r["landmark"] is None, r
        r = run("темиртау, металлургов пр-т, 1/2 үй, п3, кв8")
        assert r["city"] == "Темиртау" and r["house"] == "1/2" and r["entrance"] == "3" \
            and r["apartment"] == "8" and r["landmark"] is None, r

        # --- «осакаровка дом 5»: посёлок, а не улица (в отличие от «abay 45dom»: улица Абая есть в справочнике)
        r = run("осакаровка дом 5")
        assert r["city"] == "Осакаровка" and r["province"] == "Карагандинская область" and r["house"] == "5" \
            and r["street_name"] is None, r
        # --- «Дом Культуры» - ориентир, а не маркер дома и не часть улицы
        r = run("рядом Дом Культуры бухар жырау 12")
        assert r["street_name"] == "бухар жырау" and r["house"] == "12" and r["landmark"] == "рядом Дом Культуры", r
        r = run("Дом Культуры, Абая 5")
        assert r["street_name"] == "Абая" and r["house"] == "5" and r["landmark"] == "Дом Культуры", r

        # --- таблица подсказок для UI
        from addrparse import service
        tab = service.suggestions_table(p, [{"id": 1, "raw_address": "Абайская улица 81"},
                                            {"id": 2, "raw_address": "Алматы, Сатпаева 5"},
                                            {"id": 3, "raw_address": "Алматы, Абая 10"}])
        assert [t["id"] for t in tab] == ["1", "2"], tab
        assert tab[0]["Возможный город"].startswith("Алматы"), tab
        assert tab[1]["Возможный район"].startswith("Ауэзовский"), tab

        # --- улица Сатпаева поровну в двух районах: район не пишем, но подсказка есть
        r = p.parse("Алматы, Сатпаева 5")
        assert r.values["district"] is None, r.values
        assert [d["district"] for d in r.suggestions["district"]] == ["Ауэзовский", "Турксибский"], r.suggestions
        assert "district_suggested" in r.flags

        # --- порядковые числительные в ключах
        assert norm_key("5-й микрорайон") == norm_key("5 микрорайон")
        assert norm_key("Самал-2") == "самал 2"

        # --- null-ответ на мусор
        r = run("???")
        assert r["confidence"] == 0.0

    print("OK: все проверки справочника прошли")


if __name__ == "__main__":
    main()
