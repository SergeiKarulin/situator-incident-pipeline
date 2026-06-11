"""Детектор деградации видеосигнала (ступень [0.5] Отчёта v3 §8).

Целевой случай — cam99 ночью: цветные горизонтальные полосы по всему кадру
(срыв аналогового тракта). Признаки:
  * sat_rows_frac — доля строк кадра с высокой средней насыщенностью
    («цветные полосы»; у тумана/ночной серости насыщенность низкая);
  * hue_flicker — средний |разности| тона между соседними строками
    (полосы помех мерцают по вертикали, естественные сцены меняются плавно).
Кадр деградирован, если оба признака выше порогов конфига.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class DegradationScore:
    sat_rows_frac: float
    hue_flicker: float

    def is_degraded(self, cfg: dict) -> bool:
        return (
            self.sat_rows_frac >= cfg["sat_rows_frac"]
            and self.hue_flicker >= cfg["hue_flicker_min"]
        )


def score_image(path: str, sat_row_threshold: float = 0.45) -> DegradationScore | None:
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    small = cv2.resize(img, (256, max(1, int(256 * h / w))), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32) / 255.0
    row_sat = sat.mean(axis=1)
    sat_rows_frac = float((row_sat > sat_row_threshold).mean())

    hue = hsv[..., 0].astype(np.float32)
    row_hue = hue.mean(axis=1)
    diff = np.abs(np.diff(row_hue))
    # тон цикличен (0..179): большие скачки через ноль считаем малыми
    diff = np.minimum(diff, 180.0 - diff)
    hue_flicker = float(diff.mean())
    return DegradationScore(sat_rows_frac=sat_rows_frac, hue_flicker=hue_flicker)
