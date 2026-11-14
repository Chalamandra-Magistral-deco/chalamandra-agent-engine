"""Adquisición de flock con timeout acotado (F-7).

Antes, todos los flock() de los ledgers usaban LOCK_EX bloqueante sin
timeout: un poseedor colgado (proceso muerto sin liberar, bug) bloqueaba
indefinidamente las escrituras del ledger afectado. Ahora la espera es
acotada (LOCK_NB + reintento con límite) y el llamador recibe TimeoutError
—subclase de OSError, así que los `except OSError` existentes lo tratan
como fallo cerrado— en lugar de quedarse colgado para siempre.

Timeouts elegidos: 10 s para los ledgers (las operaciones bajo lock son
cortas: append + fsync + actualización del índice); 5 s para trazas
(telemetría que no debe bloquear respuestas HTTP; log_message ya trata
OSError como error observable). No hay sitio que exija espera indefinida:
todos los poseedores legítimos liberan en milisegundos.
"""
from __future__ import annotations

import fcntl
import time

DEFAULT_LEDGER_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_TRACE_LOCK_TIMEOUT_SECONDS = 5.0


def acquire_exclusive_lock(
    fileno: int,
    *,
    timeout_seconds: float = DEFAULT_LEDGER_LOCK_TIMEOUT_SECONDS,
    poll_interval_seconds: float = 0.05,
) -> None:
    """Acquire an exclusive flock, bounded; raise TimeoutError on expiry.

    Raises:
        ValueError: si el timeout no es positivo.
        TimeoutError: si el lock no se adquiere dentro del plazo (subclase
            de OSError: los llamadores que capturan OSError fallan cerrados).
        OSError: si flock falla por otro motivo.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds debe ser positivo")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds debe ser positivo")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"fcntl lock no adquirido en {timeout_seconds}s"
                ) from None
            time.sleep(poll_interval_seconds)
