"""Детектор «человек/техника» (ступень [1]) — YOLO11 (ultralytics).

Устройство выбирается автоматически: mps (нативный запуск на Mac) -> cpu (Docker).
Веса зашиты в образ (paths.weights_dir); после сборки сеть не нужна.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Box:
    cls: str
    conf: float
    xyxy: tuple[float, float, float, float]  # нормированные 0..1


@dataclass
class Detection:
    has_activity: bool
    classes: list[str] = field(default_factory=list)
    max_conf: float = 0.0
    boxes: list[Box] = field(default_factory=list)
    frames_with_hits: int = 0  # в скольких кадрах сработки виден объект (вес улики)


class ActivityDetector:
    def __init__(self, cfg: dict):
        from ultralytics import YOLO  # ленивый импорт: тяжёлый

        weights = os.path.join(cfg.get("weights_dir", "/app/weights"), cfg["model"])
        if not os.path.exists(weights):
            weights = cfg["model"]  # позволит ultralytics докачать при нативном запуске
        self._model = YOLO(weights)
        self._device = _pick_device()
        per_class = cfg.get("conf_per_class") or {}
        self._conf_default = float(per_class.get("default", cfg.get("conf", 0.30)))
        self._conf_per_class = {k: float(v) for k, v in per_class.items() if k != "default"}
        # нижний порог predict — минимум из всех порогов (фильтруем сами по классам)
        self._conf_floor = min([self._conf_default, *self._conf_per_class.values()])
        self._imgsz = int(cfg.get("imgsz", 960))
        wanted = set(cfg["classes"])
        self._class_ids = [i for i, n in self._model.names.items() if n in wanted]
        logger.info("детектор: %s, device=%s, классы=%s, пороги=%s/деф.%s",
                    cfg["model"], self._device, sorted(wanted),
                    self._conf_per_class, self._conf_default)

    def detect(self, image_paths: list[str]) -> Detection:
        """Сработка активна, если хоть на одном кадре есть объект интереса."""
        if not image_paths:
            return Detection(has_activity=False)
        results = self._model.predict(
            image_paths, conf=self._conf_floor, imgsz=self._imgsz,
            classes=self._class_ids, device=self._device, verbose=False,
        )
        classes: set[str] = set()
        max_conf = 0.0
        boxes: list[Box] = []
        frames_with_hits = 0
        for res in results:
            frame_hit = False
            for box in res.boxes:
                name = self._model.names[int(box.cls)]
                conf = float(box.conf)
                if conf < self._conf_per_class.get(name, self._conf_default):
                    continue
                frame_hit = True
                classes.add(name)
                max_conf = max(max_conf, conf)
                boxes.append(Box(cls=name, conf=conf,
                                 xyxy=tuple(float(v) for v in box.xyxyn[0])))
            frames_with_hits += frame_hit
        return Detection(has_activity=bool(classes), classes=sorted(classes),
                         max_conf=max_conf, boxes=boxes, frames_with_hits=frames_with_hits)


def _pick_device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
