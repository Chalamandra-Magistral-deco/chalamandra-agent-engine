"""
Registro de modelos disponibles y sus capacidades.

REGLA DURA:
  - skills_verified → solo con evidencia local (tests, benchmarks propios)
  - skills_claimed  → documentadas por el proveedor, NO verificadas aquí

No convertir claimed en verified sin correr benchmark real.
"""

from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    """
    Metadatos y evidencia de las capacidades de un modelo.

    Attributes:
        name (str): Alias del modelo en el registro.
        provider (str): Proveedor del adapter.
        local (bool): Indica si el modelo se ejecuta localmente.
        versions (list[str]): Versiones o identificadores declarados.
        skills_verified (set[str]): Skills respaldadas por pruebas/benchmarks locales.
        skills_claimed (set[str]): Skills declaradas por el proveedor, aún no verificadas localmente.
        evidence (dict[str, str]): Notas y referencias de soporte.

    Invariant:
        Una skill reclamada no debe promoverse a verificada sin evidencia de una prueba local.
    """
    name: str
    provider: str
    local: bool = False
    versions: list[str] = field(default_factory=list)
    skills_verified: set[str] = field(default_factory=set)
    skills_claimed: set[str] = field(default_factory=set)
    evidence: dict[str, str] = field(default_factory=dict)

    def all_skills(self) -> set[str]:
        """Unión de verificadas y reclamadas."""
        return self.skills_verified | self.skills_claimed

    def is_verified_for(self, skill: str) -> bool:
        """
        Comprueba si el registro contiene evidencia local para una skill.

        Args:
            skill (str): Nombre exacto de la skill.

        Returns:
            bool: `True` solo si la skill pertenece a `skills_verified`.

        Raises:
            No genera excepciones deliberadas para nombres de skill tipo string.

        Example:
            verified = model_info.is_verified_for("reasoning")
        """
        return skill in self.skills_verified


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

MODELS: dict[str, ModelInfo] = {

    # -----------------------------------------------------------------------
    # Ollama (local, verificado en VM)
    # -----------------------------------------------------------------------
    "ollama": ModelInfo(
        name="ollama",
        provider="ollama",
        local=True,
        versions=["qwen2.5:3b"],
        skills_verified={
            # Verificado por la suite actual del adapter/orchestrator.
            "reasoning",
            "analysis",
        },
        skills_claimed={
            "coding",
        },
        evidence={
            "reasoning": "tests/test_ollama.py verde 2026-09-28, latencia warm ~1.8-3.4s",
        },
    ),

    # -----------------------------------------------------------------------
    # Modelos en la nube (estructura presente, sin ejecutar)
    # -----------------------------------------------------------------------
    "gemini": ModelInfo(
        name="gemini",
        provider="google",
        versions=["gemini-3.8-flash"],
        skills_claimed={
            "research",
            "reasoning",
            "vision",
            "web",
        },
        evidence={
            "note": "credencial puede estar configurada; sin ejecución real ni benchmark local",
        },
    ),

    "gpt": ModelInfo(
        name="gpt",
        provider="openai",
        versions=["gpt-6-luna"],
        skills_claimed={"reasoning", "coding", "analysis"},
        evidence={
            "note": "adapter integrado; pendiente de ejecución/benchmark",
        },
    ),

    "claude": ModelInfo(
        name="claude",
        provider="anthropic",
        versions=["claude-sonnet-5-5"],
        skills_claimed={"documents", "reasoning", "synthesis"},
        evidence={
            "note": "adapter integrado; pendiente de ejecución/benchmark",
        },
    ),

    "deepseek": ModelInfo(
        name="deepseek",
        provider="deepseek",
        versions=["deepseek-flash"],
        skills_claimed={"coding", "reasoning"},
        evidence={
            "note": "adapter integrado; pendiente de ejecución/benchmark",
        },
    ),

    "qwen": ModelInfo(
        name="qwen",
        provider="qwen",
        skills_claimed={"reasoning", "coding"},
        evidence={"note": "sin adaptador ni ejecución"},
    ),
}


def get_model(name: str) -> ModelInfo | None:
    """Devuelve la info de un modelo, o None si no existe."""
    return MODELS.get(name)


def models_with_verified_skill(skill: str) -> list[str]:
    """Modelos que tienen evidencia local para una skill."""
    return [n for n, m in MODELS.items() if m.is_verified_for(skill)]


def models_with_claimed_skill(skill: str) -> list[str]:
    """Modelos que reclaman una skill (sin evidencia local)."""
    return [n for n, m in MODELS.items() if skill in m.skills_claimed]
