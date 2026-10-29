#!/usr/bin/env python3
"""One-shot reconciliation of verified business-cycle acceptances back to CatedralX."""
from __future__ import annotations

import json
from api.catedralx_feedback import reconcile_feedback_once


def main() -> int:
    """
    Ejecuta una única reconciliación de aceptaciones verificadas hacia CatedralX.

    Args:
        None; la configuración y los datos de entrada se resuelven dentro del módulo
        `api.catedralx_feedback`.

    Returns:
        int: 0 si la reconciliación termina sin errores registrados; 1 si una excepción
        prevista ocurre o el resultado informa errores.

    Raises:
        Las excepciones `OSError`, `ValueError`, `TypeError` y `RuntimeError` de la
        reconciliación se capturan y se convierten en estado de salida 1.

    Side effects:
        Ejecuta una reconciliación puntual y puede actualizar el estado persistente de
        feedback según el contrato de `reconcile_feedback_once`; no hace un bucle continuo.

    Example:
        code = main()
    """
    try:
        result = reconcile_feedback_once()
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:400]}))
        return 1
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
