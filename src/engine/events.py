"""Event system for AlgoEngine"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

from ..utils.logger import get_logger

logger = get_logger("events")


class EventType(Enum):
    """Event types"""
    # Market data events
    TICK = auto()
    BAR = auto()
    TRADE = auto()
    QUOTE = auto()
    
    # Order events
    ORDER_SUBMITTED = auto()
    ORDER_FILLED = auto()
    ORDER_CANCELLED = auto()
    ORDER_REJECTED = auto()
    ORDER_UPDATED = auto()
    
    # Position events
    POSITION_OPENED = auto()
    POSITION_CLOSED = auto()
    POSITION_UPDATED = auto()
    
    # Portfolio events
    PORTFOLIO_CHANGED = auto()
    CASH_CHANGED = auto()
    
    # System events
    START = auto()
    STOP = auto()
    PAUSE = auto()
    RESUME = auto()
    WARMUP_STARTED = auto()
    WARMUP_FINISHED = auto()
    
    # Time events
    MARKET_OPEN = auto()
    MARKET_CLOSE = auto()
    END_OF_DAY = auto()
    
    # Custom events
    CUSTOM = auto()
    SIGNAL = auto()
    ALERT = auto()
    
    # WebSocket events
    WEBSOCKET_MESSAGE = auto()
    WEBSOCKET_ERROR = auto()
    WEBSOCKET_CONNECTED = auto()
    WEBSOCKET_DISCONNECTED = auto()


@dataclass
class Event:
    """Event data structure"""
    event_type: EventType
    timestamp: datetime
    data: Any = None
    symbol: Optional[str] = None
    source: str = ""
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


class EventBus:
    """Central event bus for the trading engine"""
    
    def __init__(self) -> None:
        self._handlers: Dict[EventType, List[Callable[[Event], Any]]] = defaultdict(list)
        self._async_handlers: Dict[EventType, List[Callable[[Event], Any]]] = defaultdict(list)
        self._global_handlers: List[Callable[[Event], Any]] = []
        self._async_global_handlers: List[Callable[[Event], Any]] = []
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running: bool = False
        self._lock: asyncio.Lock = asyncio.Lock()
        
    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], Any],
        async_handler: bool = False
    ) -> None:
        """Subscribe to an event type"""
        if async_handler:
            self._async_handlers[event_type].append(handler)
        else:
            self._handlers[event_type].append(handler)
        logger.debug(f"Handler subscribed to {event_type.name}")
    
    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], Any],
        async_handler: bool = False
    ) -> None:
        """Unsubscribe from an event type"""
        handlers = self._async_handlers[event_type] if async_handler else self._handlers[event_type]
        if handler in handlers:
            handlers.remove(handler)
            logger.debug(f"Handler unsubscribed from {event_type.name}")
    
    def subscribe_all(
        self,
        handler: Callable[[Event], Any],
        async_handler: bool = False
    ) -> None:
        """Subscribe to all events"""
        if async_handler:
            self._async_global_handlers.append(handler)
        else:
            self._global_handlers.append(handler)
        logger.debug("Handler subscribed to all events")
    
    def unsubscribe_all(
        self,
        handler: Callable[[Event], Any],
        async_handler: bool = False
    ) -> None:
        """Unsubscribe from all events"""
        handlers = self._async_global_handlers if async_handler else self._global_handlers
        if handler in handlers:
            handlers.remove(handler)
            logger.debug("Handler unsubscribed from all events")
    
    def emit(self, event: Event) -> None:
        """Emit an event (synchronous)"""
        # Notify type-specific handlers
        for handler in self._handlers.get(event.event_type, []):
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Error in event handler: {e}")
        
        # Notify global handlers
        for handler in self._global_handlers:
            try:
                handler(event)
            except Exception as e:
                logger.error(f"Error in global event handler: {e}")
        
        # Queue async handlers
        for handler in self._async_handlers.get(event.event_type, []):
            asyncio.create_task(self._handle_async(handler, event))
        
        for handler in self._async_global_handlers:
            asyncio.create_task(self._handle_async(handler, event))
    
    async def _handle_async(self, handler: Callable[[Event], Any], event: Event) -> None:
        """Handle async event"""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error(f"Error in async event handler: {e}")
    
    async def emit_async(self, event: Event) -> None:
        """Emit an event asynchronously"""
        await self._event_queue.put(event)
    
    async def start(self) -> None:
        """Start the event loop"""
        self._running = True
        logger.info("Event bus started")
        
        while self._running:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                self.emit(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing event: {e}")
    
    def stop(self) -> None:
        """Stop the event loop"""
        self._running = False
        logger.info("Event bus stopped")
    
    def clear_handlers(self, event_type: Optional[EventType] = None) -> None:
        """Clear all handlers for an event type or all events"""
        if event_type:
            self._handlers[event_type].clear()
            self._async_handlers[event_type].clear()
            logger.debug(f"Cleared handlers for {event_type.name}")
        else:
            self._handlers.clear()
            self._async_handlers.clear()
            self._global_handlers.clear()
            self._async_global_handlers.clear()
            logger.debug("Cleared all handlers")
    
    def get_handler_count(self, event_type: Optional[EventType] = None) -> int:
        """Get the number of handlers for an event type"""
        if event_type:
            return len(self._handlers.get(event_type, [])) + \
                   len(self._async_handlers.get(event_type, []))
        else:
            total = len(self._global_handlers) + len(self._async_global_handlers)
            for handlers in self._handlers.values():
                total += len(handlers)
            for handlers in self._async_handlers.values():
                total += len(handlers)
            return total


# Global event bus instance
event_bus = EventBus()


def get_event_bus() -> EventBus:
    """Get the global event bus instance"""
    return event_bus
