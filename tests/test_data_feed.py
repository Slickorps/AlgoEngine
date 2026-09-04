"""Tests for data feed base classes, streaming, caching, and aggregation."""

import asyncio

import pytest
from datetime import datetime
from decimal import Decimal

from src.data.feed import DataFeed, StreamingDataFeed, DataCache, DataAggregator
from src.data.models import Symbol, Tick, Bar, Resolution, DataType
from src.engine.events import get_event_bus, EventType


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL", security_type="EQUITY")


def make_tick(symbol, dt, price="100"):
    return Tick(
        symbol=symbol,
        timestamp=dt,
        bid_price=Decimal(price) - Decimal("1"),
        ask_price=Decimal(price) + Decimal("1"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )


class MockFeed(DataFeed):
    """Concrete DataFeed for testing base class behavior."""

    def __init__(self):
        super().__init__("MockFeed")

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    def is_connected(self):
        return True

    async def subscribe(self, symbols):
        pass

    async def unsubscribe(self, symbols):
        pass

    def get_history(self, symbol, start, end, resolution=Resolution.DAILY):
        return None


class MockStreamingFeed(StreamingDataFeed):
    """Concrete StreamingDataFeed for testing."""

    def __init__(self):
        super().__init__("MockStreaming")

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    def is_connected(self):
        return True

    async def subscribe(self, symbols):
        pass

    async def unsubscribe(self, symbols):
        pass

    def get_history(self, symbol, start, end, resolution=Resolution.DAILY):
        return None

    async def _stream_loop(self):
        while self._running:
            await asyncio.sleep(0.01)


class TestDataFeed:
    def test_name(self):
        feed = MockFeed()
        assert feed.name == "MockFeed"

    def test_on_data_specific_type(self, symbol):
        feed = MockFeed()
        received = []
        feed.on_data(lambda d: received.append(d), DataType.TICK)

        feed._emit_data(make_tick(symbol, datetime.now()))
        feed._emit_data(Bar(
            symbol=symbol, timestamp=datetime.now(), open=Decimal("1"),
            high=Decimal("2"), low=Decimal("1"), close=Decimal("2"),
        ))

        assert len(received) == 1
        assert isinstance(received[0], Tick)

    def test_on_data_all_types(self, symbol):
        feed = MockFeed()
        received = []
        feed.on_data(lambda d: received.append(d))

        feed._emit_data(make_tick(symbol, datetime.now()))
        feed._emit_data(Bar(
            symbol=symbol, timestamp=datetime.now(), open=Decimal("1"),
            high=Decimal("2"), low=Decimal("1"), close=Decimal("2"),
        ))

        assert len(received) == 2

    def test_emit_data_callback_error_handled(self, symbol):
        feed = MockFeed()
        feed.on_data(lambda d: 1 / 0, DataType.TICK)
        feed._emit_data(make_tick(symbol, datetime.now()))  # must not raise

    def test_emit_data_to_event_bus(self, symbol):
        feed = MockFeed()
        bus = get_event_bus()
        received = []
        bus.subscribe(EventType.TICK, lambda e: received.append(e))

        feed._emit_data(make_tick(symbol, datetime.now()))

        assert len(received) == 1
        assert received[0].event_type == EventType.TICK
        assert isinstance(received[0].data, Tick)

    def test_map_data_type_to_event(self):
        feed = MockFeed()
        assert feed._map_data_type_to_event(DataType.TICK) == EventType.TICK
        assert feed._map_data_type_to_event(DataType.BAR) == EventType.BAR


class TestStreamingDataFeed:
    async def test_start_and_stop_streaming(self):
        feed = MockStreamingFeed()
        await feed.start_streaming()
        assert feed._running is True
        assert feed._stream_task is not None
        assert feed._buffer_task is not None
        await feed.stop_streaming()
        assert feed._running is False

    async def test_buffer_loop_processes_data(self, symbol):
        feed = MockStreamingFeed()
        received = []
        feed.on_data(lambda d: received.append(d), DataType.TICK)

        await feed.start_streaming()
        feed._buffer_data(make_tick(symbol, datetime.now()))
        await asyncio.sleep(0.1)
        await feed.stop_streaming()

        assert len(received) == 1
        assert isinstance(received[0], Tick)

    async def test_buffer_data_full_does_not_raise(self, symbol):
        feed = MockStreamingFeed()
        feed._buffer = asyncio.Queue(maxsize=1)
        feed._buffer_data(make_tick(symbol, datetime.now()))
        feed._buffer_data(make_tick(symbol, datetime.now()))  # full - no raise


class TestDataCacheEviction:
    async def test_eviction_when_full(self):
        cache = DataCache(max_size=2, ttl_seconds=60)
        s1, s2, s3 = Symbol("A"), Symbol("B"), Symbol("C")

        await cache.set(s1, DataType.TICK, "d1")
        await cache.set(s2, DataType.TICK, "d2")
        await cache.set(s3, DataType.TICK, "d3")

        assert await cache.get(s1, DataType.TICK) is None
        assert await cache.get(s3, DataType.TICK) == "d3"
        assert cache.get_stats()["size"] == 2

    async def test_no_eviction_when_updating_existing(self):
        cache = DataCache(max_size=2, ttl_seconds=60)
        s1, s2 = Symbol("A"), Symbol("B")

        await cache.set(s1, DataType.TICK, "d1")
        await cache.set(s2, DataType.TICK, "d2")
        await cache.set(s1, DataType.TICK, "d1-updated")

        assert await cache.get(s1, DataType.TICK) == "d1-updated"
        assert await cache.get(s2, DataType.TICK) == "d2"


class TestDataAggregator:
    def test_add_tick_same_period_returns_none(self):
        agg = DataAggregator(Resolution.MINUTE)
        symbol = Symbol("AAPL")
        base = datetime(2023, 1, 1, 9, 30, 0)

        assert agg.add_tick(make_tick(symbol, base)) is None
        assert agg.add_tick(make_tick(symbol, base.replace(second=15))) is None

    def test_add_tick_next_period_returns_bar(self):
        agg = DataAggregator(Resolution.MINUTE)
        symbol = Symbol("AAPL")
        base = datetime(2023, 1, 1, 9, 30, 0)

        agg.add_tick(make_tick(symbol, base, price="100"))
        agg.add_tick(make_tick(symbol, base.replace(second=30), price="104"))
        bar = agg.add_tick(make_tick(symbol, base.replace(minute=31), price="106"))

        assert bar is not None
        assert bar.timestamp == base.replace(second=0, microsecond=0)
        assert bar.open == Decimal("100")
        assert bar.high == Decimal("104")
        assert bar.low == Decimal("100")
        assert bar.close == Decimal("104")
        assert bar.resolution == Resolution.MINUTE

    def test_flush_returns_pending_bar(self):
        agg = DataAggregator(Resolution.MINUTE)
        symbol = Symbol("AAPL")
        base = datetime(2023, 1, 1, 9, 30, 0)

        agg.add_tick(make_tick(symbol, base, price="100"))
        bars = agg.flush()

        assert len(bars) == 1
        assert bars[0].open == Decimal("100")

    def test_flush_specific_symbol(self):
        agg = DataAggregator(Resolution.MINUTE)
        aapl = Symbol("AAPL")
        tsla = Symbol("TSLA")
        base = datetime(2023, 1, 1, 9, 30, 0)

        agg.add_tick(make_tick(aapl, base))
        agg.add_tick(make_tick(tsla, base))

        bars = agg.flush(aapl)
        assert len(bars) == 1
        assert bars[0].symbol == aapl

        # Remaining symbol still buffered
        remaining = agg.flush()
        assert len(remaining) == 1
        assert remaining[0].symbol == tsla

    def test_period_start(self):
        agg = DataAggregator(Resolution.MINUTE)
        dt = datetime(2023, 1, 1, 9, 30, 45, 500)
        assert agg._get_period_start(dt) == datetime(2023, 1, 1, 9, 30, 0)

        agg_hour = DataAggregator(Resolution.HOUR)
        assert agg_hour._get_period_start(dt) == datetime(2023, 1, 1, 9, 0, 0)

        agg_daily = DataAggregator(Resolution.DAILY)
        assert agg_daily._get_period_start(dt) == datetime(2023, 1, 1, 0, 0, 0)

    def test_flush_empty(self):
        agg = DataAggregator(Resolution.MINUTE)
        assert agg.flush() == []

    def test_create_bar_uses_ticks(self):
        agg = DataAggregator(Resolution.MINUTE)
        symbol = Symbol("AAPL")
        base = datetime(2023, 1, 1, 9, 30, 0)

        tick1 = make_tick(symbol, base, price="100")
        tick2 = make_tick(symbol, base.replace(second=10), price="108")
        agg._buffers[symbol] = [tick1, tick2]

        bar = agg._create_bar(symbol, base, [tick1, tick2])
        assert bar.high == Decimal("108")
        assert bar.volume == Decimal("400")
