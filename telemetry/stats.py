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
    """
    Calcula estadísticas de latencia para registros de trazas.

    Args:
        records (Iterable[dict]): Registros que pueden contener `model` y `latency_ms`.
        model (str | None): Si se indica, limita el cálculo a ese modelo.

    Returns:
        dict: `count` y, si hay latencias, p50/p95/p99, media entera, mínimo y máximo.
        Devuelve `{"count": 0}` cuando no hay latencias válidas no nulas.

    Raises:
        KeyError: Si un registro que incluye `latency_ms` verdadero no contiene ese campo
        (la propia selección normalmente evita este caso); otros errores de datos pueden
        propagarse desde la agregación estadística.

    Side effects:
        Ninguno; consume los registros y calcula en memoria.

    Example:
        report = latency_percentiles(tracer.iter_records(), model="ollama")
    """
    recs = _filter(records, model)
    latencies = [r["latency_ms"] for r in recs if r.get("latency_ms")]
    if not latencies:
        return {"count": 0}
    latencies.sort()
    n = len(latencies)
    def pct(p: float) -> int:
        """
        Selecciona la latencia más cercana al percentil discreto solicitado.

        Args:
            p (float): Percentil numérico, normalmente entre 0 y 100.

        Returns:
            int: Valor observado de latencia en el índice redondeado y limitado al rango.

        Raises:
            No genera excepciones para los percentiles usados por la función exterior.

        Side effects:
            Ninguno; lee la lista de latencias ordenada en el ámbito exterior.
        """
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
    """
    Suma tokens de entrada y salida de los registros seleccionados.

    Args:
        records (Iterable[dict]): Registros que pueden incluir `model`, `tokens_in` y `tokens_out`.
        model (str | None): Filtro opcional por nombre de modelo.

    Returns:
        dict: Cantidad de registros, tokens de entrada, tokens de salida y total.

    Raises:
        TypeError: Si los valores de tokens no se pueden sumar como números compatibles.

    Side effects:
        Ninguno; es una agregación en memoria.

    Example:
        totals = token_totals(tracer.iter_records())
    """
    recs = _filter(records, model)
    tin = sum(r.get("tokens_in", 0) for r in recs)
    tout = sum(r.get("tokens_out", 0) for r in recs)
    return {"count": len(recs), "tokens_in": tin, "tokens_out": tout, "tokens_total": tin + tout}


def cost_totals(records: Iterable[dict], model: str | None = None) -> dict:
    """
    Suma el coste reportado por los registros seleccionados.

    Args:
        records (Iterable[dict]): Registros con posible clave `cost_usd`.
        model (str | None): Filtro opcional por modelo.

    Returns:
        dict: Cantidad de registros y coste total en USD redondeado a seis decimales.

    Raises:
        TypeError: Si un valor de coste no es compatible con la suma numérica.

    Side effects:
        Ninguno; no consulta tarifas ni estima costes ausentes, que se tratan como cero.

    Example:
        costs = cost_totals(tracer.iter_records(), model="gpt")
    """
    recs = _filter(records, model)
    total = sum(r.get("cost_usd", 0.0) for r in recs)
    return {"count": len(recs), "cost_usd": round(total, 6)}


def error_rate(records: Iterable[dict], model: str | None = None) -> dict:
    """
    Calcula la proporción de registros cuyo estado no es `ok`.

    Args:
        records (Iterable[dict]): Registros con campos opcionales `model` y `status`.
        model (str | None): Filtro opcional por modelo.

    Returns:
        dict: `count`, `errors` y `error_rate` redondeado a cuatro decimales. Para una
        colección vacía, el conteo, los errores y la tasa son cero.

    Raises:
        No genera excepciones deliberadas; errores de iteración pueden propagarse.

    Side effects:
        Ninguno.

    Example:
        errors = error_rate(tracer.iter_records())
    """
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
    """
    Agrega métricas de latencia, tokens, coste y errores por modelo.

    Args:
        records (Iterable[dict]): Conjunto de registros que se reutilizará en cada cálculo.
        models (list[str]): Nombres de modelo que deben aparecer en el resultado.

    Returns:
        dict: Mapa por modelo con submapas `latency`, `tokens`, `cost` y `errors`.

    Raises:
        Puede propagar errores de formato de registro procedentes de las funciones de agregación.

    Side effects:
        Materializa `records` una vez en memoria; no accede al disco ni a proveedores.

    Example:
        report = compare_models(tracer.iter_records(), ["ollama", "gemini"])
    """
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
