"""CLI конвейера: situator ingest | run | bench | info."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3

import click
import yaml

from . import frames as frames_mod
from . import ingest as ingest_mod
from . import metrics as metrics_mod
from .cards import Lexicon, build_cards, write_cards
from .crosscam import merge_cross_camera
from .degradation import score_image
from .sessions import Trigger, build_sessions, split_storms

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("situator")

DEFAULT_DB = "/runs/index.db"
DEFAULT_DATA = "/data/inputs"
CONFIG_DIR = os.environ.get("SITUATOR_CONFIG", "/app/config")


def _load_cfg() -> tuple[dict, dict, dict]:
    out = []
    for name in ("pipeline.yaml", "cameras.yaml", "lexicon.yaml"):
        with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as fh:
            out.append(yaml.safe_load(fh))
    return tuple(out)


def _parse_utc(s: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s[:19])
    except (ValueError, TypeError):
        return None


@click.group()
def main() -> None:
    """Конвейер объединения сработок в инциденты (Отчёт v3 §8)."""


@main.command()
@click.option("--data", default=DEFAULT_DATA, show_default=True)
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--xlsx", "xlsx_path", default=None, help="Путь к отчёту (авто: первый report*.xlsx)")
def ingest(data: str, db: str, xlsx_path: str | None) -> None:
    """Индексация выгрузки в SQLite."""
    with click.progressbar(length=1, label="индексация") as bar:
        def progress(done: int, total: int) -> None:
            bar.length = total
            bar.update(done - bar.pos)

        stats = ingest_mod.ingest(data, db, xlsx_path, progress=progress)
        bar.update(bar.length - bar.pos)
    click.echo(json.dumps(stats, ensure_ascii=False, indent=2))


def _load_rows(db: str, obj: str | None, window: str | None,
               limit: int | None) -> list[dict]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    q = "SELECT * FROM triggers WHERE utc IS NOT NULL AND utc != ''"
    args: list = []
    if obj:
        q += " AND object = ?"
        args.append(obj)
    if window:
        frm, to = window.split("..")
        q += " AND utc >= ? AND utc < ?"
        args += [frm, to]
    q += " ORDER BY utc"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = [dict(r) for r in con.execute(q, args)]
    con.close()
    return rows


@main.command()
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--object", "obj", default=None, help="Только один объект (site-a, site-b, ...)")
@click.option("--window", default=None,
              help="Окно UTC: '2026-06-04 01:00..2026-06-04 02:00'")
@click.option("--limit", type=int, default=None)
@click.option("--out", default=None, help="Каталог результата (деф. /runs/<метка времени>)")
@click.option("--no-detector", is_flag=True, help="Без YOLO (только деградация+сессии) — быстрый смоук")
def run(db: str, obj: str | None, window: str | None, limit: int | None,
        out: str | None, no_detector: bool) -> None:
    """Прогон конвейера: деградация -> детектор -> сессии -> склейка -> карточки."""
    pipeline_cfg, cameras_cfg, lexicon_cfg = _load_cfg()
    out = out or os.path.join(
        pipeline_cfg["paths"]["runs_dir"],
        dt.datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    cache_dir = os.path.join(pipeline_cfg["paths"]["runs_dir"], "cache", "frames")

    rows = _load_rows(db, obj, window, limit)
    if not rows:
        raise click.ClickException("нет сработок под фильтр — проверь ingest/фильтры")
    click.echo(f"сработок к обработке: {len(rows)} -> {out}")

    detector = None
    if not no_detector:
        from .detector import ActivityDetector  # тяжёлый импорт по требованию

        det_cfg = dict(pipeline_cfg["detector"])
        det_cfg["weights_dir"] = pipeline_cfg["paths"]["weights_dir"]
        detector = ActivityDetector(det_cfg)

    deg_cfg = pipeline_cfg["degradation"]
    triggers: list[Trigger] = []
    skipped_no_time = 0
    with click.progressbar(rows, label="классификация сработок") as bar:
        for row in bar:
            utc = _parse_utc(row["utc"])
            if utc is None:
                skipped_no_time += 1
                continue
            t = Trigger(trigger_id=row["trigger_id"], utc=utc,
                        object=row["object"] or "?", camera=row["camera"],
                        status=row["status"] or "")
            paths = frames_mod.trigger_frames(
                row, cache_dir,
                max_frames=pipeline_cfg["detector"]["max_frames_per_trigger"],
                extract_from_clip=pipeline_cfg["detector"]["extract_from_clip"],
                use_jpgs=pipeline_cfg["detector"].get("use_jpgs", False))
            t.frames_seen = len(paths)
            if deg_cfg["enabled"] and paths:
                score = score_image(paths[0], deg_cfg["sat_row_threshold"])
                t.degraded = bool(score and score.is_degraded(deg_cfg))
            if detector is not None and paths and not t.degraded:
                det = detector.detect(paths)
                t.activity, t.classes, t.boxes = det.has_activity, det.classes, det.boxes
                t.hit_frames = det.frames_with_hits
            triggers.append(t)

    if skipped_no_time:
        logger.warning("пропущено без времени: %s", skipped_no_time)

    static_cfg = pipeline_cfg.get("static_suppression", {"enabled": False})
    static_info: dict = {}
    if static_cfg.get("enabled") and detector is not None:
        from .staticmap import suppress_static

        static_info = suppress_static(triggers, static_cfg)
        logger.info("статические кластеры: %s; подавлено боксов: %s",
                    static_info.get("static_clusters"), static_info.get("suppressed_boxes"))

    rest, storms = split_storms(triggers, deg_cfg["storm_min_triggers"])
    sessions = build_sessions(rest, pipeline_cfg["sessions"]["tau_gap_seconds"],
                              pipeline_cfg["sessions"].get("overrides"))
    incidents = merge_cross_camera(sessions, cameras_cfg.get("adjacency", []),
                                   pipeline_cfg["crosscam"]["tau_handoff_seconds"])
    camera_names = {cam: meta.get("name", cam)
                    for cam, meta in cameras_cfg["cameras"].items()}
    lexicon = Lexicon(lexicon_cfg, camera_names, cameras_cfg.get("timezones", {}))
    cards = build_cards(incidents, storms, lexicon)
    paths = write_cards(cards, out)

    scope_status = {t.trigger_id: t.status for t in triggers}
    m = metrics_mod.compute(cards, scope_status)
    if static_info:
        m["static_suppression"] = static_info
    m["skipped_no_time"] = skipped_no_time
    m["triggers_without_frames"] = sum(1 for t in triggers if t.frames_seen == 0)
    metrics_mod.write(m, os.path.join(out, "metrics.json"))

    click.echo(metrics_mod.render(m))
    click.echo(f"карточки: {paths['jsonl']}")


@main.command()
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option("--limit", type=int, default=300, show_default=True)
@click.option("--object", "obj", default=None)
def bench(db: str, limit: int, obj: str | None) -> None:
    """Замер латентности стадий 0.5-1 на выборке (реплей в хронологии)."""
    from .detector import ActivityDetector
    from .replay import run_bench

    pipeline_cfg, _, _ = _load_cfg()
    det_cfg = dict(pipeline_cfg["detector"])
    det_cfg["weights_dir"] = pipeline_cfg["paths"]["weights_dir"]
    detector = ActivityDetector(det_cfg)
    cache_dir = os.path.join(pipeline_cfg["paths"]["runs_dir"], "cache", "frames")
    deg_cfg = pipeline_cfg["degradation"]

    rows = _load_rows(db, obj, None, limit)

    def process_one(row: dict) -> None:
        paths = frames_mod.trigger_frames(
            row, cache_dir, max_frames=det_cfg["max_frames_per_trigger"],
            extract_from_clip=det_cfg["extract_from_clip"],
            use_jpgs=det_cfg.get("use_jpgs", False))
        if not paths:
            return
        score = score_image(paths[0], deg_cfg["sat_row_threshold"])
        if not (score and score.is_degraded(deg_cfg)):
            detector.detect(paths)

    with click.progressbar(length=len(rows), label="бенчмарк") as bar:
        result = run_bench(rows, process_one,
                           progress=lambda done, total: bar.update(done - bar.pos))
    click.echo(json.dumps(result.summary(), ensure_ascii=False, indent=2))


@main.command()
@click.option("--db", default=DEFAULT_DB, show_default=True)
def info(db: str) -> None:
    """Сводка по индексу."""
    con = sqlite3.connect(db)
    for label, q in [
        ("сработок", "SELECT COUNT(*) FROM triggers"),
        ("по статусам", "SELECT status, COUNT(*) FROM triggers GROUP BY status"),
        ("по объектам", "SELECT object, COUNT(*) FROM triggers GROUP BY object ORDER BY 2 DESC"),
        ("диапазон времени", "SELECT MIN(utc), MAX(utc) FROM triggers WHERE utc != ''"),
    ]:
        click.echo(f"{label}: {con.execute(q).fetchall()}")
    con.close()


if __name__ == "__main__":
    main()
