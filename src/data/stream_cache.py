"""Streaming data cache with deduplication and backpressure control"""

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Any
from threading import Lock

from .models import Symbol, Tick, Bar, MarketData
from ..utils.logger import get_logger

logger = get_logger("data.stream_cache")


class CachePolicy(Enum):
    """Cache eviction policies"""
    LRU = auto()        # Least Recently Used
    FIFO = auto()       # First In First Out
    TTL = auto()        # Time To Live
    COUNT = auto()      # Keep by count


# Global monotonic sequence counter for LRU ordering
_lru_seq_counter = 0


def _next_lru_seq() -> int:
    """Get next monotonic sequence number for LRU ordering"""
    global _lru_seq_counter
    _lru_seq_counter += 1
    return _lru_seq_counter


@dataclass
class CacheEntry:
    """Single cache entry with metadata"""
    data: MarketData
    timestamp: datetime
    access_count: int = 0
    last_access: datetime = field(default_factory=datetime.now)
    access_seq: int = field(default_factory=_next_lru_seq)
    
    def touch(self) -> None:
        """Update access metadata"""
        self.access_count += 1
        self.last_access = datetime.now()
        self.access_seq = _next_lru_seq()


@dataclass
class BackPressureConfig:
    """Backpressure configuration"""
    max_queue_size: int = 10000
    high_watermark: float = 0.8
    low_watermark: float = 0.3
    drop_policy: str = "oldest"  # oldest, newest, random
    enable_throttling: bool = True
    throttle_delay: float = 0.001  # seconds


class StreamCache:
    """High-performance streaming data cache with deduplication"""
    
    def __init__(
        self,
        max_size: int = 100000,
        policy: CachePolicy = CachePolicy.LRU,
        ttl_seconds: Optional[float] = None,
        enable_dedup: bool = True,
        backpressure: Optional[BackPressureConfig] = None
    ):
        self._max_size = max_size
        self._policy = policy
        self._ttl_seconds = ttl_seconds
        self._enable_dedup = enable_dedup
        self._backpressure = backpressure or BackPressureConfig()
        
        # Main cache storage
        self._cache: Dict[str, CacheEntry] = {}
        self._symbol_indices: Dict[Symbol, Set[str]] = defaultdict(set)
        
        # Deduplication tracking
        self._seen_hashes: deque = deque(maxlen=10000)
        self._dedup_window = timedelta(seconds=1)
        
        # Backpressure state
        self._queue_size = 0
        self._dropped_count = 0
        self._throttling = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        
        # Statistics
        self._hits = 0
        self._misses = 0
        self._dedup_hits = 0
        self._inserts = 0
        
        # Thread safety
        self._lock = Lock()
        
        # Async queue for streaming
        self._async_queue: asyncio.Queue = asyncio.Queue(maxsize=self._backpressure.max_queue_size)
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    @property
    def size(self) -> int:
        """Get current cache size"""
        with self._lock:
            return len(self._cache)
    
    @property
    def hit_rate(self) -> float:
        """Get cache hit rate"""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total
    
    @property
    def dedup_rate(self) -> float:
        """Get deduplication rate"""
        total = self._inserts + self._dedup_hits
        if total == 0:
            return 0.0
        return self._dedup_hits / total
    
    def _generate_key(self, data: MarketData) -> str:
        """Generate unique cache key for market data"""
        if isinstance(data, Tick):
            return f"{data.symbol.ticker}:{data.timestamp.isoformat()}:{data.bid_price}:{data.ask_price}"
        elif isinstance(data, Bar):
            return f"{data.symbol.ticker}:{data.timestamp.isoformat()}:{data.resolution.value}"
        else:
            return f"{data.symbol.ticker}:{data.timestamp.isoformat()}:{hash(data)}"
    
    def _generate_dedup_hash(self, data: MarketData) -> str:
        """Generate lightweight deduplication hash"""
        if isinstance(data, Tick):
            # Round prices to reduce noise
            bid = round(float(data.bid_price), 4)
            ask = round(float(data.ask_price), 4)
            return f"{data.symbol.ticker}:{bid}:{ask}:{int(data.timestamp.timestamp() * 10)}"
        elif isinstance(data, Bar):
            open_p = round(float(data.open_price), 4)
            close_p = round(float(data.close_price), 4)
            return f"{data.symbol.ticker}:{data.resolution.value}:{open_p}:{close_p}:{int(data.timestamp.timestamp())}"
        else:
            return self._generate_key(data)
    
    def _is_duplicate(self, data: MarketData) -> bool:
        """Check if data is a duplicate"""
        if not self._enable_dedup:
            return False
        
        dedup_hash = self._generate_dedup_hash(data)
        
        if dedup_hash in self._seen_hashes:
            return True
        
        self._seen_hashes.append(dedup_hash)
        return False
    
    def _should_evict(self) -> bool:
        """Check if eviction is needed"""
        return len(self._cache) >= self._max_size
    
    def _evict_entry(self) -> None:
        """Evict entry based on cache policy"""
        if not self._cache:
            return
        
        if self._policy == CachePolicy.LRU:
            # Find least recently used (use access_seq as tiebreaker for timestamp resolution)
            oldest_key = min(self._cache.keys(), key=lambda k: (self._cache[k].last_access, self._cache[k].access_seq))
        elif self._policy == CachePolicy.FIFO:
            # Find oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].timestamp)
        elif self._policy == CachePolicy.TTL:
            # Find expired entries or use LRU fallback
            now = datetime.now()
            expired = [
                k for k, v in self._cache.items()
                if self._ttl_seconds and (now - v.timestamp).total_seconds() > self._ttl_seconds
            ]
            if expired:
                oldest_key = expired[0]
            else:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].last_access)
        else:  # COUNT policy
            # Just remove the first entry
            oldest_key = next(iter(self._cache))
        
        # Remove entry
        entry = self._cache.pop(oldest_key, None)
        if entry:
            self._symbol_indices[entry.data.symbol].discard(oldest_key)
            if not self._symbol_indices[entry.data.symbol]:
                del self._symbol_indices[entry.data.symbol]
    
    def put(self, data: MarketData) -> bool:
        """Add data to cache"""
        # Check for duplicates
        if self._is_duplicate(data):
            self._dedup_hits += 1
            return False
        
        with self._lock:
            # Evict if needed
            if self._should_evict():
                self._evict_entry()
            
            # Create key and entry
            key = self._generate_key(data)
            entry = CacheEntry(data=data, timestamp=datetime.now())
            
            # Store entry
            self._cache[key] = entry
            self._symbol_indices[data.symbol].add(key)
            self._inserts += 1
        
        logger.debug(f"Cached {data.symbol.ticker} at {data.timestamp}")
        return True
    
    def get(self, symbol: Symbol, since: Optional[datetime] = None) -> List[MarketData]:
        """Get cached data for symbol"""
        with self._lock:
            if symbol not in self._symbol_indices:
                self._misses += 1
                return []
            
            results = []
            keys = list(self._symbol_indices[symbol])
            
            for key in keys:
                entry = self._cache.get(key)
                if not entry:
                    continue
                
                # Check TTL
                if self._ttl_seconds:
                    age = (datetime.now() - entry.timestamp).total_seconds()
                    if age > self._ttl_seconds:
                        continue
                
                # Filter by time
                if since and entry.data.timestamp < since:
                    continue
                
                # Update access stats
                entry.touch()
                results.append(entry.data)
                self._hits += 1
            
            # Sort by timestamp
            results.sort(key=lambda x: x.timestamp)
            
            if not results:
                self._misses += 1
            
            return results
    
    def get_latest(self, symbol: Symbol) -> Optional[MarketData]:
        """Get most recent data for symbol"""
        with self._lock:
            if symbol not in self._symbol_indices:
                self._misses += 1
                return None
            
            latest_entry = None
            latest_time = None
            
            for key in self._symbol_indices[symbol]:
                entry = self._cache.get(key)
                if not entry:
                    continue
                
                # Check TTL
                if self._ttl_seconds:
                    age = (datetime.now() - entry.timestamp).total_seconds()
                    if age > self._ttl_seconds:
                        continue
                
                if latest_time is None or entry.data.timestamp > latest_time:
                    latest_time = entry.data.timestamp
                    latest_entry = entry
            
            if latest_entry:
                latest_entry.touch()
                self._hits += 1
                return latest_entry.data
            else:
                self._misses += 1
                return None
    
    def clear(self) -> None:
        """Clear all cached data"""
        with self._lock:
            self._cache.clear()
            self._symbol_indices.clear()
            self._seen_hashes.clear()
        
        logger.info("Stream cache cleared")
    
    def clear_symbol(self, symbol: Symbol) -> int:
        """Clear cached data for specific symbol"""
        with self._lock:
            if symbol not in self._symbol_indices:
                return 0
            
            removed = 0
            keys = list(self._symbol_indices[symbol])
            for key in keys:
                if key in self._cache:
                    del self._cache[key]
                    removed += 1
            
            del self._symbol_indices[symbol]
            
            logger.info(f"Cleared {removed} entries for {symbol.ticker}")
            return removed
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'size': self.size,
            'max_size': self._max_size,
            'hit_rate': self.hit_rate,
            'dedup_rate': self.dedup_rate,
            'hits': self._hits,
            'misses': self._misses,
            'dedup_hits': self._dedup_hits,
            'inserts': self._inserts,
            'dropped': self._dropped_count,
            'policy': self._policy.name,
            'ttl_seconds': self._ttl_seconds,
            'symbol_count': len(self._symbol_indices)
        }
    
    # Async streaming interface with backpressure
    
    async def put_async(self, data: MarketData) -> bool:
        """Add data to async queue with backpressure handling"""
        # Check deduplication first
        if self._is_duplicate(data):
            self._dedup_hits += 1
            return False
        
        # Wait for pause to clear (backpressure)
        await self._pause_event.wait()
        
        try:
            # Try to put without blocking
            self._async_queue.put_nowait(data)
            self._queue_size += 1
            self._inserts += 1
            
            # Check backpressure
            self._check_backpressure()
            
            return True
            
        except asyncio.QueueFull:
            # Handle queue full based on policy
            await self._handle_queue_full(data)
            return False
    
    async def get_async(self) -> MarketData:
        """Get data from async queue"""
        data = await self._async_queue.get()
        self._queue_size -= 1
        
        # Check if we can resume (low watermark)
        if self._throttling and self._queue_size < self._backpressure.low_watermark * self._backpressure.max_queue_size:
            self._resume_streaming()
        
        return data
    
    def _check_backpressure(self) -> None:
        """Check and handle backpressure conditions"""
        queue_ratio = self._queue_size / self._backpressure.max_queue_size
        
        if queue_ratio >= self._backpressure.high_watermark:
            if not self._throttling:
                self._throttle_streaming()
        
        if queue_ratio >= 0.95:  # Critical level
            self._drop_entries()
    
    def _throttle_streaming(self) -> None:
        """Pause incoming data to reduce pressure"""
        self._throttling = True
        self._pause_event.clear()
        logger.warning(f"Backpressure: throttling enabled (queue: {self._queue_size})")
    
    def _resume_streaming(self) -> None:
        """Resume normal streaming"""
        self._throttling = False
        self._pause_event.set()
        logger.info(f"Backpressure: throttling disabled (queue: {self._queue_size})")
    
    async def _handle_queue_full(self, data: MarketData) -> None:
        """Handle queue full condition based on drop policy"""
        self._dropped_count += 1
        
        if self._backpressure.drop_policy == "newest":
            # Drop the new data
            logger.debug(f"Dropped newest data for {data.symbol.ticker}")
            
        elif self._backpressure.drop_policy == "oldest":
            # Remove oldest and add new
            try:
                self._async_queue.get_nowait()
                self._async_queue.put_nowait(data)
                logger.debug(f"Replaced oldest with new data for {data.symbol.ticker}")
            except asyncio.QueueEmpty:
                pass
                
        elif self._backpressure.drop_policy == "random":
            # Random drop (simplified - just drop new for now)
            logger.debug(f"Dropped data for {data.symbol.ticker} (random policy)")
    
    def _drop_entries(self) -> None:
        """Drop multiple entries to relieve pressure"""
        drop_count = int(self._queue_size * 0.2)  # Drop 20%
        
        for _ in range(drop_count):
            try:
                self._async_queue.get_nowait()
                self._queue_size -= 1
                self._dropped_count += 1
            except asyncio.QueueEmpty:
                break
        
        logger.warning(f"Dropped {drop_count} entries due to critical backpressure")
    
    async def start(self) -> None:
        """Start cache maintenance tasks"""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Stream cache started")
    
    async def stop(self) -> None:
        """Stop cache maintenance tasks"""
        self._running = False
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Resume any paused streaming
        self._resume_streaming()
        
        logger.info("Stream cache stopped")
    
    async def _cleanup_loop(self) -> None:
        """Periodic cleanup task"""
        while self._running:
            try:
                # Cleanup expired entries
                if self._ttl_seconds:
                    self._cleanup_expired()
                
                # Trim dedup window
                if len(self._seen_hashes) > 5000:
                    # Remove oldest half
                    for _ in range(len(self._seen_hashes) // 2):
                        self._seen_hashes.popleft()
                
                await asyncio.sleep(30)  # Cleanup every 30 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
    
    def _cleanup_expired(self) -> int:
        """Remove expired entries based on TTL"""
        if not self._ttl_seconds:
            return 0
        
        now = datetime.now()
        expired_keys = []
        
        with self._lock:
            for key, entry in self._cache.items():
                age = (now - entry.timestamp).total_seconds()
                if age > self._ttl_seconds:
                    expired_keys.append(key)
            
            # Remove expired
            for key in expired_keys:
                entry = self._cache.pop(key, None)
                if entry:
                    self._symbol_indices[entry.data.symbol].discard(key)
        
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired entries")
        
        return len(expired_keys)


class SymbolCache:
    """Dedicated cache for a single symbol with specialized operations"""
    
    def __init__(self, symbol: Symbol, max_size: int = 1000):
        self._symbol = symbol
        self._max_size = max_size
        self._ticks: deque = deque(maxlen=max_size)
        self._bars: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_size))
        self._lock = Lock()
        
        # Statistics
        self._tick_count = 0
        self._bar_count = 0
    
    @property
    def symbol(self) -> Symbol:
        return self._symbol
    
    def add_tick(self, tick: Tick) -> None:
        """Add tick to symbol cache"""
        with self._lock:
            self._ticks.append(tick)
            self._tick_count += 1
    
    def add_bar(self, bar: Bar) -> None:
        """Add bar to symbol cache"""
        with self._lock:
            resolution_key = bar.resolution.value
            self._bars[resolution_key].append(bar)
            self._bar_count += 1
    
    def get_ticks(self, count: Optional[int] = None) -> List[Tick]:
        """Get recent ticks"""
        with self._lock:
            if count is None:
                return list(self._ticks)
            return list(self._ticks)[-count:]
    
    def get_bars(self, resolution: Any, count: Optional[int] = None) -> List[Bar]:
        """Get recent bars for resolution"""
        with self._lock:
            bars = self._bars.get(resolution.value, deque())
            if count is None:
                return list(bars)
            return list(bars)[-count:]
    
    def get_latest_tick(self) -> Optional[Tick]:
        """Get latest tick"""
        with self._lock:
            if self._ticks:
                return self._ticks[-1]
            return None
    
    def get_latest_bar(self, resolution: Any) -> Optional[Bar]:
        """Get latest bar for resolution"""
        with self._lock:
            bars = self._bars.get(resolution.value)
            if bars:
                return bars[-1]
            return None
    
    def clear(self) -> None:
        """Clear all cached data for this symbol"""
        with self._lock:
            self._ticks.clear()
            self._bars.clear()
            self._tick_count = 0
            self._bar_count = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get symbol cache statistics"""
        with self._lock:
            return {
                'symbol': self._symbol.ticker,
                'tick_count': len(self._ticks),
                'total_ticks_added': self._tick_count,
                'bar_resolutions': list(self._bars.keys()),
                'total_bars_added': self._bar_count
            }


# Factory function
def create_stream_cache(
    max_size: int = 100000,
    policy: str = "LRU",
    ttl_seconds: Optional[float] = None,
    enable_dedup: bool = True,
    max_queue_size: int = 10000
) -> StreamCache:
    """Create a configured stream cache"""
    policy_enum = CachePolicy[policy.upper()]
    backpressure = BackPressureConfig(max_queue_size=max_queue_size)
    
    return StreamCache(
        max_size=max_size,
        policy=policy_enum,
        ttl_seconds=ttl_seconds,
        enable_dedup=enable_dedup,
        backpressure=backpressure
    )
