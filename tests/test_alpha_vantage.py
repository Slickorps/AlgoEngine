"""Tests for the Alpha Vantage data adapter."""

import asyncio

import pytest
import pandas as pd
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.adapters.alpha_vantage import AlphaVantageAdapter
from src.data.models import Symbol, Resolution


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL")


def intraday_payload(interval="5min"):
    return {
        f"Time Series ({interval})": {
            "2023-01-02 09:35:00": {
                "1. open": "100.0", "2. high": "101.0", "3. low": "99.0",
                "4. close": "100.5", "5. volume": "1000",
            },
            "2023-01-02 09:40:00": {
                "1. open": "100.5", "2. high": "102.0", "3. low": "100.0",
                "4. close": "101.0", "5. volume": "1500",
            },
        }
    }


def daily_payload(adjusted=True):
    key = "Time Series (Daily Adjusted)" if adjusted else "Time Series (Daily)"
    if adjusted:
        row = {
            "1. open": "100.0", "2. high": "102.0", "3. low": "99.0",
            "4. close": "101.0", "5. adjusted close": "100.8",
            "6. volume": "5000", "7. dividend amount": "0.00",
            "8. split coefficient": "1.0",
        }
    else:
        row = {
            "1. open": "100.0", "2. high": "102.0", "3. low": "99.0",
            "4. close": "101.0", "5. volume": "5000",
        }
    return {key: {"2023-01-03": row, "2023-01-04": {**row, "1. open": "101.0"}}}


def make_async_session(status=200, json_data=None):
    """Build a mocked aiohttp session exposing an async-context-manager get()."""
    session = MagicMock()

    class FakeResponse:
        async def json(self):
            return json_data if json_data is not None else {}

    class FakeContextManager:
        def __init__(self):
            self._resp = FakeResponse()
            self._resp.status = status

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, exc_type, exc, tb):
            return False

    session.get.return_value = FakeContextManager()
    return session


class TestConnection:
    async def test_connect_without_api_key(self):
        adapter = AlphaVantageAdapter(api_key=None, cache_data=False)
        adapter._api_key = ""
        assert adapter.is_connected() is False
        assert await adapter.connect() is False
        assert not adapter.is_connected()

    async def test_connect_with_api_key(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        session = MagicMock()
        session.close = AsyncMock()
        with patch("aiohttp.ClientSession", return_value=session):
            assert await adapter.connect() is True
        assert adapter.is_connected()

        await adapter.disconnect()
        session.close.assert_awaited_once()
        assert not adapter.is_connected()

    async def test_disconnect_closes_session(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        session = MagicMock()
        session.close = AsyncMock()
        with patch("aiohttp.ClientSession", return_value=session):
            await adapter.connect()
            assert adapter._session is session
            await adapter.disconnect()

        session.close.assert_awaited_once()
        assert adapter._session is None


class TestMakeRequest:
    async def test_no_session_returns_none(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        result = await adapter._make_request({"function": "x"})
        assert result is None

    async def test_successful_request(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._session = make_async_session(200, {"ok": True})

        result = await adapter._make_request({"function": "x"})

        assert result == {"ok": True}
        adapter._session.get.assert_called_once()

    async def test_error_message_in_response(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._session = make_async_session(200, {"Error Message": "Invalid API call"})

        result = await adapter._make_request({"function": "x"})

        assert result is None

    async def test_note_in_response(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._session = make_async_session(200, {"Note": "API rate limit"})

        result = await adapter._make_request({"function": "x"})

        assert result is None

    async def test_http_error(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._session = make_async_session(429)

        result = await adapter._make_request({"function": "x"})

        assert result is None

    async def test_request_exception(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        session = make_async_session(200)
        session.get.side_effect = RuntimeError("connection refused")
        adapter._session = session

        result = await adapter._make_request({"function": "x"})

        assert result is None


class TestRateLimit:
    async def test_rate_limit_no_wait_under_limit(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._call_times = []
        await adapter._rate_limit()
        assert len(adapter._call_times) == 1


class TestDataEndpoints:
    async def test_get_intraday(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=intraday_payload())

        df = await adapter.get_intraday(symbol)

        assert len(df) == 2
        assert df.iloc[0]["close"] == 100.5

    async def test_get_intraday_no_data(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=None)

        df = await adapter.get_intraday(symbol)

        assert df.empty

    async def test_get_intraday_missing_key(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value={"unexpected": {}})

        df = await adapter.get_intraday(symbol)

        assert df.empty

    async def test_get_daily_adjusted(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=daily_payload(adjusted=True))

        df = await adapter.get_daily(symbol, adjusted=True)

        assert len(df) == 2
        assert df.iloc[0]["adjusted_close"] == 100.8

    async def test_get_daily_unadjusted(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=daily_payload(adjusted=False))

        df = await adapter.get_daily(symbol, adjusted=False)

        assert len(df) == 2
        assert df.iloc[0]["volume"] == 5000

    async def test_get_quote(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value={
            "Global Quote": {
                "01. symbol": "AAPL",
                "02. open": "100.0", "03. high": "101.0", "04. low": "99.0",
                "05. price": "100.5", "06. volume": "10000",
                "07. latest trading day": "2023-01-03",
                "08. previous close": "99.5", "09. change": "1.0",
                "10. change percent": "1.005%",
            }
        })

        quote = await adapter.get_quote(symbol)

        assert quote["symbol"] == "AAPL"
        assert quote["price"] == Decimal("100.5")
        assert quote["change_percent"] == "1.005"

    async def test_get_quote_no_data(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=None)

        assert await adapter.get_quote(symbol) is None

    async def test_search_symbol(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value={
            "bestMatches": [
                {
                    "1. symbol": "AAPL", "2. name": "Apple Inc",
                    "3. type": "Equity", "4. region": "United States",
                    "5. marketOpen": "09:30", "6. marketClose": "16:00",
                    "7. timezone": "UTC-05", "8. currency": "USD",
                    "9. matchScore": "1.0",
                }
            ]
        })

        matches = await adapter.search_symbol("apple")

        assert len(matches) == 1
        assert matches[0]["symbol"] == "AAPL"
        assert matches[0]["match_score"] == 1.0

    async def test_search_symbol_no_data(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=None)

        assert await adapter.search_symbol("zzz") == []

    async def test_subscribe_unsubscribe(self):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        s = Symbol("AAPL")
        await adapter.subscribe([s])
        assert s in adapter._subscribed_symbols
        await adapter.unsubscribe([s])
        assert s not in adapter._subscribed_symbols


class TestGetHistorySync:
    def test_get_history_daily(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=daily_payload(adjusted=True))

        fake_loop = MagicMock()
        fake_loop.run_until_complete = lambda coro: asyncio.run(coro)

        with patch("asyncio.get_event_loop", return_value=fake_loop):
            df = adapter.get_history(symbol, None, None, Resolution.DAILY)

        assert len(df) == 2

    def test_get_history_minute(self, symbol):
        adapter = AlphaVantageAdapter(api_key="test-key", cache_data=False)
        adapter._make_request = AsyncMock(return_value=intraday_payload(interval="1min"))

        fake_loop = MagicMock()
        fake_loop.run_until_complete = lambda coro: asyncio.run(coro)

        with patch("asyncio.get_event_loop", return_value=fake_loop):
            df = adapter.get_history(symbol, None, None, Resolution.MINUTE)

        assert len(df) == 2
