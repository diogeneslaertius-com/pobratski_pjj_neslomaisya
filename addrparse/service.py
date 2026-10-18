"""Логика между транспортом (FastAPI / Streamlit) и парсером. Здесь нет fastapi и streamlit,
поэтому всё проверяется обычными тестами (tests/test_service.py)."""
from __future__ import annotations

from typing import Any, Dict, List

from .parser import FIELDS, AddressParser


def normalize_records(payload: Any) -> List[Dict[str, Any]]:
    """Принимает [{"id","raw_address"}, ...] или {"records": [...]}. На каждый элемент входа
    возвращает ровно один элемент (порядок сохраняется), даже если вход кривой."""
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("items", []))
    if not isinstance(payload, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            raw = item.get("raw_address")
            out.append({"id": item.get("id"), "raw_address": "" if raw is None else str(raw)})
        else:                                    # строка вместо объекта
            out.append({"id": None, "raw_address": "" if item is None else str(item)})
    return out


def parse_batch(parser: AddressParser, payload: Any) -> List[Dict[str, Any]]:
    return parser.parse_batch(normalize_records(payload))


def parse_any(parser: AddressParser, payload: Any) -> Any:
    """Один объект {"id","raw_address"} -> один ответ; список или {"records": [...]} -> список ответов."""
    if isinstance(payload, dict) and "raw_address" in payload and "records" not in payload:
        return parser.parse_record(normalize_records([payload])[0])
    return parse_batch(parser, payload)


def explain_batch(parser: AddressParser, payload: Any) -> List[Dict[str, Any]]:
    """Ответ в формате ТЗ + служебные поля с «_» (источники полей, флаги, подсказки справочника)."""
    out = []
    for rec in normalize_records(payload):
        base = parser.parse_record(rec)
        try:
            res = parser.parse(rec["raw_address"])
            base["_sources"] = {f: res.sources.get(f) for f in FIELDS if res.values.get(f) is not None}
            base["_flags"] = res.flags
            base["_suggestions"] = res.suggestions
        except Exception:
            base["_sources"], base["_flags"], base["_suggestions"] = {}, ["error"], {}
        out.append(base)
    return out


def suggestions_table(parser: AddressParser, rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Подсказки для UI: только строки, где они есть. Это ДОПОЛНИТЕЛЬНЫЙ вывод, в output.json не попадает."""
    table = []
    for row in rows:
        try:
            res = parser.parse(row.get("raw_address") or "")
        except Exception:
            continue
        sug = res.suggestions or {}
        if not sug:
            continue
        city = ", ".join(f"{s['city']} ({round(s['share'] * 100)}%)" for s in sug.get("city", []))
        dist = ", ".join(f"{s['district']} ({round(s['share'] * 100)}%)" for s in sug.get("district", []))
        table.append({
            "id": str(row.get("id")),
            "raw_address": row.get("raw_address") or "",
            "Возможный город": city,
            "Возможный район": dist,
        })
    return table
