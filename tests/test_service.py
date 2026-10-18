"""Проверки слоя между транспортом и парсером. Запуск: python tests/test_service.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from addrparse import AddressParser, service     # noqa: E402


def main():
    p = AddressParser()
    # вход: список / словарь с records / мусор - на каждый элемент ровно один ответ, порядок сохранён
    recs = [{"id": 1, "raw_address": "Алматы, Абая 10, кв 5"}, {"id": "x", "raw_address": None}, "Ул .Курильская 9 кв 1",
            {"raw_address": 123}, 5, None]
    out = service.parse_batch(p, recs)
    assert len(out) == len(recs), out
    assert out[0]["id"] == 1 and out[0]["house"] == "10" and out[0]["apartment"] == "5", out[0]
    assert out[1]["id"] == "x" and out[1]["confidence"] == 0.0 and out[1]["raw_address"] == "", out[1]
    assert out[2]["raw_address"] == "Ул .Курильская 9 кв 1" and out[2]["street_name"] == "Курильская", out[2]
    assert service.parse_batch(p, {"records": recs[:1]})[0]["house"] == "10"
    assert service.parse_batch(p, "не список") == [] and service.parse_batch(p, []) == []
    # raw_address возвращается без изменений (в том числе пробелы и регистр)
    raw = "  АЛМАТЫ ,  Абая   10  "
    assert service.parse_batch(p, [{"id": "a", "raw_address": raw}])[0]["raw_address"] == raw
    # поля ответа строго по ТЗ
    keys = ["id", "raw_address", "province", "city", "district", "living_area", "street_type", "street_name",
            "house", "apartment", "entrance", "floor", "landmark", "confidence"]
    assert list(out[0].keys()) == keys, list(out[0].keys())
    # одна запись -> один объект; список -> список
    one = service.parse_any(p, {"id": "q", "raw_address": "Алматы, Абая 10"})
    assert isinstance(one, dict) and one["id"] == "q" and one["house"] == "10", one
    assert isinstance(service.parse_any(p, [{"id": "q", "raw_address": "Алматы, Абая 10"}]), list)
    # explain добавляет служебные поля, но не ломает основные
    ex = service.explain_batch(p, recs[:1])[0]
    assert ex["house"] == "10" and "_sources" in ex and "_flags" in ex and "_suggestions" in ex
    # подсказки для UI: в основной ответ они не попадают
    tab = service.suggestions_table(p, [{"id": 1, "raw_address": "Абайская улица 81"}])
    assert all("_suggestions" not in r for r in out)
    assert isinstance(tab, list)
    print("OK: слой service проверен")


if __name__ == "__main__":
    main()
