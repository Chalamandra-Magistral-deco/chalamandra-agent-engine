"""Pruebas del adaptador Gemini sin llamadas externas."""

import asyncio

from adapters.base import ModelRequest, ModelStatus
from adapters.gemini import GeminiAdapter


def test_gemini_model_is_configurable_from_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")

    adapter = GeminiAdapter(api_key="test-key")

    assert adapter.model == "gemini-test-model"


def test_explicit_gemini_model_overrides_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")

    adapter = GeminiAdapter(api_key="test-key", model="gemini-explicit-model")

    assert adapter.model == "gemini-explicit-model"


def test_gemini_model_uses_default_when_environment_is_empty(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "")

    adapter = GeminiAdapter(api_key="test-key")

    assert adapter.model == GeminiAdapter.default_version


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


class _FakeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, url: str, *, json: dict):
        _CAPTURED_PAYLOADS.append(json)
        return _FakeResponse()


_CAPTURED_PAYLOADS: list[dict] = []


def test_gemini_3_models_omit_ignored_temperature(monkeypatch):
    _CAPTURED_PAYLOADS.clear()
    monkeypatch.setattr("adapters.gemini.httpx.AsyncClient", _FakeAsyncClient)
    adapter = GeminiAdapter(api_key="test-key", model="gemini-3.8-flash")

    response = asyncio.run(
        adapter.generate(ModelRequest(prompt="hello", temperature=0.2, max_tokens=100))
    )

    assert response.status == ModelStatus.OK
    config = _CAPTURED_PAYLOADS[-1]["generationConfig"]
    assert "temperature" not in config
    assert config["thinkingConfig"]["thinkingLevel"] == "low"


def test_gemini_2_models_keep_temperature_parameter(monkeypatch):
    _CAPTURED_PAYLOADS.clear()
    monkeypatch.setattr("adapters.gemini.httpx.AsyncClient", _FakeAsyncClient)
    adapter = GeminiAdapter(api_key="test-key", model="gemini-2.5-flash")

    response = asyncio.run(
        adapter.generate(ModelRequest(prompt="hello", temperature=0.2, max_tokens=100))
    )

    assert response.status == ModelStatus.OK
    assert _CAPTURED_PAYLOADS[-1]["generationConfig"]["temperature"] == 0.2
