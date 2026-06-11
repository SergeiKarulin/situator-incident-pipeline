"""Метрики прогона: сжатие, аудит REAL, сравнение с реестром Ситуатора.

Главный safety-критерий (Отчёт v3 §12): REAL-сработки не должны «молча» уйти в
авто-закрытые типы («среда», «деградация»). Попадание REAL в «реал»/«несхлопнуто» — ок.
"""
from __future__ import annotations

import json
from collections import Counter


def compute(cards: list[dict], triggers_status: dict[str, str]) -> dict:
    """triggers_status — статусы ТОЛЬКО обработанных сработок (скоуп прогона)."""
    by_type = Counter(c["type"] for c in cards)
    n_triggers = sum(c["n_triggers"] for c in cards)
    visible_types = {"реал", "несхлопнуто"}
    visible_cards = [c for c in cards if c["type"] in visible_types]
    auto_closed = [c for c in cards if c["type"] in {"среда", "деградация"}]

    real_ids = {tid for tid, st in triggers_status.items() if st == "REAL"}
    placement: dict[str, str] = {}
    for c in cards:
        for tid in c["trigger_ids"]:
            if tid in real_ids:
                placement[tid] = c["type"]
    lost_real = sorted(tid for tid, t in placement.items() if t in {"среда", "деградация"})
    missing_real = sorted(real_ids - set(placement))

    return {
        "cards_total": len(cards),
        "cards_by_type": dict(by_type),
        "triggers_in_cards": n_triggers,
        "cards_visible_to_operator": len(visible_cards),
        "triggers_auto_closed": sum(c["n_triggers"] for c in auto_closed),
        "auto_closed_share": round(
            sum(c["n_triggers"] for c in auto_closed) / n_triggers, 3) if n_triggers else 0.0,
        "compression_total": round(n_triggers / len(cards), 2) if cards else 0.0,
        "real_total_in_scope": len(real_ids),
        "real_in_activity_or_uncertain": sum(
            1 for t in placement.values() if t in visible_types),
        "real_lost_in_autoclosed": lost_real,
        "real_not_in_any_card": missing_real,
    }


def render(metrics: dict) -> str:
    lines = [
        f"карточек всего:            {metrics['cards_total']}  {metrics['cards_by_type']}",
        f"сработок в карточках:      {metrics['triggers_in_cards']}",
        f"видимых оператору:         {metrics['cards_visible_to_operator']} карточек",
        f"авто-закрыто сработок:     {metrics['triggers_auto_closed']}"
        f" ({metrics['auto_closed_share'] * 100:.1f}%)",
        f"сжатие (сработки/карточки): {metrics['compression_total']}×",
        f"REAL в «реал/несхлопнуто»: {metrics['real_in_activity_or_uncertain']}",
        f"REAL потеряно в авто-закрытых: {len(metrics['real_lost_in_autoclosed'])}"
        f" {metrics['real_lost_in_autoclosed'][:10]}",
        f"REAL вне карточек (вне фильтра прогона): {len(metrics['real_not_in_any_card'])}",
    ]
    return "\n".join(lines)


def write(metrics: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
