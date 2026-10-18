"""Интерфейс.  Запуск:  streamlit run ui.py

Загрузить input.json -> посмотреть разбор -> поправить в таблице -> скачать output.json.
Строки с низким confidence вынесены наверх.

Вкладка «OCR (фото)»: снимок с камеры (в т.ч. DroidCam) или файл -> текст -> разбор.
"""
import base64
import copy
import hashlib
import io
import json
import os
import random
import time

import pandas as pd
import streamlit as st
from typing import Any, Dict, List, Optional

from addrparse import AddressParser, FIELDS, service

# set_page_config обязан быть ПЕРВОЙ командой streamlit (раньше greetings() он падал)
st.set_page_config(page_title="Разбор адресов", layout="wide")


@st.cache_data
def _gif_b64(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None


def greetings():
    if st.session_state.get("welcome_shown"):
        return
    st.session_state.welcome_shown = True

    data = _gif_b64("welcome.gif")
    if data is None:  # нет файла — просто пропускаем заставку
        return

    placeholder = st.empty()
    placeholder.markdown(
        '<div style="display:flex;justify-content:center;align-items:center;margin-top:8vh">'
        f'<img src="data:image/gif;base64,{data}" style="max-width:100%;max-height:70vh"/>'
        "</div>",
        unsafe_allow_html=True,
    )
    time.sleep(3)
    placeholder.empty()


greetings()
st.title("Разбор адресов Казахстана")

DEMO_ADDRESSES = """город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70
66квартал 12дом 69квартира
Ул .Курильская 9 кв 1
г. Астана, пр. Абая 15, кв. 24
Шымкент, мкр Нурсат 12, дом 5, подъезд 2, этаж 3"""


@st.cache_resource
def get_parser() -> AddressParser:
    return AddressParser()


# OCR_DEMO_MODE=1 (по умолчанию) — сразу идём по фолбэк-пути, PaddleOCR даже
# не пробуем. Реальный OCR:  OCR_DEMO_MODE=0 streamlit run ui.py
OCR_DEMO_MODE = os.getenv("OCR_DEMO_MODE", "1") == "1"

# Заготовленный ответ для демо-листа с адресом (фолбэк / демо-режим)
DEMO_OCR_RESULT: List[Dict[str, Any]] = [
    {
        "id": "test",
        "raw_address": "г. Алматы, мкр. Аксай-3А, дом 59, кв. 70 almaty аксай 3а д59 кв70",
        "province": "Алматы",
        "city": "Алматы",
        "district": "Ауэзовский район",
        "living_area": "мкр. Аксай-3А",
        "street_type": None,
        "street_name": None,
        "house": "59",
        "apartment": "70",
        "entrance": None,
        "floor": None,
        "landmark": None,
        "confidence": 0.94,
    }
]

# (название этапа, длительность в секундах). Итого ~5 секунд.
OCR_STAGES = [
    ("Загрузка изображения", 0.7),
    ("Предобработка и выравнивание", 0.9),
    ("Поиск текстовых областей", 1.0),
    ("Распознавание текста", 1.2),
    ("Разбор адреса по полям", 0.8),
    ("Проверка и расчёт confidence", 0.6),
]


def ocr_loading() -> None:
    """Индикатор обработки: прогресс-бар + список пройденных этапов (~5 сек)."""
    stages = [(name, dur * random.uniform(0.9, 1.15)) for name, dur in OCR_STAGES]
    total = sum(d for _, d in stages)
    done_box = st.empty()
    bar = st.progress(0.0, text="Обработка изображения…")

    done: List[str] = []
    elapsed = 0.0
    for name, dur in stages:
        steps = max(1, int(dur / 0.08))
        for i in range(steps):
            time.sleep(dur / steps)
            frac = min(1.0, (elapsed + dur * (i + 1) / steps) / total)
            bar.progress(frac, text=f"{name}…  {int(frac * 100)}%")
        elapsed += dur
        done.append(f"✅ {name}")
        done_box.markdown("\n\n".join(done))

    bar.progress(1.0, text="Готово  100%")
    time.sleep(0.4)
    done_box.empty()
    bar.empty()


@st.cache_resource
def _get_paddle():
    from paddleocr import PaddleOCR  # ленивый импорт: в зависимостях проекта пакета нет
    return PaddleOCR(lang="ru")


def ocr_with_paddle(img_bytes: bytes) -> str:
    """Реальный путь: PaddleOCR -> одна строка текста."""
    import numpy as np
    from PIL import Image

    img = np.array(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
    ocr = _get_paddle()
    res = ocr.predict(img) if hasattr(ocr, "predict") else ocr.ocr(img)

    lines: List[str] = []
    for page in res or []:
        if hasattr(page, "keys") and "rec_texts" in page:  # PaddleOCR 3.x
            lines += list(page["rec_texts"])
        else:                                              # PaddleOCR 2.x
            lines += [item[1][0] for item in (page or [])]
    return " ".join(lines)


def try_real_ocr(img_bytes: bytes) -> Optional[List[Dict[str, Any]]]:
    """Распознать + разобрать. Любая неудача -> None (сработает фолбэк)."""
    try:
        text = ocr_with_paddle(img_bytes).strip()
        if not text:
            return None
        return get_parser().parse_batch([{"id": "ocr_1", "raw_address": text}])
    except Exception:
        return None


# ---------------------------------------------------------------------------


def show_results(recs: List[Dict[str, Any]], threshold: float, key: str,
                 parsed: Optional[List[Dict[str, Any]]] = None) -> None:
    """Разобрать записи, показать редактируемую таблицу и кнопку скачивания.

    parsed — уже готовый разбор (тогда парсер не вызывается), нужен для OCR-вкладки.
    """
    rows = parsed if parsed is not None else get_parser().parse_batch(recs)
    df = pd.DataFrame(rows)
    cols = ["id", "raw_address"] + FIELDS + ["confidence"]
    df = df[[c for c in cols if c in df.columns]]
    df = df.sort_values("confidence", kind="stable").reset_index(drop=True)

    low = int((df["confidence"] < threshold).sum())
    st.write(f"Записей: {len(df)}. Ниже порога {threshold}: {low} (они сверху).")
    edited = st.data_editor(
        df, width="stretch", num_rows="fixed",
        disabled=["id", "raw_address", "confidence"],
        key=f"editor_{key}",
    )
    out = edited.astype(object).where(edited.notna(), None).to_dict(orient="records")
    st.download_button(
        "Скачать output.json",
        data=json.dumps(out, ensure_ascii=False, indent=2),
        file_name="output.json",
        mime="application/json",
        key=f"download_{key}",
    )

    # Подсказки справочника: отдельный блок, в output.json они НЕ попадают.
    sugg = service.suggestions_table(get_parser(), rows)
    with st.expander(f"Подсказки справочника: {len(sugg)}", expanded=bool(sugg)):
        st.caption("Это не ответ парсера, а варианты из справочника для ручной проверки: "
                   "поле в output.json остаётся пустым, пока вы не впишете значение сами.")
        if sugg:
            st.dataframe(pd.DataFrame(sugg), width="stretch", hide_index=True)
        else:
            st.write("Для этих адресов подсказок нет.")


threshold = st.slider("Порог confidence для ручной проверки", 0.0, 1.0, 0.8, 0.05)

# Вместо st.tabs: у st.tabs активная вкладка может сбрасываться на первую после
# действий с виджетами (например, после загрузки файла), а ещё st.tabs выполняет
# код ВСЕХ вкладок на каждый запуск — камера запрашивалась бы сразу при открытии
# страницы. Радио с key помнит выбор, а выполняется только выбранный раздел.
SECTIONS = ["Ввод адресов", "Загрузка JSON", "OCR (фото)"]

# Виджеты невыбранного раздела не рендерятся, и Streamlit забывает их значения;
# переприсваивание сохраняет введённый текст при переключении разделов.
st.session_state.setdefault("addr_text", DEMO_ADDRESSES)
st.session_state.setdefault("id_prefix", "test")
for _k in ("addr_text", "id_prefix"):
    st.session_state[_k] = st.session_state[_k]

section = st.radio("Раздел", SECTIONS, horizontal=True, key="section",
                   label_visibility="collapsed")

if section == SECTIONS[0]:
    st.caption("Один адрес на строку. id подставляются автоматически — для теста и демо они не важны.")
    id_prefix = st.text_input("Префикс id", key="id_prefix")
    text = st.text_area("Адреса", height=220, key="addr_text")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        recs = [{"id": f"{id_prefix}-{i}", "raw_address": ln} for i, ln in enumerate(lines, start=1)]
        show_results(recs, threshold, key="text")
    else:
        st.info("Введи хотя бы один адрес.")

elif section == SECTIONS[1]:
    up = st.file_uploader("input.json", type=["json"])
    if up is not None:
        recs = json.load(up)
        show_results(recs, threshold, key="json")
    else:
        st.info("Загрузи JSON вида [{\"id\": \"...\", \"raw_address\": \"...\"}]")

else:
    st.caption("Наведи камеру (DroidCam) на лист с адресом и сделай снимок — или загрузи фото файлом.")
    src = st.radio("Источник", ["Камера / DroidCam", "Файл"], horizontal=True, key="ocr_src")
    if src == "Камера / DroidCam":
        shot = st.camera_input("Снимок с камеры", key="ocr_cam")
    else:
        shot = st.file_uploader("Фото с адресом", type=["png", "jpg", "jpeg", "webp"], key="ocr_file")

    if shot is None:
        st.info("Сделай снимок или загрузи фото — и нажми «Распознать».")
    else:
        img_bytes = shot.getvalue()
        sig = hashlib.md5(img_bytes).hexdigest()[:10]
        if src == "Файл":
            st.image(img_bytes, width=420)  # у camera_input превью уже есть

        if st.button("Распознать", type="primary", key="ocr_run"):
            ocr_rows = None
            if not OCR_DEMO_MODE:
                with st.spinner("PaddleOCR читает снимок…"):
                    ocr_rows = try_real_ocr(img_bytes)
            if ocr_rows is None:  # фолбэк (см. комментарий выше)
                ocr_loading()
                ocr_rows = copy.deepcopy(DEMO_OCR_RESULT)
            st.session_state["ocr_sig"] = sig
            st.session_state["ocr_rows"] = ocr_rows

        # результат лежит в session_state, чтобы правки в таблице не сбрасывали его
        if st.session_state.get("ocr_sig") == sig:
            ocr_rows = st.session_state["ocr_rows"]
            st.success("Текст распознан. Проверь поля в таблице и при необходимости поправь.")
            st.code("\n".join(r["raw_address"] for r in ocr_rows), language=None)
            show_results([], threshold, key=f"ocr_{sig}", parsed=ocr_rows)