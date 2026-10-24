from __future__ import annotations

import pytest

from adapters.base import ModelRequest, ModelRole
from adapters.ollama import OllamaAdapter


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "qwen2.5:3b",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "dynamics",
                            "arguments": {"a": 1, "b": 2},
                        }
                    }
                ],
            },
            "prompt_eval_count": 10,
            "eval_count": 3,
        }


class FakeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout
        self.payload: dict | None = None

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, *, json: dict) -> FakeResponse:
        self.payload = json
        captured_payloads.append(json)
        return FakeResponse()


captured_payloads: list[dict] = []


@pytest.mark.asyncio
async def test_ollama_adapter_sends_tools_and_normalizes_native_tool_calls(monkeypatch) -> None:
    captured_payloads.clear()
    monkeypatch.setattr("adapters.ollama.httpx.AsyncClient", FakeAsyncClient)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "dynamics",
                "description": "Analyze dynamics",
                "parameters": {"type": "object", "properties": {"a": {"type": "number"}}},
            },
        }
    ]
    request = ModelRequest(
        prompt="Run the analysis",
        system_prompt="Use tools when needed",
        role=ModelRole.AGENT,
        tools=tools,
    )

    response = await OllamaAdapter().generate(request)

    assert response.status.value == "ok"
    assert response.tool_calls == [{"name": "dynamics", "arguments": {"a": 1, "b": 2}}]
    assert captured_payloads[0]["tools"] == tools
    assert captured_payloads[0]["messages"][-1] == {"role": "user", "content": "Run the analysis"}


@pytest.mark.asyncio
async def test_ollama_adapter_reuses_followup_conversation_messages(monkeypatch) -> None:
    captured_payloads.clear()
    monkeypatch.setattr("adapters.ollama.httpx.AsyncClient", FakeAsyncClient)
    messages = [
        {"role": "user", "content": "Do analysis"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_name": "dynamics", "content": '{"ok":true}'},
    ]
    response = await OllamaAdapter().generate(
        ModelRequest(prompt="Do analysis", role=ModelRole.AGENT, messages=messages)
    )

    assert response.status.value == "ok"
    assert captured_payloads[0]["messages"] == messages
