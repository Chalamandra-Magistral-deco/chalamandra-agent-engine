"""Análisis local y determinista de informes CSV de términos de búsqueda.

La herramienta genera candidatos para revisión. No escribe en Google Ads
ni realiza cambios en campañas.
"""

from __future__ import annotations

import csv
import math
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence


VALID_PROFILES = {
    "direct-response",
    "prospecting",
    "incremental-profit",
}

ALIASES = {
    "query": (
        "search term",
        "search terms",
        "query",
        "consulta de busqueda",
        "termino de busqueda",
        "terminos de busqueda",
    ),
    "cost": (
        "cost",
        "coste",
        "costo",
        "coste total",
        "costo total",
    ),
    "conversions": (
        "conversions",
        "conversion",
        "conversiones",
    ),
    "conversion_value": (
        "conv value",
        "conv value",
        "conversion value",
        "valor de conversion",
        "valor conversion",
        "valor conv",
    ),
}


def _norm(value: str) -> str:
    """Normaliza encabezados y términos para comparaciones."""
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _find_column(
    headers: Sequence[str],
    aliases: tuple[str, ...],
    *,
    label: str,
    explicit: str | None = None,
    required: bool = True,
) -> str | None:
    normalized = {_norm(header): header for header in headers}

    if explicit:
        if explicit in headers:
            return explicit
        match = normalized.get(_norm(explicit))
        if match:
            return match
        raise ValueError(
            f"No se encontró la columna configurada para {label}: "
            f"{explicit!r}. Columnas: {headers}"
        )

    for alias in aliases:
        match = normalized.get(_norm(alias))
        if match:
            return match

    # Admite variantes como "Cost (account currency)".
    prefixes = tuple(_norm(alias) for alias in aliases)
    candidates = [
        header
        for header in headers
        if any(
            _norm(header).startswith(prefix + " ")
            for prefix in prefixes
        )
    ]
    if candidates:
        return min(candidates, key=len)

    if required:
        raise ValueError(
            f"Falta la columna requerida para {label}. "
            f"Columnas disponibles: {headers}"
        )

    return None


def _parse_number(value: Any, *, row_number: int, column: str) -> float:
    """Lee números CSV con formatos frecuentes: 1,234.56 y 1.234,56."""
    raw = "" if value is None else str(value).strip()

    if raw in {"", "-", "—", "–"}:
        return 0.0

    parenthesized_negative = raw.startswith("(") and raw.endswith(")")
    cleaned = re.sub(r"[^0-9,.\-+]", "", raw)

    if not cleaned or cleaned in {".", ",", "-", "+"}:
        raise ValueError(
            f"Número inválido en fila {row_number}, "
            f"columna {column!r}: {raw!r}"
        )

    if "," in cleaned and "." in cleaned:
        # El último separador suele ser el decimal.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        chunks = cleaned.split(",")
        if len(chunks) == 2 and len(chunks[-1]) != 3:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(".") > 1:
        whole, decimal = cleaned.rsplit(".", 1)
        cleaned = whole.replace(".", "") + "." + decimal

    try:
        result = float(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"Número inválido en fila {row_number}, "
            f"columna {column!r}: {raw!r}"
        ) from exc

    if parenthesized_negative:
        result = -abs(result)

    if not math.isfinite(result):
        raise ValueError(
            f"Número no finito en fila {row_number}, columna {column!r}"
        )

    return result


def _allowed_root() -> Path:
    configured = os.getenv("CHALAMANDRA_SEARCH_TERMS_DIR")
    raw = (
        Path(configured).expanduser()
        if configured
        else Path.home() / "workspace/data/ads/search-terms"
    )
    return raw.resolve()


def _resolve_csv(csv_path: str) -> Path:
    root = _allowed_root()
    requested = Path(csv_path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    resolved = candidate.resolve(strict=True)

    if not resolved.is_relative_to(root):
        raise ValueError(
            f"El CSV debe estar dentro del directorio autorizado: {root}"
        )

    if not resolved.is_file():
        raise ValueError("La ruta proporcionada no es un archivo regular.")

    return resolved


def mine_search_terms_tool(
    *,
    csv_path: str,
    profile: str,
    cost_floor: float,
    min_conversions: float = 1.0,
    query_column: str | None = None,
    cost_column: str | None = None,
    conversions_column: str | None = None,
    conversion_value_column: str | None = None,
    existing_keywords: list[str] | None = None,
    existing_negative_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Analiza un CSV y devuelve candidatos de keywords, negativas y n-grams.

    Parámetros obligatorios:
      - csv_path: archivo dentro del directorio autorizado.
      - profile: direct-response, prospecting o incremental-profit.
      - cost_floor: gasto mínimo para proponer una consulta como negativa.

    Las listas de keywords existentes son opcionales. Sin ellas, los
    resultados siguen siendo candidatos y requieren deduplicación manual.
    """
    try:
        profile = str(profile).strip().lower()
        if profile not in VALID_PROFILES:
            raise ValueError(
                f"Perfil inválido: {profile!r}. "
                f"Usa uno de {sorted(VALID_PROFILES)}."
            )

        cost_floor = float(cost_floor)
        min_conversions = float(min_conversions)

        if not math.isfinite(cost_floor) or cost_floor <= 0:
            raise ValueError("cost_floor debe ser un número finito mayor que 0.")

        if not math.isfinite(min_conversions) or min_conversions <= 0:
            raise ValueError("min_conversions debe ser mayor que 0.")

        source = _resolve_csv(csv_path)

        with source.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)

            try:
                dialect = csv.Sniffer().sniff(
                    sample,
                    delimiters=",;\t",
                )
            except csv.Error:
                dialect = csv.excel

            reader = csv.DictReader(stream, dialect=dialect)
            headers = reader.fieldnames

            if not headers:
                raise ValueError("El CSV está vacío o no tiene encabezados.")

            query_col = _find_column(
                headers,
                ALIASES["query"],
                label="consulta",
                explicit=query_column,
            )
            cost_col = _find_column(
                headers,
                ALIASES["cost"],
                label="coste",
                explicit=cost_column,
            )
            conv_col = _find_column(
                headers,
                ALIASES["conversions"],
                label="conversiones",
                explicit=conversions_column,
            )
            value_col = _find_column(
                headers,
                ALIASES["conversion_value"],
                label="valor de conversión",
                explicit=conversion_value_column,
                required=False,
            )
            # _find_column valida estas columnas en tiempo de ejecución, pero
            # su tipo de retorno contempla también las columnas opcionales.
            assert query_col is not None
            assert cost_col is not None
            assert conv_col is not None

            rows: list[dict[str, Any]] = []
            skipped_empty_query = 0

            for row_number, row in enumerate(reader, start=2):
                query = str(row.get(query_col) or "").strip()
                if not query:
                    skipped_empty_query += 1
                    continue

                cost = _parse_number(
                    row.get(cost_col),
                    row_number=row_number,
                    column=cost_col,
                )
                conversions = _parse_number(
                    row.get(conv_col),
                    row_number=row_number,
                    column=conv_col,
                )
                conversion_value = (
                    _parse_number(
                        row.get(value_col),
                        row_number=row_number,
                        column=value_col,
                    )
                    if value_col
                    else None
                )

                if cost < 0 or conversions < 0:
                    raise ValueError(
                        f"Fila {row_number}: coste y conversiones "
                        "no pueden ser negativos en este informe."
                    )

                if conversion_value is not None and conversion_value < 0:
                    raise ValueError(
                        f"Fila {row_number}: valor de conversión negativo."
                    )

                rows.append({
                    "query": query,
                    "normalized_query": _norm(query),
                    "cost": cost,
                    "conversions": conversions,
                    "conversion_value": conversion_value,
                })

        keywords_provided = existing_keywords is not None
        negatives_provided = existing_negative_keywords is not None

        keyword_set = {
            _norm(item) for item in (existing_keywords or []) if str(item).strip()
        }
        negative_set = {
            _norm(item)
            for item in (existing_negative_keywords or [])
            if str(item).strip()
        }

        add_candidates: list[dict[str, Any]] = []
        negative_candidates: list[dict[str, Any]] = []
        already_keyword_count = 0
        already_negative_count = 0

        waste_rows: list[dict[str, Any]] = []

        for row in rows:
            query = row["query"]
            normalized = row["normalized_query"]
            cost = row["cost"]
            conversions = row["conversions"]

            if conversions >= min_conversions:
                if normalized in keyword_set:
                    already_keyword_count += 1
                else:
                    add_candidates.append({
                        "query": query,
                        "cost": round(cost, 2),
                        "conversions": conversions,
                        "conversion_value": (
                            round(row["conversion_value"], 2)
                            if row["conversion_value"] is not None
                            else None
                        ),
                        "action": (
                            "ADD_KEYWORD"
                            if keywords_provided
                            else "ADD_KEYWORD_CANDIDATE"
                        ),
                        "match_type": "exact_candidate",
                        "deduplication": (
                            "checked_against_provided_keywords"
                            if keywords_provided
                            else "manual_deduplication_required"
                        ),
                    })

            if conversions == 0 and cost >= cost_floor:
                waste_rows.append(row)

                if normalized in negative_set:
                    already_negative_count += 1
                else:
                    negative_candidates.append({
                        "query": query,
                        "cost": round(cost, 2),
                        "conversions": conversions,
                        "match_type": "exact",
                        "level": "campaign_or_ad_group_review",
                        "action": (
                            "NEGATE_CANDIDATE"
                            if negatives_provided
                            else "NEGATIVE_KEYWORD_CANDIDATE"
                        ),
                        "deduplication": (
                            "checked_against_provided_negatives"
                            if negatives_provided
                            else "manual_deduplication_required"
                        ),
                    })

        # Cada n-gram recibe el coste de una consulta una sola vez,
        # aunque el mismo término aparezca repetido dentro de esa consulta.
        ngrams: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"cost": 0.0, "rows": 0}
        )

        for row in waste_rows:
            tokens = re.findall(r"[^\W_]+", row["query"].casefold())
            found: set[str] = set()

            for size in (1, 2, 3):
                for start in range(max(0, len(tokens) - size + 1)):
                    found.add(" ".join(tokens[start:start + size]))

            for gram in found:
                ngrams[gram]["cost"] += row["cost"]
                ngrams[gram]["rows"] += 1

        ngram_report = [
            {
                "ngram": gram,
                "measured_cost": round(values["cost"], 2),
                "query_rows": values["rows"],
            }
            for gram, values in sorted(
                ngrams.items(),
                key=lambda item: (-item[1]["cost"], item[0]),
            )[:50]
        ]

        total_spend = sum(row["cost"] for row in rows)
        waste_spend = sum(row["cost"] for row in waste_rows)
        total_conversions = sum(row["conversions"] for row in rows)

        conversion_values_available = value_col is not None
        total_conversion_value = (
            sum(row["conversion_value"] or 0.0 for row in rows)
            if conversion_values_available
            else None
        )

        warnings = [
            "Los n-grams pueden solaparse; sus costes no se deben sumar entre sí.",
            "No se realizan cambios en Google Ads.",
            "No se ha calculado una puntuación S: faltan los benchmarks "
            "auxiliares de la skill.",
        ]

        if not keywords_provided:
            warnings.append(
                "No se proporcionó la estructura actual de keywords; "
                "las altas son candidatos pendientes de deduplicación."
            )
        if not negatives_provided:
            warnings.append(
                "No se proporcionó la lista actual de negativas; "
                "las negativas son candidatos pendientes de deduplicación."
            )
        if not conversion_values_available:
            warnings.append(
                "No existe columna de valor de conversión; "
                "no se calculará valor/ gasto."
            )

        return {
            "status": "ok",
            "source_file": source.name,
            "profile": profile,
            "columns": {
                "query": query_col,
                "cost": cost_col,
                "conversions": conv_col,
                "conversion_value": value_col,
            },
            "measurement": "Measured from CSV",
            "parameters": {
                "cost_floor_user_provided": cost_floor,
                "min_conversions_user_provided": min_conversions,
            },
            "counts": {
                "rows_processed": len(rows),
                "rows_skipped_empty_query": skipped_empty_query,
                "add_candidates": len(add_candidates),
                "negative_candidates": len(negative_candidates),
                "already_keyword_rows": already_keyword_count,
                "already_negative_rows": already_negative_count,
            },
            "summary": {
                "measured_spend": round(total_spend, 2),
                "measured_conversions": total_conversions,
                "measured_waste_spend": round(waste_spend, 2),
                "measured_waste_spend_share": (
                    round(waste_spend / total_spend, 6)
                    if total_spend > 0
                    else None
                ),
                "measured_conversion_value": (
                    round(total_conversion_value, 2)
                    if total_conversion_value is not None
                    else None
                ),
                "measured_conversion_value_over_spend": (
                    round(total_conversion_value / total_spend, 6)
                    if total_conversion_value is not None and total_spend > 0
                    else None
                ),
            },
            "maintenance_diff": {
                "add": add_candidates,
                "negate": negative_candidates,
                "move": [],
                "move_note": (
                    "No se proporcionó la estructura de ad groups; "
                    "no se recomiendan movimientos automáticamente."
                ),
            },
            "ngram_waste": ngram_report,
            "roas_s": {
                "profile": profile,
                "score": None,
                "score_status": "NOT_SCORED_BENCHMARKS_UNAVAILABLE",
                "notes": (
                    "Se muestran indicadores medidos, no una puntuación S "
                    "inventada. Cargar los benchmarks antes de puntuar."
                ),
            },
            "warnings": warnings,
        }

    except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as exc:
        return {
            "status": "error",
            "error": str(exc),
        }
