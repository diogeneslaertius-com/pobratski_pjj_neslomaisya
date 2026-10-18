"""Командная строка: python parse_file.py input.json output.json

input.json  - [{"id": "...", "raw_address": "..."}, ...]
output.json - список объектов в формате из ТЗ (на каждую входную запись ровно один ответ).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from addrparse import AddressParser  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Разбор адресов Казахстана")
    ap.add_argument("input", help="входной JSON")
    ap.add_argument("output", help="выходной JSON")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        recs = json.load(f)
    if not isinstance(recs, list):
        sys.exit("input должен быть JSON-массивом записей")

    t0 = time.time()
    out = AddressParser().parse_batch(recs)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Готово: {len(out)} записей за {time.time() - t0:.2f} с -> {args.output}")


if __name__ == "__main__":
    main()
