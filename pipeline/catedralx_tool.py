"""Puente opcional hacia CatedralX: precios explícitos o referencia FX diaria.

No crea/ejecuta órdenes. La fuente Frankfurter es de referencia diaria, no intradía.

Configuración:
- CATEDRALX_PATH: raíz del checkout CatedralX (opcional si es repo hermano).
- CATEDRALX_STATE_PATH: archivo externo para el estado homeostático persistente.
"""
from __future__ import annotations

import importlib
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from pipeline.fx_market_data import fetch_daily_fx_history


_ENGINE_LOCK = threading.RLock()
_ENGINE_MODULE: Any | None = None
_ENGINE_ROOT: Path | None = None
_ENGINE_STATE_PATH: Path | None = None
_MIN_PRICE_POINTS = 30


def _resolve_root(catedralx_path: str | None) -> Path:
    configured = catedralx_path or os.getenv("CATEDRALX_PATH")
    candidate = (
        Path(configured).expanduser()
        if configured
        else Path(__file__).resolve().parents[2] / "CatedralX"
    )
    try:
        root = candidate.resolve(strict=True)
    except OSError as exc:
        raise FileNotFoundError(
            "No encuentro CatedralX. Configura CATEDRALX_PATH con la ruta del repositorio."
        ) from exc

    if not (root / "elite" / "REASON" / "orchestrator.py").is_file():
        raise FileNotFoundError(
            f"La ruta no contiene elite/REASON/orchestrator.py: {root}"
        )
    return root


def _resolve_state_path(state_path: str | None) -> Path:
    configured = state_path or os.getenv("CATEDRALX_STATE_PATH")
    candidate = (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".local" / "state" / "chalamandra" / "catedralx" / "hormonas.json"
    )
    return candidate.resolve()


def _load_catedralx_module(root: Path) -> Any:
    global _ENGINE_MODULE, _ENGINE_ROOT

    if _ENGINE_MODULE is not None:
        if _ENGINE_ROOT != root:
            raise RuntimeError(
                "Ya se cargó otra copia de CatedralX en este proceso; "
                "reinicia el proceso para cambiar CATEDRALX_PATH."
            )
        return _ENGINE_MODULE

    elite_root = (root / "elite").resolve()
    expected_reason = (elite_root / "REASON").resolve()
    existing = sys.modules.get("REASON")
    if existing is not None:
        existing_paths = {
            Path(value).resolve()
            for value in getattr(existing, "__path__", [])
        }
        if expected_reason not in existing_paths:
            raise RuntimeError(
                "Ya existe un paquete REASON procedente de otra ruta; "
                "no cargaré una versión ambigua de CatedralX."
            )

    path_value = str(elite_root)
    inserted_path = path_value not in sys.path
    modules_before = {
        name for name in sys.modules
        if name == "REASON" or name.startswith("REASON.")
    }
    if inserted_path:
        sys.path.insert(0, path_value)

    try:
        module = importlib.import_module("REASON.orchestrator")
    except Exception as exc:
        for name in list(sys.modules):
            if (
                (name == "REASON" or name.startswith("REASON."))
                and name not in modules_before
            ):
                sys.modules.pop(name, None)
        if inserted_path and path_value in sys.path:
            sys.path.remove(path_value)
        raise RuntimeError(
            f"No se pudo importar CatedralX ({type(exc).__name__}): {exc}"
        ) from exc

    _ENGINE_MODULE = module
    _ENGINE_ROOT = root
    return module


def _prepare_engine(module: Any, state_path: Path) -> None:
    global _ENGINE_STATE_PATH

    if _ENGINE_STATE_PATH == state_path:
        return

    engine = getattr(module, "_think_orchestrator", None)
    processor_type = getattr(module, "TransductorEndocrino", None)
    if engine is None or processor_type is None:
        raise RuntimeError("La interfaz de razonamiento de CatedralX no es compatible.")

    # Estado persistente fuera del código fuente; por defecto, ~/.local/state/...
    # Al crear el procesador, el estado se carga desde state_path si existe.
    engine.transductor = processor_type(output_path=state_path)
    _ENGINE_STATE_PATH = state_path


def _finite_number(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} debe ser numérico y finito.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} debe ser numérico y finito.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} debe ser numérico y finito.")
    return number


def _prices_with_current(
    historical_prices: Sequence[float] | None,
    current_price: float | None,
) -> tuple[list[float], float]:
    if historical_prices is None or isinstance(historical_prices, (str, bytes)):
        raise ValueError("historical_prices es obligatorio y debe ser una lista de precios.")

    try:
        raw_prices = list(historical_prices)
    except TypeError as exc:
        raise ValueError("historical_prices debe ser una secuencia de precios.") from exc

    prices: list[float] = []
    for index, value in enumerate(raw_prices):
        price = _finite_number(f"historical_prices[{index}]", value)
        if price <= 0:
            raise ValueError(f"historical_prices[{index}] debe ser mayor que cero.")
        prices.append(price)

    if current_price is None:
        if not prices:
            raise ValueError("Proporciona current_price o al menos un precio histórico.")
        current = prices[-1]
    else:
        current = _finite_number("current_price", current_price)
        if current <= 0:
            raise ValueError("current_price debe ser mayor que cero.")

    if not prices or prices[-1] != current:
        prices.append(current)

    if len(prices) < _MIN_PRICE_POINTS:
        raise ValueError(
            f"CatedralX necesita al menos {_MIN_PRICE_POINTS} precios explícitos; "
            f"se recibieron {len(prices)}. No se generarán datos sintéticos."
        )
    return prices, current


def catedralx_reasoning_tool(
    *,
    historical_prices: Sequence[float] | None = None,
    current_price: float | None = None,
    gap: float = 0.0,
    inertia: float = 0.0,
    asset: str = "unspecified",
    cycle_id: int | None = None,
    catedralx_path: str | None = None,
    state_path: str | None = None,
    data_source: str = "provided",
    base_currency: str = "EUR",
    quote_currency: str = "USD",
    observations: int = 60,
    max_staleness_days: int = 7,
) -> dict[str, Any]:
    """Analiza precios explícitos o referencia FX diaria; jamás envía órdenes."""
    try:
        source = str(data_source).strip().lower()
        market_metadata: dict[str, Any] = {}
        if source == "frankfurter_daily":
            if historical_prices is not None or current_price is not None:
                raise ValueError(
                    "No combines datos manuales con data_source='frankfurter_daily'."
                )
            market_metadata = fetch_daily_fx_history(
                base_currency=base_currency,
                quote_currency=quote_currency,
                observations=observations,
                max_staleness_days=max_staleness_days,
            )
            historical_prices = market_metadata["prices"]
            current_price = market_metadata["prices"][-1]
            if asset == "unspecified":
                asset = market_metadata["symbol"]
        elif source != "provided":
            raise ValueError(
                "data_source debe ser 'provided' o 'frankfurter_daily'."
            )
        prices, current = _prices_with_current(historical_prices, current_price)
        gap_value = _finite_number("gap", gap)
        inertia_value = _finite_number("inertia", inertia)
        cycle = int(time.time()) if cycle_id is None else int(cycle_id)

        root = _resolve_root(catedralx_path)
        persisted_state = _resolve_state_path(state_path)
        if persisted_state == root or root in persisted_state.parents:
            raise ValueError(
                "CATEDRALX_STATE_PATH debe estar fuera del repositorio para proteger sus archivos."
            )

        with _ENGINE_LOCK:
            module = _load_catedralx_module(root)
            _prepare_engine(module, persisted_state)
            sensory_input = module.SensoryInput(
                precio_historico=prices,
                precio_actual=current,
                gap=gap_value,
                inercia=inertia_value,
            )
            state = module.think(sensory_input, prices, cycle)

        perception = getattr(state, "percepcion_espacial", {}) or {}
        decision_value = str(getattr(state, "decision", "HOLD"))
        regime_value = str(getattr(state, "regimen", "INDEFINIDO"))
        signal_value = str(getattr(state, "señal_combinada", "lateral"))
        decisions_value = list(getattr(state, "decisiones", []) or [])
        financial_analysis = perception.get("analisis_financiero") or {}
        if not isinstance(financial_analysis, dict):
            financial_analysis = {}
        financial_risk_flags = financial_analysis.get("risk_flags", [])
        if financial_risk_flags:
            consistency_warnings = [
                "La capa financiera detectó factores de riesgo que requieren revisión: "
                + ", ".join(str(flag) for flag in financial_risk_flags)
                + "."
            ]
        else:
            consistency_warnings = []
        directional_decisions = {
            "buy", "sell", "comprar", "vender",
            "entrar_largo", "entrar_corto", "entrada_larga", "entrada_corta",
        }
        directional_signal = decision_value.strip().lower() in directional_decisions
        if directional_signal and not decisions_value:
            consistency_warnings.append(
                "El motor emitió una decisión direccional, pero la lista de decisiones validadas está vacía."
            )
        if directional_signal and signal_value.strip().lower() in {
            "lateral", "hold", "indefinido", "undefined", "",
        }:
            consistency_warnings.append(
                "La decisión direccional contradice la señal combinada del motor."
            )
        if directional_signal and regime_value.strip().upper() in {
            "INDEFINIDO", "UNDEFINED", "UNKNOWN", "",
        }:
            consistency_warnings.append(
                "El motor emitió una decisión direccional sin un régimen definido."
            )
        analysis_status = "review_required" if consistency_warnings else "ok"
        return {
            "status": analysis_status,
            "engine": "CatedralX",
            "asset": str(asset).strip() or "unspecified",
            "timestamp": getattr(state, "timestamp", None),
            "cycle_id": cycle,
            "decision": decision_value,
            "confidence": float(getattr(state, "confianza", 0.0)),
            "confidence_type": "heuristic_signal_strength_not_probability",
            "signal_strength": float(getattr(state, "confianza", 0.0)),
            "signal_strength_is_probability": False,
            "decision_reason": financial_analysis.get("decision_reason"),
            "financial_analysis": financial_analysis or None,
            "regime": regime_value,
            "combined_signal": signal_value,
            "signal_consistent": not consistency_warnings,
            "consistency_warnings": consistency_warnings,
            "trading_eligible": False,
            "free_energy": float(getattr(state, "free_energy", 0.0)),
            "hamiltonian": float(getattr(state, "hamiltoniano", 0.0)),
            "cognitive_energy": float(getattr(state, "energia_cognitiva", 0.0)),
            "hurst": perception.get("hurst"),
            "neurovector": getattr(state, "neurovector", []),
            "decisions": decisions_value,
            "state_saved_to": str(persisted_state),
            "data_source": source,
            "market_data": (
                {
                    "source": market_metadata.get("source"),
                    "symbol": market_metadata.get("symbol"),
                    "interval": market_metadata.get("interval"),
                    "intraday": market_metadata.get("intraday", False),
                    "as_of_date": market_metadata.get("as_of_date"),
                    "observation_count": market_metadata.get("observation_count"),
                    "age_days": market_metadata.get("age_days"),
                }
                if market_metadata
                else {"source": "caller_supplied", "interval": "unspecified"}
            ),
            "orders_created": False,
            "orders_executed": False,
            "note": (
                "Se usaron tipos de cambio de referencia diarios de Frankfurter; "
                "no son cotizaciones intradía. No se creó ni envió ninguna orden."
                if market_metadata
                else "Se usaron precios proporcionados por el llamador. "
                "No se consultó un mercado en vivo ni se creó o envió una orden."
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "engine": "CatedralX",
            "error": str(exc),
            "signal_consistent": False,
            "trading_eligible": False,
            "orders_created": False,
            "orders_executed": False,
        }
