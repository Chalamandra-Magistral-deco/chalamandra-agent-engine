"""Barrido reproducible de parámetros del sistema dinámico."""

from __future__ import annotations

from dataclasses import dataclass

from dynamics.analyze import analyze_trajectory
from dynamics.model import CoupledDelayModel
from dynamics.stability import (
    analyze_equilibrium,
    hopf_boundary,
)


@dataclass(frozen=True)
class SweepResult:
    """
    Resumen inmutable de una simulación y sus diagnósticos locales.

    Attributes:
        alpha (float): Parámetro de acoplamiento biológico.
        gamma (float): Parámetro de acoplamiento sintético.
        tau (float): Retardo usado en el modelo.
        p_bio (float): Equilibrio del canal biológico.
        p_sint (float): Equilibrio del canal sintético.
        effective_coupling (float): Acoplamiento efectivo evaluado.
        local_hopf_possible (bool): Indica si el criterio local permite Hopf.
        tau_hopf (float | None): Retardo crítico cuando aplica.
        omega_hopf (float | None): Frecuencia angular crítica cuando aplica.
        amplitude_bio (float): Amplitud medida en el canal biológico.
        amplitude_sint (float): Amplitud medida en el canal sintético.
        period_bio (float | None): Periodo detectado, si existe.
        frequency_bio (float | None): Frecuencia detectada en Hz, si existe.

    Note:
        El objeto resume una simulación local; no realiza efectos fuera del proceso.
    """
    alpha: float
    gamma: float
    tau: float

    p_bio: float
    p_sint: float

    effective_coupling: float

    local_hopf_possible: bool
    tau_hopf: float | None
    omega_hopf: float | None

    amplitude_bio: float
    amplitude_sint: float

    period_bio: float | None
    frequency_bio: float | None


def _extract_signal_stats(
    analysis: dict,
    candidates: tuple[str, ...],
) -> dict:
    """Obtiene estadísticas tolerando nombres de señal distintos."""

    for key in candidates:
        value = analysis.get(key)

        if isinstance(value, dict):
            return value

    raise KeyError(
        f"No se encontró ninguna señal compatible: {candidates}. "
        f"Claves disponibles: {list(analysis.keys())}"
    )


def run_case(
    alpha: float,
    gamma: float,
    tau: float,
    *,
    a: float = 1.0,
    b: float = 1.0,
    K: float = 1.0,
    duration: float = 60.0,
    dt: float = 0.001,
) -> SweepResult:
    """Ejecuta análisis local + Hopf + simulación temporal."""

    # --------------------------------------------------------
    # 1. EQUILIBRIO NO LINEAL
    # --------------------------------------------------------

    eq = analyze_equilibrium(
        a=a,
        b=b,
        alpha=alpha,
        gamma=gamma,
        tau=tau,
        K=K,
    )

    # --------------------------------------------------------
    # 2. HOPF LOCAL
    # --------------------------------------------------------

    threshold = a * b

    tau_hopf, omega_hopf = hopf_boundary(
        a=a,
        b=b,
        alpha_gamma=eq.effective_coupling,
        n=0,
    )

    local_hopf_possible = (
        abs(eq.effective_coupling) > threshold
    )

    # --------------------------------------------------------
    # 3. SIMULACIÓN
    # --------------------------------------------------------

    model = CoupledDelayModel(
        a=a,
        b=b,
        alpha=alpha,
        gamma=gamma,
        tau=tau,
        K=K,
    )

    trajectory = model.simulate(
        duration=duration,
        dt=dt,
    )

    analysis = analyze_trajectory(trajectory)

    # --------------------------------------------------------
    # 4. ESTADÍSTICAS
    # --------------------------------------------------------

    bio_data = _extract_signal_stats(
        analysis,
        (
            "P_bio",
            "bio",
            "p_bio",
            "Pbio",
        ),
    )

    sint_data = _extract_signal_stats(
        analysis,
        (
            "P_sint",
            "sint",
            "p_sint",
            "Psint",
        ),
    )

    return SweepResult(
        alpha=alpha,
        gamma=gamma,
        tau=tau,

        p_bio=eq.p_bio,
        p_sint=eq.p_sint,

        effective_coupling=eq.effective_coupling,

        local_hopf_possible=local_hopf_possible,
        tau_hopf=tau_hopf if local_hopf_possible else None,
        omega_hopf=omega_hopf if local_hopf_possible else None,

        amplitude_bio=float(
            bio_data.get("amplitude", 0.0)
        ),
        amplitude_sint=float(
            sint_data.get("amplitude", 0.0)
        ),

        period_bio=bio_data.get("period"),
        frequency_bio=bio_data.get("frequency_hz"),
    )
