"""Additional tests for the IG Markets broker adapter client and error paths."""

import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from src.adapters.ig_broker import (
    IGApiClient,
    IGBroker,
    IGConfig,
    IGEnvironment,
    IGPosition,
    IGWorkingOrder,
    IGDealConfirmation,
    IGMarketSnapshot,
    IGDealStatus,
    IGOrderType,
    IGDealDirection,
    IGPositionDirection,
    IGPriceResolution,
    IGApiError,
    IGAuthenticationError,
    IGRateLimitError,
)
from src.trading.models import Symbol, Order, OrderType, OrderSide


class FakeResp:
    """Fake aiohttp response usable as an async context manager."""

    def __init__(self, status=200, json_data=None, text="", headers=None):
        self.status = status
        self._json = json_data if json_data is not None else {}
        self._text = text
        self.headers = headers if headers is not None else {"Content-Type": "application/json"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, request_resp=None, get_resp=None, post_resp=None, delete_resp=None):
        self._request_resp = request_resp
        self._get_resp = get_resp
        self._post_resp = post_resp
        self._delete_resp = delete_resp

    def request(self, **kwargs):
        return self._request_resp

    def get(self, *args, **kwargs):
        return self._get_resp

    def post(self, *args, **kwargs):
        return self._post_resp

    def delete(self, *args, **kwargs):
        return self._delete_resp

    async def close(self):
        pass


def make_config():
    return IGConfig(api_key="k", username="u", password="p", environment=IGEnvironment.DEMO)


def make_order(symbol="EURUSD", order_type=OrderType.MARKET, side=OrderSide.BUY, quantity="1"):
    return Order(
        symbol=Symbol(symbol), side=side, quantity=Decimal(quantity),
        order_type=order_type,
        limit_price=Decimal("1") if order_type == OrderType.LIMIT else None,
        stop_price=Decimal("1") if order_type == OrderType.STOP else None,
    )


def make_position(deal_id="D1", epic="CS.D.EURUSD.TODAY.IP", direction=IGPositionDirection.BUY):
    return IGPosition(
        deal_id=deal_id, epic=epic, instrument_name="EUR/USD",
        direction=direction, size=Decimal("1"), level=Decimal("1.10"),
    )


class TestConfigAndModels:
    def test_get_versioned_url_adds_slash(self):
        cfg = make_config()
        assert cfg.get_versioned_url("x").endswith("/x")

    def test_position_unrealized_pnl(self):
        assert make_position().unrealized_pnl == Decimal("0")

    def test_snapshot_mid_price_none(self):
        snap = IGMarketSnapshot(epic="E", instrument_name="n", instrument_type="t")
        assert snap.mid_price is None


class TestIGApiClientEndpoints:
    @pytest.fixture
    def client(self):
        return IGApiClient(make_config())

    async def test_get_account_details_active_account(self, client):
        client._active_account_id = "ACC1"
        client._request = AsyncMock(return_value={"accounts": [
            {"accountId": "ACC1", "accountType": "CFD", "currency": "USD", "balance": "100"},
        ]})
        details = await client.get_account_details()
        assert details["account_id"] == "ACC1"

    async def test_get_account_details_fallback_first(self, client):
        client._active_account_id = "MISSING"
        client._request = AsyncMock(return_value={"accounts": [
            {"accountId": "ACC2", "accountType": "CFD", "currency": "USD", "balance": "100"},
        ]})
        details = await client.get_account_details()
        assert details["account_id"] == "ACC2"

    async def test_get_account_details_no_accounts(self, client):
        client._request = AsyncMock(return_value={"accounts": []})
        assert await client.get_account_details() is None

    async def test_get_positions(self, client):
        client._request = AsyncMock(return_value={"positions": [
            {"position": {"dealId": "D1", "epic": "CS.D.EURUSD.TODAY.IP",
                          "direction": "BUY", "size": "1", "level": "1.1"}},
        ]})
        positions = await client.get_positions()
        assert len(positions) == 1
        assert positions[0].deal_id == "D1"

    async def test_get_position_by_deal_id_success(self, client):
        client._request = AsyncMock(return_value={"position": {
            "dealId": "D1", "epic": "E", "direction": "BUY", "size": "1", "level": "1",
        }})
        position = await client.get_position_by_deal_id("D1")
        assert position.deal_id == "D1"

    async def test_get_position_by_deal_id_error(self, client):
        client._request = AsyncMock(side_effect=IGApiError("nope", 404))
        assert await client.get_position_by_deal_id("D1") is None

    async def test_create_position(self, client):
        client._request = AsyncMock(return_value={"dealReference": "REF1"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="REF1", deal_status=IGDealStatus.ACCEPTED,
        ))
        confirmation = await client.create_position(
            "CS.D.EURUSD.TODAY.IP", IGDealDirection.BUY, Decimal("1"),
            stop_level=Decimal("1.0"), limit_level=Decimal("1.2"), guaranteed_stop=True,
        )
        assert confirmation.deal_status == IGDealStatus.ACCEPTED

    async def test_close_position(self, client):
        client._request = AsyncMock(return_value={"dealReference": "REF2"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="REF2", deal_status=IGDealStatus.CLOSED,
        ))
        confirmation = await client.close_position(
            "D1", size=Decimal("1"), direction=IGDealDirection.SELL,
        )
        assert confirmation.deal_status == IGDealStatus.CLOSED

    async def test_get_working_orders(self, client):
        client._request = AsyncMock(return_value={"workingOrders": [
            {"workingOrderData": {"dealId": "W1", "epic": "E", "direction": "BUY",
                                  "orderType": "LIMIT", "level": "1", "size": "1"}},
        ]})
        orders = await client.get_working_orders()
        assert len(orders) == 1
        assert orders[0].deal_id == "W1"

    async def test_create_working_order_with_good_till(self, client):
        client._request = AsyncMock(return_value={"dealReference": "R"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.ACCEPTED,
        ))
        confirmation = await client.create_working_order(
            "E", IGDealDirection.BUY, IGOrderType.LIMIT, Decimal("1"), Decimal("1"),
            stop_level=Decimal("0.9"), limit_level=Decimal("1.1"),
            guaranteed_stop=True, good_till_date=datetime(2023, 1, 1),
        )
        assert confirmation.deal_status == IGDealStatus.ACCEPTED

    async def test_create_working_order_gtc(self, client):
        client._request = AsyncMock(return_value={"dealReference": "R"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.ACCEPTED,
        ))
        confirmation = await client.create_working_order(
            "E", IGDealDirection.SELL, IGOrderType.STOP, Decimal("1"), Decimal("1"),
        )
        assert confirmation.deal_status == IGDealStatus.ACCEPTED

    async def test_update_working_order(self, client):
        client._request = AsyncMock(return_value={"dealReference": "R"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.AMENDED,
        ))
        confirmation = await client.update_working_order(
            "W1", Decimal("1"), Decimal("2"),
            stop_level=Decimal("0.9"), limit_level=Decimal("1.1"),
            good_till_date=datetime(2023, 1, 1),
        )
        assert confirmation.deal_status == IGDealStatus.AMENDED

    async def test_delete_working_order(self, client):
        client._request = AsyncMock(return_value={"dealReference": "R"})
        client._wait_for_confirmation = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.DELETED,
        ))
        confirmation = await client.delete_working_order("W1")
        assert confirmation.deal_status == IGDealStatus.DELETED

    async def test_get_market_snapshot(self, client):
        client._request = AsyncMock(return_value={
            "instrument": {"name": "EUR/USD", "type": "CURRENCIES"},
            "snapshot": {"bid": "1.1", "offer": "1.2",
                         "updateTime": "2023-01-01T00:00:00Z", "marketStatus": "TRADEABLE"},
        })
        snapshot = await client.get_market_snapshot("CS.D.EURUSD.TODAY.IP")
        assert snapshot.instrument_name == "EUR/USD"
        assert snapshot.mid_price == Decimal("1.15")

    async def test_get_market_snapshot_error(self, client):
        client._request = AsyncMock(side_effect=IGApiError("x", 500))
        assert await client.get_market_snapshot("E") is None

    async def test_get_market_snapshots(self, client):
        client._request = AsyncMock(return_value={"marketDetails": [
            {"epic": "E1", "instrument": {"name": "n", "type": "t"},
             "snapshot": {"bid": "1", "offer": "2"}},
        ]})
        snapshots = await client.get_market_snapshots(["E1"])
        assert len(snapshots) == 1
        assert snapshots[0].epic == "E1"

    async def test_get_market_snapshots_error_swallowed(self, client):
        client._request = AsyncMock(side_effect=Exception("boom"))
        assert await client.get_market_snapshots(["E1"]) == []

    async def test_get_price_history_with_range(self, client):
        client._request = AsyncMock(return_value={
            "prices": [{"x": 1}], "allowance": {"remainingAllowance": 0},
        })
        prices = await client.get_price_history(
            "E", resolution=IGPriceResolution.HOUR,
            start=datetime(2023, 1, 1), end=datetime(2023, 1, 2),
        )
        assert prices == [{"x": 1}]

    async def test_get_price_history_no_range(self, client):
        client._request = AsyncMock(return_value={"prices": []})
        assert await client.get_price_history("E", max_points=5000) == []

    async def test_search_markets(self, client):
        client._request = AsyncMock(return_value={"markets": [{"epic": "E"}]})
        assert await client.search_markets("eur") == [{"epic": "E"}]

    async def test_wait_for_confirmation_success(self, client):
        client._request = AsyncMock(return_value={"dealReference": "R", "dealStatus": "ACCEPTED"})
        confirmation = await client._wait_for_confirmation("R", max_retries=1, delay=0)
        assert confirmation.deal_status == IGDealStatus.ACCEPTED

    async def test_wait_for_confirmation_timeout(self, client):
        client._request = AsyncMock(return_value={})
        confirmation = await client._wait_for_confirmation("R", max_retries=2, delay=0)
        assert confirmation.deal_status == IGDealStatus.REJECTED

    async def test_wait_for_confirmation_error(self, client):
        client._request = AsyncMock(side_effect=Exception("boom"))
        confirmation = await client._wait_for_confirmation("R", max_retries=1, delay=0)
        assert confirmation.deal_status == IGDealStatus.REJECTED

    def test_helpers(self, client):
        assert client._generate_deal_reference().startswith("AE")
        assert client._is_forex_epic("CS.D.EURUSD.TODAY.IP") is True
        assert client._is_forex_epic("AAPL") is False

    def test_auth_headers_with_tokens(self, client):
        client._cst = "c"
        client._security_token = "s"
        client._active_account_id = "ACC"
        headers = client._get_auth_headers()
        assert headers["X-IG-API-KEY"] == "k"
        assert headers["CST"] == "c"
        assert headers["X-SECURITY-TOKEN"] == "s"
        assert headers["IG-ACCOUNT-ID"] == "ACC"


class TestIGApiClientSession:
    @pytest.fixture
    def client(self):
        return IGApiClient(make_config())

    async def test_authenticate_success_first_account(self, client):
        resp = FakeResp(200, {"accounts": [{"accountId": "ACC1"}]},
                        headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
        client._session = FakeSession(post_resp=resp)
        assert await client.authenticate() is True
        assert client._cst == "c"
        assert client._active_account_id == "ACC1"

    async def test_authenticate_matching_identifier(self, client):
        resp = FakeResp(200, {"accounts": [{"accountId": "ACC1"}, {"accountId": "ACC2"}]},
                        headers={"CST": "c", "X-SECURITY-TOKEN": "s"})
        client._session = FakeSession(post_resp=resp)
        assert await client.authenticate("ACC2") is True
        assert client._active_account_id == "ACC2"

    async def test_authenticate_creates_session(self, client):
        resp = FakeResp(200, {"accounts": []}, headers={"CST": "c", "X-SECURITY-TOKEN": "s"})

        async def fake_connect():
            client._session = FakeSession(post_resp=resp)

        client.connect = fake_connect
        assert await client.authenticate() is True

    async def test_authenticate_non_200(self, client):
        client._session = FakeSession(post_resp=FakeResp(401, text="bad"))
        assert await client.authenticate() is False

    async def test_authenticate_missing_tokens(self, client):
        client._session = FakeSession(post_resp=FakeResp(200, {"accounts": []}, headers={}))
        assert await client.authenticate() is False

    async def test_authenticate_timeout(self, client):
        session = FakeSession()
        session.post = MagicMock(side_effect=asyncio.TimeoutError)
        client._session = session
        assert await client.authenticate() is False

    async def test_authenticate_client_error(self, client):
        session = FakeSession()
        session.post = MagicMock(side_effect=aiohttp.ClientError("x"))
        client._session = session
        assert await client.authenticate() is False

    async def test_ensure_authenticated_already(self, client):
        client._cst = "c"
        client._security_token = "s"
        assert await client.ensure_authenticated() is True

    async def test_ensure_authenticated_renews(self, client):
        client.authenticate = AsyncMock(return_value=True)
        assert await client.ensure_authenticated() is True

    async def test_logout_not_authenticated(self, client):
        assert await client.logout() is True

    async def test_logout_success(self, client):
        client._cst = "c"
        client._security_token = "s"
        client._session = FakeSession(delete_resp=FakeResp(200))
        assert await client.logout() is True
        assert client._cst is None

    async def test_logout_exception(self, client):
        client._cst = "c"
        client._security_token = "s"
        session = FakeSession()
        session.delete = MagicMock(side_effect=Exception("boom"))
        client._session = session
        assert await client.logout() is True

    async def test_get_accounts_non_200(self, client):
        client._session = FakeSession(get_resp=FakeResp(500))
        assert await client.get_accounts() == []

    async def test_get_accounts_exception(self, client):
        session = FakeSession()
        session.get = MagicMock(side_effect=Exception("boom"))
        client._session = session
        assert await client.get_accounts() == []

    async def test_do_request_401_clears_tokens(self, client):
        client._cst = "c"
        client._security_token = "s"
        client._session = FakeSession(request_resp=FakeResp(401))
        with pytest.raises(IGAuthenticationError):
            await client._do_request("GET", "url", {})
        assert client._cst is None

    async def test_do_request_429(self, client):
        client._session = FakeSession(request_resp=FakeResp(
            429, headers={"Retry-After": "7", "Content-Type": "application/json"},
        ))
        with pytest.raises(IGRateLimitError):
            await client._do_request("GET", "url", {})

    async def test_do_request_error_status(self, client):
        client._session = FakeSession(request_resp=FakeResp(500, text="boom"))
        with pytest.raises(IGApiError):
            await client._do_request("GET", "url", {})

    async def test_do_request_json(self, client):
        client._session = FakeSession(request_resp=FakeResp(
            200, {"a": 1}, headers={"Content-Type": "application/json"},
        ))
        assert await client._do_request("GET", "url", {}) == {"a": 1}

    async def test_do_request_non_json(self, client):
        client._session = FakeSession(request_resp=FakeResp(
            204, headers={"Content-Type": "text/plain"},
        ))
        assert await client._do_request("GET", "url", {}) == {}

    async def test_request_auth_failure(self, client):
        client._session = FakeSession()
        client.authenticate = AsyncMock(return_value=False)
        with pytest.raises(IGAuthenticationError):
            await client._request("GET", "/x", auth_required=True)

    async def test_request_success(self, client):
        client._cst = "c"
        client._security_token = "s"
        client._session = FakeSession()
        client._do_request = AsyncMock(return_value={"ok": True})
        assert await client._request("GET", "/x") == {"ok": True}

    async def test_request_creates_session(self, client):
        async def fake_connect():
            client._session = FakeSession()

        client.connect = fake_connect
        client._cst = "c"
        client._security_token = "s"
        client._do_request = AsyncMock(return_value={"ok": True})
        assert await client._request("GET", "/x") == {"ok": True}

    async def test_get_accounts_creates_session(self, client):
        async def fake_connect():
            client._session = FakeSession(get_resp=FakeResp(200, {"accounts": []}))

        client.connect = fake_connect
        assert await client.get_accounts() == []


class TestIGBrokerTransactionHandler:
    @pytest.fixture
    def broker(self):
        return IGBroker(make_config())

    async def test_process_order_market(self, broker):
        broker.submit_order = AsyncMock(return_value=True)
        order = make_order(order_type=OrderType.MARKET)
        order.broker_order_id = "D1"
        event = await broker.process_order(order)
        assert event.status.name == "FILLED"
        assert event.filled_quantity == Decimal("1")

    async def test_process_order_limit(self, broker):
        broker.submit_order = AsyncMock(return_value=True)
        order = make_order(order_type=OrderType.LIMIT)
        order.broker_order_id = "D2"
        event = await broker.process_order(order)
        assert event.status.name == "SUBMITTED"

    async def test_process_order_failure(self, broker):
        broker.submit_order = AsyncMock(return_value=False)
        event = await broker.process_order(make_order())
        assert event.status.name == "REJECTED"

    async def test_update_order_with_id(self, broker):
        broker.cancel_order = AsyncMock(return_value=True)
        broker.process_order = AsyncMock(return_value="EVENT")
        order = make_order()
        order.broker_order_id = "D1"
        assert await broker.update_order(order) == "EVENT"
        broker.cancel_order.assert_awaited_once()

    async def test_update_order_without_id(self, broker):
        broker.process_order = AsyncMock(return_value="EVENT")
        assert await broker.update_order(make_order()) == "EVENT"

    async def test_get_open_orders_filtered(self, broker):
        first = make_order("EURUSD")
        first.broker_order_id = "1"
        second = make_order("AAPL")
        second.broker_order_id = "2"
        broker.get_orders = AsyncMock(return_value=[first, second])
        orders = await broker.get_open_orders(Symbol("EURUSD"))
        assert len(orders) == 1
        assert orders[0].broker_order_id == "1"

    async def test_get_open_orders_all(self, broker):
        broker.get_orders = AsyncMock(return_value=["a", "b"])
        assert await broker.get_open_orders() == ["a", "b"]

    async def test_get_order_by_id_position(self, broker):
        broker._positions = {"D1": make_position(deal_id="D1")}
        order = await broker.get_order_by_id("D1")
        assert order.symbol.ticker == "EURUSD"

    async def test_get_order_by_id_working_order(self, broker):
        broker._orders = {"W1": IGWorkingOrder(
            deal_id="W1", direction=IGDealDirection.BUY, epic="CS.D.EURUSD.TODAY.IP",
            order_type=IGOrderType.LIMIT, level=Decimal("1"), size=Decimal("1"),
        )}
        order = await broker.get_order_by_id("W1")
        assert order.broker_order_id == "W1"

    async def test_get_order_by_id_not_found(self, broker):
        assert await broker.get_order_by_id("missing") is None


class TestIGBrokerErrorBranches:
    @pytest.fixture
    def broker(self):
        return IGBroker(make_config())

    async def test_connect_exception(self, broker):
        broker._client.connect = AsyncMock(side_effect=Exception("boom"))
        assert await broker.connect() is False

    async def test_disconnect_logout_exception(self, broker):
        broker._client.logout = AsyncMock(side_effect=Exception("boom"))
        broker._client.disconnect = AsyncMock()
        await broker.disconnect()
        assert broker.is_connected() is False

    async def test_submit_order_unsupported_type(self, broker):
        broker._connected = True
        order = make_order()
        order.order_type = "WEIRD"
        assert await broker.submit_order(order) is False

    async def test_submit_order_generic_exception(self, broker):
        broker._connected = True
        broker._client.create_position = AsyncMock(side_effect=Exception("boom"))
        assert await broker.submit_order(make_order()) is False

    async def test_submit_order_rate_limited(self, broker):
        broker._connected = True
        broker._client.create_position = AsyncMock(side_effect=IGRateLimitError("slow down"))
        assert await broker.submit_order(make_order()) is False

    async def test_cancel_order_not_connected(self, broker):
        order = make_order()
        order.broker_order_id = "1"
        assert await broker.cancel_order(order) is False

    async def test_cancel_order_no_id(self, broker):
        broker._connected = True
        assert await broker.cancel_order(make_order()) is False

    async def test_cancel_order_failure_status(self, broker):
        broker._connected = True
        order = make_order()
        order.broker_order_id = "W1"
        broker._client.delete_working_order = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.REJECTED, reason="no",
        ))
        assert await broker.cancel_order(order) is False

    async def test_cancel_order_exception(self, broker):
        broker._connected = True
        order = make_order()
        order.broker_order_id = "W1"
        broker._client.delete_working_order = AsyncMock(side_effect=Exception("boom"))
        assert await broker.cancel_order(order) is False

    async def test_get_account_info_not_connected(self, broker):
        assert await broker.get_account_info() == {}

    async def test_get_account_info_exception(self, broker):
        broker._connected = True
        broker._client.get_account_details = AsyncMock(side_effect=Exception("boom"))
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

    async def test_close_position_failure(self, broker):
        broker._connected = True
        broker._positions = {"D1": make_position()}
        broker._client.close_position = AsyncMock(return_value=IGDealConfirmation(
            deal_reference="R", deal_status=IGDealStatus.REJECTED, reason="x",
        ))
        assert await broker.close_position(Symbol("EURUSD")) is False

    async def test_close_position_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_epic = MagicMock(side_effect=Exception("boom"))
        assert await broker.close_position(Symbol("EURUSD")) is False

    async def test_get_market_price_not_connected(self, broker):
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_market_price_no_snapshot(self, broker):
        broker._connected = True
        broker._client.get_market_snapshot = AsyncMock(return_value=None)
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_market_price_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_epic = MagicMock(side_effect=Exception("boom"))
        assert await broker.get_market_price(Symbol("EURUSD")) is None

    async def test_get_historical_data_not_connected(self, broker):
        assert await broker.get_historical_data(Symbol("EURUSD")) == []

    async def test_get_historical_data_exception(self, broker):
        broker._connected = True
        broker._convert_symbol_to_epic = MagicMock(side_effect=Exception("boom"))
        assert await broker.get_historical_data(Symbol("EURUSD")) == []

    async def test_refresh_positions_exception(self, broker):
        broker._client.get_positions = AsyncMock(side_effect=Exception("boom"))
        await broker._refresh_positions()

    async def test_refresh_orders_exception(self, broker):
        broker._client.get_working_orders = AsyncMock(side_effect=Exception("boom"))
        await broker._refresh_orders()

    def test_convert_symbol_to_epic_forex(self, broker):
        assert broker._convert_symbol_to_epic(Symbol("EURUSD")) == "CS.D.EURUSD.TODAY.IP"

    def test_convert_symbol_to_epic_passthrough(self, broker):
        epic = "CS.D.EURUSD.TODAY.IP"
        assert broker._convert_symbol_to_epic(Symbol(epic)) == epic

    def test_convert_symbol_to_epic_other(self, broker):
        assert broker._convert_symbol_to_epic(Symbol("AAPL")) == "AAPL"

    def test_convert_epic_to_symbol_forex(self, broker):
        assert broker._convert_epic_to_symbol("CS.D.EURUSD.TODAY.IP").ticker == "EURUSD"

    def test_convert_epic_to_symbol_index(self, broker):
        assert broker._convert_epic_to_symbol("IX.D.FTSE.DAILY.IP").ticker == "FTSE"

    def test_convert_epic_to_symbol_crude(self, broker):
        assert broker._convert_epic_to_symbol("CRUDE.OIL").ticker == "CRUDE.OIL"

    def test_convert_epic_to_symbol_fallback(self, broker):
        assert broker._convert_epic_to_symbol("WEIRD").ticker == "WEIRD"

    async def test_get_historical_data_success(self, broker):
        broker._connected = True
        broker._client.get_price_history = AsyncMock(return_value=[{"x": 1}])
        assert await broker.get_historical_data(Symbol("EURUSD")) == [{"x": 1}]

    async def test_refresh_positions_success(self, broker):
        broker._client.get_positions = AsyncMock(return_value=[make_position(deal_id="D1")])
        await broker._refresh_positions()
        assert "D1" in broker._positions

    async def test_refresh_orders_success(self, broker):
        broker._client.get_working_orders = AsyncMock(return_value=[IGWorkingOrder(
            deal_id="W1", direction=IGDealDirection.BUY, epic="E",
            order_type=IGOrderType.LIMIT, level=Decimal("1"), size=Decimal("1"),
        )])
        await broker._refresh_orders()
        assert "W1" in broker._orders
