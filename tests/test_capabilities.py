from adapters.base import Skill
from adapters.ollama import OllamaAdapter
from core.orchestrator import Orchestrator
from registry.models import MODELS


def test_ollama_adapter_and_registry_verified_capabilities_match():
    adapter = OllamaAdapter()
    registry = MODELS["ollama"]

    adapter_verified = {
        skill.value if isinstance(skill, Skill) else str(skill)
        for skill in adapter.verified_skills
    }

    assert adapter_verified == registry.skills_verified


def test_orchestrator_uses_adapter_verified_capabilities():
    orchestrator = Orchestrator()

    assert orchestrator.models_for_skill(
        "reasoning",
        only_verified=True,
    ) == ["ollama"]

    assert orchestrator.models_for_skill(
        "analysis",
        only_verified=True,
    ) == ["ollama"]


def test_ollama_coding_is_not_verified():
    adapter = OllamaAdapter()

    verified = {
        skill.value if isinstance(skill, Skill) else str(skill)
        for skill in adapter.verified_skills
    }

    assert "coding" not in verified
