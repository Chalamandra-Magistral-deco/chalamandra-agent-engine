"""Solver numérico del sistema acoplado con retardo.

La historia del sistema para t <= 0 se considera constante e igual a las
condiciones iniciales. Para retardos no alineados con la malla temporal se
interpola linealmente la historia conocida, en vez de redondear tau/dt.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass
class CoupledDelayModel:
    """Modelo de dos estados acoplados mediante un retardo temporal.

    Sistema:
        dP_bio/dt  = -a P_bio + alpha*tanh(P_sint(t-tau)/K)
        dP_sint/dt = -b P_sint + gamma*tanh(P_bio(t-tau)/K)

    Se usa Euler explícito con paso efectivo <= dt. La salida conserva la API
    ndarray (N, 3): tiempo, P_bio, P_sint; incluye exactamente t=0 y t=duration.
    """

    a: float
    b: float
    alpha: float
    gamma: float
    tau: float
    K: float = 1.0

    @staticmethod
    def _finite_float(name: str, value: float) -> float:
        """Convierte un escalar a float y rechaza valores no finitos."""
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} debe ser un número finito") from exc
        if not math.isfinite(result):
            raise ValueError(f"{name} debe ser un número finito")
        return result

    def simulate(
        self,
        duration: float,
        dt: float = 0.001,
        p_bio0: float = 0.1,
        p_sint0: float = 0.0,
        *,
        max_steps: int = 5_000_000,
    ) -> np.ndarray:
        """Simula la trayectoria con retardo interpolado.

        Args:
            duration: horizonte de simulación (> 0).
            dt: máximo paso de integración (> 0).
            p_bio0: estado inicial e historia pasada de P_bio.
            p_sint0: estado inicial e historia pasada de P_sint.
            max_steps: límite de seguridad para evitar asignaciones accidentales
                de memoria excesiva; se puede aumentar explícitamente.

        Raises:
            ValueError: si los parámetros son inválidos o se supera max_steps.
            FloatingPointError: si la integración genera estados no finitos.
        """
        a = self._finite_float("a", self.a)
        b = self._finite_float("b", self.b)
        alpha = self._finite_float("alpha", self.alpha)
        gamma = self._finite_float("gamma", self.gamma)
        tau = self._finite_float("tau", self.tau)
        K = self._finite_float("K", self.K)
        duration = self._finite_float("duration", duration)
        dt = self._finite_float("dt", dt)
        p_bio0 = self._finite_float("p_bio0", p_bio0)
        p_sint0 = self._finite_float("p_sint0", p_sint0)

        if a <= 0 or b <= 0:
            raise ValueError("a y b deben ser > 0")
        if tau < 0:
            raise ValueError("tau debe ser >= 0")
        if K <= 0:
            raise ValueError("K debe ser > 0")
        if duration <= 0:
            raise ValueError("duration debe ser > 0")
        if dt <= 0:
            raise ValueError("dt debe ser > 0")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps debe ser un entero > 0")

        ratio = duration / dt
        if not math.isfinite(ratio):
            raise ValueError("duration/dt es demasiado grande para simularse")

        # ceil mantiene todo el horizonte pedido y nunca supera el dt máximo.
        steps = max(1, math.ceil(ratio))
        if steps > max_steps:
            raise ValueError(
                f"La simulación requiere {steps} pasos; max_steps={max_steps}. "
                "Aumenta max_steps explícitamente o usa un dt mayor."
            )

        step = duration / steps
        times = np.linspace(0.0, duration, steps + 1, dtype=float)
        bio = np.empty(steps + 1, dtype=float)
        sint = np.empty(steps + 1, dtype=float)
        bio[0] = p_bio0
        sint[0] = p_sint0

        def delayed_value(history: np.ndarray, current_index: int, t_delay: float, initial: float) -> float:
            """Evalúa una historia conocida en t_delay por interpolación lineal."""
            if t_delay <= 0.0:
                return initial

            # t_delay nunca debe superar el tiempo actual; el clamp absorbe
            # exclusivamente pequeñas desviaciones de redondeo en coma flotante.
            position = min(float(current_index), max(0.0, t_delay / step))
            left = int(math.floor(position))
            right = min(left + 1, current_index)
            fraction = position - left
            return float(history[left] + fraction * (history[right] - history[left]))

        for i in range(steps):
            current_bio = float(bio[i])
            current_sint = float(sint[i])
            delayed_time = float(times[i] - tau)

            delayed_sint = delayed_value(sint, i, delayed_time, p_sint0)
            delayed_bio = delayed_value(bio, i, delayed_time, p_bio0)

            dbio = -a * current_bio + alpha * math.tanh(delayed_sint / K)
            dsint = -b * current_sint + gamma * math.tanh(delayed_bio / K)

            next_bio = current_bio + step * dbio
            next_sint = current_sint + step * dsint
            if not math.isfinite(next_bio) or not math.isfinite(next_sint):
                raise FloatingPointError(
                    f"Estado no finito en el paso {i + 1}; revisa dt y los parámetros"
                )
            bio[i + 1] = next_bio
            sint[i + 1] = next_sint

        return np.column_stack((times, bio, sint))
