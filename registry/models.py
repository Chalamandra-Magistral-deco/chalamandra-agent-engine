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
            # Verificado con tests/test_ollama.py y test_orchestrator.py
            "reasoning",
        },
        skills_claimed={
            "coding",
            "analysis",
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
            "note": "adapters/gemini.py escrito, sin API key ni ejecución real",
        },
    ),

    "gpt": ModelInfo(
        name="gpt",
        provider="openai",
        skills_claimed={"reasoning", "coding", "analysis"},
        evidence={"note": "sin adaptador ni ejecución"},
    ),

    "claude": ModelInfo(
        name="claude",
        provider="anthropic",
        skills_claimed={"documents", "reasoning", "synthesis"},
        evidence={"note": "sin adaptador ni ejecución"},
    ),

    "deepseek": ModelInfo(
        name="deepseek",
        provider="deepseek",
        skills_claimed={"coding", "reasoning"},
        evidence={"note": "sin adaptador ni ejecución"},
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
