from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    name: str
    provider: str
    skills: set[str] = field(default_factory=set)
    local: bool = False


MODELS = {
    "gemini": ModelInfo(
        name="gemini",
        provider="google",
        skills={
            "research",
            "reasoning",
            "vision",
            "web",
        },
    ),

    "gpt": ModelInfo(
        name="gpt",
        provider="openai",
        skills={
            "reasoning",
            "coding",
            "analysis",
        },
    ),

    "claude": ModelInfo(
        name="claude",
        provider="anthropic",
        skills={
            "documents",
            "reasoning",
            "synthesis",
        },
    ),

    "deepseek": ModelInfo(
        name="deepseek",
        provider="deepseek",
        skills={
            "coding",
            "reasoning",
        },
    ),

    "qwen": ModelInfo(
        name="qwen",
        provider="qwen",
        skills={
            "reasoning",
            "coding",
        },
    ),

    "ollama": ModelInfo(
        name="ollama",
        provider="ollama",
        skills={
            "reasoning",
            "coding",
            "analysis",
        },
        local=True,
    ),
}
