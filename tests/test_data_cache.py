"""Tests for data cache"""

import pytest
import asyncio

from src.data.feed import DataCache
from src.data.models import Symbol, DataType, Resolution


class TestDataCache:
    """Test DataCache class"""
    
    @pytest.fixture
    def cache(self):
        """Create cache instance"""
        return DataCache(max_size=100, ttl_seconds=60)
    
    @pytest.fixture
    def sample_symbol(self):
        """Create sample symbol"""
        return Symbol(ticker="AAPL", security_type="EQUITY")
    
    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, cache, sample_symbol):
        """Test setting and getting cache values"""
        data = [1, 2, 3, 4, 5]
        
        await cache.set(sample_symbol, DataType.BAR, data, Resolution.DAILY)
        retrieved = await cache.get(sample_symbol, DataType.BAR, Resolution.DAILY)
        
        assert retrieved == data
    
    @pytest.mark.asyncio
    async def test_cache_miss(self, cache, sample_symbol):
        """Test cache miss"""
        retrieved = await cache.get(sample_symbol, DataType.BAR, Resolution.DAILY)
        assert retrieved is None
    
    @pytest.mark.asyncio
    async def test_cache_expiration(self):
        """Test cache entry expiration"""
        cache = DataCache(max_size=100, ttl_seconds=1)  # 1 second TTL
        symbol = Symbol(ticker="AAPL")
        
        await cache.set(symbol, DataType.BAR, [1, 2, 3], Resolution.DAILY)
        
        # Wait for expiration
        await asyncio.sleep(1.1)
        
        # Should be expired
        retrieved = await cache.get(symbol, DataType.BAR, Resolution.DAILY)
        assert retrieved is None
    
    @pytest.mark.asyncio
    async def test_cache_clear(self, cache, sample_symbol):
        """Test clearing cache"""
        await cache.set(sample_symbol, DataType.BAR, [1, 2, 3], Resolution.DAILY)
        await cache.clear()
        
        retrieved = await cache.get(sample_symbol, DataType.BAR, Resolution.DAILY)
        assert retrieved is None
    
    @pytest.mark.asyncio
    async def test_cache_clear_symbol(self, cache, sample_symbol):
        """Test clearing specific symbol"""
        symbol2 = Symbol(ticker="MSFT")
        
        await cache.set(sample_symbol, DataType.BAR, [1, 2, 3], Resolution.DAILY)
        await cache.set(symbol2, DataType.BAR, [4, 5, 6], Resolution.DAILY)
        
        await cache.clear(sample_symbol)
        
        assert await cache.get(sample_symbol, DataType.BAR, Resolution.DAILY) is None
        assert await cache.get(symbol2, DataType.BAR, Resolution.DAILY) == [4, 5, 6]
    
    def test_cache_stats(self, cache):
        """Test cache statistics"""
        stats = cache.get_stats()
        
        assert stats['size'] == 0
        assert stats['max_size'] == 100
        assert stats['ttl_seconds'] == 60
