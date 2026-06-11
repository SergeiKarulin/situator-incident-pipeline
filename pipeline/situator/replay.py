"""Бенчмарк: реплей сработок в хронологии с замером латентности стадий 0.5-1.

CPU-числа из Docker на Mac — ориентир для разработки; авторитетный замер — тот же
образ на целевом GPU (A10) либо нативный запуск с device=mps.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass


@dataclass
class BenchResult:
    n: int
    total_s: float
    latencies_ms: list[float]

    def summary(self) -> dict:
        lat = sorted(self.latencies_ms)
        if not lat:
            return {"n": 0}
        return {
            "n": self.n,
            "throughput_per_s": round(self.n / self.total_s, 2) if self.total_s else None,
            "latency_ms_p50": round(statistics.median(lat), 1),
            "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 1),
            "latency_ms_max": round(lat[-1], 1),
        }


def run_bench(rows: list[dict], process_one, limit: int | None = None,
              progress=None) -> BenchResult:
    """process_one(row) — обработка одной сработки (деградация + детекция)."""
    rows = rows[:limit] if limit else rows
    latencies: list[float] = []
    t_start = time.perf_counter()
    for i, row in enumerate(rows):
        t0 = time.perf_counter()
        process_one(row)
        latencies.append((time.perf_counter() - t0) * 1000)
        if progress:
            progress(i + 1, len(rows))
    return BenchResult(n=len(rows), total_s=time.perf_counter() - t_start,
                       latencies_ms=latencies)
