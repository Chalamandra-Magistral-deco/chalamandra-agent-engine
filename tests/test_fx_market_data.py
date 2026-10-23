from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline import fx_market_data as fx


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise fx.httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=fx.httpx.Request("GET", fx.API_URL),
                response=fx.httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def _series(end: date, count: int, *, base: str = "EUR", quote: str = "USD"):
    return [
        {
            "date": (end - timedelta(days=count - index - 1)).isoformat(),
            "base": base,
            "quote": quote,
            "rate": 1.0 + index / 1000,
        }
        for index in range(count)
    ]


def test_fetches_and_validates_daily_reference_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 10, 23)
    payload = _series(today, 80)
    calls: list[dict] = []

    def fake_get(url: str, *, params: dict, timeout: float, headers: dict):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(payload)

    monkeypatch.setattr(fx.httpx, "get", fake_get)
    result = fx.fetch_daily_fx_history(observations=60, today=today)

    assert calls[0]["url"] == fx.API_URL
    assert calls[0]["params"]["base"] == "EUR"
    assert calls[0]["params"]["quotes"] == "USD"
    assert result["status"] == "ok"
    assert result["symbol"] == "EUR/USD"
    assert result["interval"] == "daily_reference"
    assert result["intraday"] is False
    assert result["trading_enabled"] is False
    assert result["observation_count"] == 60
    assert result["as_of_date"] == today.isoformat()
    assert len(result["prices"]) == 60
    assert result["prices"][-1] == pytest.approx(1.079)


def test_rejects_stale_daily_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 10, 23)
    payload = _series(today - timedelta(days=10), 80)
    monkeypatch.setattr(
        fx.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(RuntimeError, match="antigüedad"):
        fx.fetch_daily_fx_history(observations=60, today=today)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_currency": "EURO"},
        {"quote_currency": "EUR"},
        {"base_currency": "EUR", "quote_currency": "EUR"},
        {"observations": 10},
        {"observations": 501},
        {"max_staleness_days": 31},
    ],
)
def test_rejects_invalid_request_without_http(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict,
) -> None:
    def no_request(*args, **kwargs):
        pytest.fail("No debe llamar al servidor con parámetros inválidos.")

    monkeypatch.setattr(fx.httpx, "get", no_request)
    with pytest.raises(ValueError):
        fx.fetch_daily_fx_history(**kwargs)


def test_rejects_response_for_wrong_currency_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2026, 10, 23)
    payload = _series(today, 80, base="GBP", quote="USD")
    monkeypatch.setattr(
        fx.httpx,
        "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(RuntimeError, match="distinto al solicitado"):
        fx.fetch_daily_fx_history(observations=60, today=today)
