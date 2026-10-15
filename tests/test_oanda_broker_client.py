"""Additional tests for the OANDA broker client and error paths."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from src.adapters.oanda_broker import (
    OandaApiClient,
    OandaBroker,
    OandaConfig,
    OandaEnvironment,
    OandaTimeframe,
    OandaAccount,
    OandaOrder,
    OandaPosition,
    create_oanda_broker,
)
from src.engine.interfaces import Symbol, Order, OrderType, PositionSide


class FakeResp:
    def __init__(self, status=200, json_data=None, headers=None):
        self.status = status
        self._json = json_data if json_data is not None else {}
        self.headers = headers if headers is not None else {}

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
    def __init__(self, resps=None):
        self._resps = list(resps or [])

    def request(self, **kwargs):
        item = self._resps.pop(0) if self._resps else FakeResp(200, {})
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self):
        pass


def make_config(**kwargs):
    base = dict(
        api_key="key", account_id="ACC",
        environment=OandaEnvironment.PRACTICE, retry_delay=0.0,
    )
    base.update(kwargs)
    return OandaConfig(**base)


def make_account():
    return OandaAccount(
        id="ACC", currency="USD", balance=Decimal("1000"),
        unrealized_pl=Decimal("0"), realized_pl=Decimal("0"),
        margin_used=Decimal("0"), margin_available=Decimal("1000"),
        open_positions=0, open_orders=0, last_updated=datetime.now(),
    )


def make_oanda_position(instrument="EUR_USD", side="long"):
    return OandaPosition(
        instrument=instrument, side=side, units=Decimal("1000"),
        avg_price=Decimal("1.10"), current_price=Decimal("1.11"),
        unrealized_pl=Decimal("10"), margin_used=Decimal("100"),
    )


def make_oanda_order(order_id="1"):
    return OandaOrder(
        id=order_id, instrument="EUR_USD", type="MARKET", side="buy",
        units=Decimal("1000"), price=None, stop_loss=None, take_profit=None,
        status="PENDING", create_time=None, cancel_time=None, fill_time=None,
    )


def make_order(order_type=OrderType.MARKET, price=None):
    return Order(
        id="", symbol=Symbol("EURUSD"), order_type=order_type,
        side="BUY", quantity=Decimal("1000"), price=price,
    )


class TestOandaApiClientRequest:
    @pytest.fixture
    def client(self):
        return OandaApiClient(make_config(retry_attempts=3))

    async def test_request_success(self, client):
        client._session = FakeSession([FakeResp(200, {"ok": True})])
        assert await client._request("GET", "/x") == {"ok": True}

    async def test_request_rate_limited_then_success(self, client):
        client._session = FakeSession([
            FakeResp(429, headers={"Retry-After": "0"}),
            FakeResp(200, {"ok": True}),
        ])
        assert await client._request("GET", "/x") == {"ok": True}

    async def test_request_retries_client_error(self, client):
        client._session = FakeSession([
            aiohttp.ClientError("boom"),
            FakeResp(200, {"ok": True}),
        ])
        assert await client._request("GET", "/x") == {"ok": True}

    async def test_request_raises_after_exhausted(self):
        client = OandaApiClient(make_config(retry_attempts=2))
        client._session = FakeSession([aiohttp.ClientError("boom")] * 2)
        with pytest.raises(aiohttp.ClientError):
            await client._request("GET", "/x")

    async def test_request_exhausted_without_response(self):
        client = OandaApiClient(make_config(retry_attempts=1))
        client._session = FakeSession([FakeResp(429, headers={"Retry-After": "0"})])
        with pytest.raises(Exception):
            await client._request("GET", "/x")


class TestOandaApiClientEndpoints:
    @pytest.fixture
    def client(self):
        return OandaApiClient(make_config())

    async def test_get_account(self, client):
        client._request = AsyncMock(return_value={"account": {
            "id": "ACC", "currency": "USD", "balance": "1000",
            "unrealizedPL": "0", "realizedPL": "0", "marginUsed": "0",
            "marginAvailable": "1000", "openPositionCount": 0,
            "openOrderCount": 0, "lastTransactionID": "1",
        }})
        account = await client.get_account()
        assert account.id == "ACC"

    async def test_get_positions(self, client):
        client._request = AsyncMock(return_value={"positions": [{
            "instrument": "EUR_USD",
            "long": {"units": "1000", "averagePrice": "1.1",
                     "unrealizedPL": "10", "marginUsed": "100"},
            "short": {"units": "0", "averagePrice": "1.1",
                      "unrealizedPL": "0", "marginUsed": "0"},
        }]})
        positions = await client.get_positions()
        assert len(positions) == 1
        assert positions[0].side == "long"

    async def test_get_orders(self, client):
        client._request = AsyncMock(return_value={"orders": [{
            "id": "1", "instrument": "EUR_USD", "units": "1000", "type": "MARKET",
        }]})
        orders = await client.get_orders()
        assert len(orders) == 1

    async def test_get_orders_empty(self, client):
        client._request = AsyncMock(return_value={})
        assert await client.get_orders() == []

    async def test_create_market_order_with_stops(self, client):
        client._request = AsyncMock(return_value={"orderCreateTransaction": {
            "id": "1", "instrument": "EUR_USD", "units": "1000", "type": "MARKET",
        }})
        await client.create_market_order("EUR_USD", 1000, stop_loss=1.0, take_profit=1.2)
        params = client._request.call_args.kwargs["data"]
        assert params["order"]["stopLossOnFill"] == {"price": "1.0"}
        assert params["order"]["takeProfitOnFill"] == {"price": "1.2"}

    async def test_create_limit_order_with_stops(self, client):
        client._request = AsyncMock(return_value={"orderCreateTransaction": {
            "id": "2", "instrument": "EUR_USD", "units": "1000", "type": "LIMIT",
        }})
        await client.create_limit_order("EUR_USD", 1000, 1.1, stop_loss=1.0, take_profit=1.2)
        params = client._request.call_args.kwargs["data"]
        assert params["order"]["stopLossOnFill"] == {"price": "1.0"}
        assert params["order"]["takeProfitOnFill"] == {"price": "1.2"}

    async def test_cancel_order_success(self, client):
        client._request = AsyncMock(return_value={})
        assert await client.cancel_order("1") is True

    async def test_cancel_order_failure(self, client):
        client._request = AsyncMock(side_effect=Exception("boom"))
        assert await client.cancel_order("1") is False

    async def test_close_position_success(self, client):
        client._request = AsyncMock(return_value={})
        assert await client.close_position("EUR_USD") is True

    async def test_close_position_failure(self, client):
        client._request = AsyncMock(side_effect=Exception("boom"))
        assert await client.close_position("EUR_USD") is False

    async def test_get_pricing(self, client):
        client._request = AsyncMock(return_value={"prices": [{"instrument": "EUR_USD"}]})
        assert await client.get_pricing(["EUR_USD"]) == [{"instrument": "EUR_USD"}]

    async def test_get_candles_with_range(self, client):
        client._request = AsyncMock(return_value={"candles": [{"t": 1}]})
        candles = await client.get_candles(
            "EUR_USD", OandaTimeframe.H1, count=10,
            from_time=datetime(2023, 1, 1), to_time=datetime(2023, 1, 2),
        )
        assert candles == [{"t": 1}]
        params = client._request.call_args.kwargs["params"]
        assert "from" in params and "to" in params

    async def test_get_candles_no_range(self, client):
        client._request = AsyncMock(return_value={})
        assert await client.get_candles("EUR_USD", OandaTimeframe.H1) == []


class TestOandaBrokerSync:
    @pytest.fixture
    def broker(self):
        return OandaBroker(make_config())

    def test_sync_methods_raise(self, broker):
        with pytest.raises(NotImplementedError):
            broker.process_order(make_order())
        with pytest.raises(NotImplementedError):
            broker.cancel_order("1")
        with pytest.raises(NotImplementedError):
            broker.update_order(make_order())

    def test_callbacks(self, broker):
        broker.set_order_filled_callback(lambda o: None)
        broker.set_position_opened_callback(lambda p: None)
        broker.set_position_closed_callback(lambda p: None)
        assert broker._on_order_filled is not None
        assert broker._on_position_opened is not None
        assert broker._on_position_closed is not None

    async def test_connect_exception(self, broker):
        broker._client.connect = AsyncMock(side_effect=Exception("boom"))
        assert await broker.connect() is False

    async def test_submit_order_not_connected(self, broker):
        assert await broker.submit_order(make_order()) is False

    async def test_submit_order_unsupported_type(self, broker):
        broker._connected = True
        assert await broker.submit_order(make_order(OrderType.STOP_LIMIT)) is False

    async def test_submit_order_exception(self, broker):
        broker._connected = True
        broker._client.create_market_order = AsyncMock(side_effect=Exception("boom"))
        assert await broker.submit_order(make_order()) is False

    async def test_cancel_order_by_id_no_id(self, broker):
        assert await broker.cancel_order_by_id("") is False

    async def test_cancel_order_by_id_exception(self, broker):
        broker._client.cancel_order = AsyncMock(side_effect=Exception("boom"))
        assert await broker.cancel_order_by_id("1") is False

    async def test_cancel_order_by_id_updates_status(self, broker):
        broker._client.cancel_order = AsyncMock(return_value=True)
        broker._orders["1"] = make_oanda_order("1")
        assert await broker.cancel_order_by_id("1") is True
        assert broker._orders["1"].status == "CANCELLED"

    async def test_get_account_info_not_connected(self, broker):
        assert await broker.get_account_info() == {}

    async def test_get_account_info_exception(self, broker):
        broker._connected = True
        broker._client.get_account = AsyncMock(side_effect=Exception("boom"))
        assert await broker.get_account_info() == {}

    async def test_get_positions_not_connected(self, broker):
        assert await broker.get_positions() == []

    async def test_get_positions_exception(self, broker):
        broker._connected = True
        broker._refresh_positions = AsyncMock(side_effect=Exception("boom"))
        assert await broker.get_positions() == []

    async def test_get_orders_not_connected(self, broker):
        assert await broker.get_orders() == []

    async def test_get_orders_exception(self, broker):
        broker._connected = True
        broker._refresh_orders = AsyncMock(side_effect=Exception("boom"))
        assert await broker.get_orders() == []

    async def test_close_position_not_connected(self, broker):
        assert await broker.close_position(Symbol("EURUSD")) is False

    async def test_close_position_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_oanda = MagicMock(side_effect=Exception("boom"))
        assert await broker.close_position(Symbol("EURUSD")) is False

    async def test_refresh_positions_success(self, broker):
        broker._client.get_positions = AsyncMock(return_value=[make_oanda_position()])
        await broker._refresh_positions()
        assert "EUR_USD" in broker._positions

    async def test_refresh_positions_exception(self, broker):
        broker._client.get_positions = AsyncMock(side_effect=Exception("boom"))
        await broker._refresh_positions()

    async def test_refresh_orders_success(self, broker):
        broker._client.get_orders = AsyncMock(return_value=[make_oanda_order("1")])
        await broker._refresh_orders()
        assert "1" in broker._orders

    async def test_refresh_orders_exception(self, broker):
        broker._client.get_orders = AsyncMock(side_effect=Exception("boom"))
        await broker._refresh_orders()

    def test_convert_symbol_to_oanda(self, broker):
        assert broker._convert_symbol_to_oanda(Symbol("EUR_USD")) == "EUR_USD"
        assert broker._convert_symbol_to_oanda(Symbol("EURUSD")) == "EUR_USD"
        assert broker._convert_symbol_to_oanda(Symbol("AAPL")) == "AAPL_USD"

    def test_convert_oanda_to_symbol(self, broker):
        assert broker._convert_oanda_to_symbol("EUR_USD").ticker == "EURUSD"
        assert broker._convert_oanda_to_symbol("AAPL").ticker == "AAPL"

    async def test_get_market_price_not_connected(self, broker):
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_market_price_closeout_mid(self, broker):
        broker._connected = True
        broker._client.get_pricing = AsyncMock(return_value=[{"closeoutMid": "1.15"}])
        assert await broker.get_market_price(Symbol("EURUSD")) == 1.15

    async def test_get_market_price_bid_ask(self, broker):
        broker._connected = True
        broker._client.get_pricing = AsyncMock(return_value=[{"bid": "1.0", "ask": "1.2"}])
        assert await broker.get_market_price(Symbol("EURUSD")) == pytest.approx(1.1)

    async def test_get_market_price_no_pricing(self, broker):
        broker._connected = True
        broker._client.get_pricing = AsyncMock(return_value=[])
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_market_price_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_oanda = MagicMock(side_effect=Exception("boom"))
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_historical_data_not_connected(self, broker):
        assert await broker.get_historical_data(Symbol("EURUSD"), OandaTimeframe.H1) == []

    async def test_get_historical_data_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_oanda = MagicMock(side_effect=Exception("boom"))
        assert await broker.get_historical_data(Symbol("EURUSD"), OandaTimeframe.H1) == []


class TestFactory:
    def test_create_oanda_broker(self):
        broker = create_oanda_broker("key", "ACC", environment=OandaEnvironment.PRACTICE)
        assert isinstance(broker, OandaBroker)
        assert broker._config.environment == OandaEnvironment.PRACTICE
