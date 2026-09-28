"""Solver numérico del sistema acoplado con retardo."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class CoupledDelayModel:
    """
    Sistema:

        dP_bio/dt  = -a P_bio
                     + alpha*tanh(P_sint(t-tau)/K)

        dP_sint/dt = -b P_sint
                     + gamma*tanh(P_bio(t-tau)/K)

    Integración temporal mediante Euler explícito.
    """

    a: float
    b: float
    alpha: float
    gamma: float
    tau: float
    K: float = 1.0

    def simulate(
        self,
        duration: float,
        dt: float = 0.001,
        p_bio0: float = 0.1,
        p_sint0: float = 0.0,
    ) -> np.ndarray:

        if self.a <= 0 or self.b <= 0:
            raise ValueError("a y b deben ser > 0")

        if self.tau < 0:
            raise ValueError("tau debe ser >= 0")

        if self.K <= 0:
            raise ValueError("K debe ser > 0")

        if duration <= 0:
            raise ValueError("duration debe ser > 0")

        if dt <= 0:
            raise ValueError("dt debe ser > 0")

        steps = int(round(duration / dt))
        delay_steps = int(round(self.tau / dt))

        bio = np.empty(steps + delay_steps + 1)
        sint = np.empty(steps + delay_steps + 1)

        bio[: delay_steps + 1] = p_bio0
        sint[: delay_steps + 1] = p_sint0

        for i in range(delay_steps, steps + delay_steps):
            delayed_index = i - delay_steps

            delayed_sint = sint[delayed_index]
            delayed_bio = bio[delayed_index]

            current_bio = bio[i]
            current_sint = sint[i]

            dbio = (
                -self.a * current_bio
                + self.alpha * np.tanh(delayed_sint / self.K)
            )

            dsint = (
                -self.b * current_sint
                + self.gamma * np.tanh(delayed_bio / self.K)
            )

            bio[i + 1] = current_bio + dt * dbio
            sint[i + 1] = current_sint + dt * dsint

        start = delay_steps

        times = np.arange(steps + 1) * dt

        return np.column_stack(
            (
                times,
                bio[start : start + steps + 1],
                sint[start : start + steps + 1],
            )
        )
