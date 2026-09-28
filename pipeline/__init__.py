"""Pipeline de orquestación multi-modelo."""

from pipeline.parallel import run_on_models

from pipeline.comparison import (
    consensus_score,
    detect_outliers,
    pairwise_similarity,
    summarize_comparison,
)

from pipeline.synthesis import (
    SynthesisResult,
    synthesize,
)

__all__ = [
    "run_on_models",
    "consensus_score",
    "detect_outliers",
    "pairwise_similarity",
    "summarize_comparison",
    "SynthesisResult",
    "synthesize",
]
