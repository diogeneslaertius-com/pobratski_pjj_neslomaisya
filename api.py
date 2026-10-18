"""REST API.  Запуск:  uvicorn api:app --host 0.0.0.0 --port 8000

POST /parse_batch    тело: [{"id","raw_address"}, ...] (или {"records": [...]})  ->  список ответов в формате ТЗ,
                     на каждую входную запись ровно одна выходная, порядок сохранён
POST /parse          одна запись {"id","raw_address"} -> один ответ; список принимает так же, как /parse_batch
POST /parse/explain  то же + служебные поля _sources / _flags / _suggestions (для UI и защиты)
GET  /health         проверка готовности (справочник загружен)
"""
from typing import Any, Dict, List

from fastapi import Body, FastAPI

from addrparse import AddressParser, service

app = FastAPI(title="KZ address parser", version="1.0")
parser = AddressParser()          # справочник грузится один раз при старте процесса


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "reference": parser.ref.stats()}


@app.post("/parse_batch")
def parse_batch(payload: Any = Body(...)) -> List[Dict[str, Any]]:
    return service.parse_batch(parser, payload)


@app.post("/parse")
def parse(payload: Any = Body(...)) -> Any:
    return service.parse_any(parser, payload)


@app.post("/parse/explain")
def parse_explain(payload: Any = Body(...)) -> List[Dict[str, Any]]:
    return service.explain_batch(parser, payload)
