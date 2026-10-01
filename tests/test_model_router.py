from core.model_router import ModelRouter


class FakeOrchestrator:
    def __init__(self):
        self.routes = {
            "analysis": {
                "verified": ["ollama"],
                "active": ["ollama"],
            },
            "reasoning": {
                "verified": ["ollama"],
                "active": ["ollama", "gemini"],
            },
        }

    def models_for_skill(self, skill, *, only_verified=True):
        data = self.routes.get(skill, {})
        return list(
            data.get(
                "verified" if only_verified else "active",
                [],
            )
        )


def main():
    router = ModelRouter(FakeOrchestrator())

    assert router.verified("analysis") == ["ollama"]
    assert router.active("analysis") == ["ollama"]
    assert router.select("analysis") == "ollama"

    assert router.verified("reasoning") == ["ollama"]
    assert router.active("reasoning") == ["ollama", "gemini"]
    assert router.fallback("reasoning") == ["gemini"]
    assert router.route("reasoning") == ["ollama", "gemini"]

    print("ROUTER OK")


if __name__ == "__main__":
    main()
