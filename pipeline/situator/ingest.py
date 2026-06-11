"""Индексация выгрузки: папки сработок + xlsx-отчёт -> SQLite.

Папка сработки: <страница>/<trigger_id>/<YYYYMMDD>/ с clip.mp4, *_list.txt, 0-3 jpg.
Камера берётся из list.txt (/recordings/camXX/) — sensor_name заполнен лишь частично.
Время: из xlsx (incident_date, UTC); если строки в xlsx нет — из имени файла клипа
(<id>_<YYYYMMDDTHHMMSSZ>_...).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field

from . import xlsx

logger = logging.getLogger(__name__)

_CAM_RE = re.compile(r"/recordings/(cam\d+)/")
_TS_RE = re.compile(r"_(\d{8}T\d{6})Z?_")

SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    trigger_id TEXT PRIMARY KEY,
    utc TEXT,
    object TEXT,
    camera TEXT,
    sensor_name TEXT,
    status TEXT,
    action TEXT,
    jpgs TEXT NOT NULL DEFAULT '[]',
    clip TEXT,
    folder TEXT,
    src TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_triggers_time ON triggers(object, camera, utc);
"""


@dataclass
class FolderEntry:
    trigger_id: str
    folder: str
    camera: str | None = None
    clip: str | None = None
    jpgs: list[str] = field(default_factory=list)
    file_utc: str | None = None


def _scan_trigger_folder(trigger_id: str, path: str) -> FolderEntry:
    entry = FolderEntry(trigger_id=trigger_id, folder=path)
    for date_dir in sorted(os.listdir(path)):
        dp = os.path.join(path, date_dir)
        if not os.path.isdir(dp):
            continue
        for fname in sorted(os.listdir(dp)):
            fp = os.path.join(dp, fname)
            if fname.endswith(".jpg"):
                entry.jpgs.append(fp)
            elif fname.endswith(".mp4"):
                entry.clip = fp
                m = _TS_RE.search(fname)
                if m:
                    ts = m.group(1)
                    entry.file_utc = (
                        f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
                    )
            elif fname.endswith("list.txt"):
                try:
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        m = _CAM_RE.search(fh.read())
                    if m:
                        entry.camera = m.group(1)
                except OSError:
                    logger.warning("не прочитан list.txt: %s", fp)
    return entry


def scan_folders(data_dir: str) -> dict[str, FolderEntry]:
    """Обходит все страницы выгрузки; принимает и плоскую папку сработок."""
    entries: dict[str, FolderEntry] = {}
    for page in sorted(os.listdir(data_dir)):
        page_path = os.path.join(data_dir, page)
        if not os.path.isdir(page_path):
            continue
        if page.isdigit():  # плоская выгрузка: сразу папки-сработки
            entries[page] = _scan_trigger_folder(page, page_path)
            continue
        for trig in os.listdir(page_path):
            tp = os.path.join(page_path, trig)
            if trig.isdigit() and os.path.isdir(tp):
                entries[trig] = _scan_trigger_folder(trig, tp)
    return entries


def find_report_xlsx(data_dir: str) -> str | None:
    candidates = [
        f for f in sorted(os.listdir(data_dir))
        if f.endswith(".xlsx") and "consolidated" not in f and not f.startswith("~")
    ]
    return os.path.join(data_dir, candidates[0]) if candidates else None


def ingest(data_dir: str, db_path: str, report_xlsx: str | None = None,
           progress=None) -> dict[str, int]:
    """Собирает индекс. progress — колбэк (done, total) для индикатора."""
    report_xlsx = report_xlsx or find_report_xlsx(data_dir)
    rows: list[dict] = []
    if report_xlsx and os.path.exists(report_xlsx):
        rows = xlsx.read_sheet(report_xlsx, "Список сработок")
        logger.info("xlsx: %s строк из %s", len(rows), report_xlsx)
    else:
        logger.warning("xlsx-отчёт не найден — индекс только по папкам")

    folders = scan_folders(data_dir)
    logger.info("папок сработок: %s", len(folders))

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM triggers")

    seen: set[str] = set()
    total = len(rows) + len(folders)
    done = 0
    for r in rows:
        tid = str(r.get("incident_id", "")).strip()
        if not tid:
            continue
        seen.add(tid)
        fe = folders.get(tid)
        con.execute(
            "INSERT OR REPLACE INTO triggers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid,
                (r.get("incident_date") or "")[:19] or (fe.file_utc if fe else None),
                r.get("object_name") or "",
                fe.camera if fe else None,
                r.get("sensor_name") or "",
                r.get("status") or "",
                r.get("action") or "",
                json.dumps(fe.jpgs if fe else []),
                fe.clip if fe else None,
                fe.folder if fe else None,
                "xlsx+folder" if fe else "xlsx",
            ),
        )
        done += 1
        if progress and done % 500 == 0:
            progress(done, total)

    for tid, fe in folders.items():
        if tid in seen:
            done += 1
            continue
        con.execute(
            "INSERT OR REPLACE INTO triggers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, fe.file_utc, "", fe.camera, "", "", "", json.dumps(fe.jpgs),
             fe.clip, fe.folder, "folder"),
        )
        done += 1
        if progress and done % 500 == 0:
            progress(done, total)

    con.commit()
    stats = {
        "triggers": con.execute("SELECT COUNT(*) FROM triggers").fetchone()[0],
        "with_time": con.execute(
            "SELECT COUNT(*) FROM triggers WHERE utc IS NOT NULL AND utc != ''"
        ).fetchone()[0],
        "with_camera": con.execute(
            "SELECT COUNT(*) FROM triggers WHERE camera IS NOT NULL"
        ).fetchone()[0],
        "with_jpg": con.execute("SELECT COUNT(*) FROM triggers WHERE jpgs != '[]'").fetchone()[0],
        "with_clip": con.execute(
            "SELECT COUNT(*) FROM triggers WHERE clip IS NOT NULL"
        ).fetchone()[0],
    }
    con.close()
    return stats
