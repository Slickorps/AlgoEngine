"""Tests for WebSocket feed functionality"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from src.data.websocket_feed import (
    WebSocketConfig, WebSocketConnection, WebSocketState,
    WebSocketFeedManager, WebSocketDataFeed
)
from src.data.models import Symbol


class TestWebSocketConfig:
    """Test WebSocket configuration"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = WebSocketConfig("wss://test.example.com/ws")
        
        assert config.url == "wss://test.example.com/ws"
        assert config.headers == {}
        assert config.ping_interval == 30
        assert config.ping_timeout == 10
        assert config.close_timeout == 10
        assert config.max_size == 2**20
        assert config.max_queue == 2**5
        assert config.compression is None
        assert config.ssl_context is None
        assert config.origin is None
    
    def test_custom_config(self):
        """Test custom configuration values"""
        headers = {"Authorization": "Bearer token123"}
        config = WebSocketConfig(
            url="ws://test.example.com/ws",
            headers=headers,
            ping_interval=60,
            ping_timeout=20,
            compression="deflate"
        )
        
        assert config.url == "ws://test.example.com/ws"
        assert config.headers == headers
        assert config.ping_interval == 60
        assert config.ping_timeout == 20
        assert config.compression == "deflate"


class TestWebSocketConnection:
    """Test WebSocket connection management"""
    
    @pytest.fixture
    def config(self):
        """Test configuration"""
        return WebSocketConfig("wss://test.example.com/ws")
    
    @pytest.fixture
    def connection(self, config):
        """Test connection"""
        return WebSocketConnection(config, "test-conn")
    
    def test_connection_initialization(self, connection):
        """Test connection initialization"""
        assert connection.connection_id == "test-conn"
        assert connection.state == WebSocketState.DISCONNECTED
        assert not connection.is_connected
        assert len(connection._message_handlers) == 0
        assert len(connection._error_handlers) == 0
    
    def test_add_handlers(self, connection):
        """Test adding message and error handlers"""
        message_handler = MagicMock()
        error_handler = MagicMock()
        
        connection.add_message_handler(message_handler)
        connection.add_error_handler(error_handler)
        
        assert len(connection._message_handlers) == 1
        assert len(connection._error_handlers) == 1
    
    @pytest.mark.asyncio
    async def test_connect_success(self, connection):
        """Test successful connection"""
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Mock the message loop
            with patch.object(connection, '_message_loop', new=AsyncMock()):
                success = await connection.connect()
                
                assert success
                assert connection.state == WebSocketState.CONNECTED
                assert connection.is_connected
    
    @pytest.mark.asyncio
    async def test_connect_failure(self, connection):
        """Test connection failure"""
        with patch('websockets.connect', side_effect=Exception("Connection failed")):
            success = await connection.connect()
            
            assert not success
            assert connection.state == WebSocketState.ERROR
            assert not connection.is_connected
    
    @pytest.mark.asyncio
    async def test_disconnect(self, connection):
        """Test disconnection"""
        # Mock connected state
        connection._state = WebSocketState.CONNECTED
        mock_ws = AsyncMock()
        connection._websocket = mock_ws
        # Create a real cancelled task that can be awaited
        async def dummy_task():
            while True:
                await asyncio.sleep(1)
        
        real_task = asyncio.ensure_future(dummy_task())
        await asyncio.sleep(0)  # Let the task start
        connection._task = real_task
        
        await connection.disconnect()
        
        assert connection.state == WebSocketState.DISCONNECTED
        assert not connection.is_connected
        mock_ws.close.assert_awaited_once()
    
    @pytest.mark.asyncio
    async def test_send_message_success(self, connection):
        """Test successful message sending"""
        # Mock connected state
        connection._state = WebSocketState.CONNECTED
        connection._websocket = AsyncMock()
        
        message = {"type": "subscribe", "symbol": "AAPL"}
        success = await connection.send_message(message)
        
        assert success
        connection._websocket.send.assert_called_once()
        call_args = connection._websocket.send.call_args[0][0]
        sent_data = json.loads(call_args)
        assert sent_data == message
    
    @pytest.mark.asyncio
    async def test_send_message_not_connected(self, connection):
        """Test sending message when not connected"""
        message = {"type": "subscribe", "symbol": "AAPL"}
        success = await connection.send_message(message)
        
        assert not success
    
    @pytest.mark.asyncio
    async def test_ping_success(self, connection):
        """Test successful ping"""
        # Mock connected state
        connection._state = WebSocketState.CONNECTED
        connection._websocket = AsyncMock()
        
        success = await connection.ping()
        
        assert success
        connection._websocket.ping.assert_called_once()
    
    def test_get_connection_info(self, connection):
        """Test getting connection information"""
        connection._reconnect_attempts = 2
        info = connection.get_connection_info()
        
        assert info['connection_id'] == "test-conn"
        assert info['url'] == "wss://test.example.com/ws"
        assert info['state'] == WebSocketState.DISCONNECTED.name
        assert info['reconnect_attempts'] == 2
        assert 'last_ping' in info


class TestWebSocketFeedManager:
    """Test WebSocket feed manager"""
    
    @pytest.fixture
    def manager(self):
        """Test manager"""
        return WebSocketFeedManager()
    
    @pytest.fixture
    def config(self):
        """Test configuration"""
        return WebSocketConfig("wss://test.example.com/ws")
    
    @pytest.mark.asyncio
    async def test_add_connection_success(self, manager, config):
        """Test successful connection addition"""
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            success = await manager.add_connection("test-conn", config)
            
            assert success
            assert "test-conn" in manager._connections
    
    @pytest.mark.asyncio
    async def test_add_duplicate_connection(self, manager, config):
        """Test adding duplicate connection"""
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add first connection
            await manager.add_connection("test-conn", config)
            
            # Try to add duplicate
            success = await manager.add_connection("test-conn", config)
            
            assert not success
    
    @pytest.mark.asyncio
    async def test_remove_connection(self, manager, config):
        """Test connection removal"""
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add connection first
            await manager.add_connection("test-conn", config)
            
            # Remove connection
            await manager.remove_connection("test-conn")
            
            assert "test-conn" not in manager._connections
    
    @pytest.mark.asyncio
    async def test_subscribe_symbol(self, manager, config):
        """Test symbol subscription"""
        symbol = Symbol("AAPL")
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add connection first
            await manager.add_connection("test-conn", config)
            
            # Subscribe to symbol
            success = await manager.subscribe_symbol(symbol, "test-conn")
            
            assert success
            assert symbol in manager._subscriptions
            assert "test-conn" in manager._subscriptions[symbol]
    
    @pytest.mark.asyncio
    async def test_unsubscribe_symbol(self, manager, config):
        """Test symbol unsubscription"""
        symbol = Symbol("AAPL")
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add connection and subscribe
            await manager.add_connection("test-conn", config)
            await manager.subscribe_symbol(symbol, "test-conn")
            
            # Unsubscribe
            await manager.unsubscribe_symbol(symbol, "test-conn")
            
            assert symbol not in manager._subscriptions
    
    @pytest.mark.asyncio
    async def test_send_to_connection(self, manager, config):
        """Test sending message to specific connection"""
        message = {"type": "test", "data": "hello"}
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add connection
            await manager.add_connection("test-conn", config)
            
            # Send message
            success = await manager.send_to_connection("test-conn", message)
            
            assert success
    
    @pytest.mark.asyncio
    async def test_broadcast_to_symbol(self, manager, config):
        """Test broadcasting message to symbol subscribers"""
        symbol = Symbol("AAPL")
        message = {"type": "price", "symbol": "AAPL", "price": 150.0}
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add two connections and subscribe both to symbol
            await manager.add_connection("conn1", config)
            await manager.add_connection("conn2", config)
            await manager.subscribe_symbol(symbol, "conn1")
            await manager.subscribe_symbol(symbol, "conn2")
            
            # Broadcast message
            success_count = await manager.broadcast_to_symbol(symbol, message)
            
            assert success_count == 2
    
    def test_get_connection_status(self, manager):
        """Test getting connection status"""
        status = manager.get_connection_status()
        
        assert isinstance(status, dict)
        assert len(status) == len(manager._connections)
    
    def test_get_subscriptions(self, manager):
        """Test getting subscriptions"""
        subscriptions = manager.get_subscriptions()
        
        assert isinstance(subscriptions, dict)
    
    @pytest.mark.asyncio
    async def test_shutdown(self, manager, config):
        """Test manager shutdown"""
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add multiple connections
            await manager.add_connection("conn1", config)
            await manager.add_connection("conn2", config)
            
            # Shutdown
            await manager.shutdown()
            
            assert len(manager._connections) == 0
            assert len(manager._subscriptions) == 0


class TestWebSocketDataFeed:
    """Test WebSocket data feed"""
    
    @pytest.fixture
    def feed(self):
        """Test feed"""
        return WebSocketDataFeed("TestFeed")
    
    def test_feed_initialization(self, feed):
        """Test feed initialization"""
        assert feed.name == "TestFeed"
        assert not feed.is_connected()
        assert len(feed._symbol_connections) == 0
    
    @pytest.mark.asyncio
    async def test_connect(self, feed):
        """Test connection"""
        # Base implementation returns True
        success = await feed.connect()
        assert success
    
    @pytest.mark.asyncio
    async def test_disconnect(self, feed):
        """Test disconnection"""
        await feed.disconnect()
        assert not feed.is_connected()
    
    @pytest.mark.asyncio
    async def test_subscribe(self, feed):
        """Test symbol subscription"""
        symbols = [Symbol("AAPL"), Symbol("MSFT")]
        
        # Mock a connected connection - must have proper state return
        mock_conn = MagicMock()
        mock_conn.get_connection_info.return_value = {'state': 'CONNECTED'}
        
        # Directly add to the manager's connections dict
        feed._manager._connections["test-conn"] = mock_conn
        
        await feed.subscribe(symbols)
        
        assert len(feed._symbol_connections) == len(symbols)
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, feed):
        """Test symbol unsubscription"""
        symbols = [Symbol("AAPL"), Symbol("MSFT")]
        
        # Mock connections and subscriptions
        feed._symbol_connections = {sym: "test" for sym in symbols}
        feed._manager._subscriptions = {sym: {"test"} for sym in symbols}
        
        await feed.unsubscribe(symbols)
        
        assert len(feed._symbol_connections) == 0
    
    def test_get_history_not_supported(self, feed):
        """Test that historical data is not supported"""
        symbol = Symbol("AAPL")
        start = datetime.now() - timedelta(days=1)
        end = datetime.now()
        
        with pytest.raises(NotImplementedError):
            feed.get_history(symbol, start, end)
    
    def test_get_feed_status(self, feed):
        """Test getting feed status"""
        status = feed.get_feed_status()
        
        assert isinstance(status, dict)
        assert 'name' in status
        assert 'connected' in status
        assert 'connections' in status
        assert 'subscriptions' in status
        assert 'symbol_count' in status
        assert status['name'] == "TestFeed"


class TestWebSocketIntegration:
    """Integration tests for WebSocket functionality"""
    
    @pytest.mark.asyncio
    async def test_message_flow(self):
        """Test complete message flow"""
        manager = WebSocketFeedManager()
        config = WebSocketConfig("wss://test.example.com/ws")
        mock_websocket = AsyncMock()
        
        async def mock_connect(*args, **kwargs):
            return mock_websocket
        
        with patch('websockets.connect', side_effect=mock_connect):
            # Add connection
            await manager.add_connection("test-conn", config)
            
            # Subscribe to symbol
            symbol = Symbol("AAPL")
            await manager.subscribe_symbol(symbol, "test-conn")
            
            # Send message
            message = {"type": "price", "symbol": "AAPL", "price": 150.0}
            success = await manager.send_to_connection("test-conn", message)
            
            assert success
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test error handling in WebSocket connections"""
        manager = WebSocketFeedManager()
        config = WebSocketConfig("wss://invalid.example.com/ws")
        
        # Connection should fail
        success = await manager.add_connection("invalid-conn", config)
        
        assert not success
        assert "invalid-conn" not in manager._connections


if __name__ == "__main__":
    pytest.main([__file__])
