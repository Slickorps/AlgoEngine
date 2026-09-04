"""Tests for the Yahoo Finance data adapter."""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from src.adapters.yahoo_finance import YahooFinanceAdapter
from src.data.models import Symbol, Resolution


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL", security_type="EQUITY", exchange="NASDAQ")


def sample_df():
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000, 2000],
        },
        index=pd.to_datetime(["2023-01-02", "2023-01-03"]),
    )


class TestSymbolConversion:
    def test_symbol_to_ticker_equity(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        assert adapter._symbol_to_ticker(Symbol("AAPL")) == "AAPL"

    def test_symbol_to_ticker_crypto(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        assert adapter._symbol_to_ticker(Symbol("BTC", security_type="CRYPTO")) == "BTC-USD"

    def test_symbol_to_ticker_forex(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        assert adapter._symbol_to_ticker(Symbol("EURUSD", security_type="FOREX")) == "EURUSD=X"

    def test_ticker_to_symbol_equity(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        symbol = adapter._ticker_to_symbol("AAPL")
        assert symbol.ticker == "AAPL"
        assert symbol.security_type == "EQUITY"

    def test_ticker_to_symbol_crypto(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        symbol = adapter._ticker_to_symbol("BTC-USD")
        assert symbol.ticker == "BTC"
        assert symbol.security_type == "CRYPTO"

    def test_ticker_to_symbol_forex(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        symbol = adapter._ticker_to_symbol("EURUSD=X")
        assert symbol.ticker == "EURUSD"
        assert symbol.security_type == "FOREX"


class TestConnection:
    async def test_connect_disconnect(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        assert adapter.is_connected() is False
        assert await adapter.connect() is True
        assert adapter.is_connected() is True
        await adapter.disconnect()
        assert adapter.is_connected() is False


class TestSubscriptions:
    async def test_subscribe_unsubscribe(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        s = Symbol("AAPL")
        await adapter.subscribe([s])
        assert s in adapter._subscribed_symbols
        await adapter.unsubscribe([s])
        assert s not in adapter._subscribed_symbols


class TestGetHistory:
    def test_history_returns_dataframe(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = sample_df()
            df = adapter.get_history(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert not df.empty
        assert "open" in df.columns
        assert "close" in df.columns

    def test_history_empty(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = pd.DataFrame()
            df = adapter.get_history(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert df.empty

    def test_history_exception(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = RuntimeError("boom")
            df = adapter.get_history(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert df.empty

    def test_history_minute_adjusts_start(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        captured = {}
        end = datetime.now()
        start = end - timedelta(days=30)

        def fake_history(**kwargs):
            captured.update(kwargs)
            return sample_df()

        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = fake_history
            adapter.get_history(symbol, start, end, Resolution.MINUTE)

        assert captured["interval"] == "1m"
        assert (end - captured["start"]).days <= 7


class TestGetBars:
    def test_get_bars_converts_dataframe(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = sample_df()
            bars = adapter.get_bars(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert len(bars) == 2
        assert bars[0].open == Decimal("100")
        assert bars[0].close == Decimal("101")
        assert bars[0].volume == Decimal("1000")

    def test_get_bars_empty(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = pd.DataFrame()
            bars = adapter.get_bars(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert bars == []

    def test_get_bars_uses_cache(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        storage = MagicMock()
        cached = [MagicMock()]
        storage.load_bars.return_value = cached
        adapter._storage = storage

        bars = adapter.get_bars(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert bars == cached
        storage.save_bars.assert_not_called()

    def test_get_bars_saves_to_cache(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        storage = MagicMock()
        storage.load_bars.return_value = []
        adapter._storage = storage

        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = sample_df()
            bars = adapter.get_bars(symbol, datetime(2023, 1, 1), datetime(2023, 1, 10))

        assert len(bars) == 2
        storage.save_bars.assert_called_once()


class TestAsyncDownload:
    async def test_download_and_cache(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = sample_df()
            bars = await adapter.download_and_cache(
                symbol, datetime(2023, 1, 1), datetime(2023, 1, 10)
            )

        assert len(bars) == 2

    async def test_batch_download(self):
        adapter = YahooFinanceAdapter(cache_data=False)
        adapter._rate_limit_delay = 0
        aapl = Symbol("AAPL")
        tsla = Symbol("TSLA")
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = sample_df()
            results = await adapter.batch_download(
                [aapl, tsla], datetime(2023, 1, 1), datetime(2023, 1, 10)
            )

        assert set(results.keys()) == {"AAPL", "TSLA"}
        assert len(results["AAPL"]) == 2


class TestCompanyInfo:
    def test_get_company_info(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        info = {
            "longName": "Apple Inc",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "marketCap": 2_000_000_000_000,
            "trailingPE": 30.0,
            "dividendYield": 0.005,
            "country": "US",
            "website": "https://apple.com",
        }
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.info = info
            result = adapter.get_company_info(symbol)

        assert result["name"] == "Apple Inc"
        assert result["sector"] == "Technology"
        assert result["market_cap"] == 2_000_000_000_000

    def test_get_company_info_exception(self, symbol):
        adapter = YahooFinanceAdapter(cache_data=False)
        with patch("src.adapters.yahoo_finance.yf.Ticker") as mock_ticker:
            mock_ticker.return_value.info = None
            result = adapter.get_company_info(symbol)

        assert result is None
