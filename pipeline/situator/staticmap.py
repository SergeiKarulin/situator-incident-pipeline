"""Подавление статических объектов — «пер-камерный фон нормы» (Отчёт v3 §10.2).

Припаркованная машина или лодка под чехлом детектируются YOLO в каждом кадре,
но событием не являются. Бокс считается «мебелью сцены», если кластер почти
одинаковых боксов (IoU >= порога) встречается у камеры:
  * в >= min_share доли её сработок с кадрами,
  * не менее min_triggers раз,
  * на интервале >= min_span_hours (минивэн, приехавший на 46 минут, мебелью НЕ станет).
Класс person не подавляется никогда (safety).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from .sessions import Trigger


@dataclass
class _Cluster:
    xyxy: list[float]
    cls: str
    trigger_ids: set[str] = field(default_factory=set)
    first: dt.datetime | None = None
    last: dt.datetime | None = None

    def span_hours(self) -> float:
        if not (self.first and self.last):
            return 0.0
        return (self.last - self.first).total_seconds() / 3600.0


def _iou(a: tuple | list, b: tuple | list) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def build_clusters(triggers: list[Trigger], iou_threshold: float,
                   never_suppress: set[str]) -> dict[str | None, list[_Cluster]]:
    by_camera: dict[str | None, list[_Cluster]] = defaultdict(list)
    for t in sorted(triggers, key=lambda t: t.utc):
        for box in t.boxes:
            if box.cls in never_suppress:
                continue
            clusters = by_camera[t.camera]
            best, best_iou = None, iou_threshold
            for c in clusters:
                if c.cls != box.cls:
                    continue
                iou = _iou(c.xyxy, box.xyxy)
                if iou >= best_iou:
                    best, best_iou = c, iou
            if best is None:
                best = _Cluster(xyxy=list(box.xyxy), cls=box.cls)
                clusters.append(best)
            else:  # скользящее среднее позиции — кластер «дышит» с бликами
                best.xyxy = [0.9 * c + 0.1 * n for c, n in zip(best.xyxy, box.xyxy)]
            best.trigger_ids.add(t.trigger_id)
            best.first = best.first or t.utc
            best.last = t.utc
    return by_camera


def suppress_static(triggers: list[Trigger], cfg: dict) -> dict:
    """Помечает статические боксы; пересчитывает activity/classes сработок."""
    never = set(cfg.get("never_suppress", ["person"]))
    clusters = build_clusters(triggers, cfg["iou_threshold"], never)

    frames_per_cam: dict[str | None, int] = defaultdict(int)
    for t in triggers:
        if t.frames_seen:
            frames_per_cam[t.camera] += 1

    static: dict[str | None, list[_Cluster]] = defaultdict(list)
    for camera, cl_list in clusters.items():
        total = max(1, frames_per_cam[camera])
        for c in cl_list:
            if (len(c.trigger_ids) >= cfg["min_triggers"]
                    and len(c.trigger_ids) / total >= cfg["min_share"]
                    and c.span_hours() >= cfg["min_span_hours"]):
                static[camera].append(c)

    match_iou = cfg.get("match_iou", cfg["iou_threshold"])
    # класс-группы матчинга: туман/ночь меняют car<->bus<->boat у одного объекта
    groups: dict[str, frozenset[str]] = {}
    for grp in cfg.get("match_class_groups", ()):
        fs = frozenset(grp)
        for cls in grp:
            groups[cls] = fs

    def same_class(a: str, b: str) -> bool:
        return a == b or groups.get(a, frozenset((a,))) == groups.get(b, frozenset((b,)))

    suppressed_boxes = 0
    for t in triggers:
        if not t.boxes:
            continue
        kept: list = []
        for box in t.boxes:
            is_static = any(
                same_class(c.cls, box.cls) and _iou(c.xyxy, box.xyxy) >= match_iou
                for c in static.get(t.camera, ())
            ) if box.cls not in never else False
            if is_static:
                suppressed_boxes += 1
            else:
                kept.append(box)
        t.activity = bool(kept)
        t.classes = sorted({b.cls for b in kept})

    return {
        "static_clusters": {cam or "?": [
            {"cls": c.cls, "n": len(c.trigger_ids), "span_h": round(c.span_hours(), 1)}
            for c in cl] for cam, cl in static.items() if cl},
        "suppressed_boxes": suppressed_boxes,
    }
