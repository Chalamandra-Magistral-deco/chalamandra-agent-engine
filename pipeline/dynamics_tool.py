"""Puente entre Master y el motor dynamics.

Interfaz: dict estructurado, aplanado y estable.
El Master consume el resultado sin conocer la estructura interna de analyze.py.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from dynamics.analyze import analyze_trajectory
from dynamics.model import CoupledDelayModel
from dynamics.stability import (
    analyze_equilibrium,
    local_hopf_boundary,
)
from dynamics.sweep import run_case


def _as_dict(obj):
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
        equilibrium = analyze_equilibrium(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        hopf = local_hopf_boundary(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        model = CoupledDelayModel(
            a=a, b=b, alpha=alpha, gamma=gamma, tau=tau, K=K,
        )
        trajectory = model.simulate(duration=duration, dt=dt)
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
