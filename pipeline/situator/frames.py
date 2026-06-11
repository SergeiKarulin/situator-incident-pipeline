"""Кадры сработки — в прод-режиме ТОЛЬКО из clip.mp4 (в проде jpg не существует).

jpg из выгрузки оставлены как отладочный режим (use_jpgs=true в конфиге) — они были
сгенерированы специально для ручного анализа и в бою их не будет.

Грабли из Experiment_003: у части клипов видеопоток короче длительности контейнера —
ffmpeg на seek за последний кадр молча выходит с кодом 0, не записав файл. Поэтому
каскад позиций и проверка существования.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# позиции (сек) на клип ~10-15 с; конец клипа обязателен — объект часто входит в кадр
# к концу записи MD-события (кейс 5982278: person только на 12-й секунде)
_FRAME_POSITIONS = ("0.5", "4.0", "9.0", "12.0")
_FALLBACK = ("2.0", "0.5", "0")


def _extract_at(clip_path: str, ss: str, out_path: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-y", "-ss", ss, "-i", clip_path,
         "-frames:v", "1", out_path],
        capture_output=True,
    )
    return r.returncode == 0 and os.path.exists(out_path)


def extract_frames(clip_path: str, cache_base: str, max_frames: int) -> list[str]:
    """До max_frames кадров из клипа в кэш; имена <base>_<k>.jpg."""
    os.makedirs(os.path.dirname(cache_base), exist_ok=True)
    out: list[str] = []
    for k, ss in enumerate(_FRAME_POSITIONS[:max_frames]):
        path = f"{cache_base}_{k}.jpg"
        if os.path.exists(path) or _extract_at(clip_path, ss, path):
            out.append(path)
    if not out:  # все позиции мимо (очень короткий поток) — каскад с нуля
        path = f"{cache_base}_f.jpg"
        for ss in _FALLBACK:
            if os.path.exists(path) or _extract_at(clip_path, ss, path):
                out.append(path)
                break
    if not out:
        logger.warning("не извлечено ни кадра: %s", clip_path)
    return out


def trigger_frames(row: dict, cache_dir: str, max_frames: int = 3,
                   extract_from_clip: bool = True, use_jpgs: bool = False) -> list[str]:
    """Пути к кадрам сработки. Прод-режим: use_jpgs=False -> только клип."""
    if use_jpgs:
        jpgs = json.loads(row["jpgs"] or "[]")
        if jpgs:
            return jpgs[:max_frames]
    clip = row["clip"]
    if not (extract_from_clip and clip):
        return []
    return extract_frames(clip, os.path.join(cache_dir, row["trigger_id"]), max_frames)
