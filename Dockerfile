# Один образ для API и UI. Справочник (data/reference.sqlite) лежит ВНУТРИ образа:
# в финальном прогоне сеть выключена, ничего не скачивается.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ADDR_REFERENCE=/app/data/reference.sqlite

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Сборка падает, если справочника нет: иначе в образ тихо попадёт игрушечный seed.
# Для отладки без справочника:  docker compose build --build-arg ALLOW_SEED=1
ARG ALLOW_SEED=0
RUN if [ ! -f "$ADDR_REFERENCE" ] && [ "$ALLOW_SEED" != "1" ]; then \
      echo "ОШИБКА: нет data/reference.sqlite. Соберите: python tools/extract_osm.py <pbf> data/reference.sqlite"; \
      echo "(или --build-arg ALLOW_SEED=1 для отладки на мини-справочнике)"; exit 1; fi

# Проверка при сборке: справочник открывается, парсер отвечает.
RUN python -c "import time; t=time.time(); from addrparse import AddressParser; p=AddressParser(); \
print('reference:', p.ref.stats(), 'load %.1fs' % (time.time()-t)); \
print(p.parse_record({'id': 'build', 'raw_address': 'Алматы, Абая 10, кв 5'}))"

EXPOSE 8000 8501

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
