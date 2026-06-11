"""Карточки инцидентов: номер, тип, описание (<=160 симв., по словарю), trigger_ids.

Описание — черновик для оператора, собирается из config/lexicon.yaml без LLM:
«<Тип>: <кто> — <камера(ы)>; <время суток> <период>; <N сработок>[; пометки]».
Корзина «среда» агрегируется per (камера, час) — аудируемо, не теряется (§10.5).
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict

from .crosscam import Incident
from .sessions import Session

_KIND_RU = {"activity": "реал", "environment": "среда",
            "degradation": "деградация", "uncertain": "несхлопнуто"}

DESCRIPTION_LIMIT = 160


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


class Lexicon:
    """Обёртка config/lexicon.yaml + контекст имён камер и часовых поясов."""

    def __init__(self, lexicon_cfg: dict, camera_names: dict[str, str],
                 tz_offsets: dict[str, int]):
        self._lex = lexicon_cfg
        self._camera_names = camera_names
        self._tz = tz_offsets

    def _daypart(self, obj: str, start_utc: dt.datetime) -> str:
        local_h = (start_utc.hour + self._tz.get(obj, self._tz.get("default", 3))) % 24
        if 5 <= local_h < 11:
            key = "morning"
        elif 11 <= local_h < 17:
            key = "day"
        elif 17 <= local_h < 23:
            key = "evening"
        else:
            key = "night"
        return self._lex["dayparts"][key]

    def _actors(self, classes: list[str]) -> str:
        order = {c: i for i, c in enumerate(self._lex.get("class_order", []))}
        names = self._lex["classes"]
        seen: list[str] = []
        for c in sorted(classes, key=lambda c: order.get(c, 99)):
            ru = names.get(c, c)
            if ru not in seen:
                seen.append(ru)
        return ", ".join(seen) if seen else "объект не распознан"

    def describe(self, kind: str, obj: str, cameras: list[str], classes: list[str],
                 n: int, start: dt.datetime, end: dt.datetime,
                 extras: list[str] | None = None) -> str:
        head = self._lex["types"][kind]
        place = " + ".join(self._camera_names.get(c, c) for c in cameras) \
            or "камера неизвестна"
        period = f"{start:%H:%M}–{end:%H:%M}" if end > start else f"{start:%H:%M}"
        count = f"{n} {ru_plural(n, 'сработка', 'сработки', 'сработок')}"
        parts = [head + (f": {self._actors(classes)}" if kind == "activity" else "")]
        parts.append(place)
        parts.append(f"{self._daypart(obj, start)} {period} UTC")
        parts.append(count)
        for e in extras or ():
            parts.append(self._lex["extras"].get(e, e))
        text = f"{parts[0]} — {'; '.join(parts[1:])}"
        if len(text) > DESCRIPTION_LIMIT:
            text = text[:DESCRIPTION_LIMIT - 1].rstrip() + "…"
        return text


def build_cards(incidents: list[Incident], storms: list[Session],
                lexicon: Lexicon, aggregate_environment: bool = True) -> list[dict]:
    cards: list[dict] = []

    def add(kind: str, obj: str, cameras: list[str], classes: list[str],
            start: dt.datetime, end: dt.datetime, trigger_ids: list[str],
            extras: list[str] | None = None) -> None:
        cards.append({
            "type": _KIND_RU[kind],
            "object": obj,
            "cameras": cameras,
            "period_utc": [start.isoformat(sep=" "), end.isoformat(sep=" ")],
            "n_triggers": len(trigger_ids),
            "description": lexicon.describe(kind, obj, cameras, classes, len(trigger_ids),
                                            start, end, extras),
            "trigger_ids": trigger_ids,
        })

    env_bucket: dict[tuple[str, str | None, str], list[Session]] = defaultdict(list)
    for inc in incidents:
        if inc.kind == "environment" and aggregate_environment:
            for s in inc.sessions:
                env_bucket[(s.object, s.camera, s.start.strftime("%Y-%m-%d %H"))].append(s)
            continue
        extras: list[str] = []
        if inc.kind == "activity" and len(inc.cameras) > 1:
            extras.append("cross_camera")
        if inc.kind == "uncertain":
            no_frames = all(t.frames_seen == 0 for s in inc.sessions for t in s.triggers)
            extras.append("no_frames" if no_frames else "weak_evidence")
        add(inc.kind, inc.object, inc.cameras, inc.classes, inc.start, inc.end,
            inc.trigger_ids, extras)

    for (obj, camera, _hour), batch in sorted(env_bucket.items(),
                                              key=lambda kv: kv[1][0].start):
        trigger_ids = [t.trigger_id for s in batch for t in s.triggers]
        add("environment", obj, [camera] if camera else [], [],
            min(s.start for s in batch), max(s.end for s in batch), trigger_ids)

    for storm in storms:
        add("degradation", storm.object, [storm.camera] if storm.camera else [], [],
            storm.start, storm.end, [t.trigger_id for t in storm.triggers],
            ["storm_hint"])

    cards.sort(key=lambda c: c["period_utc"][0])
    for i, card in enumerate(cards, 1):
        card["incident_id"] = i
    return cards


def write_cards(cards: list[dict], out_dir: str) -> dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "jsonl": os.path.join(out_dir, "cards.jsonl"),
        "csv": os.path.join(out_dir, "cards.csv"),
        "xlsx": os.path.join(out_dir, "cards.xlsx"),
    }
    with open(paths["jsonl"], "w", encoding="utf-8") as fh:
        for card in cards:
            fh.write(json.dumps(card, ensure_ascii=False) + "\n")

    import pandas as pd

    df = pd.DataFrame([
        {
            "incident_id": c["incident_id"],
            "type": c["type"],
            "object": c["object"],
            "cameras": ",".join(c["cameras"]),
            "from_utc": c["period_utc"][0],
            "to_utc": c["period_utc"][1],
            "n_triggers": c["n_triggers"],
            "description": c["description"],
            "trigger_ids": ",".join(c["trigger_ids"]),
        }
        for c in cards
    ])
    df.to_csv(paths["csv"], index=False)
    df.to_excel(paths["xlsx"], index=False)
    return paths
