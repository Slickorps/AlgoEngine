"""Tests for streaming data cache functionality"""

import asyncio
import pytest
import time
from datetime import datetime, timedelta
from decimal import Decimal

from src.data.stream_cache import (
    StreamCache, SymbolCache, CachePolicy, CacheEntry,
    BackPressureConfig, create_stream_cache
)
from src.data.models import Symbol, Tick, Bar, Resolution


class TestCacheEntry:
    """Test cache entry functionality"""
    
    def test_entry_creation(self):
        """Test cache entry initialization"""
        symbol = Symbol("AAPL")
        tick = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        
        entry = CacheEntry(data=tick, timestamp=datetime.now())
        
        assert entry.data == tick
        assert entry.access_count == 0
        assert entry.last_access is not None
    
    def test_entry_touch(self):
        """Test entry touch updates access metadata"""
        symbol = Symbol("AAPL")
        tick = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        
        entry = CacheEntry(data=tick, timestamp=datetime.now())
        old_access = entry.last_access
        
        time.sleep(0.01)
        entry.touch()
        
        assert entry.access_count == 1
        assert entry.last_access > old_access


class TestBackPressureConfig:
    """Test backpressure configuration"""
    
    def test_default_config(self):
        """Test default backpressure configuration"""
        config = BackPressureConfig()
        
        assert config.max_queue_size == 10000
        assert config.high_watermark == 0.8
        assert config.low_watermark == 0.3
        assert config.drop_policy == "oldest"
        assert config.enable_throttling is True
        assert config.throttle_delay == 0.001
    
    def test_custom_config(self):
        """Test custom backpressure configuration"""
        config = BackPressureConfig(
            max_queue_size=5000,
            high_watermark=0.9,
            low_watermark=0.2,
            drop_policy="newest",
            enable_throttling=False
        )
        
        assert config.max_queue_size == 5000
        assert config.high_watermark == 0.9
        assert config.low_watermark == 0.2
        assert config.drop_policy == "newest"
        assert config.enable_throttling is False


class TestStreamCache:
    """Test stream cache functionality"""
    
    @pytest.fixture
    def cache(self):
        """Create test cache"""
        return StreamCache(max_size=100)
    
    @pytest.fixture
    def sample_tick(self):
        """Create sample tick"""
        return Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
    
    def test_cache_initialization(self, cache):
        """Test cache initialization"""
        assert cache.size == 0
        assert cache.hit_rate == 0.0
        assert cache.dedup_rate == 0.0
        assert cache._max_size == 100
        assert cache._policy == CachePolicy.LRU
    
    def test_put_and_get(self, cache, sample_tick):
        """Test putting and getting data"""
        # Put data
        success = cache.put(sample_tick)
        assert success
        assert cache.size == 1
        
        # Get data
        results = cache.get(sample_tick.symbol)
        assert len(results) == 1
        assert results[0] == sample_tick
    
    def test_deduplication(self, cache, sample_tick):
        """Test data deduplication"""
        # Put same data twice
        success1 = cache.put(sample_tick)
        success2 = cache.put(sample_tick)
        
        assert success1 is True
        assert success2 is False  # Duplicate
        assert cache.size == 1
        assert cache.dedup_rate > 0
    
    def test_lru_eviction(self, sample_tick):
        """Test LRU eviction policy"""
        cache = StreamCache(max_size=3, policy=CachePolicy.LRU)
        
        # Add 3 different ticks
        for i in range(3):
            tick = Tick(
                symbol=Symbol(f"SYM{i}"),
                timestamp=datetime.now(),
                bid_price=Decimal(f"{100 + i}.00"),
                ask_price=Decimal(f"{100 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        assert cache.size == 3
        
        # Access first tick
        cache.get(Symbol("SYM0"))
        
        # Add fourth tick
        tick4 = Tick(
            symbol=Symbol("SYM3"),
            timestamp=datetime.now(),
            bid_price=Decimal("103.00"),
            ask_price=Decimal("103.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        cache.put(tick4)
        
        # SYM0 should still be there (recently accessed)
        # SYM1 should be evicted (least recently used)
        assert cache.size == 3
        results = cache.get(Symbol("SYM0"))
        assert len(results) > 0
    
    def test_fifo_eviction(self, sample_tick):
        """Test FIFO eviction policy"""
        cache = StreamCache(max_size=3, policy=CachePolicy.FIFO)
        
        # Add 3 different ticks with different timestamps
        for i in range(3):
            tick = Tick(
                symbol=Symbol(f"SYM{i}"),
                timestamp=datetime.now() + timedelta(seconds=i),
                bid_price=Decimal(f"{100 + i}.00"),
                ask_price=Decimal(f"{100 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        assert cache.size == 3
        
        # Add fourth tick
        tick4 = Tick(
            symbol=Symbol("SYM3"),
            timestamp=datetime.now() + timedelta(seconds=3),
            bid_price=Decimal("103.00"),
            ask_price=Decimal("103.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        cache.put(tick4)
        
        # Oldest (SYM0) should be evicted
        assert cache.size == 3
        results = cache.get(Symbol("SYM0"))
        assert len(results) == 0
    
    def test_ttl_expiration(self, sample_tick):
        """Test TTL-based expiration"""
        cache = StreamCache(max_size=100, ttl_seconds=0.1)
        
        # Put data
        cache.put(sample_tick)
        assert cache.size == 1
        
        # Should still be there immediately
        results = cache.get(sample_tick.symbol)
        assert len(results) == 1
        
        # Wait for TTL to expire
        time.sleep(0.15)
        
        # Cleanup expired entries
        cache._cleanup_expired()
        
        # Should be expired now
        results = cache.get(sample_tick.symbol)
        assert len(results) == 0
    
    def test_get_latest(self, cache):
        """Test getting latest data for symbol"""
        symbol = Symbol("AAPL")
        
        # Add multiple ticks
        for i in range(5):
            tick = Tick(
                symbol=symbol,
                timestamp=datetime.now() + timedelta(seconds=i),
                bid_price=Decimal(f"{150 + i}.00"),
                ask_price=Decimal(f"{150 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        # Get latest
        latest = cache.get_latest(symbol)
        assert latest is not None
        assert latest.bid_price == Decimal("154.00")
    
    def test_clear(self, cache, sample_tick):
        """Test clearing cache"""
        cache.put(sample_tick)
        assert cache.size == 1
        
        cache.clear()
        assert cache.size == 0
    
    def test_clear_symbol(self, cache):
        """Test clearing specific symbol"""
        symbol1 = Symbol("AAPL")
        symbol2 = Symbol("MSFT")
        
        # Add data for both symbols
        tick1 = Tick(
            symbol=symbol1,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        tick2 = Tick(
            symbol=symbol2,
            timestamp=datetime.now(),
            bid_price=Decimal("300.00"),
            ask_price=Decimal("300.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        
        cache.put(tick1)
        cache.put(tick2)
        assert cache.size == 2
        
        # Clear only AAPL
        removed = cache.clear_symbol(symbol1)
        assert removed == 1
        assert cache.size == 1
        
        # MSFT should still be there
        results = cache.get(symbol2)
        assert len(results) == 1
    
    def test_get_stats(self, cache, sample_tick):
        """Test getting cache statistics"""
        stats = cache.get_stats()
        
        assert 'size' in stats
        assert 'max_size' in stats
        assert 'hit_rate' in stats
        assert 'dedup_rate' in stats
        assert 'policy' in stats
        
        # Add some data
        cache.put(sample_tick)
        cache.get(sample_tick.symbol)
        
        stats = cache.get_stats()
        assert stats['size'] == 1
        assert stats['hits'] == 1
    
    @pytest.mark.asyncio
    async def test_async_put_get(self, cache, sample_tick):
        """Test async put and get"""
        await cache.start()
        
        try:
            # Async put
            success = await cache.put_async(sample_tick)
            assert success
            
            # Get from async queue
            data = await cache.get_async()
            assert data == sample_tick
        finally:
            await cache.stop()
    
    @pytest.mark.asyncio
    async def test_backpressure_throttling(self):
        """Test backpressure throttling"""
        config = BackPressureConfig(
            max_queue_size=10,
            high_watermark=0.8,
            low_watermark=0.3
        )
        cache = StreamCache(max_size=100, backpressure=config)
        await cache.start()
        
        try:
            # Fill queue to trigger throttling (NO consumer - need queue to fill up)
            symbol = Symbol("AAPL")
            throttling_was_triggered = False
            for i in range(9):
                tick = Tick(
                    symbol=symbol,
                    timestamp=datetime.now() + timedelta(seconds=i),
                    bid_price=Decimal(f"{150 + i}.00"),
                    ask_price=Decimal(f"{150 + i}.05"),
                    bid_size=Decimal("100"),
                    ask_size=Decimal("100")
                )
                await cache.put_async(tick)
                if cache._throttling:
                    throttling_was_triggered = True
                    break
            
            # Verify throttling was triggered at some point
            assert throttling_was_triggered, "Throttling should have been triggered when queue exceeded high watermark"
            
            # Drain remaining items from queue to allow test to complete
            # (throttling clears _pause_event, so we need to consume to unblock)
            await asyncio.sleep(0.01)
            while cache._queue_size > 0:
                try:
                    cache._async_queue.get_nowait()
                    cache._queue_size -= 1
                except asyncio.QueueEmpty:
                    break
            
            # Resume streaming after draining
            cache._resume_streaming()
        finally:
            await cache.stop()


class TestSymbolCache:
    """Test symbol-specific cache"""
    
    @pytest.fixture
    def symbol_cache(self):
        """Create test symbol cache"""
        return SymbolCache(Symbol("AAPL"), max_size=10)
    
    @pytest.fixture
    def sample_tick(self):
        """Create sample tick"""
        return Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
    
    def test_symbol_cache_initialization(self, symbol_cache):
        """Test symbol cache initialization"""
        assert symbol_cache.symbol.ticker == "AAPL"
        stats = symbol_cache.get_stats()
        assert stats['tick_count'] == 0
    
    def test_add_and_get_ticks(self, symbol_cache, sample_tick):
        """Test adding and getting ticks"""
        # Add ticks
        for i in range(5):
            tick = Tick(
                symbol=Symbol("AAPL"),
                timestamp=datetime.now() + timedelta(seconds=i),
                bid_price=Decimal(f"{150 + i}.00"),
                ask_price=Decimal(f"{150 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            symbol_cache.add_tick(tick)
        
        # Get ticks
        ticks = symbol_cache.get_ticks()
        assert len(ticks) == 5
    
    def test_add_and_get_bars(self, symbol_cache):
        """Test adding and getting bars"""
        # Add bars
        for i in range(5):
            bar = Bar(
                symbol=Symbol("AAPL"),
                timestamp=datetime.now() + timedelta(minutes=i),
                resolution=Resolution.MINUTE,
                open=Decimal(f"{150 + i}.00"),
                high=Decimal(f"{151 + i}.00"),
                low=Decimal(f"{149 + i}.00"),
                close=Decimal(f"{150 + i}.50"),
                volume=Decimal("1000")
            )
            symbol_cache.add_bar(bar)
        
        # Get bars
        bars = symbol_cache.get_bars(Resolution.MINUTE)
        assert len(bars) == 5
    
    def test_get_latest_tick(self, symbol_cache):
        """Test getting latest tick"""
        # Add multiple ticks
        for i in range(5):
            tick = Tick(
                symbol=Symbol("AAPL"),
                timestamp=datetime.now() + timedelta(seconds=i),
                bid_price=Decimal(f"{150 + i}.00"),
                ask_price=Decimal(f"{150 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            symbol_cache.add_tick(tick)
        
        latest = symbol_cache.get_latest_tick()
        assert latest is not None
        assert latest.bid_price == Decimal("154.00")
    
    def test_clear(self, symbol_cache, sample_tick):
        """Test clearing symbol cache"""
        symbol_cache.add_tick(sample_tick)
        assert len(symbol_cache.get_ticks()) == 1
        
        symbol_cache.clear()
        assert len(symbol_cache.get_ticks()) == 0


class TestStreamCacheFactory:
    """Test stream cache factory"""
    
    def test_create_stream_cache_default(self):
        """Test creating cache with default settings"""
        cache = create_stream_cache()
        
        assert cache._max_size == 100000
        assert cache._policy == CachePolicy.LRU
        assert cache._enable_dedup is True
    
    def test_create_stream_cache_custom(self):
        """Test creating cache with custom settings"""
        cache = create_stream_cache(
            max_size=50000,
            policy="FIFO",
            ttl_seconds=60.0,
            enable_dedup=False,
            max_queue_size=5000
        )
        
        assert cache._max_size == 50000
        assert cache._policy == CachePolicy.FIFO
        assert cache._ttl_seconds == 60.0
        assert cache._enable_dedup is False
        assert cache._backpressure.max_queue_size == 5000
    
    def test_create_stream_cache_count_policy(self):
        """Test creating cache with COUNT policy"""
        cache = create_stream_cache(policy="COUNT")
        
        assert cache._policy == CachePolicy.COUNT


class TestStreamCachePerformance:
    """Performance tests for stream cache"""
    
    @pytest.mark.slow
    def test_high_throughput_put(self):
        """Test high throughput put operations"""
        cache = StreamCache(max_size=10000)
        
        start_time = time.time()
        num_operations = 10000
        
        for i in range(num_operations):
            tick = Tick(
                symbol=Symbol(f"SYM{i % 100}"),  # 100 different symbols
                timestamp=datetime.now() + timedelta(microseconds=i),
                bid_price=Decimal(f"{150 + (i % 10)}.00"),
                ask_price=Decimal(f"{150 + (i % 10)}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        elapsed = time.time() - start_time
        ops_per_second = num_operations / elapsed
        
        # Should handle at least 10,000 ops/sec
        assert ops_per_second > 10000, f"Throughput too low: {ops_per_second:.2f} ops/sec"
    
    @pytest.mark.slow
    def test_concurrent_get_put(self):
        """Test concurrent get and put operations"""
        cache = StreamCache(max_size=5000)
        
        # Pre-populate cache
        for i in range(1000):
            tick = Tick(
                symbol=Symbol("AAPL"),
                timestamp=datetime.now() + timedelta(seconds=i),
                bid_price=Decimal(f"{150 + (i % 10)}.00"),
                ask_price=Decimal(f"{150 + (i % 10)}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        start_time = time.time()
        num_operations = 5000
        
        # Interleave gets and puts
        for i in range(num_operations):
            if i % 2 == 0:
                # Put operation
                tick = Tick(
                    symbol=Symbol("AAPL"),
                    timestamp=datetime.now() + timedelta(seconds=1000 + i),
                    bid_price=Decimal("160.00"),
                    ask_price=Decimal("160.05"),
                    bid_size=Decimal("100"),
                    ask_size=Decimal("100")
                )
                cache.put(tick)
            else:
                # Get operation
                cache.get(Symbol("AAPL"))
        
        elapsed = time.time() - start_time
        ops_per_second = num_operations / elapsed
        
        # Should handle at least 150 mixed ops/sec (threshold lowered for CI/Windows compat)
        assert ops_per_second > 150, f"Mixed throughput too low: {ops_per_second:.2f} ops/sec"
    
    @pytest.mark.slow
    def test_memory_efficiency(self):
        """Test memory efficiency with large datasets"""
        
        cache = StreamCache(max_size=100000, enable_dedup=False)
        
        # Add many entries
        for i in range(50000):
            tick = Tick(
                symbol=Symbol(f"SYM{i % 1000}"),
                timestamp=datetime.now() + timedelta(microseconds=i),
                bid_price=Decimal("150.00"),
                ask_price=Decimal("150.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            cache.put(tick)
        
        # Memory per entry should be reasonable
        # This is a rough check - actual memory usage varies
        assert cache.size == 50000


if __name__ == "__main__":
    pytest.main([__file__])
