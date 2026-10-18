"""Замер скорости.  python tools/bench.py [-n 1000] [--url http://localhost:8000]

Без --url: загрузка справочника + разбор N грязных адресов в процессе.
С --url: то же через HTTP (POST /parse_batch) - это и есть «1000 записей за 5 минут» из ТЗ.
Старт контейнера (лимит 3 мин) меряйте так:  time docker compose up -d --wait
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=1000)
    ap.add_argument("--url")
    a = ap.parse_args()

    import gen_noisy
    rng = random.Random(7)
    recs = [{"id": str(i), "raw_address": gen_noisy.gen(rng)[0]} for i in range(a.n)]

    if a.url:
        req = urllib.request.Request(a.url.rstrip("/") + "/parse_batch", data=json.dumps(recs).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.time()
        out = json.load(urllib.request.urlopen(req, timeout=600))
        print(f"HTTP: {len(out)} записей за {time.time() - t:.2f} с")
    else:
        from addrparse import AddressParser
        t = time.time()
        p = AddressParser()
        print(f"загрузка справочника: {time.time() - t:.2f} с  {p.ref.stats()}")
        t = time.time()
        out = p.parse_batch(recs)
        print(f"разбор: {len(out)} записей за {time.time() - t:.2f} с")
    assert len(out) == len(recs)


if __name__ == "__main__":
    main()
