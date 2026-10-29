"""Puente entre Master y el motor dynamics.

Interfaz: dict estructurado, aplanado y estable.
El Master consume el resultado sin conocer la estructura interna de analyze.py.
"""

from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass

from dynamics.analyze import analyze_trajectory
from dynamics.model import CoupledDelayModel
from dynamics.stability import (
    analyze_equilibrium,
    local_hopf_boundary,
)
from dynamics.sweep import run_case


MAX_SIMULATION_STEPS = 1_000_000


def _as_dict(obj):
    """
    Convierte una salida dinámica conocida a un formato serializable cuando es posible.

    Args:
        obj (Any): Objeto devuelto por el análisis dinámico, un dataclass u otro valor.

    Returns:
        Any: `None`, un diccionario convertido con `asdict`, el diccionario original o un envoltorio `{"value": obj}` según el tipo de entrada.

    Raises:
        TypeError: Puede propagar errores de `dataclasses.asdict` para objetos incompatibles.

    Side effects:
        No realiza simulación ni persistencia; prepara una representación en memoria.

    Example:
        payload = _as_dict(result)
    """

    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    return {"value": obj}


def _flatten_analysis(analysis: dict) -> dict:
    """Extrae los campos clave del canal bio y aplana la estructura."""
    bio = analysis.get("bio", {}) or {}
    sint = analysis.get("sint", {}) or {}
    return {
        "regime":         bio.get("regime"),
        "lambda_num":     bio.get("lambda_num"),
        "amplitude_bio":  bio.get("amplitude_final", bio.get("amplitude")),
        "amplitude_sint": sint.get("amplitude_final", sint.get("amplitude")),
        "period":         bio.get("period"),
        "frequency_hz":   bio.get("frequency_hz"),
        "zero_crossings": bio.get("zero_crossings"),
        "raw":            analysis,
    }


def analyze_dynamics(
    *,
    a: float,
    b: float,
    alpha: float,
    gamma: float,
    tau: float,
    K: float = 1.0,
    duration: float = 60.0,
    dt: float = 0.001,
) -> dict:
    """Simula y devuelve un resultado estructurado y aplanado."""

    params = {
        "a": a, "b": b, "alpha": alpha, "gamma": gamma,
        "tau": tau, "K": K, "duration": duration, "dt": dt,
    }

    try:
        if isinstance(duration, bool) or isinstance(dt, bool):
            raise ValueError("duration y dt deben ser números finitos positivos.")

        duration_value = float(duration)
        dt_value = float(dt)
        if not math.isfinite(duration_value) or duration_value <= 0:
            raise ValueError("duration debe ser finito y mayor que cero.")
        if not math.isfinite(dt_value) or dt_value <= 0:
            raise ValueError("dt debe ser finito y mayor que cero.")

        ratio = duration_value / dt_value
        if not math.isfinite(ratio):
            raise ValueError("duration/dt es demasiado grande para simularse.")
        if math.ceil(ratio) > MAX_SIMULATION_STEPS:
            raise ValueError(
                f"La herramienta dynamics admite como máximo "
                f"{MAX_SIMULATION_STEPS} pasos por simulación."
            )

        equilibrium = analyze_equilibrium(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        hopf = local_hopf_boundary(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        model = CoupledDelayModel(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        trajectory = model.simulate(duration=duration_value, dt=dt_value)
        analysis = analyze_trajectory(trajectory)
    except Exception as e:
        return {"status": "error", "error": repr(e), "params": params}

    return {
        "status": "ok",
        "params": params,
        "equilibrium": _as_dict(equilibrium),
        "hopf": _as_dict(hopf),
        "analysis": _flatten_analysis(analysis),
    }


def sweep_dynamics(*, cases: list[dict]) -> list[dict]:
    """Ejecuta múltiples configuraciones de dynamics."""
    results: list[dict] = []
    for case in cases:
        try:
            r = run_case(**case)
            results.append(_as_dict(r))
        except Exception as e:
            results.append({"status": "error", "case": case, "error": repr(e)})
    return results
