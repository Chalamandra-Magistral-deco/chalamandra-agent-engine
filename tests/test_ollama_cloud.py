"""Pruebas de Ollama Cloud sin consumo real ni llamadas externas."""

import asyncio

import httpx
import pytest

from adapters.base import ModelRequest, ModelStatus
from adapters.ollama_cloud import OllamaCloudAdapter
from core.orchestrator import Orchestrator


_CAPTURED: list[dict] = []


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "model": "deepseek-v4.1-flash",
            "message": {
                "role": "assistant",
                "content": "Resultado de prueba",
                "tool_calls": [
                    {
                        "function": {
                            "name": "inspect_status",
                            "arguments": {"scope": "read-only"},
                        }
                    }
                ],
            },
            "prompt_eval_count": 1000,
            "eval_count": 500,
        }


class _FakeAsyncClient:
    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def post(self, url: str, *, headers: dict, json: dict):
        _CAPTURED.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse()


def test_cloud_adapter_requires_api_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OLLAMA_API_KEY"):
        OllamaCloudAdapter()


def test_cloud_adapter_uses_configured_model_and_valid_prices(monkeypatch):
    monkeypatch.setenv("OLLAMA_CLOUD_MODEL", "deepseek-v4.1-flash-test")
    monkeypatch.setenv("OLLAMA_CLOUD_INPUT_USD_PER_MILLION", "0.15")
    monkeypatch.setenv("OLLAMA_CLOUD_OUTPUT_USD_PER_MILLION", "0.60")

    adapter = OllamaCloudAdapter(api_key="test-secret")

    assert adapter.model == "deepseek-v4.1-flash-test"
    assert adapter.input_price_per_million == 0.15
    assert adapter.output_price_per_million == 0.60


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "not-a-number"])
def test_cloud_adapter_rejects_invalid_prices(monkeypatch, value):
    monkeypatch.setenv("OLLAMA_CLOUD_INPUT_USD_PER_MILLION", value)
    with pytest.raises(ValueError, match="OLLAMA_CLOUD_INPUT_USD_PER_MILLION"):
        OllamaCloudAdapter(api_key="test-secret")


def test_cloud_adapter_posts_auth_and_returns_cost_estimate(monkeypatch):
    _CAPTURED.clear()
    monkeypatch.setattr("adapters.ollama_cloud.httpx.AsyncClient", _FakeAsyncClient)
    adapter = OllamaCloudAdapter(
        api_key="test-secret",
        input_price_per_million=0.30,
        output_price_per_million=1.20,
    )
    request = ModelRequest(
        prompt="Analiza este escenario",
        system_prompt="Responde con precisión.",
        temperature=0.0,
        max_tokens=128,
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "inspect_status",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    response = asyncio.run(adapter.generate(request))

    assert response.status == ModelStatus.OK
    assert response.model == "ollama_cloud"
    assert response.model_version == "deepseek-v4.1-flash"
    assert response.tokens_in == 1000
    assert response.tokens_out == 500
    assert response.cost_usd == pytest.approx(0.0009)
    assert response.tool_calls == [
        {"name": "inspect_status", "arguments": {"scope": "read-only"}}
    ]
    assert _CAPTURED[0]["url"] == "https://ollama.com/api/chat"
    assert _CAPTURED[0]["headers"]["Authorization"] == "Bearer test-secret"
    assert _CAPTURED[0]["json"]["model"] == "deepseek-v4.1-flash"
    assert _CAPTURED[0]["json"]["stream"] is False
    assert _CAPTURED[0]["json"]["messages"][0]["role"] == "system"
    assert _CAPTURED[0]["json"]["tools"]
    assert response.raw["chalamandra_cost_estimate"]["usage_complete"] is True
    assert response.raw["chalamandra_cost_estimate"]["cached_input_discount_applied"] is False
    assert "test-secret" not in str(response.model_dump())


class _RateLimitedResponse:
    status_code = 429

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://ollama.com/api/chat")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)


class _RateLimitedClient(_FakeAsyncClient):
    async def post(self, url: str, *, headers: dict, json: dict):
        _CAPTURED.append({"url": url, "headers": headers, "json": json})
        return _RateLimitedResponse()


def test_cloud_adapter_does_not_retry_billable_requests(monkeypatch):
    _CAPTURED.clear()
    monkeypatch.setattr(
        "adapters.ollama_cloud.httpx.AsyncClient",
        _RateLimitedClient,
    )
    adapter = OllamaCloudAdapter(api_key="test-secret")

    response = asyncio.run(adapter.generate(ModelRequest(prompt="Prueba")))

    assert response.status == ModelStatus.ERROR
    assert len(_CAPTURED) == 1
    assert "no se reintentó" in response.errors[0]


def test_cloud_adapter_is_opt_in_and_not_verified(monkeypatch):
    monkeypatch.setenv("OLLAMA_CLOUD_ENABLED", "false")
    monkeypatch.setenv("OLLAMA_API_KEY", "test-secret")
    monkeypatch.delenv("AZURE_KEYVAULT_URI", raising=False)

    orchestrator = Orchestrator()

    assert "ollama_cloud" not in orchestrator.available_models()
    assert orchestrator.credential_status["OLLAMA_API_KEY"] == "disabled"


def test_cloud_adapter_registers_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OLLAMA_CLOUD_ENABLED", "true")
    monkeypatch.setenv("OLLAMA_API_KEY", "test-secret")
    monkeypatch.delenv("AZURE_KEYVAULT_URI", raising=False)

    orchestrator = Orchestrator()
    states = orchestrator.adapter_states()

    assert "ollama_cloud" in orchestrator.available_models()
    assert states["ollama_cloud"]["active"] is True
    assert states["ollama_cloud"]["state"] == "ACTIVE"
    assert states["ollama_cloud"]["skills_verified"] == []
    assert "ollama_cloud" in orchestrator.models_for_skill(
        "reasoning",
        only_verified=False,
    )
    assert "ollama_cloud" not in orchestrator.models_for_skill(
        "reasoning",
        only_verified=True,
    )



def test_cloud_adapter_falls_back_to_prompt_for_empty_messages(monkeypatch):
    _CAPTURED.clear()
    monkeypatch.setattr("adapters.ollama_cloud.httpx.AsyncClient", _FakeAsyncClient)
    adapter = OllamaCloudAdapter(api_key="test-secret")
    request = ModelRequest(
        prompt="Usa el prompt canónico",
        system_prompt="Sistema canónico",
        messages=[],
    )

    response = asyncio.run(adapter.generate(request))

    assert response.status == ModelStatus.OK
    assert _CAPTURED[0]["json"]["messages"] == [
        {"role": "system", "content": "Sistema canónico"},
        {"role": "user", "content": "Usa el prompt canónico"},
    ]


class _MissingUsageResponse(_FakeResponse):
    def json(self) -> dict:
        return {
            "model": "deepseek-v4.1-flash",
            "message": {"role": "assistant", "content": "Respuesta sin métricas"},
        }


class _MissingUsageClient(_FakeAsyncClient):
    async def post(self, url: str, *, headers: dict, json: dict):
        _CAPTURED.append({"url": url, "headers": headers, "json": json})
        return _MissingUsageResponse()


def test_cloud_adapter_never_reports_missing_usage_as_free(monkeypatch):
    monkeypatch.setattr(
        "adapters.ollama_cloud.httpx.AsyncClient",
        _MissingUsageClient,
    )
    adapter = OllamaCloudAdapter(api_key="test-secret")

    response = asyncio.run(adapter.generate(ModelRequest(prompt="Prueba")))

    assert response.status == ModelStatus.OK
    assert response.tokens_in == 0
    assert response.tokens_out == 0
    assert response.cost_usd is None
    assert response.raw["chalamandra_cost_estimate"]["estimated"] is False
    assert response.raw["chalamandra_cost_estimate"]["usage_complete"] is False
    assert (
        response.raw["chalamandra_cost_estimate"]["unavailable_reason"]
        == "provider_usage_counters_missing_or_invalid"
    )


def test_cloud_adapter_rejects_invalid_tool_call_shape(monkeypatch):
    class InvalidToolResponse(_FakeResponse):
        def json(self) -> dict:
            return {
                "model": "deepseek-v4.1-flash",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "", "arguments": {}}},
                        {"function": {"name": "   ", "arguments": {"scope": "read"}}},
                        {"function": {"name": "bad_arguments", "arguments": []}},
                        {"function": {"name": "valid_read", "arguments": {"scope": "read"}}},
                    ],
                },
                "prompt_eval_count": 10,
                "eval_count": 5,
            }

    class InvalidToolClient(_FakeAsyncClient):
        async def post(self, url: str, *, headers: dict, json: dict):
            return InvalidToolResponse()

    monkeypatch.setattr(
        "adapters.ollama_cloud.httpx.AsyncClient",
        InvalidToolClient,
    )
    adapter = OllamaCloudAdapter(api_key="test-secret")
    response = asyncio.run(adapter.generate(ModelRequest(prompt="Prueba")))

    assert response.status == ModelStatus.OK
    assert response.tool_calls == [
        {"name": "valid_read", "arguments": {"scope": "read"}}
    ]
