"""Tests for IG Markets broker adapter"""

import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal

from src.adapters.ig_broker import (
    IGBroker, IGApiClient, IGConfig, IGEnvironment,
    IGAccount, IGPosition, IGWorkingOrder, IGDealConfirmation,
    IGDealStatus, IGDealDirection, IGOrderType, IGPositionDirection,
    IGAccountType, IGTimeInForce, IGMarketSnapshot,
    create_ig_broker, IGError, IGAuthenticationError, IGConnectionError,
    IGRateLimitError, IGApiError,
)
from src.trading.models import Symbol, Order, OrderType, OrderSide, OrderStatus


class TestIGConfig:
    """Test IG configuration"""

    def test_config_creation(self):
        """Test configuration initialization"""
        config = IGConfig(
            api_key="test_api_key",
            username="test_user",
            password="test_pass",
            account_id="ABCDE123",
            environment=IGEnvironment.DEMO,
            timeout=30,
        )

        assert config.api_key == "test_api_key"
        assert config.username == "test_user"
        assert config.password == "test_pass"
        assert config.account_id == "ABCDE123"
        assert config.environment == IGEnvironment.DEMO
        assert config.timeout == 30

    def test_get_base_url(self):
        """Test base URL generation"""
        config = IGConfig(
            api_key="test_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.DEMO,
        )

        assert "demo-api.ig.com" in config.get_base_url()

        config_live = IGConfig(
            api_key="test_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.LIVE,
        )

        assert "api.ig.com" in config_live.get_base_url()

    def test_get_versioned_url(self):
        """Test versioned URL building"""
        config = IGConfig(
            api_key="test_key",
            username="test_user",
            password="test_pass",
        )

        url = config.get_versioned_url("/positions", version=2)
        assert "/positions" in url
        assert "gateway/deal" in url


class TestIGAccount:
    """Test IG account model"""

    def test_account_from_response(self):
        """Test account creation from API response"""
        response = {
            "accountId": "ABCDE123",
            "accountName": "Test Account",
            "accountAlias": "Primary",
            "status": "ACTIVE",
            "accountType": "CFD",
            "currency": "USD",
            "balance": "10000.00",
            "deposit": "5000.00",
            "profitLoss": "250.50",
            "availableCash": "9500.00",
            "margin": "500.00",
            "equity": "10500.00",
        }

        account = IGAccount.from_response(response)

        assert account.account_id == "ABCDE123"
        assert account.account_name == "Test Account"
        assert account.account_alias == "Primary"
        assert account.status == "ACTIVE"
        assert account.account_type == IGAccountType.CFD
        assert account.currency == "USD"
        assert account.balance == Decimal("10000.00")
        assert account.deposit == Decimal("5000.00")
        assert account.profit_loss == Decimal("250.50")
        assert account.available_cash == Decimal("9500.00")
        assert account.margin == Decimal("500.00")
        assert account.equity == Decimal("10500.00")


class TestIGPosition:
    """Test IG position model"""

    def test_position_from_response_long(self):
        """Test position creation for long position"""
        response = {
            "dealId": "DIAAAA12345",
            "epic": "CS.D.EURUSD.TODAY.IP",
            "instrumentName": "EUR/USD",
            "direction": "BUY",
            "size": "1000",
            "level": "1.1234",
            "currency": "USD",
            "createdDateUtc": "2024-01-15T10:30:00.000Z",
            "dealReference": "TESTREF123",
        }

        position = IGPosition.from_response(response)

        assert position.deal_id == "DIAAAA12345"
        assert position.epic == "CS.D.EURUSD.TODAY.IP"
        assert position.instrument_name == "EUR/USD"
        assert position.direction == IGPositionDirection.BUY
        assert position.size == Decimal("1000")
        assert position.level == Decimal("1.1234")
        assert position.is_long()

    def test_position_from_response_short(self):
        """Test position creation for short position"""
        response = {
            "dealId": "DIAAAA67890",
            "epic": "CS.D.GBPUSD.TODAY.IP",
            "instrumentName": "GBP/USD",
            "direction": "SELL",
            "size": "500",
            "level": "1.2567",
            "currency": "USD",
            "createdDateUtc": "2024-01-15T11:00:00.000Z",
        }

        position = IGPosition.from_response(response)

        assert position.direction == IGPositionDirection.SELL
        assert position.size == Decimal("500")
        assert not position.is_long()

    def test_position_with_stops(self):
        """Test position creation with stop/limit levels"""
        response = {
            "dealId": "DIAAAA12345",
            "epic": "CS.D.EURUSD.TODAY.IP",
            "instrumentName": "EUR/USD",
            "direction": "BUY",
            "size": "1000",
            "level": "1.1234",
            "limitLevel": "1.1300",
            "stopLevel": "1.1200",
            "currency": "USD",
            "controlledRisk": True,
            "trailingStop": False,
        }

        position = IGPosition.from_response(response)

        assert position.limit_level == Decimal("1.1300")
        assert position.stop_level == Decimal("1.1200")
        assert position.controlled_risk
        assert not position.trailing_stop


class TestIGWorkingOrder:
    """Test IG working order model"""

    def test_working_order_from_response(self):
        """Test working order creation from API response"""
        response = {
            "dealId": "WOAAAA12345",
            "direction": "BUY",
            "epic": "CS.D.EURUSD.TODAY.IP",
            "orderType": "LIMIT",
            "level": "1.1100",
            "size": "1000",
            "currency": "USD",
            "timeInForce": "GOOD_TILL_CANCELLED",
            "dealReference": "REF123",
        }

        order = IGWorkingOrder.from_response(response)

        assert order.deal_id == "WOAAAA12345"
        assert order.direction == IGDealDirection.BUY
        assert order.epic == "CS.D.EURUSD.TODAY.IP"
        assert order.order_type == IGOrderType.LIMIT
        assert order.level == Decimal("1.1100")
        assert order.size == Decimal("1000")
        assert order.time_in_force == IGTimeInForce.GOOD_TILL_CANCELLED


class TestIGDealConfirmation:
    """Test IG deal confirmation model"""

    def test_confirmation_accepted(self):
        """Test accepted deal confirmation"""
        response = {
            "dealReference": "DEALREF12345",
            "dealStatus": "ACCEPTED",
            "dealId": "DIAAAA12345",
            "epic": "CS.D.EURUSD.TODAY.IP",
            "direction": "BUY",
            "level": "1.1234",
            "size": "1000",
        }

        confirmation = IGDealConfirmation.from_response(response)

        assert confirmation.deal_reference == "DEALREF12345"
        assert confirmation.deal_status == IGDealStatus.ACCEPTED
        assert confirmation.deal_id == "DIAAAA12345"
        assert confirmation.epic == "CS.D.EURUSD.TODAY.IP"
        assert confirmation.direction == IGDealDirection.BUY
        assert confirmation.level == Decimal("1.1234")
        assert confirmation.size == Decimal("1000")

    def test_confirmation_rejected(self):
        """Test rejected deal confirmation"""
        response = {
            "dealReference": "DEALREF67890",
            "dealStatus": "REJECTED",
            "reason": "Insufficient margin",
        }

        confirmation = IGDealConfirmation.from_response(response)

        assert confirmation.deal_status == IGDealStatus.REJECTED
        assert confirmation.reason == "Insufficient margin"


class TestIGMarketSnapshot:
    """Test IG market snapshot model"""

    def test_snapshot_from_response(self):
        """Test market snapshot creation"""
        response = {
            "epic": "CS.D.EURUSD.TODAY.IP",
            "instrument": {
                "name": "EUR/USD",
                "type": "CURRENCY",
            },
            "snapshot": {
                "bid": "1.12340",
                "offer": "1.12350",
                "high": "1.12400",
                "low": "1.12300",
                "change": "0.0005",
                "changePct": "0.04",
                "updateTime": "2024-01-15T12:00:00.000Z",
                "marketStatus": "TRADEABLE",
                "scalingFactor": 10000,
                "minDealSize": "1000",
                "maxDealSize": "10000000",
                "lotSize": 1000,
            },
        }

        snapshot = IGMarketSnapshot.from_response(response)

        assert snapshot.epic == "CS.D.EURUSD.TODAY.IP"
        assert snapshot.instrument_name == "EUR/USD"
        assert snapshot.instrument_type == "CURRENCY"
        assert snapshot.bid == Decimal("1.12340")
        assert snapshot.offer == Decimal("1.12350")
        assert snapshot.high == Decimal("1.12400")
        assert snapshot.low == Decimal("1.12300")
        assert snapshot.change == Decimal("0.0005")
        assert snapshot.change_percent == Decimal("0.04")
        assert snapshot.market_status == "TRADEABLE"
        assert snapshot.mid_price == Decimal("1.12345")


class FakeResponse:
    """A fake aiohttp response for testing"""
    def __init__(self, status_code, json_data):
        self.status = status_code
        self._json_data = json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self._json_data


class TestIGApiClient:
    """Test IG API client"""

    @pytest.fixture
    def config(self):
        """Test configuration"""
        return IGConfig(
            api_key="test_api_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.DEMO,
        )

    @pytest.fixture
    def client(self, config):
        """Test client"""
        return IGApiClient(config)

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, client):
        """Test connection management"""
        await client.connect()
        assert client._session is not None

        await client.disconnect()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_context_manager(self, client):
        """Test async context manager"""
        async with client as c:
            assert c._session is not None

        assert client._session is None

    def test_authentication_property(self, client):
        """Test authenticated property"""
        assert not client.authenticated

        client._cst = "test_cst"
        client._security_token = "test_security_token"
        assert client.authenticated

    @pytest.mark.asyncio
    async def test_get_accounts(self, client):
        """Test getting accounts"""
        mock_response = {
            "accounts": [
                {
                    "accountId": "ABCDE123",
                    "accountName": "Test Account",
                    "accountAlias": "Primary",
                    "status": "ACTIVE",
                    "accountType": "CFD",
                    "currency": "USD",
                    "balance": "10000.00",
                    "deposit": "5000.00",
                    "profitLoss": "250.50",
                    "availableCash": "9500.00",
                    "margin": "500.00",
                    "equity": "10500.00",
                }
            ]
        }

        await client.connect()

        # Replace the session's get method with one that returns a FakeResponse
        original_get = client._session.get
        client._session.get = MagicMock(return_value=FakeResponse(200, mock_response))

        accounts = await client.get_accounts()

        assert len(accounts) == 1
        assert accounts[0].account_id == "ABCDE123"
        assert accounts[0].currency == "USD"
        assert accounts[0].balance == Decimal("10000.00")

        # Clean up
        client._session.get = original_get
        await client.disconnect()


class TestIGBroker:
    """Test IG broker adapter"""

    @pytest.fixture
    def config(self):
        """Test configuration"""
        return IGConfig(
            api_key="test_api_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.DEMO,
        )

    @pytest.fixture
    def broker(self, config):
        """Test broker"""
        return IGBroker(config)

    def test_broker_initialization(self, broker):
        """Test broker initialization"""
        assert broker._config.account_id == ""
        assert not broker._connected
        assert broker._account is None
        assert len(broker._positions) == 0
        assert len(broker._orders) == 0

    @pytest.mark.asyncio
    async def test_connect(self, broker):
        """Test connection to IG"""
        mock_account = {
            "account_id": "ABCDE123",
            "account_name": "Test Account",
            "account_type": "CFD",
            "currency": "USD",
            "balance": 10000.00,
            "deposit": 5000.00,
            "profit_loss": 250.50,
            "available_cash": 9500.00,
            "margin": 500.00,
            "equity": 10500.00,
            "status": "ACTIVE",
        }

        with patch.object(broker._client, 'connect'), \
             patch.object(broker._client, 'authenticate', return_value=True), \
             patch.object(broker._client, 'get_account_details', return_value=mock_account), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            success = await broker.connect()

            assert success
            assert broker._connected
            assert broker._account is not None
            assert broker._account['account_id'] == "ABCDE123"

    @pytest.mark.asyncio
    async def test_connect_failure_auth(self, broker):
        """Test connection failure due to authentication"""
        with patch.object(broker._client, 'connect'), \
             patch.object(broker._client, 'authenticate', return_value=False):

            success = await broker.connect()

            assert not success
            assert not broker._connected

    @pytest.mark.asyncio
    async def test_disconnect(self, broker):
        """Test disconnection"""
        broker._connected = True

        with patch.object(broker._client, 'logout', return_value=True), \
             patch.object(broker._client, 'disconnect'):

            await broker.disconnect()

            assert not broker._connected
            assert broker._account is None
            assert len(broker._positions) == 0
            assert len(broker._orders) == 0

    def test_is_connected(self, broker):
        """Test connection status"""
        assert not broker.is_connected()

        broker._connected = True
        assert broker.is_connected()

    @pytest.mark.asyncio
    async def test_submit_market_order(self, broker):
        """Test submitting market order"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF123",
            deal_status=IGDealStatus.ACCEPTED,
            deal_id="DIAAAA12345",
            epic="CS.D.EURUSD.TODAY.IP",
            direction=IGDealDirection.BUY,
            level=Decimal("1.1234"),
            size=Decimal("1000"),
        )

        with patch.object(broker._client, 'create_position', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000"),
                side=OrderSide.BUY,
            )

            success = await broker.submit_order(order)

            assert success
            assert order.id == "DIAAAA12345"

    @pytest.mark.asyncio
    async def test_submit_market_order_not_connected(self, broker):
        """Test submitting order when not connected"""
        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.MARKET,
            quantity=Decimal("1000"),
            side=OrderSide.BUY,
        )

        success = await broker.submit_order(order)
        assert not success

    @pytest.mark.asyncio
    async def test_submit_limit_order(self, broker):
        """Test submitting limit order"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF456",
            deal_status=IGDealStatus.ACCEPTED,
            deal_id="WOAAAA12345",
            epic="CS.D.EURUSD.TODAY.IP",
            direction=IGDealDirection.BUY,
            level=Decimal("1.1100"),
            size=Decimal("1000"),
        )

        with patch.object(broker._client, 'create_working_order', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.LIMIT,
                quantity=Decimal("1000"),
                side=OrderSide.BUY,
                limit_price=Decimal("1.1100"),
            )

            success = await broker.submit_order(order)

            assert success
            assert order.id == "WOAAAA12345"

    @pytest.mark.asyncio
    async def test_submit_limit_order_no_price(self, broker):
        """Test submitting limit order without price"""
        broker._connected = True

        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.LIMIT,
            quantity=Decimal("1000"),
            side=OrderSide.BUY,
            # No limit_price set
        )

        success = await broker.submit_order(order)
        assert not success

    @pytest.mark.asyncio
    async def test_submit_stop_order(self, broker):
        """Test submitting stop order"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF789",
            deal_status=IGDealStatus.ACCEPTED,
            deal_id="WOAAAA67890",
            epic="CS.D.EURUSD.TODAY.IP",
            direction=IGDealDirection.BUY,
            level=Decimal("1.1300"),
            size=Decimal("1000"),
        )

        with patch.object(broker._client, 'create_working_order', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.STOP,
                quantity=Decimal("1000"),
                side=OrderSide.BUY,
                stop_price=Decimal("1.1300"),
            )

            success = await broker.submit_order(order)

            assert success
            assert order.id == "WOAAAA67890"

    @pytest.mark.asyncio
    async def test_submit_stop_order_no_price(self, broker):
        """Test submitting stop order without price"""
        broker._connected = True

        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.STOP,
            quantity=Decimal("1000"),
            side=OrderSide.BUY,
            # No stop_price set
        )

        success = await broker.submit_order(order)
        assert not success

    @pytest.mark.asyncio
    async def test_submit_order_rejected(self, broker):
        """Test order rejected by IG"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF_REJECTED",
            deal_status=IGDealStatus.REJECTED,
            reason="Insufficient margin",
        )

        with patch.object(broker._client, 'create_position', return_value=mock_confirmation):

            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000000"),
                side=OrderSide.BUY,
            )

            success = await broker.submit_order(order)
            assert not success

    @pytest.mark.asyncio
    async def test_submit_order_rate_limited(self, broker):
        """Test order submission when rate limited"""
        broker._connected = True

        with patch.object(broker._client, 'create_position',
                          side_effect=IGRateLimitError("Rate limited")):

            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000"),
                side=OrderSide.BUY,
            )

            success = await broker.submit_order(order)
            assert not success

    @pytest.mark.asyncio
    async def test_cancel_working_order(self, broker):
        """Test cancelling a working order"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF_CANCEL",
            deal_status=IGDealStatus.DELETED,
        )

        # Add working order to internal state (not a position)
        mock_ig_order = IGWorkingOrder(
            deal_id="WOAAAA12345",
            direction=IGDealDirection.BUY,
            epic="CS.D.EURUSD.TODAY.IP",
            order_type=IGOrderType.LIMIT,
            level=Decimal("1.1100"),
            size=Decimal("1000"),
        )

        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.LIMIT,
            quantity=Decimal("1000"),
            side=OrderSide.BUY,
            limit_price=Decimal("1.1100"),
        )
        order.id = "WOAAAA12345"

        with patch.object(broker._client, 'delete_working_order', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            success = await broker.cancel_order(order)
            assert success

    @pytest.mark.asyncio
    async def test_cancel_position_order(self, broker):
        """Test cancelling/closing a position"""
        broker._connected = True

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF_CLOSE",
            deal_status=IGDealStatus.CLOSED,
        )

        mock_ig_position = IGPosition(
            deal_id="DIAAAA12345",
            epic="CS.D.EURUSD.TODAY.IP",
            instrument_name="EUR/USD",
            direction=IGPositionDirection.BUY,
            size=Decimal("1000"),
            level=Decimal("1.1234"),
        )

        broker._positions = {"DIAAAA12345": mock_ig_position}

        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.MARKET,
            quantity=Decimal("1000"),
            side=OrderSide.BUY,
        )
        order.id = "DIAAAA12345"

        with patch.object(broker._client, 'close_position', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):

            success = await broker.cancel_order(order)
            assert success

    @pytest.mark.asyncio
    async def test_get_account_info(self, broker):
        """Test getting account information"""
        broker._connected = True

        mock_account = {
            "account_id": "ABCDE123",
            "currency": "USD",
            "balance": 10000.00,
            "equity": 10500.00,
            "margin": 500.00,
            "available_cash": 9500.00,
            "profit_loss": 250.50,
        }

        with patch.object(broker._client, 'get_account_details', return_value=mock_account):
            info = await broker.get_account_info()

            assert info['account_id'] == "ABCDE123"
            assert info['currency'] == "USD"
            assert info['balance'] == 10000.00

    @pytest.mark.asyncio
    async def test_get_positions(self, broker):
        """Test getting positions"""
        broker._connected = True

        mock_ig_position = IGPosition(
            deal_id="DIAAAA12345",
            epic="CS.D.EURUSD.TODAY.IP",
            instrument_name="EUR/USD",
            direction=IGPositionDirection.BUY,
            size=Decimal("1000"),
            level=Decimal("1.1234"),
        )

        with patch.object(broker, '_refresh_positions'):
            broker._positions = {"DIAAAA12345": mock_ig_position}

            positions = await broker.get_positions()

            assert len(positions) == 1
            assert positions[0].symbol.ticker == "EURUSD"
            assert positions[0].side == OrderSide.BUY
            assert positions[0].quantity == Decimal("1000")

    @pytest.mark.asyncio
    async def test_get_orders(self, broker):
        """Test getting working orders"""
        broker._connected = True

        mock_ig_order = IGWorkingOrder(
            deal_id="WOAAAA12345",
            direction=IGDealDirection.BUY,
            epic="CS.D.EURUSD.TODAY.IP",
            order_type=IGOrderType.LIMIT,
            level=Decimal("1.1100"),
            size=Decimal("1000"),
        )

        with patch.object(broker, '_refresh_orders'):
            broker._orders = {"WOAAAA12345": mock_ig_order}

            orders = await broker.get_orders()

            assert len(orders) == 1
            assert orders[0].symbol.ticker == "EURUSD"
            assert orders[0].order_type == OrderType.LIMIT
            assert orders[0].status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_close_position(self, broker):
        """Test closing a position"""
        broker._connected = True

        mock_ig_position = IGPosition(
            deal_id="DIAAAA12345",
            epic="CS.D.EURUSD.TODAY.IP",
            instrument_name="EUR/USD",
            direction=IGPositionDirection.BUY,
            size=Decimal("1000"),
            level=Decimal("1.1234"),
        )

        broker._positions = {"DIAAAA12345": mock_ig_position}

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF_CLOSE",
            deal_status=IGDealStatus.CLOSED,
        )

        with patch.object(broker._client, 'close_position', return_value=mock_confirmation), \
             patch.object(broker, '_refresh_positions'):

            success = await broker.close_position(Symbol("EURUSD"))
            assert success

    @pytest.mark.asyncio
    async def test_close_position_not_found(self, broker):
        """Test closing a non-existent position"""
        broker._connected = True

        # No positions stored

        success = await broker.close_position(Symbol("EURUSD"))
        assert not success

    @pytest.mark.asyncio
    async def test_get_market_price(self, broker):
        """Test getting market price"""
        broker._connected = True

        mock_snapshot = IGMarketSnapshot(
            epic="CS.D.EURUSD.TODAY.IP",
            instrument_name="EUR/USD",
            instrument_type="CURRENCY",
            bid=Decimal("1.12340"),
            offer=Decimal("1.12350"),
        )

        with patch.object(broker._client, 'get_market_snapshot', return_value=mock_snapshot):
            price = await broker.get_market_price(Symbol("EURUSD"))
            assert price == 1.12345

    def test_result_handlers(self, broker):
        """Test result handler management"""
        handler1 = MagicMock()
        handler2 = MagicMock()

        broker.add_result_handler(handler1)
        broker.add_result_handler(handler2)

        assert len(broker._result_handlers) == 2
        assert handler1 in broker._result_handlers
        assert handler2 in broker._result_handlers

        broker.remove_result_handler(handler1)
        assert len(broker._result_handlers) == 1
        assert handler1 not in broker._result_handlers

    def test_symbol_conversion(self, broker):
        """Test symbol format conversion"""
        # Convert to epic
        symbol1 = Symbol("EURUSD")
        epic = broker._convert_symbol_to_epic(symbol1)
        assert epic == "CS.D.EURUSD.TODAY.IP"

        # Convert epic to symbol
        converted_symbol = broker._convert_epic_to_symbol("CS.D.EURUSD.TODAY.IP")
        assert converted_symbol.ticker == "EURUSD"

        # Handle index epics
        index_symbol = broker._convert_epic_to_symbol("IX.D.FTSE.DAILY.IP")
        assert index_symbol.ticker == "FTSE"

        # Handle already-epic format
        epic_result = broker._convert_symbol_to_epic(Symbol("CS.D.EURUSD.TODAY.IP"))
        assert epic_result == "CS.D.EURUSD.TODAY.IP"

    def test_callbacks(self, broker):
        """Test callback registration"""
        callback = MagicMock()

        broker.set_order_filled_callback(callback)
        assert broker._on_order_filled == callback

        broker.set_position_opened_callback(callback)
        assert broker._on_position_opened == callback

        broker.set_position_closed_callback(callback)
        assert broker._on_position_closed == callback


class TestIGExceptions:
    """Test IG exception hierarchy"""

    def test_base_exception(self):
        """Test base IG exception"""
        error = IGError("Test error")
        assert str(error) == "Test error"

    def test_authentication_error(self):
        """Test authentication exception"""
        error = IGAuthenticationError("Auth failed")
        assert str(error) == "Auth failed"
        assert isinstance(error, IGError)

    def test_connection_error(self):
        """Test connection exception"""
        error = IGConnectionError("Connection failed")
        assert isinstance(error, IGError)

    def test_rate_limit_error(self):
        """Test rate limit exception"""
        error = IGRateLimitError("Rate limited")
        assert isinstance(error, IGError)

    def test_api_error(self):
        """Test API error exception"""
        error = IGApiError("Bad request", 400)
        assert str(error) == "Bad request"
        assert error.status_code == 400
        assert isinstance(error, IGError)


class TestFactory:
    """Test factory functions"""

    def test_create_ig_broker(self):
        """Test IG broker factory"""
        broker = create_ig_broker(
            api_key="test_api_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.DEMO,
            timeout=30,
        )

        assert broker._config.api_key == "test_api_key"
        assert broker._config.username == "test_user"
        assert broker._config.password == "test_pass"
        assert broker._config.environment == IGEnvironment.DEMO
        assert broker._config.timeout == 30

    def test_create_ig_broker_live(self):
        """Test IG broker factory with live environment"""
        broker = create_ig_broker(
            api_key="live_key",
            username="live_user",
            password="live_pass",
            account_id="LIVE123",
            environment=IGEnvironment.LIVE,
        )

        assert broker._config.api_key == "live_key"
        assert broker._config.account_id == "LIVE123"
        assert "api.ig.com" in broker._config.get_base_url()


class TestIntegration:
    """Integration tests"""

    @pytest.mark.asyncio
    async def test_full_order_workflow(self):
        """Test complete order workflow"""
        config = IGConfig(
            api_key="test_key",
            username="test_user",
            password="test_pass",
            environment=IGEnvironment.DEMO,
        )
        broker = IGBroker(config)

        mock_account = {
            "account_id": "ABCDE123",
            "account_name": "Test Account",
            "account_type": "CFD",
            "currency": "USD",
            "balance": 10000.00,
            "deposit": 5000.00,
            "profit_loss": 0.00,
            "available_cash": 10000.00,
            "margin": 0.00,
            "equity": 10000.00,
            "status": "ACTIVE",
        }

        mock_confirmation = IGDealConfirmation(
            deal_reference="DEALREF_INT",
            deal_status=IGDealStatus.ACCEPTED,
            deal_id="DIAAAA_INT_12345",
            epic="CS.D.EURUSD.TODAY.IP",
            direction=IGDealDirection.BUY,
            level=Decimal("1.1234"),
            size=Decimal("1000"),
        )

        with patch.object(broker._client, 'connect'), \
             patch.object(broker._client, 'authenticate', return_value=True), \
             patch.object(broker._client, 'get_account_details', return_value=mock_account), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'), \
             patch.object(broker._client, 'create_position', return_value=mock_confirmation):

            # Connect
            await broker.connect()
            assert broker.is_connected()

            # Submit market order
            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000"),
                side=OrderSide.BUY,
            )

            success = await broker.submit_order(order)
            assert success
            assert order.id == "DIAAAA_INT_12345"

            # Get account info
            info = await broker.get_account_info()
            assert info['account_id'] == "ABCDE123"

            # Disconnect
            await broker.disconnect()
            assert not broker.is_connected()


if __name__ == "__main__":
    pytest.main([__file__])