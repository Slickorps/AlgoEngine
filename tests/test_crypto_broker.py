"""Tests for cryptocurrency broker and data adapters."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.crypto_broker import (
    BinanceAdapter,
    CoinGeckoAdapter,
    CryptoKline,
    CryptoTicker,
    KlineInterval,
    get_crypto_adapter,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    with patch("aiohttp.ClientSession") as mock_cls:
        session = AsyncMock()
        mock_cls.return_value = session

        resp = AsyncMock()
        resp.raise_for_status = MagicMock()

        class FakeContextManager:
            async def __aenter__(self):
                return resp
            async def __aexit__(self, *args):
                pass

        session.get = MagicMock(return_value=FakeContextManager())
        session.post = MagicMock(return_value=FakeContextManager())
        session.ws_connect = MagicMock(return_value=FakeContextManager())

        yield session, resp


@pytest.fixture
async def binance_adapter(mock_session):
    adapter = BinanceAdapter()
    await adapter.connect()
    yield adapter
    await adapter.disconnect()


@pytest.fixture
async def coingecko_adapter(mock_session):
    adapter = CoinGeckoAdapter()
    await adapter.connect()
    yield adapter
    await adapter.disconnect()


# ── Kline Parsing ─────────────────────────────────────────────────


class TestBinanceKlineParsing:
    def test_parse_single_kline(self):
        raw = [
            [
                1499040000000, "0.01634790", "0.80000000",
                "0.01575800", "0.01577100", "148976.11427815",
                1499644799999, "2434.19055334", 308,
                "1756.87402397", "28.46694368", "0",
            ]
        ]
        result = BinanceAdapter._parse_klines("BTCUSDT", "1m", raw)
        assert len(result) == 1
        k = result[0]
        assert k.symbol == "BTCUSDT"
        assert k.interval == KlineInterval.M1
        assert k.open == Decimal("0.01634790")
        assert k.high == Decimal("0.80000000")
        assert k.low == Decimal("0.01575800")
        assert k.close == Decimal("0.01577100")
        assert k.volume == Decimal("148976.11427815")
        assert k.trades == 308

    def test_parse_ticker(self):
        raw = {
            "symbol": "BTCUSDT", "priceChange": "-94.99999800",
            "priceChangePercent": "-95.960", "weightedAvgPrice": "0.29628482",
            "lastPrice": "4.00000200", "lastQty": "200.00000000",
            "bidPrice": "4.00000000", "bidQty": "100.00000000",
            "askPrice": "4.00000200", "askQty": "100.00000000",
            "openPrice": "99.00000000", "highPrice": "100.00000000",
            "lowPrice": "0.10000000", "volume": "8913.30000000",
            "quoteVolume": "15.30000000", "openTime": 1499644799999,
            "closeTime": 1499644799999, "count": 1234,
        }
        ticker = BinanceAdapter._parse_ticker(raw)
        assert ticker.symbol == "BTCUSDT"
        assert ticker.last_price == Decimal("4.00000200")
        assert ticker.volume == Decimal("8913.30000000")

    def test_kline_to_bar(self):
        kline = CryptoKline(
            symbol="ETHUSDT", interval=KlineInterval.H1,
            open_time=1499040000000, open=Decimal("100"), high=Decimal("110"),
            low=Decimal("95"), close=Decimal("105"),
            volume=Decimal("1000"), close_time=1499644799999,
            quote_volume=Decimal("100000"), trades=500,
            taker_buy_volume=Decimal("600"),
            taker_buy_quote_volume=Decimal("60000"),
        )
        bar = kline.to_bar()
        assert bar.symbol.ticker == "ETHUSDT"
        assert bar.open == Decimal("100")
        assert bar.close == Decimal("105")

    def test_ticker_to_tick(self):
        ticker = CryptoTicker(
            symbol="BTCUSDT", price_change=Decimal("-94"),
            price_change_pct=Decimal("-95"), weighted_avg_price=Decimal("0.3"),
            last_price=Decimal("4"), last_qty=Decimal("200"),
            bid_price=Decimal("4"), bid_qty=Decimal("100"),
            ask_price=Decimal("4.000002"), ask_qty=Decimal("100"),
            open_price=Decimal("99"), high_price=Decimal("100"),
            low_price=Decimal("0.1"), volume=Decimal("8913"),
            quote_volume=Decimal("15"), open_time=1499644799999,
            close_time=1499644799999, count=1234,
        )
        tick = ticker.to_tick()
        assert tick.symbol.ticker == "BTCUSDT"
        assert tick.bid_price == Decimal("4")
        assert tick.ask_price == Decimal("4.000002")


# ── Binance REST ──────────────────────────────────────────────────


class TestBinanceREST:
    @pytest.mark.asyncio
    async def test_ping(self, binance_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value={})
        result = await binance_adapter.ping()
        assert result is True

    @pytest.mark.asyncio
    async def test_ping_failure(self, binance_adapter):
        result = await binance_adapter.ping()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_price(self, binance_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value={"symbol": "BTCUSDT", "price": "50000.00"})
        price = await binance_adapter.get_price("btcusdt")
        assert price == Decimal("50000.00")

    @pytest.mark.asyncio
    async def test_get_klines(self, binance_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value=[
            [
                1499040000000, "0.01634790", "0.80000000",
                "0.01575800", "0.01577100", "148976.11427815",
                1499644799999, "2434.19055334", 308,
                "1756.87402397", "28.46694368", "0",
            ]
        ])
        klines = await binance_adapter.get_klines("BTCUSDT", "1m", limit=50)
        assert len(klines) == 1

    @pytest.mark.asyncio
    async def test_get_ticker_24h(self, binance_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value={
            "symbol": "BTCUSDT", "priceChange": "100.00",
            "priceChangePercent": "1.5", "weightedAvgPrice": "50100.00",
            "lastPrice": "50200.00", "lastQty": "1.5",
            "bidPrice": "50199.00", "bidQty": "2.0",
            "askPrice": "50201.00", "askQty": "3.0",
            "openPrice": "50000.00", "highPrice": "51000.00",
            "lowPrice": "49000.00", "volume": "15000.0",
            "quoteVolume": "750000000.0", "openTime": 1499644799999,
            "closeTime": 1499644799999, "count": 50000,
        })
        ticker = await binance_adapter.get_ticker_24h("btcusdt")
        assert ticker is not None
        assert ticker.last_price == Decimal("50200.00")

    @pytest.mark.asyncio
    async def test_get_bars(self, binance_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value=[
            [1499040000000, "100", "110", "95", "105", "1000",
             1499644799999, "100000", 500, "600", "60000", "0",
            ]
        ])
        bars = await binance_adapter.get_bars("ETHUSDT", "1h", limit=10)
        assert len(bars) == 1
        assert bars[0].symbol.ticker == "ETHUSDT"


# ── CoinGecko REST ────────────────────────────────────────────────


class TestCoinGeckoREST:
    @pytest.mark.asyncio
    async def test_get_price(self, coingecko_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value={
            "bitcoin": {"usd": 50000, "eur": 42000},
        })
        prices = await coingecko_adapter.get_price("bitcoin", "usd")
        assert "bitcoin" in prices
        assert prices["bitcoin"]["usd"] == Decimal("50000")

    @pytest.mark.asyncio
    async def test_get_markets(self, coingecko_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value=[
            {
                "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
                "current_price": {"usd": 50000},
                "market_cap": {"usd": 900000000000},
                "market_cap_rank": 1,
                "total_volume": {"usd": 20000000000},
                "high_24h": {"usd": 51000},
                "low_24h": {"usd": 49000},
                "price_change_24h": 500,
                "price_change_percentage_24h": 1.01,
                "circulating_supply": 19000000,
                "total_supply": 21000000,
                "max_supply": 21000000,
                "ath": 69000, "ath_change_percentage": -27.5,
                "ath_date": "2021-11-10T00:00:00.000Z",
                "last_updated": "2024-01-01T00:00:00.000Z",
            }
        ])
        markets = await coingecko_adapter.get_markets(per_page=1)
        assert len(markets) == 1
        m = markets[0]
        assert m.name == "Bitcoin"
        assert m.current_price["usd"] == Decimal("50000")

    @pytest.mark.asyncio
    async def test_parse_market(self):
        raw = {
            "id": "ethereum", "symbol": "eth", "name": "Ethereum",
            "current_price": {"usd": 3000},
            "market_cap": {"usd": 360000000000},
            "market_cap_rank": 2,
            "total_volume": {"usd": 10000000000},
            "high_24h": {"usd": 3100},
            "low_24h": {"usd": 2900},
            "price_change_24h": 50,
            "price_change_percentage_24h": 1.7,
            "circulating_supply": 120000000,
            "total_supply": None,
            "max_supply": None,
            "ath": 4800, "ath_change_percentage": -37.5,
            "ath_date": "2021-11-10T00:00:00.000Z",
            "last_updated": "2024-01-01T00:00:00.000Z",
        }
        m = CoinGeckoAdapter._parse_market(raw)
        assert m.name == "Ethereum"
        assert m.current_price["usd"] == Decimal("3000")
        assert m.total_supply is None
        assert m.max_supply is None

    @pytest.mark.asyncio
    async def test_get_ohlc(self, coingecko_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value=[
            [1499040000000, 100.0, 110.0, 95.0, 105.0],
            [1499040001000, 106.0, 112.0, 104.0, 108.0],
        ])
        with patch.object(
            coingecko_adapter, "_resolve_coin_id", return_value="bitcoin"
        ):
            data = await coingecko_adapter.get_ohlc("btc")
            assert len(data) == 2

    @pytest.mark.asyncio
    async def test_get_crypto_bars(self, coingecko_adapter, mock_session):
        _, resp = mock_session
        resp.json = AsyncMock(return_value=[
            [1499040000000, 100.0, 110.0, 95.0, 105.0],
        ])
        with patch.object(
            coingecko_adapter, "_resolve_coin_id", return_value="bitcoin"
        ):
            bars = await coingecko_adapter.get_crypto_bars("btc", days=1)
            assert len(bars) == 1
            assert bars[0].symbol.ticker == "btc"
            assert bars[0].open == Decimal("100")

    @pytest.mark.asyncio
    async def test_coin_resolution_cached(self, coingecko_adapter, mock_session):
        mock_coin_id = "bitcoin"
        coingecko_adapter._coin_id_cache["BTC"] = mock_coin_id
        with patch.object(
            coingecko_adapter, "_resolve_coin_id", wraps=coingecko_adapter._resolve_coin_id
        ) as mock_resolve:
            result = await coingecko_adapter._resolve_coin_id("BTC")
            assert result == mock_coin_id
            mock_resolve.assert_called_once()


# ── Connection & Session ──────────────────────────────────────────


class TestConnection:
    @pytest.mark.asyncio
    async def test_connect_disconnect(self, mock_session):
        adapter = BinanceAdapter()
        assert not adapter.is_connected()
        await adapter.connect()
        assert adapter.is_connected()
        await adapter.disconnect()
        assert not adapter.is_connected()

    @pytest.mark.asyncio
    async def test_connect_disconnect_coingecko(self, mock_session):
        adapter = CoinGeckoAdapter()
        await adapter.connect()
        assert adapter.is_connected()
        await adapter.disconnect()
        assert not adapter.is_connected()

    @pytest.mark.asyncio
    async def test_get_without_connect_raises(self):
        adapter = BinanceAdapter()
        with pytest.raises(RuntimeError, match="not connected"):
            await adapter._get("ping")


# ── Factory ───────────────────────────────────────────────────────


class TestFactory:
    @pytest.mark.asyncio
    async def test_get_crypto_adapter_binance(self):
        adapter = await get_crypto_adapter("binance")
        assert isinstance(adapter, BinanceAdapter)
        assert adapter.is_connected()
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_crypto_adapter_coingecko(self):
        adapter = await get_crypto_adapter("coingecko")
        assert isinstance(adapter, CoinGeckoAdapter)
        assert adapter.is_connected()
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_crypto_adapter_unknown(self):
        with pytest.raises(ValueError, match="Unknown"):
            await get_crypto_adapter("unknown_provider")


# ── KlineInterval ─────────────────────────────────────────────────


class TestKlineInterval:
    def test_interval_values(self):
        assert KlineInterval.M1.value == "1m"
        assert KlineInterval.H1.value == "1h"
        assert KlineInterval.D1.value == "1d"
        assert KlineInterval.W1.value == "1w"

    def test_interval_from_value(self):
        assert KlineInterval("1h") == KlineInterval.H1
        assert KlineInterval("1d") == KlineInterval.D1
