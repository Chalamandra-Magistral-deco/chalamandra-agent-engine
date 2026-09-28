"""Agregaciones sobre los registros de trazas. Funciones puras."""

from __future__ import annotations

import statistics
from typing import Iterable


def _filter(records: Iterable[dict], model: str | None = None) -> list[dict]:
    recs = list(records)
    if model is not None:
        recs = [r for r in recs if r.get("model") == model]
    return recs


def latency_percentiles(records: Iterable[dict], model: str | None = None) -> dict:
    recs = _filter(records, model)
    latencies = [r["latency_ms"] for r in recs if r.get("latency_ms")]
    if not latencies:
        return {"count": 0}
    latencies.sort()
    n = len(latencies)
    def pct(p: float) -> int:
        idx = max(0, min(n - 1, int(round(p / 100 * (n - 1)))))
        return latencies[idx]
    return {
        "count": n,
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "mean": int(statistics.mean(latencies)),
        "min": latencies[0],
        "max": latencies[-1],
    }


def token_totals(records: Iterable[dict], model: str | None = None) -> dict:
    recs = _filter(records, model)
    tin = sum(r.get("tokens_in", 0) for r in recs)
    tout = sum(r.get("tokens_out", 0) for r in recs)
    return {"count": len(recs), "tokens_in": tin, "tokens_out": tout, "tokens_total": tin + tout}


def cost_totals(records: Iterable[dict], model: str | None = None) -> dict:
    recs = _filter(records, model)
    total = sum(r.get("cost_usd", 0.0) for r in recs)
    return {"count": len(recs), "cost_usd": round(total, 6)}


def error_rate(records: Iterable[dict], model: str | None = None) -> dict:
    recs = _filter(records, model)
    if not recs:
        return {"count": 0, "errors": 0, "error_rate": 0.0}
    errors = sum(1 for r in recs if r.get("status") != "ok")
    return {
        "count": len(recs),
        "errors": errors,
        "error_rate": round(errors / len(recs), 4),
    }


def compare_models(records: Iterable[dict], models: list[str]) -> dict:
    recs = list(records)
    return {
        m: {
            "latency": latency_percentiles(recs, model=m),
            "tokens": token_totals(recs, model=m),
            "cost": cost_totals(recs, model=m),
            "errors": error_rate(recs, model=m),
        }
        for m in models
    }


if __name__ == "__main__":
    import json
    from telemetry.trace import Tracer

    t = Tracer()
    recs = list(t.iter_records())
    print(json.dumps({
        "total_records": len(recs),
        "global_latency": latency_percentiles(recs),
        "global_tokens": token_totals(recs),
        "global_cost": cost_totals(recs),
        "global_errors": error_rate(recs),
    }, indent=2))
