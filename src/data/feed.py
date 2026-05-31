"""Data feed base classes for AlgoEngine"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Set, Optional, Callable, Any
from collections import defaultdict
import pandas as pd

from .models import Symbol, Tick, Bar, Resolution, DataType, MarketData
from ..engine.events import Event, EventType, get_event_bus
from ..utils.logger import get_logger

logger = get_logger("data.feed")


class DataFeed(ABC):
    """Base class for data feed providers"""
    
    def __init__(self, name: str = "DataFeed") -> None:
        self._name = name
        self._subscribed_symbols: Set[Symbol] = set()
        self._running: bool = False
        self._event_bus = get_event_bus()
        self._callbacks: Dict[DataType, List[Callable[[MarketData], None]]] = defaultdict(list)
        self._lock: asyncio.Lock = asyncio.Lock()
        
    @property
    def name(self) -> str:
        """Get feed name"""
        return self._name
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to data source"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from data source"""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected"""
        pass
    
    @abstractmethod
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """Subscribe to symbols for real-time data"""
        pass
    
    @abstractmethod
    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        """Unsubscribe from symbols"""
        pass
    
    @abstractmethod
    def get_history(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> pd.DataFrame:
        """Get historical data"""
        pass
    
    def on_data(self, callback: Callable[[MarketData], None], data_type: Optional[DataType] = None) -> None:
        """Register callback for incoming data"""
        if data_type:
            self._callbacks[data_type].append(callback)
        else:
            for dt in DataType:
                self._callbacks[dt].append(callback)
    
    def _emit_data(self, data: MarketData) -> None:
        """Emit data to registered callbacks and event bus"""
        # Call registered callbacks
        for callback in self._callbacks.get(data.data_type, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in data callback: {e}")
        
        # Emit to event bus
        event_type = self._map_data_type_to_event(data.data_type)
        if event_type:
            self._event_bus.emit(Event(
                event_type=event_type,
                timestamp=data.timestamp,
                data=data,
                symbol=str(data.symbol)
            ))
    
    def _map_data_type_to_event(self, data_type: DataType) -> Optional[EventType]:
        """Map data type to event type"""
        mapping = {
            DataType.TICK: EventType.TICK,
            DataType.BAR: EventType.BAR,
            DataType.TRADE: EventType.TRADE,
            DataType.QUOTE: EventType.QUOTE,
        }
        return mapping.get(data_type)


class StreamingDataFeed(DataFeed):
    """Streaming data feed with async processing"""
    
    def __init__(self, name: str = "StreamingFeed") -> None:
        super().__init__(name)
        self._stream_task: Optional[asyncio.Task] = None
        self._buffer: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._buffer_task: Optional[asyncio.Task] = None
        
    async def start_streaming(self) -> None:
        """Start streaming data processing"""
        self._running = True
        self._stream_task = asyncio.create_task(self._stream_loop())
        self._buffer_task = asyncio.create_task(self._buffer_loop())
        logger.info(f"Started streaming for {self._name}")
    
    async def stop_streaming(self) -> None:
        """Stop streaming"""
        self._running = False
        
        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        
        if self._buffer_task:
            self._buffer_task.cancel()
            try:
                await self._buffer_task
            except asyncio.CancelledError:
                pass
        
        logger.info(f"Stopped streaming for {self._name}")
    
    @abstractmethod
    async def _stream_loop(self) -> None:
        """Main streaming loop - override in subclasses"""
        pass
    
    async def _buffer_loop(self) -> None:
        """Process buffered data"""
        while self._running:
            try:
                data = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
                self._emit_data(data)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in buffer loop: {e}")
    
    def _buffer_data(self, data: MarketData) -> None:
        """Add data to buffer"""
        try:
            self._buffer.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning(f"Data buffer full, dropping data for {data.symbol}")


class DataCache:
    """In-memory data cache for frequently accessed data"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
    
    def _make_key(self, symbol: Symbol, data_type: DataType, resolution: Optional[Resolution] = None) -> str:
        """Create cache key"""
        key = f"{symbol.ticker}:{data_type.name}"
        if resolution:
            key += f":{resolution.value}"
        return key
    
    async def get(
        self,
        symbol: Symbol,
        data_type: DataType,
        resolution: Optional[Resolution] = None
    ) -> Optional[Any]:
        """Get cached data"""
        async with self._lock:
            key = self._make_key(symbol, data_type, resolution)
            
            if key not in self._cache:
                return None
            
            timestamp = self._timestamps.get(key)
            if timestamp and (datetime.now() - timestamp).total_seconds() > self._ttl_seconds:
                # Expired
                del self._cache[key]
                del self._timestamps[key]
                return None
            
            return self._cache[key]
    
    async def set(
        self,
        symbol: Symbol,
        data_type: DataType,
        data: Any,
        resolution: Optional[Resolution] = None
    ) -> None:
        """Set cached data"""
        async with self._lock:
            key = self._make_key(symbol, data_type, resolution)
            
            # Evict oldest if at capacity
            if len(self._cache) >= self._max_size and key not in self._cache:
                oldest_key = min(self._timestamps, key=self._timestamps.get)
                del self._cache[oldest_key]
                del self._timestamps[oldest_key]
            
            self._cache[key] = data
            self._timestamps[key] = datetime.now()
    
    async def clear(self, symbol: Optional[Symbol] = None) -> None:
        """Clear cache for symbol or all"""
        async with self._lock:
            if symbol:
                keys_to_remove = [
                    k for k in self._cache.keys()
                    if k.startswith(f"{symbol.ticker}:")
                ]
                for key in keys_to_remove:
                    del self._cache[key]
                    if key in self._timestamps:
                        del self._timestamps[key]
            else:
                self._cache.clear()
                self._timestamps.clear()
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            'size': len(self._cache),
            'max_size': self._max_size,
            'ttl_seconds': self._ttl_seconds
        }


class DataAggregator:
    """Aggregate high-frequency data into bars"""
    
    def __init__(self, resolution: Resolution = Resolution.MINUTE) -> None:
        self._resolution = resolution
        self._buffers: Dict[Symbol, List[Tick]] = defaultdict(list)
        self._last_bar_time: Dict[Symbol, datetime] = {}
    
    def add_tick(self, tick: Tick) -> Optional[Bar]:
        """Add tick and return bar if ready"""
        symbol = tick.symbol
        current_time = tick.timestamp
        
        # Get period start time
        period_start = self._get_period_start(current_time)
        
        # Check if we need to emit previous bar
        if symbol in self._last_bar_time:
            last_period = self._get_period_start(self._last_bar_time[symbol])
            if period_start > last_period and self._buffers[symbol]:
                bar = self._create_bar(symbol, last_period, self._buffers[symbol])
                self._buffers[symbol] = []
                self._buffers[symbol].append(tick)
                self._last_bar_time[symbol] = current_time
                return bar
        
        self._buffers[symbol].append(tick)
        self._last_bar_time[symbol] = current_time
        return None
    
    def _get_period_start(self, dt: datetime) -> datetime:
        """Get start of aggregation period"""
        if self._resolution == Resolution.MINUTE:
            return dt.replace(second=0, microsecond=0)
        elif self._resolution == Resolution.HOUR:
            return dt.replace(minute=0, second=0, microsecond=0)
        elif self._resolution == Resolution.DAILY:
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt
    
    def _create_bar(self, symbol: Symbol, timestamp: datetime, ticks: List[Tick]) -> Bar:
        """Create bar from ticks"""
        prices = [t.mid_price for t in ticks]
        volumes = [t.bid_size + t.ask_size for t in ticks]
        
        return Bar(
            symbol=symbol,
            timestamp=timestamp,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=sum(volumes),
            resolution=self._resolution
        )
    
    def flush(self, symbol: Optional[Symbol] = None) -> List[Bar]:
        """Flush all pending ticks to bars"""
        bars = []
        
        if symbol:
            symbols = [symbol]
        else:
            symbols = list(self._buffers.keys())
        
        for sym in symbols:
            if self._buffers[sym]:
                last_time = self._last_bar_time.get(sym, datetime.now())
                period_start = self._get_period_start(last_time)
                bar = self._create_bar(sym, period_start, self._buffers[sym])
                bars.append(bar)
                self._buffers[sym] = []
        
        return bars
