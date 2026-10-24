"""Análisis cuantitativo de trayectorias del sistema dinámico."""

from __future__ import annotations

import numpy as np


def _estimate_growth_rate(
    time: np.ndarray,
    signal: np.ndarray,
    *,
    windows: int = 4,
) -> float | None:
    """
    Estima λ_num mediante regresión lineal de log(amplitud)
    contra tiempo de cada ventana.

        A(t) ~ exp(λ t)

        λ = d(log A) / dt

    Devuelve None si no hay suficiente señal para estimarlo.
    """

    if len(time) < windows * 10:
        return None

    total_duration = float(time[-1] - time[0])

    if total_duration <= 0:
        return None

    edges = np.linspace(
        time[0],
        time[-1],
        windows + 1,
    )

    amplitudes: list[float] = []
    centers: list[float] = []

    for start, end in zip(edges[:-1], edges[1:]):
        mask = (
            (time >= start)
            & (time < end)
        )

        segment = signal[mask]

        if len(segment) < 10:
            continue

        amplitude = float(
            (np.max(segment) - np.min(segment)) / 2.0
        )

        if amplitude <= 1e-12:
            continue

        amplitudes.append(amplitude)
        centers.append(
            float((start + end) / 2.0)
        )

    if len(amplitudes) < 2:
        return None

    log_amplitudes = np.log(
        np.asarray(amplitudes, dtype=float)
    )

    times = np.asarray(
        centers,
        dtype=float,
    )

    slope, _ = np.polyfit(
        times,
        log_amplitudes,
        1,
    )

    return float(slope)


def _classify_regime(
    growth_rate: float | None,
    *,
    threshold: float = 1e-3,
) -> str:
    """Clasifica el régimen dinámico según λ_num."""

    if growth_rate is None:
        return "UNRESOLVED"

    if growth_rate < -threshold:
        return "DECAYING"

    if growth_rate > threshold:
        return "GROWING"

    return "CRITICAL"


def _final_amplitude(
    time: np.ndarray,
    signal: np.ndarray,
    *,
    fraction: float = 0.25,
) -> float:
    """Amplitud medida únicamente en la ventana final."""

    if not 0 < fraction <= 1:
        raise ValueError("fraction debe estar en (0, 1]")

    cutoff = time[-1] - (
        time[-1] - time[0]
    ) * fraction

    final = signal[time >= cutoff]

    if len(final) == 0:
        return 0.0

    return float(
        (np.max(final) - np.min(final)) / 2.0
    )


def analyze_trajectory(
    trajectory: np.ndarray,
    *,
    growth_windows: int = 4,
    regime_threshold: float = 1e-3,
) -> dict:
    """
    Analiza una trayectoria con columnas:

        tiempo, P_bio, P_sint

    Mantiene la API original y añade:

        lambda_num
        regime
        amplitude_final
        frequency_hz
        period
    """

    try:
        trajectory = np.asarray(trajectory, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("trajectory debe contener datos numéricos") from exc

    if trajectory.ndim != 2 or trajectory.shape[1] != 3:
        raise ValueError(
            "trajectory debe tener forma (N, 3): "
            "tiempo, P_bio, P_sint"
        )
    if not np.all(np.isfinite(trajectory)):
        raise ValueError("trajectory no puede contener NaN ni infinitos")
    if (
        isinstance(growth_windows, bool)
        or not isinstance(growth_windows, (int, np.integer))
        or growth_windows < 2
    ):
        raise ValueError("growth_windows debe ser un entero >= 2")
    try:
        regime_threshold = float(regime_threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("regime_threshold debe ser finito y >= 0") from exc
    if not np.isfinite(regime_threshold) or regime_threshold < 0:
        raise ValueError("regime_threshold debe ser finito y >= 0")

    time = trajectory[:, 0]
    bio = trajectory[:, 1]
    sint = trajectory[:, 2]

    if len(time) < 3:
        raise ValueError(
            "Se necesitan al menos 3 muestras"
        )
    if np.any(np.diff(time) <= 0):
        raise ValueError("La columna tiempo debe ser estrictamente creciente")

    dt = float(
        np.mean(np.diff(time))
    )

    def channel_stats(
        signal: np.ndarray,
    ) -> dict:

        amplitude = float(
            (np.max(signal) - np.min(signal)) / 2.0
        )

        mean = float(
            np.mean(signal)
        )

        centered = signal - mean

        crossings = np.where(
            (centered[:-1] <= 0)
            & (centered[1:] > 0)
        )[0]

        if len(crossings) >= 2:
            periods = np.diff(
                time[crossings]
            )

            period = float(
                np.mean(periods)
            )

            frequency = (
                1.0 / period
                if period > 0
                else None
            )
        else:
            period = None
            frequency = None

        growth_rate = _estimate_growth_rate(
            time,
            signal,
            windows=growth_windows,
        )

        regime = _classify_regime(
            growth_rate,
            threshold=regime_threshold,
        )

        return {
            "min": float(
                np.min(signal)
            ),
            "max": float(
                np.max(signal)
            ),
            "amplitude": amplitude,
            "amplitude_final": _final_amplitude(
                time,
                signal,
            ),
            "mean": mean,

            # Mantener compatibilidad con la API anterior.
            "zero_crossings": int(
                len(crossings)
            ),

            "period": period,
            "frequency_hz": frequency,

            "lambda_num": growth_rate,
            "regime": regime,
        }

    return {
        "samples": int(
            len(time)
        ),
        "duration": float(
            time[-1] - time[0]
        ),
        "dt": dt,
        "bio": channel_stats(bio),
        "sint": channel_stats(sint),
    }
