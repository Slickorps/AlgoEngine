"""Tests for OANDA broker adapter"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from decimal import Decimal

from src.adapters.oanda_broker import (
    OandaBroker, OandaApiClient, OandaConfig, OandaEnvironment,
    OandaAccount, OandaPosition, OandaOrder, OandaOrderType,
    create_oanda_broker
)
from src.models import Symbol, Order, OrderType, OrderStatus, PositionSide, Position


class TestOandaConfig:
    """Test OANDA configuration"""
    
    def test_config_creation(self):
        """Test configuration initialization"""
        config = OandaConfig(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.PRACTICE,
            timeout=60
        )
        
        assert config.api_key == "test_key"
        assert config.account_id == "123-456-789"
        assert config.environment == OandaEnvironment.PRACTICE
        assert config.timeout == 60
    
    def test_get_base_url(self):
        """Test base URL generation"""
        config = OandaConfig(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.LIVE
        )
        
        assert config.get_base_url() == "https://api-fxtrade.oanda.com"
    
    def test_get_headers(self):
        """Test header generation"""
        config = OandaConfig(
            api_key="test_key",
            account_id="123-456-789"
        )
        
        headers = config.get_headers()
        assert headers["Authorization"] == "Bearer test_key"
        assert headers["Content-Type"] == "application/json"


class TestOandaAccount:
    """Test OANDA account model"""
    
    def test_account_from_response(self):
        """Test account creation from API response"""
        response = {
            "id": "123-456-789",
            "currency": "USD",
            "balance": "10000.00",
            "unrealizedPL": "150.50",
            "realizedPL": "250.75",
            "marginUsed": "500.00",
            "marginAvailable": "9500.00",
            "openPositionCount": 3,
            "openOrderCount": 5,
            "lastTransactionID": "1234567890"
        }
        
        account = OandaAccount.from_response(response)
        
        assert account.id == "123-456-789"
        assert account.currency == "USD"
        assert account.balance == Decimal("10000.00")
        assert account.unrealized_pl == Decimal("150.50")
        assert account.realized_pl == Decimal("250.75")
        assert account.margin_used == Decimal("500.00")
        assert account.margin_available == Decimal("9500.00")
        assert account.open_positions == 3
        assert account.open_orders == 5


class TestOandaPosition:
    """Test OANDA position model"""
    
    def test_position_from_response_long(self):
        """Test position creation for long position"""
        response = {
            "instrument": "EUR_USD",
            "long": {
                "units": "1000",
                "averagePrice": "1.1234",
                "unrealizedPL": "50.00",
                "marginUsed": "100.00"
            },
            "short": {
                "units": "0",
                "averagePrice": "0",
                "unrealizedPL": "0",
                "marginUsed": "0"
            }
        }
        
        position = OandaPosition.from_response(response)
        
        assert position.instrument == "EUR_USD"
        assert position.side == "long"
        assert position.units == Decimal("1000")
        assert position.avg_price == Decimal("1.1234")
        assert position.unrealized_pl == Decimal("50.00")
    
    def test_position_from_response_short(self):
        """Test position creation for short position"""
        response = {
            "instrument": "EUR_USD",
            "long": {
                "units": "0",
                "averagePrice": "0",
                "unrealizedPL": "0",
                "marginUsed": "0"
            },
            "short": {
                "units": "-500",
                "averagePrice": "1.1235",
                "unrealizedPL": "-25.00",
                "marginUsed": "50.00"
            }
        }
        
        position = OandaPosition.from_response(response)
        
        assert position.instrument == "EUR_USD"
        assert position.side == "short"
        assert position.units == Decimal("500")
        assert position.avg_price == Decimal("1.1235")
        assert position.unrealized_pl == Decimal("-25.00")
    
    def test_position_from_response_flat(self):
        """Test position creation for flat position"""
        response = {
            "instrument": "EUR_USD",
            "long": {
                "units": "0",
                "averagePrice": "0",
                "unrealizedPL": "0",
                "marginUsed": "0"
            },
            "short": {
                "units": "0",
                "averagePrice": "0",
                "unrealizedPL": "0",
                "marginUsed": "0"
            }
        }
        
        position = OandaPosition.from_response(response)
        
        assert position.instrument == "EUR_USD"
        assert position.side == "flat"
        assert position.units == Decimal("0")


class TestOandaOrder:
    """Test OANDA order model"""
    
    def test_order_from_response(self):
        """Test order creation from API response"""
        response = {
            "id": "12345",
            "instrument": "EUR_USD",
            "type": "MARKET",
            "units": "1000",
            "price": "1.1234",
            "stopLossOnFill": {
                "price": "1.1200"
            },
            "takeProfitOnFill": {
                "price": "1.1300"
            },
            "state": "FILLED",
            "createTime": "1234567890",
            "fillTime": "1234567895"
        }
        
        order = OandaOrder.from_response(response)
        
        assert order.id == "12345"
        assert order.instrument == "EUR_USD"
        assert order.type == "MARKET"
        assert order.side == "buy"
        assert order.units == Decimal("1000")
        assert order.price == Decimal("1.1234")
        assert order.stop_loss == Decimal("1.1200")
        assert order.take_profit == Decimal("1.1300")
        assert order.status == "FILLED"


class TestOandaApiClient:
    """Test OANDA API client"""
    
    @pytest.fixture
    def config(self):
        """Test configuration"""
        return OandaConfig(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.SANDBOX
        )
    
    @pytest.fixture
    def client(self, config):
        """Test client"""
        return OandaApiClient(config)
    
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
    
    @pytest.mark.asyncio
    async def test_get_account(self, client):
        """Test getting account information"""
        mock_response = {
            "account": {
                "id": "123-456-789",
                "currency": "USD",
                "balance": "10000.00",
                "unrealizedPL": "150.50",
                "realizedPL": "250.75",
                "marginUsed": "500.00",
                "marginAvailable": "9500.00",
                "openPositionCount": 3,
                "openOrderCount": 5,
                "lastTransactionID": "1234567890"
            }
        }
        
        with patch.object(client, '_request', return_value=mock_response):
            account = await client.get_account()
            
            assert account.id == "123-456-789"
            assert account.currency == "USD"
            assert account.balance == Decimal("10000.00")
    
    @pytest.mark.asyncio
    async def test_get_positions(self, client):
        """Test getting positions"""
        mock_response = {
            "positions": [
                {
                    "instrument": "EUR_USD",
                    "long": {
                        "units": "1000",
                        "averagePrice": "1.1234",
                        "unrealizedPL": "50.00",
                        "marginUsed": "100.00"
                    },
                    "short": {
                        "units": "0",
                        "averagePrice": "0",
                        "unrealizedPL": "0",
                        "marginUsed": "0"
                    }
                }
            ]
        }
        
        with patch.object(client, '_request', return_value=mock_response):
            positions = await client.get_positions()
            
            assert len(positions) == 1
            assert positions[0].instrument == "EUR_USD"
            assert positions[0].side == "long"
    
    @pytest.mark.asyncio
    async def test_create_market_order(self, client):
        """Test creating market order"""
        mock_response = {
            "orderCreateTransaction": {
                "id": "12345",
                "instrument": "EUR_USD",
                "type": "MARKET",
                "units": "1000",
                "state": "FILLED",
                "createTime": "1234567890"
            }
        }
        
        with patch.object(client, '_request', return_value=mock_response):
            order = await client.create_market_order("EUR_USD", 1000)
            
            assert order.id == "12345"
            assert order.instrument == "EUR_USD"
            assert order.type == "MARKET"
    
    @pytest.mark.asyncio
    async def test_create_limit_order(self, client):
        """Test creating limit order"""
        mock_response = {
            "orderCreateTransaction": {
                "id": "12346",
                "instrument": "EUR_USD",
                "type": "LIMIT",
                "units": "1000",
                "price": "1.1234",
                "state": "PENDING",
                "createTime": "1234567890"
            }
        }
        
        with patch.object(client, '_request', return_value=mock_response):
            order = await client.create_limit_order("EUR_USD", 1000, 1.1234)
            
            assert order.id == "12346"
            assert order.type == "LIMIT"
            assert order.price == Decimal("1.1234")
    
    @pytest.mark.asyncio
    async def test_cancel_order(self, client):
        """Test cancelling order"""
        with patch.object(client, '_request', return_value={}):
            success = await client.cancel_order("12345")
            assert success
    
    @pytest.mark.asyncio
    async def test_close_position(self, client):
        """Test closing position"""
        with patch.object(client, '_request', return_value={}):
            success = await client.close_position("EUR_USD")
            assert success
    
    @pytest.mark.asyncio
    async def test_get_pricing(self, client):
        """Test getting pricing"""
        mock_response = {
            "prices": [
                {
                    "instrument": "EUR_USD",
                    "closeoutMid": "1.12345",
                    "bid": "1.12340",
                    "ask": "1.12350"
                }
            ]
        }
        
        with patch.object(client, '_request', return_value=mock_response):
            pricing = await client.get_pricing(["EUR_USD"])
            
            assert len(pricing) == 1
            assert pricing[0]["instrument"] == "EUR_USD"
            assert pricing[0]["closeoutMid"] == "1.12345"


class TestOandaBroker:
    """Test OANDA broker adapter"""
    
    @pytest.fixture
    def config(self):
        """Test configuration"""
        return OandaConfig(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.SANDBOX
        )
    
    @pytest.fixture
    def broker(self, config):
        """Test broker"""
        return OandaBroker(config)
    
    def test_broker_initialization(self, broker):
        """Test broker initialization"""
        assert broker._config.account_id == "123-456-789"
        assert not broker._connected
        assert broker._account is None
    
    @pytest.mark.asyncio
    async def test_connect(self, broker):
        """Test connection to OANDA"""
        # Mock API responses
        mock_account = {
            "account": {
                "id": "123-456-789",
                "currency": "USD",
                "balance": "10000.00",
                "unrealizedPL": "150.50",
                "realizedPL": "250.75",
                "marginUsed": "500.00",
                "marginAvailable": "9500.00",
                "openPositionCount": 0,
                "openOrderCount": 0,
                "lastTransactionID": "1234567890"
            }
        }
        
        with patch.object(broker._client, 'connect'), \
             patch.object(broker._client, 'get_account', return_value=OandaAccount.from_response(mock_account['account'])), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'):
            
            success = await broker.connect()
            
            assert success
            assert broker._connected
            assert broker._account is not None
            assert broker._account.id == "123-456-789"
    
    @pytest.mark.asyncio
    async def test_disconnect(self, broker):
        """Test disconnection"""
        broker._connected = True
        
        with patch.object(broker._client, 'disconnect'):
            await broker.disconnect()
            
            assert not broker._connected
    
    def test_is_connected(self, broker):
        """Test connection status"""
        assert not broker.is_connected()
        
        broker._connected = True
        assert broker.is_connected()
    
    @pytest.mark.asyncio
    async def test_submit_market_order(self, broker):
        """Test submitting market order"""
        broker._connected = True
        
        # Mock order creation
        mock_oanda_order = OandaOrder(
            id="12345",
            instrument="EUR_USD",
            type="MARKET",
            side="buy",
            units=Decimal("1000"),
            price=None,
            stop_loss=None,
            take_profit=None,
            status="FILLED",
            create_time=datetime.now()
        )
        
        with patch.object(broker._client, 'create_market_order', return_value=mock_oanda_order):
            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000"),
                side="buy"
            )
            
            success = await broker.submit_order(order)
            
            assert success
            assert order.id == "12345"
    
    @pytest.mark.asyncio
    async def test_submit_limit_order(self, broker):
        """Test submitting limit order"""
        broker._connected = True
        
        # Mock order creation
        mock_oanda_order = OandaOrder(
            id="12346",
            instrument="EUR_USD",
            type="LIMIT",
            side="buy",
            units=Decimal("1000"),
            price=Decimal("1.1234"),
            stop_loss=None,
            take_profit=None,
            status="PENDING",
            create_time=datetime.now()
        )
        
        with patch.object(broker._client, 'create_limit_order', return_value=mock_oanda_order):
            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.LIMIT,
                quantity=Decimal("1000"),
                side="buy",
                limit_price=Decimal("1.1234")
            )
            
            success = await broker.submit_order(order)
            
            assert success
            assert order.id == "12346"
    
    @pytest.mark.asyncio
    async def test_cancel_order(self, broker):
        """Test cancelling order"""
        broker._connected = True
        
        order = Order(
            symbol=Symbol("EURUSD"),
            order_type=OrderType.MARKET,
            quantity=Decimal("1000"),
            side="buy"
        )
        order.id = "12345"
        
        with patch.object(broker._client, 'cancel_order', return_value=True):
            success = await broker.cancel_order(order)
            assert success
    
    @pytest.mark.asyncio
    async def test_get_account_info(self, broker):
        """Test getting account information"""
        broker._connected = True
        
        mock_account = OandaAccount(
            id="123-456-789",
            currency="USD",
            balance=Decimal("10000.00"),
            unrealized_pl=Decimal("150.50"),
            realized_pl=Decimal("250.75"),
            margin_used=Decimal("500.00"),
            margin_available=Decimal("9500.00"),
            open_positions=3,
            open_orders=5,
            last_updated=datetime.now()
        )
        
        with patch.object(broker._client, 'get_account', return_value=mock_account):
            info = await broker.get_account_info()
            
            assert info['account_id'] == "123-456-789"
            assert info['currency'] == "USD"
            assert info['balance'] == 10000.00
            assert info['open_positions'] == 3
    
    @pytest.mark.asyncio
    async def test_get_positions(self, broker):
        """Test getting positions"""
        broker._connected = True
        
        mock_oanda_position = OandaPosition(
            instrument="EUR_USD",
            side="long",
            units=Decimal("1000"),
            avg_price=Decimal("1.1234"),
            current_price=Decimal("1.1240"),
            unrealized_pl=Decimal("50.00"),
            margin_used=Decimal("100.00")
        )
        
        with patch.object(broker, '_refresh_positions'):
            broker._positions = {"EUR_USD": mock_oanda_position}
            
            positions = await broker.get_positions()
            
            assert len(positions) == 1
            assert positions[0].symbol.ticker == "EURUSD"
            assert positions[0].side == PositionSide.LONG
            assert positions[0].size == Decimal("1000")
    
    @pytest.mark.asyncio
    async def test_get_orders(self, broker):
        """Test getting orders"""
        broker._connected = True
        
        mock_oanda_order = OandaOrder(
            id="12345",
            instrument="EUR_USD",
            type="MARKET",
            side="buy",
            units=Decimal("1000"),
            price=None,
            stop_loss=None,
            take_profit=None,
            status="FILLED",
            create_time=datetime.now()
        )
        
        with patch.object(broker, '_refresh_orders'):
            broker._orders = {"12345": mock_oanda_order}
            
            orders = await broker.get_orders()
            
            assert len(orders) == 1
            assert orders[0].symbol.ticker == "EURUSD"
            assert orders[0].order_type == OrderType.MARKET
            assert orders[0].status == OrderStatus.FILLED
    
    @pytest.mark.asyncio
    async def test_close_position(self, broker):
        """Test closing position"""
        broker._connected = True
        
        with patch.object(broker._client, 'close_position', return_value=True), \
             patch.object(broker, '_refresh_positions'):
            
            success = await broker.close_position(Symbol("EURUSD"))
            assert success
    
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
        # Convert to OANDA format
        symbol1 = Symbol("EURUSD")
        oanda_instrument = broker._convert_symbol_to_oanda(symbol1)
        assert oanda_instrument == "EUR_USD"
        
        symbol2 = Symbol("EUR_USD")
        oanda_instrument = broker._convert_symbol_to_oanda(symbol2)
        assert oanda_instrument == "EUR_USD"
        
        # Convert from OANDA format
        oanda_symbol = broker._convert_oanda_to_symbol("EUR_USD")
        assert oanda_symbol.ticker == "EURUSD"
    
    def test_order_type_conversion(self, broker):
        """Test order type conversion"""
        assert broker._convert_oanda_order_type("MARKET") == OrderType.MARKET
        assert broker._convert_oanda_order_type("LIMIT") == OrderType.LIMIT
        assert broker._convert_oanda_order_type("STOP") == OrderType.STOP
    
    def test_order_status_conversion(self, broker):
        """Test order status conversion"""
        assert broker._convert_oanda_order_status("FILLED") == OrderStatus.FILLED
        assert broker._convert_oanda_order_status("CANCELLED") == OrderStatus.CANCELLED
        assert broker._convert_oanda_order_status("PENDING") == OrderStatus.PENDING
    
    @pytest.mark.asyncio
    async def test_get_market_price(self, broker):
        """Test getting market price"""
        broker._connected = True
        
        mock_pricing = [
            {
                "instrument": "EUR_USD",
                "closeoutMid": "1.12345",
                "bid": "1.12340",
                "ask": "1.12350"
            }
        ]
        
        with patch.object(broker._client, 'get_pricing', return_value=mock_pricing):
            price = await broker.get_market_price(Symbol("EURUSD"))
            assert price == 1.12345
    
    @pytest.mark.asyncio
    async def test_get_historical_data(self, broker):
        """Test getting historical data"""
        broker._connected = True
        
        mock_candles = [
            {
                "time": "2023-01-01T00:00:00Z",
                "mid": {"o": "1.1234", "h": "1.1240", "l": "1.1230", "c": "1.1235"},
                "volume": 1000
            }
        ]
        
        with patch.object(broker._client, 'get_candles', return_value=mock_candles):
            candles = await broker.get_historical_data(Symbol("EURUSD"), OandaTimeframe.M1, 100)
            assert len(candles) == 1


class TestFactory:
    """Test factory functions"""
    
    def test_create_oanda_broker(self):
        """Test OANDA broker factory"""
        broker = create_oanda_broker(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.PRACTICE,
            timeout=60
        )
        
        assert broker._config.api_key == "test_key"
        assert broker._config.account_id == "123-456-789"
        assert broker._config.environment == OandaEnvironment.PRACTICE
        assert broker._config.timeout == 60


class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_full_order_workflow(self):
        """Test complete order workflow"""
        config = OandaConfig(
            api_key="test_key",
            account_id="123-456-789",
            environment=OandaEnvironment.SANDBOX
        )
        broker = OandaBroker(config)
        
        # Mock API responses
        mock_account = {
            "account": {
                "id": "123-456-789",
                "currency": "USD",
                "balance": "10000.00",
                "unrealizedPL": "0.00",
                "realizedPL": "0.00",
                "marginUsed": "0.00",
                "marginAvailable": "10000.00",
                "openPositionCount": 0,
                "openOrderCount": 0,
                "lastTransactionID": "1234567890"
            }
        }
        
        mock_oanda_order = OandaOrder(
            id="12345",
            instrument="EUR_USD",
            type="MARKET",
            side="buy",
            units=Decimal("1000"),
            price=None,
            stop_loss=None,
            take_profit=None,
            status="FILLED",
            create_time=datetime.now()
        )
        
        with patch.object(broker._client, 'connect'), \
             patch.object(broker._client, 'get_account', return_value=OandaAccount.from_response(mock_account['account'])), \
             patch.object(broker, '_refresh_positions'), \
             patch.object(broker, '_refresh_orders'), \
             patch.object(broker._client, 'create_market_order', return_value=mock_oanda_order):
            
            # Connect
            await broker.connect()
            assert broker.is_connected()
            
            # Submit order
            order = Order(
                symbol=Symbol("EURUSD"),
                order_type=OrderType.MARKET,
                quantity=Decimal("1000"),
                side="buy"
            )
            
            success = await broker.submit_order(order)
            assert success
            assert order.id == "12345"
            
            # Get account info
            info = await broker.get_account_info()
            assert info['account_id'] == "123-456-789"
            
            # Disconnect
            await broker.disconnect()
            assert not broker.is_connected()


if __name__ == "__main__":
    pytest.main([__file__])
