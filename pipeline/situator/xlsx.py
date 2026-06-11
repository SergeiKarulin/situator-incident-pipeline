"""Чтение xlsx-выгрузок Ситуатора (inlineStr, без sharedStrings) без openpyxl.

Выгрузки содержат строки как t="inlineStr"; проверенный на этих файлах парсер
zipfile + ElementTree надёжнее универсальных библиотек и не тянет зависимостей.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _cell_value(cell: ET.Element) -> str:
    inline = cell.find(f"{_NS}is")
    if inline is not None:
        return "".join(t.text or "" for t in inline.iter(f"{_NS}t"))
    v = cell.find(f"{_NS}v")
    return v.text if v is not None and v.text else ""


def sheet_names(path: str) -> list[str]:
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name", "") for s in wb.iter(f"{_NS}sheet")]


def read_sheet(path: str, name: str) -> list[dict[str, Any]]:
    """Лист -> список словарей по заголовку первой строки."""
    names = sheet_names(path)
    if name not in names:
        raise KeyError(f"лист {name!r} не найден в {path}: есть {names}")
    sheet_file = f"xl/worksheets/sheet{names.index(name) + 1}.xml"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(sheet_file))
    rows_el = root.find(f"{_NS}sheetData")
    if rows_el is None:
        return []

    header: dict[str, str] | None = None
    rows: list[dict[str, Any]] = []
    for row in rows_el:
        values: dict[str, str] = {}
        for cell in row:
            ref = cell.get("r", "")
            col = re.match(r"[A-Z]+", ref)
            if col:
                values[col.group()] = _cell_value(cell)
        if header is None:
            header = values
            continue
        rows.append({header.get(col, col): val for col, val in values.items()})
    return rows
