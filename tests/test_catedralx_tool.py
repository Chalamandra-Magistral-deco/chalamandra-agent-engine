from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.master import Master
from pipeline import catedralx_tool as bridge


class FakeProcessor:
    def __init__(self, *, output_path: Path) -> None:
        self.output_path = output_path
        self.loaded = False

    def _cargar(self) -> None:
        self.loaded = True


def _fake_module(captured: dict):
    state = SimpleNamespace(
        timestamp=1234,
        decision="HOLD",
        confianza=0.6,
        regimen="LATERAL",
        señal_combinada="lateral",
        free_energy=0.2,
        hamiltoniano=0.1,
        energia_cognitiva=0.3,
        percepcion_espacial={"hurst": 0.51},
        neurovector=[0.1, 0.2],
        decisiones=[],
        coherencia={"coherencia_direccional": 0.9, "disonancia": 0.05},
        metacognicion={"riesgo_caos": 0.1},
        contexto_cognitivo={"fase_actual": "TENDENCIA"},
        memory={"precision_20_ciclos": 0.75},
        contraevidencia={"fuerza_contra": 0.0},
        sources={"decision": {"version": None}},
        cognitive_gate_reasons=[],
    )

    def fake_think(sensory_input, historial_precios, ciclo_id):
        captured["sensory_input"] = sensory_input
        captured["history"] = historial_precios
        captured["cycle_id"] = ciclo_id
        return state

    return SimpleNamespace(
        _think_orchestrator=SimpleNamespace(transductor=FakeProcessor(output_path=Path("/tmp/unused"))),
        TransductorEndocrino=FakeProcessor,
        SensoryInput=lambda **kwargs: SimpleNamespace(**kwargs),
        think=fake_think,
    )


def test_catedralx_tool_is_registered_without_loading_engine() -> None:
    master = Master()
    assert "catedralx_reasoning" in master.inspect()["tools"]
    assert "catedralx_reasoning" in master.route_skill("reasoning")["tools"]
    assert bridge._ENGINE_MODULE is None


def test_requires_at_least_30_explicit_price_points() -> None:
    result = bridge.catedralx_reasoning_tool(historical_prices=[100.0] * 29)
    assert result["status"] == "error"
    assert "al menos 30 precios explícitos" in result["error"]
    assert result["orders_created"] is False
    assert result["orders_executed"] is False


@pytest.mark.parametrize("bad_price", [0, -1, float("nan"), float("inf")])
def test_rejects_invalid_prices(bad_price: float) -> None:
    prices = [100.0 + i for i in range(29)] + [bad_price]
    result = bridge.catedralx_reasoning_tool(historical_prices=prices)
    assert result["status"] == "error"
    assert result["orders_created"] is False
    assert result["orders_executed"] is False


def test_state_file_must_be_outside_source_checkout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)
    monkeypatch.setattr(bridge, "_resolve_root", lambda: tmp_path)
    monkeypatch.setattr(
        bridge,
        "_resolve_state_path",
        lambda: tmp_path / "03-OUTPUT" / "hormonas.json",
    )
    monkeypatch.setattr(
        bridge,
        "_load_catedralx_module",
        lambda _: pytest.fail("No debe importar el motor cuando el estado apunta al código fuente."),
    )

    result = bridge.catedralx_reasoning_tool(historical_prices=[100.0 + i for i in range(30)])
    assert result["status"] == "error"
    assert "fuera del repositorio" in result["error"]


def test_explicit_prices_return_signal_without_order_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)
    monkeypatch.setattr(bridge, "_resolve_root", lambda: tmp_path / "CatedralX")
    state_path = tmp_path / "state" / "hormonas.json"
    monkeypatch.setattr(bridge, "_resolve_state_path", lambda: state_path)

    captured: dict = {}
    fake = _fake_module(captured)
    monkeypatch.setattr(bridge, "_load_catedralx_module", lambda _: fake)

    result = bridge.catedralx_reasoning_tool(
        historical_prices=[100.0 + i for i in range(30)],
        current_price=131.0,
        gap=0.01,
        inertia=0.2,
        asset="EUR/USD",
        cycle_id=77,
    )

    assert result["status"] == "ok"
    assert result["engine"] == "CatedralX"
    assert result["asset"] == "EUR/USD"
    assert result["decision"] == "HOLD"
    assert result["cycle_id"] == 77
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert result["coherencia"]["coherencia_direccional"] == 0.9
    assert result["metacognicion"]["riesgo_caos"] == 0.1
    assert result["contexto_cognitivo"]["fase_actual"] == "TENDENCIA"
    assert result["memory"]["precision_20_ciclos"] == 0.75
    assert result["contraevidencia"]["fuerza_contra"] == 0.0
    assert result["sources"]["decision"]["version"] is None
    assert result["cognitive_gate_reasons"] == []
    assert result["state_saved_to"] == str(state_path.resolve())
    assert captured["cycle_id"] == 77
    assert len(captured["history"]) == 31
    assert captured["history"][-1] == 131.0
    assert captured["sensory_input"].precio_actual == 131.0
    assert captured["sensory_input"].gap == 0.01
    assert fake._think_orchestrator.transductor.output_path == state_path.resolve()
    assert not state_path.exists()


def test_master_dispatches_catedralx_tool_without_running_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    master = Master()
    result = asyncio.run(
        master.execute_tool("catedralx_reasoning", historical_prices=[100.0] * 10)
    )

    assert result["status"] == "error"
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert bridge._ENGINE_MODULE is None


def test_frankfurter_daily_source_runs_catedralx_in_analysis_only_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)
    monkeypatch.setattr(bridge, "_resolve_root", lambda: tmp_path / "CatedralX")
    state_path = tmp_path / "state" / "hormonas.json"
    monkeypatch.setattr(bridge, "_resolve_state_path", lambda: state_path)

    prices = [1.05 + i / 10000 for i in range(60)]
    monkeypatch.setattr(
        bridge,
        "fetch_daily_fx_history",
        lambda **kwargs: {
            "status": "ok",
            "source": "Frankfurter",
            "symbol": "EUR/USD",
            "interval": "daily_reference",
            "intraday": False,
            "as_of_date": "2026-10-23",
            "age_days": 0,
            "observation_count": 60,
            "prices": prices,
            "trading_enabled": False,
        },
    )
    captured: dict = {}
    fake = _fake_module(captured)
    monkeypatch.setattr(bridge, "_load_catedralx_module", lambda _: fake)

    result = bridge.catedralx_reasoning_tool(data_source="frankfurter_daily")

    assert result["status"] == "ok"
    assert result["asset"] == "EUR/USD"
    assert result["data_source"] == "frankfurter_daily"
    assert result["market_data"]["source"] == "Frankfurter"
    assert result["market_data"]["interval"] == "daily_reference"
    assert result["market_data"]["intraday"] is False
    assert result["market_data"]["as_of_date"] == "2026-10-23"
    assert captured["history"] == prices
    assert captured["sensory_input"].precio_actual == prices[-1]
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert "no son cotizaciones intradía" in result["note"]
    assert result["signal_consistent"] is True
    assert result["trading_eligible"] is False
    assert not state_path.exists()


def test_conflicting_catedralx_signal_requires_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)
    monkeypatch.setattr(bridge, "_resolve_root", lambda: tmp_path / "CatedralX")
    state_path = tmp_path / "state" / "hormonas.json"
    monkeypatch.setattr(bridge, "_resolve_state_path", lambda: state_path)

    captured: dict = {}
    fake = _fake_module(captured)
    original_think = fake.think

    def conflicting_think(sensory_input, historial_precios, ciclo_id):
        state = original_think(sensory_input, historial_precios, ciclo_id)
        state.decision = "sell"
        state.regimen = "INDEFINIDO"
        state.señal_combinada = "lateral"
        state.decisiones = []
        return state

    fake.think = conflicting_think
    monkeypatch.setattr(bridge, "_load_catedralx_module", lambda _: fake)

    result = bridge.catedralx_reasoning_tool(
        historical_prices=[100.0 + i for i in range(30)],
        asset="EUR/USD",
    )

    assert result["status"] == "review_required"
    assert result["signal_consistent"] is False
    assert result["trading_eligible"] is False
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert len(result["consistency_warnings"]) == 3


def test_financial_risk_flags_require_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)
    monkeypatch.setattr(bridge, "_resolve_root", lambda: tmp_path / "CatedralX")
    monkeypatch.setattr(
        bridge,
        "_resolve_state_path",
        lambda: tmp_path / "state" / "hormonas.json",
    )
    captured: dict = {}
    fake = _fake_module(captured)
    fake.think = lambda sensory_input, historial_precios, ciclo_id: fake._state
    fake._state = SimpleNamespace(
        timestamp=1234, decision="hold", confianza=0.0, regimen="VOLATILIDAD_EN_EXPANSION",
        señal_combinada="lateral", free_energy=0.2, hamiltoniano=0.1,
        energia_cognitiva=0.3, percepcion_espacial={
            "hurst": 0.51, "analisis_financiero": {"risk_flags": ["volatility_expansion"]}
        }, neurovector=[], decisiones=[],
    )
    monkeypatch.setattr(bridge, "_load_catedralx_module", lambda _: fake)

    result = bridge.catedralx_reasoning_tool(
        historical_prices=[100.0 + i for i in range(30)], asset="EUR/USD"
    )

    assert result["status"] == "review_required"
    assert result["trading_eligible"] is False
    assert result["orders_created"] is False
    assert result["orders_executed"] is False
    assert "volatility_expansion" in result["consistency_warnings"][0]


def test_catedralx_schema_does_not_expose_infrastructure_paths() -> None:
    parameters = Master().tool_schemas(
        ["catedralx_reasoning"]
    )[0]["function"]["parameters"]
    properties = parameters["properties"]

    assert "catedralx_path" not in properties
    assert "state_path" not in properties


@pytest.mark.parametrize("field", ["catedralx_path", "state_path"])
def test_model_cannot_override_catedralx_infrastructure_paths(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.setattr(bridge, "_ENGINE_MODULE", None)
    monkeypatch.setattr(bridge, "_ENGINE_ROOT", None)
    monkeypatch.setattr(bridge, "_ENGINE_STATE_PATH", None)

    master = Master()
    tool_call = {
        "name": "catedralx_reasoning",
        "arguments": {
            "historical_prices": [100.0 + index for index in range(30)],
            field: "/tmp/untrusted",
        },
    }

    with pytest.raises(ValueError, match="Argumentos inválidos"):
        asyncio.run(master.execute_agent_tool_call("analyst", tool_call))

    assert bridge._ENGINE_MODULE is None
