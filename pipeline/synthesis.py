"""Motor determinista de síntesis multi-modelo.

Estrategias:
    vote                -> grupo mayoritario por similitud
    longest             -> output más largo
    highest_confidence  -> mayor confidence auto-reportada

No utiliza LLM adicional.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adapters.base import ModelResponse
from pipeline.comparison import similarity


@dataclass
class SynthesisResult:
    """
    Resultado de combinar salidas de varios modelos.

    Attributes:
        output (str): Texto elegido por la estrategia.
        strategy (str): Estrategia aplicada: `vote`, `longest` o `highest_confidence`.
        contributors (list[str]): Modelos que contribuyeron al resultado seleccionado.
        confidence (float): Métrica de consenso o confianza autodeclarada; no implica
            calibración estadística.
        rationale (str): Explicación de la selección.
        errors (list[str]): Errores como ausencia de respuestas válidas o estrategia desconocida.

    Note:
        El motor no invoca un LLM adicional. `highest_confidence` se basa en una
        confianza declarada por el proveedor y puede usar `longest` como fallback.
    """
    output: str
    strategy: str
    contributors: list[str]
    confidence: float
    rationale: str
    errors: list[str] = field(default_factory=list)


def _ok_only(
    responses: dict[str, ModelResponse],
) -> dict[str, ModelResponse]:
    """Conserva únicamente respuestas exitosas."""
    return {
        model: response
        for model, response in responses.items()
        if response.status.value == "ok" and response.output.strip()
    }


def _empty_result(
    strategy: str,
    error: str,
    rationale: str,
) -> SynthesisResult:
    """
    Construye una respuesta de síntesis explícitamente vacía y con error explicativo.

    Args:
        strategy (str): Estrategia solicitada.
        error (str): Código/motivo del resultado vacío.
        rationale (str): Explicación visible de por qué no se seleccionó una salida.

    Returns:
        SynthesisResult: Resultado con output vacío, contribuciones vacías y el error documentado.

    Raises:
        No genera excepciones de dominio para cadenas válidas.

    Side effects:
        Ninguno; no llama modelos ni persiste el resultado.

    Example:
        result = _empty_result("vote", "no_valid_responses", "No hubo respuestas exitosas")
    """

    return SynthesisResult(
        output="",
        strategy=strategy,
        contributors=[],
        confidence=0.0,
        rationale=rationale,
        errors=[error],
    )


def _vote(
    responses: dict[str, ModelResponse],
    threshold: float = 0.9,
) -> SynthesisResult:
    """Selecciona el grupo mayoritario por similitud."""
    ok = _ok_only(responses)

    if not ok:
        return _empty_result(
            "vote",
            "all_models_failed",
            "Sin respuestas válidas",
        )

    models = list(ok.keys())
    visited: set[str] = set()
    groups: list[list[str]] = []

    for model in models:
        if model in visited:
            continue

        group = [model]
        visited.add(model)

        for other in models:
            if other in visited:
                continue

            score = similarity(
                ok[model].output,
                ok[other].output,
            )

            if score >= threshold:
                group.append(other)
                visited.add(other)

        groups.append(group)

    groups.sort(key=len, reverse=True)

    winner_group = groups[0]
    representative = winner_group[0]

    total = len(models)
    consensus_ratio = len(winner_group) / total

    return SynthesisResult(
        output=ok[representative].output,
        strategy="vote",
        contributors=winner_group,
        confidence=round(consensus_ratio, 4),
        rationale=(
            f"{len(winner_group)}/{total} modelos coinciden "
            f"(threshold={threshold})"
        ),
    )


def _longest(
    responses: dict[str, ModelResponse],
) -> SynthesisResult:
    """Selecciona el output válido más largo."""
    ok = _ok_only(responses)

    if not ok:
        return _empty_result(
            "longest",
            "all_models_failed",
            "Sin respuestas válidas",
        )

    winner = max(
        ok.items(),
        key=lambda item: len(item[1].output),
    )

    model, response = winner

    return SynthesisResult(
        output=response.output,
        strategy="longest",
        contributors=[model],
        confidence=0.5,
        rationale=(
            f"Output más largo: "
            f"{len(response.output)} chars de {model}"
        ),
    )


def _highest_confidence(
    responses: dict[str, ModelResponse],
) -> SynthesisResult:
    """Selecciona la respuesta con mayor confianza auto-reportada."""
    ok = _ok_only(responses)

    if not ok:
        return _empty_result(
            "highest_confidence",
            "all_models_failed",
            "Sin respuestas válidas",
        )

    with_confidence = [
        (model, response)
        for model, response in ok.items()
        if response.confidence_self_reported is not None
    ]

    if not with_confidence:
        result = _longest(ok)
        result.strategy = "highest_confidence"
        result.rationale = (
            "Ningún modelo reportó confidence; "
            "fallback a longest"
        )
        return result

    model, response = max(
        with_confidence,
        key=lambda item: item[1].confidence_self_reported or 0.0,
    )

    confidence = response.confidence_self_reported or 0.0

    return SynthesisResult(
        output=response.output,
        strategy="highest_confidence",
        contributors=[model],
        confidence=confidence,
        rationale=f"Mayor confidence auto-reportada: {model}",
    )


_STRATEGIES = {
    "vote": _vote,
    "longest": _longest,
    "highest_confidence": _highest_confidence,
}


def synthesize(
    responses: dict[str, ModelResponse],
    strategy: str = "vote",
) -> SynthesisResult:
    """
    Combina respuestas exitosas con una estrategia determinista.

    Args:
        responses (dict[str, ModelResponse]): Respuestas por alias de modelo.
        strategy (str): `vote` agrupa por similitud, `longest` elige el texto más
            extenso, `highest_confidence` usa confianza autodeclarada.

    Returns:
        SynthesisResult: Resultado seleccionado; para estrategia desconocida o
        ausencia de respuestas válidas devuelve output vacío y registra el error.

    Raises:
        Propaga errores inesperados de la estrategia o del cálculo de similitud.

    Side effects:
        Ninguno externo; no hace llamadas a modelos ni persiste resultados.

    Example:
        result = synthesize(responses, strategy="vote")
    """
    fn = _STRATEGIES.get(strategy)

    if fn is None:
        return _empty_result(
            strategy,
            "unknown_strategy",
            f"Estrategia desconocida: {strategy}",
        )

    return fn(responses)
