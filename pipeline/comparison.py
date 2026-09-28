"""Comparación de respuestas multi-modelo.

Sin embeddings ni LLMs externos: comparación léxica simple.
Objetivo: detectar consenso, disenso y outliers.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from adapters.base import ModelResponse


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def similarity(a: str, b: str) -> float:
    """Ratio de similitud entre dos textos (0.0 a 1.0)."""
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def pairwise_similarity(responses: dict[str, ModelResponse]) -> dict[tuple[str, str], float]:
    """Similitud entre cada par de modelos."""
    models = list(responses.keys())
    out: dict[tuple[str, str], float] = {}
    for i in range(len(models)):
        for j in range(i + 1, len(models)):
            a, b = models[i], models[j]
            out[(a, b)] = round(similarity(responses[a].output, responses[b].output), 4)
    return out


def consensus_score(responses: dict[str, ModelResponse]) -> float:
    """Media de similitud de todos los pares. 1.0 = consenso total, 0.0 = caos."""
    pairs = pairwise_similarity(responses)
    if not pairs:
        return 0.0
    return round(sum(pairs.values()) / len(pairs), 4)


def detect_outliers(
    responses: dict[str, ModelResponse],
    threshold: float = 0.3,
) -> list[str]:
    """Modelos cuyo output se aleja del resto por debajo del umbral."""
    pairs = pairwise_similarity(responses)
    if not pairs:
        return []
    models = list(responses.keys())
    outliers: list[str] = []
    for m in models:
        sims = [v for (a, b), v in pairs.items() if m in (a, b)]
        if sims and sum(sims) / len(sims) < threshold:
            outliers.append(m)
    return outliers


def summarize_comparison(responses: dict[str, ModelResponse]) -> dict:
    """Resumen estructurado de la comparación."""
    return {
        "models": list(responses.keys()),
        "consensus_score": consensus_score(responses),
        "pairwise_similarity": {f"{a}↔{b}": v for (a, b), v in pairwise_similarity(responses).items()},
        "outliers": detect_outliers(responses),
        "statuses": {m: r.status.value for m, r in responses.items()},
    }
