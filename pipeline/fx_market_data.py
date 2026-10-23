"""Daily reference FX data from Frankfurter; not an intraday trading feed.

Rates are fetched on demand and kept in memory. This module never stores
credentials, creates orders, or writes local files.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx


API_URL = "https://api.frankfurter.dev/v2/rates"
DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_OBSERVATIONS = 500


def fetch_daily_fx_history(
    *,
    base_currency: str = "EUR",
    quote_currency: str = "USD",
    observations: int = 60,
    max_staleness_days: int = 7,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    today: date | None = None,
) -> dict[str, Any]:
    """Fetch validated daily reference rates; default 60 observations of EUR/USD."""
    base = str(base_currency).strip().upper()
    quote = str(quote_currency).strip().upper()

    if not re.fullmatch(r"[A-Z]{3}", base) or not re.fullmatch(r"[A-Z]{3}", quote):
        raise ValueError("Las divisas deben ser códigos de tres letras, como EUR y USD.")
    if base == quote:
        raise ValueError("La divisa base y la cotizada deben ser distintas.")
    if isinstance(observations, bool) or not isinstance(observations, int):
        raise ValueError("observations debe ser un entero.")
    if not 30 <= observations <= MAX_OBSERVATIONS:
        raise ValueError(f"observations debe estar entre 30 y {MAX_OBSERVATIONS}.")
    if isinstance(max_staleness_days, bool) or not isinstance(max_staleness_days, int):
        raise ValueError("max_staleness_days debe ser un entero.")
    if not 0 <= max_staleness_days <= 30:
        raise ValueError("max_staleness_days debe estar entre 0 y 30.")
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("timeout_seconds debe estar entre 0 y 30.")

    end_date = today or datetime.now(timezone.utc).date()
    if not isinstance(end_date, date):
        raise ValueError("today debe ser una fecha.")
    # Daily rates normally appear on business days; use a generous calendar window.
    start_date = end_date - timedelta(days=max(120, observations * 3))

    try:
        response = httpx.get(
            API_URL,
            params={
                "base": base,
                "quotes": quote,
                "from": start_date.isoformat(),
                "to": end_date.isoformat(),
            },
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "Chalamandra-Agent-Engine/0.1"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"No se pudo consultar la fuente diaria Frankfurter: {exc}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("Respuesta inválida de Frankfurter: se esperaba una lista.")

    by_date: dict[date, float] = {}
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise RuntimeError(f"Registro {index} inválido en la respuesta de Frankfurter.")
        try:
            observation_date = date.fromisoformat(str(item["date"]))
            item_base = str(item["base"]).upper()
            item_quote = str(item["quote"]).upper()
            rate = float(item["rate"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"Registro {index} incompleto en la respuesta de Frankfurter.") from exc

        if item_base != base or item_quote != quote:
            raise RuntimeError("Frankfurter devolvió un par de divisas distinto al solicitado.")
        if observation_date > end_date:
            continue
        if not math.isfinite(rate) or rate <= 0:
            raise RuntimeError(f"Tipo de cambio inválido para {observation_date.isoformat()}.")
        by_date[observation_date] = rate

    records = [
        {"date": observed.isoformat(), "rate": by_date[observed]}
        for observed in sorted(by_date)
    ]
    if len(records) < observations:
        raise RuntimeError(
            f"Frankfurter devolvió {len(records)} observaciones válidas; "
            f"se requieren {observations}."
        )

    selected = records[-observations:]
    latest_date = date.fromisoformat(str(selected[-1]["date"]))
    age_days = (end_date - latest_date).days
    if age_days > max_staleness_days:
        raise RuntimeError(
            f"El último dato es del {latest_date.isoformat()} "
            f"({age_days} días de antigüedad); supera el límite de "
            f"{max_staleness_days} días."
        )

    return {
        "status": "ok",
        "source": "Frankfurter",
        "symbol": f"{base}/{quote}",
        "base_currency": base,
        "quote_currency": quote,
        "interval": "daily_reference",
        "intraday": False,
        "as_of_date": latest_date.isoformat(),
        "age_days": age_days,
        "observation_count": len(selected),
        "observations": selected,
        "prices": [record["rate"] for record in selected],
        "trading_enabled": False,
    }
