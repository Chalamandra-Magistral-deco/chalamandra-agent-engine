"""Test formal del estado operativo de los adapters."""

from core.orchestrator import Orchestrator


def main() -> None:
    orchestrator = Orchestrator()
    states = orchestrator.adapter_states()

    print("===== ADAPTER STATE TEST =====")

    for name, info in states.items():
        print(
            f"{name}: "
            f"state={info['state']} "
            f"registered={info['registered']} "
            f"active={info['active']} "
            f"verified={info['verified']}"
        )

    # Ollama está activo y tiene skills verificadas.
    assert states["ollama"]["active"] is True
    assert states["ollama"]["verified"] is True
    assert states["ollama"]["state"] == "VERIFIED"

    # Estos adapters ya tienen implementación.
    for name in ("gemini", "deepseek", "gpt", "claude"):
        assert states[name]["registered"] is True

    # Gemini depende de su credencial.
    if "gemini" in orchestrator.available_models():
        assert states["gemini"]["active"] is True
        assert states["gemini"]["state"] in {"ACTIVE", "VERIFIED"}

    # DeepSeek/GPT/Claude no deben fingir verificación.
    for name in ("deepseek", "gpt", "claude"):
        if states[name]["active"]:
            assert states[name]["state"] in {"ACTIVE", "VERIFIED"}
        else:
            assert states[name]["state"] == "REGISTERED"
            assert states[name]["verified"] is False

    # Qwen está en registry pero no tiene adapter.
    assert states["qwen"]["state"] == "UNAVAILABLE"

    print("TEST ADAPTER STATES OK")


if __name__ == "__main__":
    main()
