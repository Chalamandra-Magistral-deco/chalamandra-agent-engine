"""Análisis de estabilidad global y local del sistema dinámico."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import root


# ============================================================
# ESTRUCTURAS
# ============================================================

@dataclass(frozen=True)
class Equilibrium:
    """Equilibrio no lineal y sus magnitudes derivadas."""

    p_bio: float
    p_sint: float
    residual: float
    alpha_eff: float
    gamma_eff: float
    effective_coupling: float


# ============================================================
# ANALISIS DEL MODELO LINEALIZADO EN EL ORIGEN
# ============================================================

def coupling_threshold(a: float, b: float) -> float:
    """Umbral algebraico |alpha*gamma| = a*b."""

    if a <= 0 or b <= 0:
        raise ValueError("a y b deben ser > 0")

    return a * b


def hopf_frequency(
    a: float,
    b: float,
    alpha_gamma: float,
) -> float | None:
    """
    Frecuencia candidata de Hopf.

    Para:

        (s+a)(s+b) - alpha_gamma*exp(-2*s*tau) = 0

    devuelve omega > 0 si:

        |alpha_gamma| > a*b
    """

    if a <= 0 or b <= 0:
        raise ValueError("a y b deben ser > 0")

    k = alpha_gamma

    if abs(k) <= a * b:
        return None

    discriminant = (
        (a * a - b * b) ** 2
        + 4.0 * k * k
    )

    omega_sq = (
        math.sqrt(discriminant)
        - (a * a + b * b)
    ) / 2.0

    if omega_sq <= 0:
        return None

    return math.sqrt(omega_sq)


def hopf_boundary(
    a: float,
    b: float,
    alpha_gamma: float,
    n: int = 0,
) -> tuple[float | None, float | None]:
    """Calcula tau_c y omega para la rama n de Hopf."""

    if a <= 0 or b <= 0:
        raise ValueError("a y b deben ser > 0")

    if n < 0:
        raise ValueError("n debe ser >= 0")

    omega = hopf_frequency(a, b, alpha_gamma)

    if omega is None:
        return None, None

    k = alpha_gamma

    theta = math.atan2(
        (a + b) * omega,
        a * b - omega * omega,
    )

    if k > 0:
        phase = (
            2.0 * math.pi - theta
        ) % (2.0 * math.pi)
    else:
        phase = (
            math.pi - theta
        ) % (2.0 * math.pi)

    if phase == 0:
        phase = 2.0 * math.pi

    tau = (
        phase + 2.0 * math.pi * n
    ) / (2.0 * omega)

    return tau, omega


# ============================================================
# EQUILIBRIO NO LINEAL
# ============================================================

def solve_equilibrium(
    a: float,
    b: float,
    alpha: float,
    gamma: float,
    K: float = 1.0,
    initial_guess: tuple[float, float] = (1.0, 1.0),
) -> tuple[float, float]:
    """
    Resuelve el equilibrio:

        0 = -a*P_bio + alpha*tanh(P_sint/K)
        0 = -b*P_sint + gamma*tanh(P_bio/K)

    `initial_guess=(0,0)` recupera el equilibrio trivial.
    """

    if a <= 0 or b <= 0:
        raise ValueError("a y b deben ser > 0")

    if K <= 0:
        raise ValueError("K debe ser > 0")

    def equations(x: np.ndarray) -> np.ndarray:
        p_bio, p_sint = x

        return np.array(
            [
                -a * p_bio
                + alpha * math.tanh(p_sint / K),

                -b * p_sint
                + gamma * math.tanh(p_bio / K),
            ],
            dtype=float,
        )

    result = root(
        equations,
        np.asarray(initial_guess, dtype=float),
    )

    if not result.success:
        raise RuntimeError(
            f"No se encontró equilibrio: {result.message}"
        )

    p_bio, p_sint = map(float, result.x)

    residual_vector = equations(
        np.array([p_bio, p_sint], dtype=float)
    )

    residual = float(
        np.linalg.norm(residual_vector)
    )

    if not math.isfinite(residual):
        raise RuntimeError(
            "Residual no finito al resolver equilibrio"
        )

    return p_bio, p_sint


def _sech2(x: float) -> float:
    """sech(x)^2 calculada de forma estable."""
    t = math.tanh(x)
    return 1.0 - t * t


def analyze_equilibrium(
    a: float,
    b: float,
    alpha: float,
    gamma: float,
    tau: float,
    K: float = 1.0,
    initial_guess: tuple[float, float] = (1.0, 1.0),
) -> Equilibrium:
    """
    Analiza estabilidad local alrededor del equilibrio no lineal.

    La linealización local reemplaza:

        alpha -> alpha_eff
        gamma -> gamma_eff

    con las derivadas de tanh evaluadas en el equilibrio.
    """

    if tau < 0:
        raise ValueError("tau debe ser >= 0")

    p_bio, p_sint = solve_equilibrium(
        a=a,
        b=b,
        alpha=alpha,
        gamma=gamma,
        K=K,
        initial_guess=initial_guess,
    )

    alpha_eff = (
        alpha
        / K
        * _sech2(p_sint / K)
    )

    gamma_eff = (
        gamma
        / K
        * _sech2(p_bio / K)
    )

    effective_coupling = (
        alpha_eff * gamma_eff
    )

    residual_bio = (
        -a * p_bio
        + alpha * math.tanh(p_sint / K)
    )

    residual_sint = (
        -b * p_sint
        + gamma * math.tanh(p_bio / K)
    )

    residual = math.hypot(
        residual_bio,
        residual_sint,
    )

    return Equilibrium(
        p_bio=p_bio,
        p_sint=p_sint,
        residual=residual,
        alpha_eff=alpha_eff,
        gamma_eff=gamma_eff,
        effective_coupling=effective_coupling,
    )


def local_hopf_boundary(
    a: float,
    b: float,
    alpha: float,
    gamma: float,
    tau: float,
    K: float = 1.0,
    initial_guess: tuple[float, float] = (1.0, 1.0),
    branch: int = 0,
) -> dict[str, float | bool | None]:
    """
    Calcula la frontera Hopf local alrededor del equilibrio encontrado.

    No afirma estabilidad global; describe la linealización local.
    """

    eq = analyze_equilibrium(
        a=a,
        b=b,
        alpha=alpha,
        gamma=gamma,
        tau=tau,
        K=K,
        initial_guess=initial_guess,
    )

    threshold = coupling_threshold(a, b)

    hopf_tau, omega = hopf_boundary(
        a=a,
        b=b,
        alpha_gamma=eq.effective_coupling,
        n=branch,
    )

    return {
        "p_bio": eq.p_bio,
        "p_sint": eq.p_sint,
        "residual": eq.residual,
        "alpha_eff": eq.alpha_eff,
        "gamma_eff": eq.gamma_eff,
        "effective_coupling": eq.effective_coupling,
        "threshold": threshold,
        "hopf_possible": (
            abs(eq.effective_coupling) > threshold
        ),
        "tau": tau,
        "tau_hopf": hopf_tau,
        "omega_hopf": omega,
    }
