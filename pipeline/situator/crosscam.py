"""Межкамерная склейка (ступень [3]): только по графу соседних камер.

Activity-сессии соседних камер одного объекта объединяются, если их интервалы
пересекаются либо пауза между ними <= tau_handoff (переход субъекта).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .sessions import Session


@dataclass
class Incident:
    sessions: list[Session] = field(default_factory=list)

    @property
    def start(self) -> dt.datetime:
        return min(s.start for s in self.sessions)

    @property
    def end(self) -> dt.datetime:
        return max(s.end for s in self.sessions)

    @property
    def cameras(self) -> list[str]:
        return sorted({s.camera for s in self.sessions if s.camera})

    @property
    def object(self) -> str:
        return self.sessions[0].object

    @property
    def kind(self) -> str:
        return self.sessions[0].kind

    @property
    def trigger_ids(self) -> list[str]:
        out: list[str] = []
        for s in sorted(self.sessions, key=lambda s: s.start):
            out.extend(t.trigger_id for t in s.triggers)
        return out

    @property
    def classes(self) -> list[str]:
        out: set[str] = set()
        for s in self.sessions:
            out.update(s.classes)
        return sorted(out)


def _adjacent(a: str | None, b: str | None, pairs: set[frozenset[str]]) -> bool:
    return a is not None and b is not None and frozenset((a, b)) in pairs


def _mergeable(x: Session, y: Session, pairs: set[frozenset[str]],
               handoff: dt.timedelta) -> bool:
    if x.object != y.object or x.kind != "activity" or y.kind != "activity":
        return False
    if x.camera == y.camera or not _adjacent(x.camera, y.camera, pairs):
        return False
    gap = max(x.start, y.start) - min(x.end, y.end)
    return gap <= handoff  # отрицательный gap = интервалы пересекаются


def merge_cross_camera(sessions: list[Session], adjacency: list[dict],
                       tau_handoff_s: int) -> list[Incident]:
    """Union-find по activity-сессиям; остальные сессии -> одиночные инциденты."""
    pairs = {frozenset((p["a"], p["b"])) for p in adjacency}
    handoff = dt.timedelta(seconds=tau_handoff_s)

    parent = list(range(len(sessions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    activity = [i for i, s in enumerate(sessions) if s.kind == "activity"]
    for ai in range(len(activity)):
        for bi in range(ai + 1, len(activity)):
            i, j = activity[ai], activity[bi]
            if _mergeable(sessions[i], sessions[j], pairs, handoff):
                union(i, j)

    groups: dict[int, Incident] = {}
    for i, s in enumerate(sessions):
        root = find(i)
        groups.setdefault(root, Incident()).sessions.append(s)
    return sorted(groups.values(), key=lambda inc: inc.start)
