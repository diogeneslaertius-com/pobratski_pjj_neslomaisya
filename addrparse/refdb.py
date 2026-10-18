"""Формат reference.sqlite: запись (для extract_osm.py) и загрузка в память (для парсера)."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional, Tuple

from .refdata import Reference

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE cities (cid INTEGER PRIMARY KEY, name TEXT NOT NULL, province TEXT NOT NULL,
                     rank INTEGER NOT NULL, republic INTEGER NOT NULL, lon REAL, lat REAL);
CREATE TABLE city_keys (key TEXT NOT NULL, cid INTEGER NOT NULL);
CREATE TABLE districts (did INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_cid INTEGER, province TEXT);
CREATE TABLE district_keys (key TEXT NOT NULL, did INTEGER NOT NULL);
CREATE TABLE items (cid INTEGER NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                    type TEXT, did INTEGER, cnt INTEGER NOT NULL);
CREATE INDEX items_cid ON items (cid);
"""


def create(path: str) -> sqlite3.Connection:
    import os
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def load(path: str) -> Reference:
    from . import seed
    ref = Reference()
    seed.fill_provinces(ref)         # 17 областей + алиасы: ручной словарь, не из OSM
    con = sqlite3.connect(path)
    try:
        keys_by_cid: dict = {}
        for key, cid in con.execute("SELECT key, cid FROM city_keys"):
            keys_by_cid.setdefault(cid, []).append(key)
        for cid, name, province, rank, republic in con.execute(
                "SELECT cid, name, province, rank, republic FROM cities ORDER BY cid"):
            ref.add_city(name, province, keys_by_cid.get(cid, []), bool(republic), rank, cid)
        dkeys: dict = {}
        for key, did in con.execute("SELECT key, did FROM district_keys"):
            dkeys.setdefault(did, []).append(key)
        for did, name, parent, province in con.execute("SELECT did, name, parent_cid, province FROM districts"):
            ref.add_district(name, ref.city_by_id.get(parent) if parent else None, province,
                             dkeys.get(did, []), did)
        for cid, key, name, kind, type_, did, cnt in con.execute(
                "SELECT cid, key, name, kind, type, did, cnt FROM items"):
            ref.add_item_raw(cid, key, name, kind, type_, did, cnt)
    finally:
        con.close()
    return ref


def write(path: str, cities: Iterable[Tuple], city_keys: Iterable[Tuple], districts: Iterable[Tuple],
          district_keys: Iterable[Tuple], items: Iterable[Tuple], meta: Optional[dict] = None) -> None:
    """cities: (cid,name,province,rank,republic,lon,lat); city_keys: (key,cid);
    districts: (did,name,parent_cid,province); district_keys: (key,did);
    items: (cid,key,name,kind,type,did,cnt)."""
    con = create(path)
    con.executemany("INSERT INTO cities VALUES (?,?,?,?,?,?,?)", cities)
    con.executemany("INSERT INTO city_keys VALUES (?,?)", city_keys)
    con.executemany("INSERT INTO districts VALUES (?,?,?,?)", districts)
    con.executemany("INSERT INTO district_keys VALUES (?,?)", district_keys)
    con.executemany("INSERT INTO items VALUES (?,?,?,?,?,?,?)", items)
    for k, v in (meta or {}).items():
        con.execute("INSERT INTO meta VALUES (?,?)", (k, str(v)))
    con.execute("CREATE INDEX city_keys_key ON city_keys (key)")
    con.commit()
    con.close()
