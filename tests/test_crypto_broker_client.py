"""Additional tests for the crypto exchange/data adapters."""

import asyncio
import json
import time as _time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.adapters.crypto_broker import (
    BinanceAdapter,
    CoinGeckoAdapter,
    CryptoKline,
    KlineInterval,
    get_crypto_adapter,
)


class FakeResp:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json = json_data if json_data is not None else {}

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, None, status=self.status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._json


class FakeSession:
    def __init__(self, get_resp=None, post_resp=None):
        self._get_resp = get_resp
        self._post_resp = post_resp

    def get(self, *args, **kwargs):
        return self._get_resp

    def post(self, *args, **kwargs):
        return self._post_resp

    async def close(self):
        pass


class FakeWS:
    def __init__(self, messages, on_exhaust=None):
        self._messages = list(messages)
        self._on_exhaust = on_exhaust

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            if self._on_exhaust:
                self._on_exhaust()
            raise StopAsyncIteration
        return self._messages.pop(0)

    def exception(self):
        return None


class FakeSessionCtx:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def ws_connect(self, url):
        return self._ws


def make_kline(symbol="BTCUSDT", interval=KlineInterval.H1, close_time=100):
    return CryptoKline(
        symbol=symbol, interval=interval, open_time=0,
        open=Decimal("1"), high=Decimal("2"), low=Decimal("0.5"), close=Decimal("1.5"),
        volume=Decimal("10"), close_time=close_time, quote_volume=Decimal("15"),
        trades=3, taker_buy_volume=Decimal("5"), taker_buy_quote_volume=Decimal("7"),
    )


def make_ws_message(msg_type, data=None):
    msg = MagicMock()
    msg.type = msg_type
    if data is not None:
        msg.data = data
    return msg


class TestBaseAdapter:
    async def test_post_success(self):
        adapter = BinanceAdapter()
        adapter._session = FakeSession(post_resp=FakeResp(200, {"ok": True}))
        assert await adapter._post("endpoint", {"a": 1}) == {"ok": True}

    async def test_post_not_connected(self):
        with pytest.raises(RuntimeError):
            await BinanceAdapter()._post("endpoint")

    async def test_get_not_connected(self):
        with pytest.raises(RuntimeError):
            await BinanceAdapter()._get("endpoint")

    async def test_rate_limit_sleeps(self):
        adapter = BinanceAdapter()
        adapter._rate_limit_interval = 0.01
        adapter._last_request_time = _time.monotonic()
        await adapter._rate_limit()

    def test_name_property(self):
        assert BinanceAdapter().name == "Binance"
        assert CoinGeckoAdapter().name == "CoinGecko"


class TestBinanceRest:
    def test_signed_params_with_secret(self):
        adapter = BinanceAdapter(api_secret="secret")
        params = adapter._signed_params({"symbol": "BTCUSDT"})
        assert "timestamp" in params
        assert "signature" in params

    def test_signed_params_without_secret(self):
        assert BinanceAdapter()._signed_params({"a": 1}) == {"a": 1}

    def test_auth_headers(self):
        assert BinanceAdapter(api_key="key")._auth_headers() == {"X-MBX-APIKEY": "key"}
        assert BinanceAdapter()._auth_headers() == {}

    async def test_ping_true(self):
        adapter = BinanceAdapter()
        adapter._session = FakeSession(get_resp=FakeResp(200, {}))
        assert await adapter.ping() is True

    async def test_ping_exception(self):
        adapter = BinanceAdapter()
        adapter._session = FakeSession(get_resp=FakeResp(500, {}))
        assert await adapter.ping() is False

    async def test_get_server_time(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value={"serverTime": 123})
        assert await adapter.get_server_time() == 123

    async def test_get_exchange_info(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value={"symbols": []})
        assert await adapter.get_exchange_info() == {"symbols": []}

    async def test_get_klines_with_time_range(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value=[])
        await adapter.get_klines("btcusdt", KlineInterval.H1, start_time=1, end_time=2)
        args, _ = adapter._get.call_args
        assert args[1]["startTime"] == 1
        assert args[1]["endTime"] == 2

    async def test_get_klines_range_single_batch(self):
        adapter = BinanceAdapter()
        kline = make_kline(close_time=100)
        adapter.get_klines = AsyncMock(return_value=[kline])
        result = await adapter.get_klines_range("BTCUSDT", "1h", 0, 1000)
        assert result == [kline]

    async def test_get_klines_range_empty(self):
        adapter = BinanceAdapter()
        adapter.get_klines = AsyncMock(return_value=[])
        assert await adapter.get_klines_range("BTCUSDT", "1h", 0, 1000) == []

    async def test_get_klines_range_paginates(self):
        adapter = BinanceAdapter()
        adapter.get_klines = AsyncMock(side_effect=[[make_kline(close_time=100)] * 1000, []])
        result = await adapter.get_klines_range("BTCUSDT", "1h", 0, 100000)
        assert len(result) == 1000

    async def test_get_ticker_24h(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value={"symbol": "BTCUSDT", "lastPrice": "100"})
        ticker = await adapter.get_ticker_24h("BTCUSDT")
        assert ticker.symbol == "BTCUSDT"

    async def test_get_ticker_24h_none(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value=None)
        assert await adapter.get_ticker_24h("BTCUSDT") is None

    async def test_get_tickers_24h(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value=[{"symbol": "BTCUSDT"}, {}])
        tickers = await adapter.get_tickers_24h()
        assert len(tickers) == 1

    async def test_get_price(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value={"price": "100.5"})
        assert await adapter.get_price("BTCUSDT") == Decimal("100.5")

    async def test_get_order_book(self):
        adapter = BinanceAdapter()
        adapter._get = AsyncMock(return_value={"bids": []})
        assert await adapter.get_order_book("BTCUSDT") == {"bids": []}

    async def test_get_bars(self):
        adapter = BinanceAdapter()
        adapter.get_klines = AsyncMock(return_value=[make_kline()])
        bars = await adapter.get_bars("BTCUSDT", "1h")
        assert len(bars) == 1
        assert bars[0].symbol.ticker == "BTCUSDT"

    async def test_get_account_info(self):
        adapter = BinanceAdapter(api_key="k", api_secret="s")
        adapter._session = FakeSession(get_resp=FakeResp(200, {"balances": []}))
        assert await adapter.get_account_info() == {"balances": []}

    async def test_get_account_info_not_connected(self):
        with pytest.raises(RuntimeError):
            await BinanceAdapter().get_account_info()


class TestBinanceWebSocket:
    async def test_start_and_stop(self):
        adapter = BinanceAdapter()
        adapter._websocket_loop = AsyncMock()
        await adapter.start_websocket(["btcusdt@kline_1h"])
        assert adapter._ws_running is True
        await adapter.stop_websocket()
        assert adapter._ws_running is False
        assert adapter._ws_task is None

    async def test_start_idempotent(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True
        await adapter.start_websocket(["x"])

    async def test_loop_text_message(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True
        received = []
        ws = FakeWS(
            [make_ws_message(aiohttp.WSMsgType.TEXT, json.dumps({"e": "kline"}))],
            on_exhaust=lambda: setattr(adapter, "_ws_running", False),
        )
        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(ws)):
            await adapter._websocket_loop(["btcusdt@kline_1h"], received.append)
        assert received == [{"e": "kline"}]

    async def test_loop_combined_stream_url(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True
        ws = FakeWS([], on_exhaust=lambda: setattr(adapter, "_ws_running", False))
        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(ws)):
            await adapter._websocket_loop(["a", "b"], None)

    async def test_loop_callback_error_swallowed(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True

        def boom(data):
            raise ValueError("boom")

        ws = FakeWS(
            [make_ws_message(aiohttp.WSMsgType.TEXT, "{}")],
            on_exhaust=lambda: setattr(adapter, "_ws_running", False),
        )
        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(ws)):
            await adapter._websocket_loop(["s"], boom)

    async def test_loop_error_message(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True
        ws = FakeWS(
            [make_ws_message(aiohttp.WSMsgType.ERROR)],
            on_exhaust=lambda: setattr(adapter, "_ws_running", False),
        )
        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(ws)):
            await adapter._websocket_loop(["s"], None)

    async def test_loop_stops_between_messages(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True

        def stop_after_first(data):
            adapter._ws_running = False

        ws = FakeWS([
            make_ws_message(aiohttp.WSMsgType.TEXT, "{}"),
            make_ws_message(aiohttp.WSMsgType.TEXT, "{}"),
        ])
        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(ws)):
            await adapter._websocket_loop(["s"], stop_after_first)

    async def test_loop_cancellation(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True

        class SlowWS:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(10)
                raise StopAsyncIteration

            def exception(self):
                return None

        with patch("aiohttp.ClientSession", return_value=FakeSessionCtx(SlowWS())):
            task = asyncio.create_task(adapter._websocket_loop(["s"], None))
            await asyncio.sleep(0.02)
            task.cancel()
            await task

    async def test_loop_reconnects_after_exception(self):
        adapter = BinanceAdapter()
        adapter._ws_running = True

        class BoomSession:
            async def __aenter__(self):
                adapter._ws_running = False
                return self

            async def __aexit__(self, *args):
                return False

            def ws_connect(self, url):
                raise RuntimeError("connection failed")

        with patch("aiohttp.ClientSession", return_value=BoomSession()), \
             patch("src.adapters.crypto_broker.asyncio.sleep", new=AsyncMock()):
            await adapter._websocket_loop(["s"], None)

    async def test_subscribe_kline(self):
        adapter = BinanceAdapter()
        adapter.start_websocket = AsyncMock()
        received = []
        await adapter.subscribe_kline("BTCUSDT", KlineInterval.H1, received.append)

        handler = adapter._ws_callbacks["btcusdt@kline_1h"][0]
        await handler({"k": {
            "s": "BTCUSDT", "t": 1, "o": "1", "h": "2", "l": "0.5",
            "c": "1.5", "v": "10", "T": 2, "q": "15", "n": 3, "V": "5", "Q": "7",
        }})
        assert received and received[0].close == Decimal("1.5")

    async def test_subscribe_kline_combined(self):
        adapter = BinanceAdapter()
        adapter.start_websocket = AsyncMock()
        received = []
        await adapter.subscribe_kline("BTCUSDT", "1h", received.append, use_combined=True)

        handler = adapter._ws_callbacks["btcusdt@kline_1h"][0]
        await handler({"data": {"k": {"s": "BTCUSDT", "c": "2"}}})
        assert received and received[0].close == Decimal("2")


class TestCoinGecko:
    async def test_get_coins_list(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": "bitcoin", "symbol": "btc"}])
        assert await adapter.get_coins_list() == [{"id": "bitcoin", "symbol": "btc"}]

    async def test_resolve_by_symbol(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": "bitcoin", "symbol": "btc"}])
        assert await adapter._resolve_coin_id("btc") == "bitcoin"

    async def test_resolve_by_id(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": "bitcoin", "symbol": "btc"}])
        assert await adapter._resolve_coin_id("BITCOIN") == "bitcoin"

    async def test_resolve_cached(self):
        adapter = CoinGeckoAdapter()
        adapter._coin_id_cache["btc"] = "bitcoin"
        assert await adapter._resolve_coin_id("btc") == "bitcoin"

    async def test_resolve_unknown(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[])
        with pytest.raises(ValueError):
            await adapter._resolve_coin_id("nope")

    async def test_get_price_list_inputs(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value={"bitcoin": {"usd": 100, "eur": 90}})
        result = await adapter.get_price(["bitcoin"], ["usd", "eur"])
        assert result["bitcoin"]["usd"] == Decimal("100")

    async def test_get_price_scalar_inputs(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value={"bitcoin": {"usd": 100}})
        result = await adapter.get_price("bitcoin")
        assert result["bitcoin"]["usd"] == Decimal("100")

    async def test_get_markets_with_ids(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": "bitcoin", "symbol": "btc"}])
        markets = await adapter.get_markets(coin_ids=["bitcoin"])
        assert markets[0].coin_id == "bitcoin"

    async def test_get_coin_market(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": "bitcoin", "symbol": "btc"}])
        market = await adapter.get_coin_market("btc")
        assert market.coin_id == "bitcoin"

    async def test_get_coin_market_empty(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[])
        adapter._coin_id_cache["btc"] = "bitcoin"
        assert await adapter.get_coin_market("btc") is None

    async def test_get_trending(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value={"coins": [{"item": {}}]})
        assert await adapter.get_trending() == [{"item": {}}]

    async def test_get_top_coins(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value=[{"id": f"c{i}"} for i in range(5)])
        coins = await adapter.get_top_coins(limit=3)
        assert len(coins) == 3

    async def test_get_coin_history(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value={"market_data": {}})
        adapter._coin_id_cache["bitcoin"] = "bitcoin"
        assert await adapter.get_coin_history("bitcoin", "01-01-2023") == {"market_data": {}}

    async def test_get_market_chart(self):
        adapter = CoinGeckoAdapter()
        adapter._get = AsyncMock(return_value={"prices": []})
        adapter._coin_id_cache["bitcoin"] = "bitcoin"
        assert await adapter.get_market_chart("bitcoin") == {"prices": []}

    async def test_get_crypto_bars(self):
        adapter = CoinGeckoAdapter()
        adapter.get_ohlc = AsyncMock(return_value=[[1000, 1, 2, 0.5, 1.5]])
        bars = await adapter.get_crypto_bars("bitcoin")
        assert len(bars) == 1
        assert bars[0].close == Decimal("1.5")


class TestFactory:
    async def test_factory_binance(self):
        adapter = await get_crypto_adapter("binance")
        assert isinstance(adapter, BinanceAdapter)
        await adapter.disconnect()

    async def test_factory_coingecko(self):
        adapter = await get_crypto_adapter("coingecko")
        assert isinstance(adapter, CoinGeckoAdapter)
        await adapter.disconnect()

    async def test_factory_unknown(self):
        with pytest.raises(ValueError):
            await get_crypto_adapter("unknown-exchange")
