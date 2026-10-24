"""Protege la coherencia entre la registry y los IDs de cada adaptador."""

from adapters.claude import ClaudeAdapter
from adapters.deepseek import DeepSeekAdapter
from adapters.gemini import GeminiAdapter
from adapters.gpt import GPTAdapter
from adapters.ollama import OllamaAdapter
from registry.models import MODELS


def test_registry_versions_match_adapter_defaults():
    adapters = {
        "ollama": OllamaAdapter,
        "gemini": GeminiAdapter,
        "gpt": GPTAdapter,
        "claude": ClaudeAdapter,
        "deepseek": DeepSeekAdapter,
    }

    for name, adapter_type in adapters.items():
        assert MODELS[name].versions == [adapter_type.default_version]
