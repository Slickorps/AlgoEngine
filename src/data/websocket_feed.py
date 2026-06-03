"""WebSocket real-time data feed for AlgoEngine"""

import asyncio
import json
import websockets
from datetime import datetime
from typing import Dict, List, Optional, Set, Callable, Any
from enum import Enum, auto
import ssl
from urllib.parse import urlparse

from .feed import StreamingDataFeed
from .models import Symbol
from ..engine.events import EventBus, EventType
from ..utils.logger import get_logger

logger = get_logger("data.websocket")


class WebSocketState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()


class WebSocketConfig:
    """WebSocket connection configuration"""
    
    def __init__(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        close_timeout: int = 10,
        max_size: int = 2**20,  # 1MB
        max_queue: int = 2**5,    # 32
        compression: Optional[str] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
        origin: Optional[str] = None
    ):
        self.url = url
        self.headers = headers or {}
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.close_timeout = close_timeout
        self.max_size = max_size
        self.max_queue = max_queue
        self.compression = compression
        self.ssl_context = ssl_context
        self.origin = origin


class WebSocketConnection:
    """Individual WebSocket connection manager"""
    
    def __init__(self, config: WebSocketConfig, connection_id: str):
        self._config = config
        self._connection_id = connection_id
        self._websocket: Optional[websockets.WebSocketServerProtocol] = None
        self._state = WebSocketState.DISCONNECTED
        self._last_ping = datetime.now()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 1.0
        self._message_handlers: List[Callable[[dict], None]] = []
        self._error_handlers: List[Callable[[Exception], None]] = []
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
    @property
    def state(self) -> WebSocketState:
        """Get current connection state"""
        return self._state
    
    @property
    def connection_id(self) -> str:
        """Get connection ID"""
        return self._connection_id
    
    @property
    def is_connected(self) -> bool:
        """Check if connection is active"""
        return self._state == WebSocketState.CONNECTED
    
    def add_message_handler(self, handler: Callable[[dict], None]) -> None:
        """Add message handler"""
        self._message_handlers.append(handler)
    
    def add_error_handler(self, handler) -> None:
        """Add error handler"""
        self._error_handlers.append(handler)
    
    async def connect(self) -> bool:
        """Establish WebSocket connection"""
        async with self._lock:
            if self._state in [WebSocketState.CONNECTING, WebSocketState.CONNECTED]:
                return True
            
            self._state = WebSocketState.CONNECTING
            
            try:
                logger.info(f"Connecting to {self._config.url} (ID: {self._connection_id})")
                
                # Parse URL and extract scheme
                parsed_url = urlparse(self._config.url)
                use_ssl = parsed_url.scheme == 'wss'
                
                # Create WebSocket connection
                self._websocket = await websockets.connect(
                    self._config.url,
                    extra_headers=self._config.headers,
                    ping_interval=self._config.ping_interval,
                    ping_timeout=self._config.ping_timeout,
                    close_timeout=self._config.close_timeout,
                    max_size=self._config.max_size,
                    max_queue=self._config.max_queue,
                    compression=self._config.compression,
                    ssl=self._config.ssl_context if use_ssl else None,
                    origin=self._config.origin
                )
                
                self._state = WebSocketState.CONNECTED
                self._reconnect_attempts = 0
                self._last_ping = datetime.now()
                
                logger.info(f"Connected to {self._config.url} (ID: {self._connection_id})")
                
                # Start message processing task
                self._task = asyncio.create_task(self._message_loop())
                
                return True
                
            except Exception as e:
                self._state = WebSocketState.ERROR
                logger.error(f"Failed to connect to {self._config.url}: {e}")
                await self._handle_error(e)
                return False
    
    async def disconnect(self) -> None:
        """Close WebSocket connection"""
        async with self._lock:
            if self._state == WebSocketState.DISCONNECTED:
                return
            
            logger.info(f"Disconnecting from {self._config.url} (ID: {self._connection_id})")
            
            self._state = WebSocketState.DISCONNECTED
            
            # Cancel message loop task
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            
            # Close WebSocket
            if self._websocket:
                try:
                    await self._websocket.close()
                except Exception as e:
                    logger.warning(f"Error closing WebSocket: {e}")
                finally:
                    self._websocket = None
            
            logger.info(f"Disconnected from {self._config.url} (ID: {self._connection_id})")
    
    async def send_message(self, message: dict) -> bool:
        """Send message through WebSocket"""
        if not self.is_connected or not self._websocket:
            logger.warning(f"Cannot send message - not connected (ID: {self._connection_id})")
            return False
        
        try:
            message_str = json.dumps(message)
            await self._websocket.send(message_str)
            logger.debug(f"Sent message: {message_str} (ID: {self._connection_id})")
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e} (ID: {self._connection_id})")
            await self._handle_error(e)
            return False
    
    async def _message_loop(self) -> None:
        """Main message processing loop"""
        try:
            async for message in self._websocket:
                try:
                    # Parse JSON message
                    data = json.loads(message)
                    
                    # Update ping time
                    self._last_ping = datetime.now()
                    
                    # Call message handlers
                    for handler in self._message_handlers:
                        try:
                            handler(data)
                        except Exception as e:
                            logger.error(f"Error in message handler: {e}")
                
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"WebSocket connection closed (ID: {self._connection_id})")
            await self._handle_connection_loss()
        except Exception as e:
            logger.error(f"Error in message loop: {e} (ID: {self._connection_id})")
            await self._handle_error(e)
    
    async def _handle_connection_loss(self) -> None:
        """Handle connection loss and attempt reconnection"""
        if self._state == WebSocketState.DISCONNECTED:
            return
        
        self._state = WebSocketState.RECONNECTING
        
        if self._reconnect_attempts < self._max_reconnect_attempts:
            self._reconnect_attempts += 1
            delay = self._reconnect_delay * (2 ** (self._reconnect_attempts - 1))  # Exponential backoff
            
            logger.info(f"Attempting reconnection {self._reconnect_attempts}/{self._max_reconnect_attempts} "
                      f"in {delay:.1f}s (ID: {self._connection_id})")
            
            await asyncio.sleep(delay)
            
            # Clean up existing connection
            if self._websocket:
                try:
                    await self._websocket.close()
                except:
                    pass
                self._websocket = None
            
            # Attempt reconnection
            await self.connect()
        else:
            logger.error(f"Max reconnection attempts reached (ID: {self._connection_id})")
            self._state = WebSocketState.ERROR
    
    async def _handle_error(self, error: Exception) -> None:
        """Handle connection errors"""
        # Call error handlers
        for handler in self._error_handlers:
            try:
                result = handler(error)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Error in error handler: {e}")
        
        # Attempt reconnection for connection errors
        if isinstance(error, (websockets.exceptions.ConnectionClosed, 
                            websockets.exceptions.ConnectionClosedOK,
                            ConnectionError)):
            await self._handle_connection_loss()
    
    async def ping(self) -> bool:
        """Send ping to check connection health"""
        if not self.is_connected or not self._websocket:
            return False
        
        try:
            await self._websocket.ping()
            self._last_ping = datetime.now()
            return True
        except Exception as e:
            logger.error(f"Ping failed: {e} (ID: {self._connection_id})")
            await self._handle_error(e)
            return False
    
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information"""
        return {
            'connection_id': self._connection_id,
            'url': self._config.url,
            'state': self._state.name,
            'last_ping': self._last_ping.isoformat(),
            'reconnect_attempts': self._reconnect_attempts,
            'connected_at': datetime.now().isoformat() if self.is_connected else None
        }


class WebSocketFeedManager:
    """Manager for multiple WebSocket connections"""
    
    def __init__(self, event_bus: Optional[EventBus] = None):
        self._connections: Dict[str, WebSocketConnection] = {}
        self._event_bus = event_bus or EventBus()
        self._subscriptions: Dict[Symbol, Set[str]] = {}  # symbol -> connection_ids
        self._lock = asyncio.Lock()
        
    async def add_connection(self, connection_id: str, config: WebSocketConfig) -> bool:
        """Add a new WebSocket connection"""
        async with self._lock:
            if connection_id in self._connections:
                logger.warning(f"Connection {connection_id} already exists")
                return False
            
            connection = WebSocketConnection(config, connection_id)
            
            # Add default message handler
            connection.add_message_handler(
                lambda data: self._handle_message(connection_id, data)
            )
            
            # Add default error handler
            connection.add_error_handler(
                lambda error: self._handle_error(connection_id, error)
            )
            
            self._connections[connection_id] = connection
            
            # Connect
            success = await connection.connect()
            if success:
                logger.info(f"Added WebSocket connection: {connection_id}")
            else:
                # Remove failed connection
                del self._connections[connection_id]
            
            return success
    
    async def remove_connection(self, connection_id: str) -> None:
        """Remove a WebSocket connection"""
        async with self._lock:
            if connection_id not in self._connections:
                return
            
            connection = self._connections[connection_id]
            await connection.disconnect()
            
            # Remove subscriptions
            symbols_to_remove = []
            for symbol, conn_ids in self._subscriptions.items():
                conn_ids.discard(connection_id)
                if not conn_ids:
                    symbols_to_remove.append(symbol)
            
            for symbol in symbols_to_remove:
                del self._subscriptions[symbol]
            
            del self._connections[connection_id]
            logger.info(f"Removed WebSocket connection: {connection_id}")
    
    async def subscribe_symbol(self, symbol: Symbol, connection_id: str) -> bool:
        """Subscribe to symbol on specific connection"""
        async with self._lock:
            if connection_id not in self._connections:
                logger.error(f"Connection {connection_id} not found")
                return False
            
            if symbol not in self._subscriptions:
                self._subscriptions[symbol] = set()
            
            self._subscriptions[symbol].add(connection_id)
            logger.info(f"Subscribed {symbol.ticker} on connection {connection_id}")
            return True
    
    async def unsubscribe_symbol(self, symbol: Symbol, connection_id: Optional[str] = None) -> None:
        """Unsubscribe from symbol"""
        async with self._lock:
            if symbol not in self._subscriptions:
                return
            
            if connection_id:
                # Unsubscribe from specific connection
                self._subscriptions[symbol].discard(connection_id)
                if not self._subscriptions[symbol]:
                    del self._subscriptions[symbol]
            else:
                # Unsubscribe from all connections
                del self._subscriptions[symbol]
            
            logger.info(f"Unsubscribed {symbol.ticker}")
    
    async def send_to_connection(self, connection_id: str, message: dict) -> bool:
        """Send message to specific connection"""
        async with self._lock:
            if connection_id not in self._connections:
                return False
            
            connection = self._connections[connection_id]
            return await connection.send_message(message)
    
    async def broadcast_to_symbol(self, symbol: Symbol, message: dict) -> int:
        """Broadcast message to all connections subscribed to symbol"""
        async with self._lock:
            if symbol not in self._subscriptions:
                return 0
            
            connection_ids = self._subscriptions[symbol].copy()
            success_count = 0
            
            for conn_id in connection_ids:
                if conn_id in self._connections:
                    connection = self._connections[conn_id]
                    if await connection.send_message(message):
                        success_count += 1
            
            return success_count
    
    def get_connection_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all connections"""
        status = {}
        for conn_id, connection in self._connections.items():
            status[conn_id] = connection.get_connection_info()
        return status
    
    def get_subscriptions(self) -> Dict[str, List[str]]:
        """Get current subscriptions"""
        return {
            symbol.ticker: list(conn_ids)
            for symbol, conn_ids in self._subscriptions.items()
        }
    
    async def _handle_message(self, connection_id: str, data: dict) -> None:
        """Handle incoming WebSocket message"""
        try:
            # Emit to event bus
            self._event_bus.emit(EventType.WEBSOCKET_MESSAGE, {
                'connection_id': connection_id,
                'data': data,
                'timestamp': datetime.now()
            })
            
            # Symbol-specific handling can be implemented in subclasses
            self._process_symbol_data(connection_id, data)
            
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
    
    async def _handle_error(self, connection_id: str, error: Exception) -> None:
        """Handle WebSocket connection error"""
        logger.error(f"WebSocket error on connection {connection_id}: {error}")
        
        # Emit error event
        self._event_bus.emit(EventType.WEBSOCKET_ERROR, {
            'connection_id': connection_id,
            'error': str(error),
            'timestamp': datetime.now()
        })
    
    def _process_symbol_data(self, connection_id: str, data: dict) -> None:
        """Process symbol-specific data - override in subclasses"""
        pass
    
    async def shutdown(self) -> None:
        """Shutdown all connections"""
        logger.info("Shutting down WebSocket feed manager")
        
        async with self._lock:
            tasks = []
            for connection in self._connections.values():
                tasks.append(connection.disconnect())
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            self._connections.clear()
            self._subscriptions.clear()
        
        logger.info("WebSocket feed manager shutdown complete")


class WebSocketDataFeed(StreamingDataFeed):
    """WebSocket-based data feed implementation"""
    
    def __init__(self, name: str = "WebSocketFeed"):
        super().__init__(name)
        self._manager = WebSocketFeedManager(self._event_bus)
        self._symbol_connections: Dict[Symbol, str] = {}  # symbol -> connection_id
    
    async def connect(self) -> bool:
        """Connect to WebSocket data source"""
        # Override in subclasses to implement specific connection logic
        return True
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket data source"""
        await self._manager.shutdown()
    
    def is_connected(self) -> bool:
        """Check if connected"""
        status = self._manager.get_connection_status()
        return any(conn['state'] == 'CONNECTED' for conn in status.values())
    
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """Subscribe to symbols"""
        for symbol in symbols:
            # Assign symbol to a connection (round-robin or specific logic)
            connection_id = self._get_connection_for_symbol(symbol)
            if connection_id:
                await self._manager.subscribe_symbol(symbol, connection_id)
                self._symbol_connections[symbol] = connection_id
    
    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        """Unsubscribe from symbols"""
        for symbol in symbols:
            connection_id = self._symbol_connections.get(symbol)
            if connection_id:
                await self._manager.unsubscribe_symbol(symbol, connection_id)
                del self._symbol_connections[symbol]
    
    def get_history(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution = None
    ):
        """Get historical data - not supported by WebSocket feed"""
        raise NotImplementedError("WebSocket feed does not support historical data")
    
    def _get_connection_for_symbol(self, symbol: Symbol) -> Optional[str]:
        """Get connection ID for symbol - override in subclasses"""
        status = self._manager.get_connection_status()
        connected_ids = [
            conn_id for conn_id, info in status.items()
            if info['state'] == 'CONNECTED'
        ]
        
        if connected_ids:
            # Simple round-robin assignment
            return connected_ids[hash(symbol.ticker) % len(connected_ids)]
        
        return None
    
    async def _stream_loop(self) -> None:
        """Main streaming loop - process incoming WebSocket data"""
        while self._running:
            try:
                # In a real implementation, this would process data from the WebSocket
                # For this base class, simply yield to allow other tasks to run
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in WebSocket stream loop: {e}")

    def get_feed_status(self) -> Dict[str, Any]:
        """Get comprehensive feed status"""
        return {
            'name': self._name,
            'connected': self.is_connected(),
            'connections': self._manager.get_connection_status(),
            'subscriptions': self._manager.get_subscriptions(),
            'symbol_count': len(self._symbol_connections)
        }
