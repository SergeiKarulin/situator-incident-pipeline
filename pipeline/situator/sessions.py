"""Темпоральные сессии (ступень [2]) и шторма деградации.

Сессия — серия сработок одной (объект, камера) с паузами <= tau_gap.
Шторм — час камеры, где деградированных сработок >= storm_min_triggers:
они изымаются из обычного потока и схлопываются в одну карточку.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Trigger:
    trigger_id: str
    utc: dt.datetime
    object: str
    camera: str | None
    status: str
    activity: bool = False
    classes: list[str] = field(default_factory=list)
    degraded: bool = False
    frames_seen: int = 0
    hit_frames: int = 0  # в скольких кадрах сработки виден объект
    boxes: list = field(default_factory=list)  # detector.Box


@dataclass
class Session:
    object: str
    camera: str | None
    triggers: list[Trigger]
    kind: str = "environment"  # activity | environment | degradation | uncertain

    @property
    def start(self) -> dt.datetime:
        return self.triggers[0].utc

    @property
    def end(self) -> dt.datetime:
        return self.triggers[-1].utc

    @property
    def classes(self) -> list[str]:
        out: set[str] = set()
        for t in self.triggers:
            out.update(t.classes)
        return sorted(out)


def split_storms(triggers: list[Trigger], storm_min: int) -> tuple[list[Trigger], list[Session]]:
    """Выделяет шторма деградации per (камера, час); возвращает (остальные, шторма)."""
    by_hour: dict[tuple[str | None, str, str], list[Trigger]] = defaultdict(list)
    for t in triggers:
        if t.degraded and not t.activity:
            by_hour[(t.camera, t.object, t.utc.strftime("%Y-%m-%d %H"))].append(t)

    storms: list[Session] = []
    storm_ids: set[str] = set()
    for (camera, obj, _hour), batch in sorted(by_hour.items(), key=lambda kv: kv[1][0].utc):
        if len(batch) < storm_min:
            continue
        storms.append(Session(object=obj, camera=camera, triggers=batch, kind="degradation"))
        storm_ids.update(t.trigger_id for t in batch)

    rest = [t for t in triggers if t.trigger_id not in storm_ids]
    return rest, storms


def build_sessions(triggers: list[Trigger], tau_gap_s: int,
                   overrides: dict[str, int] | None = None) -> list[Session]:
    overrides = overrides or {}
    by_cam: dict[tuple[str, str | None], list[Trigger]] = defaultdict(list)
    for t in triggers:
        by_cam[(t.object, t.camera)].append(t)

    sessions: list[Session] = []
    for (obj, camera), batch in by_cam.items():
        batch.sort(key=lambda t: t.utc)
        gap = dt.timedelta(seconds=overrides.get(obj, tau_gap_s))
        current: list[Trigger] = []
        for t in batch:
            if current and t.utc - current[-1].utc > gap:
                sessions.extend(_finalize(obj, camera, current, gap))
                current = []
            current.append(t)
        if current:
            sessions.extend(_finalize(obj, camera, current, gap))
    sessions.sort(key=lambda s: s.start)
    return sessions


def _finalize(obj: str, camera: str | None, triggers: list[Trigger],
              gap: dt.timedelta) -> list[Session]:
    """Правило веса улик с локальностью (v0.3).

    Активные сработки группируются в «ядра» тем же tau_gap. Ядро подтверждено,
    если в нём >=2 активных сработок ЛИБО объект виден в >=2 кадрах одной.
    Сессия «реал» = подтверждённое ядро (с шумовыми сработками внутри его
    интервала). Одиночная слабая улика -> «несхлопнуто» (оператору). Остальной
    шум -> «среда». Так 2 случайных блоба за 2.5 часа дождя не клеят ночь в
    «реал», а минивэн с регулярными хитами остаётся одним инцидентом."""
    active = [t for t in triggers if t.activity]
    cores: list[list[Trigger]] = []
    for t in active:
        if cores and t.utc - cores[-1][-1].utc <= gap:
            cores[-1].append(t)
        else:
            cores.append([t])

    spans: list[tuple[dt.datetime, dt.datetime]] = []
    weak_ids: set[str] = set()
    for core in cores:
        if len(core) >= 2 or any(t.hit_frames >= 2 for t in core):
            spans.append((core[0].utc, core[-1].utc))
        else:
            weak_ids.update(t.trigger_id for t in core)

    def label(t: Trigger) -> str:
        if t.trigger_id in weak_ids:
            return "weak"
        for i, (s, e) in enumerate(spans):
            if s <= t.utc <= e:
                return f"core{i}"
        return "rest"

    out: list[Session] = []
    run: list[Trigger] = []
    run_label = ""
    for t in triggers:
        lab = label(t)
        if run and lab != run_label:
            out.append(_make(obj, camera, run, run_label))
            run = []
        run.append(t)
        run_label = lab
    if run:
        out.append(_make(obj, camera, run, run_label))
    return out


def _make(obj: str, camera: str | None, triggers: list[Trigger], lab: str) -> Session:
    if lab.startswith("core"):
        kind = "activity"
    elif lab == "weak":
        kind = "uncertain"
    else:
        kind = "uncertain" if all(t.frames_seen == 0 for t in triggers) else "environment"
    return Session(object=obj, camera=camera, triggers=list(triggers), kind=kind)
