"""Tests for indicator registry, cache, and batch processor."""

from decimal import Decimal

import numpy as np
import pytest

from src.algorithms.indicators_registry import (
    IndicatorRegistry,
    IndicatorCategory,
    IndicatorCache,
    IndicatorBatchProcessor,
    register_indicator,
    flush_pending_registrations,
)
from src.algorithms.indicators import SMA, RSI


# ── Helpers ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    IndicatorRegistry.reset()
    yield
    IndicatorRegistry.reset()


@pytest.fixture
def price_data():
    prices = [Decimal(str(100.0 + i * 0.5 + np.sin(i * 0.3) * 2))
              for i in range(200)]
    return prices


@pytest.fixture
def price_array(price_data):
    return np.array([float(p) for p in price_data], dtype=np.float64)


# ── Registry Tests ──────────────────────────────────────────────────


class TestIndicatorRegistry:
    def test_singleton(self):
        r1 = IndicatorRegistry.instance()
        r2 = IndicatorRegistry.instance()
        assert r1 is r2

    def test_reset_creates_new_instance(self):
        r1 = IndicatorRegistry.instance()
        IndicatorRegistry.reset()
        r2 = IndicatorRegistry.instance()
        assert r1 is not r2

    def test_register_and_retrieve(self):
        registry = IndicatorRegistry.instance()

        def factory(**kw):
            return SMA(**kw)

        spec = registry.register(
            "my_sma", factory, IndicatorCategory.MOVING_AVERAGE,
            description="Test SMA", parameters={"period": 10},
            tags=["test"],
        )
        assert spec.name == "my_sma"
        assert spec.category == IndicatorCategory.MOVING_AVERAGE

        retrieved = registry.get("my_sma")
        assert retrieved is spec
        assert "test" in retrieved.tags

    def test_register_class(self):
        registry = IndicatorRegistry.instance()
        spec = registry.register_class(
            SMA, name="custom_sma", category=IndicatorCategory.MOVING_AVERAGE,
            period=42,
        )
        assert spec.name == "custom_sma"

        indicator = spec.create()
        assert indicator.period == 42

        indicator2 = spec.create(period=7)
        assert indicator2.period == 7

    def test_list_all(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="sma1")
        registry.register_class(RSI, name="rsi1")
        assert len(registry.list_all()) == 2

    def test_list_names(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="sma")
        assert "sma" in registry.list_names()

    def test_list_by_category(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="s", category=IndicatorCategory.MOVING_AVERAGE)
        registry.register_class(RSI, name="r", category=IndicatorCategory.OSCILLATOR)
        ma = registry.list_by_category(IndicatorCategory.MOVING_AVERAGE)
        osc = registry.list_by_category("oscillator")
        assert len(ma) == 1
        assert len(osc) == 1

    def test_list_by_tag(self):
        registry = IndicatorRegistry.instance()
        registry.register("tagged", lambda **kw: SMA(**kw), tags=["trend", "demo"])
        tagged = registry.list_by_tag("trend")
        assert len(tagged) == 1
        assert "demo" in tagged[0].tags

    def test_unregister(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="sma")
        assert registry.unregister("sma")
        assert registry.get("sma") is None
        assert not registry.unregister("nonexistent")

    def test_create_unknown(self):
        registry = IndicatorRegistry.instance()
        assert registry.create("ghost") is None

    def test_create_with_overrides(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="sma", period=5)
        ind = registry.create("sma", {"period": 30})
        assert ind is not None
        assert ind.period == 30

    def test_create_all(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="fast_sma", period=5)
        registry.register_class(RSI, name="slow_rsi", period=20)
        configs = {"fast_sma": {"period": 10}, "slow_rsi": {}}
        results = registry.create_all(configs)
        assert len(results) == 2
        assert results["fast_sma"].period == 10
        assert results["slow_rsi"].period == 20

    def test_discover_builtins(self):
        registry = IndicatorRegistry.instance()
        specs = registry.discover_builtins()
        assert len(specs) >= 4
        names = {s.name for s in specs}
        for builtin in ["sma", "ema", "rsi", "atr"]:
            assert builtin in names

    def test_summarize(self):
        registry = IndicatorRegistry.instance()
        registry.register_class(SMA, name="sma", category=IndicatorCategory.MOVING_AVERAGE)
        summary = registry.summarize()
        assert summary["total_indicators"] == 1
        assert "moving_average" in summary["categories"]


# ── Decorator Registration ──────────────────────────────────────────


class TestDecoratorRegistration:
    def test_register_indicator_decorator(self):
        @register_indicator("decorated_sma", category="moving_average")
        class DecoratedSMA(SMA):
            pass

        flush_pending_registrations()
        registry = IndicatorRegistry.instance()
        spec = registry.get("decorated_sma")
        assert spec is not None
        assert spec.name == "decorated_sma"
        assert spec.category == IndicatorCategory.MOVING_AVERAGE

    def test_flush_no_pending(self):
        count = flush_pending_registrations()
        assert count == 0

    def test_double_registration_prevented(self):
        @register_indicator("dup")
        class DupSMA(SMA):
            pass

        @register_indicator("dup")
        class DupSMA2(SMA):
            pass

        count = flush_pending_registrations()
        assert count == 1
        registry = IndicatorRegistry.instance()
        assert registry.get("dup") is not None


# ── Cache Tests ─────────────────────────────────────────────────────


class TestIndicatorCache:
    def test_put_and_get(self):
        cache = IndicatorCache(max_size=10)
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        cache.put("sma", {"period": 3}, data, np.array([2.0, 3.0, 4.0]))
        result = cache.get("sma", {"period": 3}, data)
        assert result is not None
        np.testing.assert_array_equal(result, [2.0, 3.0, 4.0])

    def test_different_params_new_key(self):
        cache = IndicatorCache(max_size=10)
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        cache.put("sma", {"period": 3}, data, np.array([2.0, 3.0, 4.0]))
        result = cache.get("sma", {"period": 5}, data)
        assert result is None

    def test_different_data_new_key(self):
        cache = IndicatorCache(max_size=10)
        cache.put("sma", {"period": 3}, [1.0, 2.0, 3.0], np.array([2.0]))
        result = cache.get("sma", {"period": 3}, [4.0, 5.0, 6.0])
        assert result is None

    def test_same_data_different_order_same_key(self):
        cache = IndicatorCache(max_size=10)
        data = [1.0, 2.0, 3.0]
        cache.put("sma", {"period": 2}, data, np.array([1.5]))
        result = cache.get("sma", {"period": 2}, [1.0, 2.0, 3.0])
        assert result is not None

    def test_lru_eviction(self):
        cache = IndicatorCache(max_size=3)
        for i in range(5):
            data = [float(i), float(i + 1)]
            cache.put("sma", {"period": i}, data, np.array([i]))
        assert cache.size == 3

    def test_invalidate_by_name(self):
        cache = IndicatorCache(max_size=10)
        cache.put("sma", {"period": 3}, [1.0, 2.0], np.array([1.5]))
        cache.put("rsi", {"period": 14}, [1.0, 2.0], np.array([50.0]))
        count = cache.invalidate("sma")
        assert count == 1
        assert cache.get("sma", {"period": 3}, [1.0, 2.0]) is None
        assert cache.get("rsi", {"period": 14}, [1.0, 2.0]) is not None

    def test_invalidate_all(self):
        cache = IndicatorCache(max_size=10)
        cache.put("sma", {"period": 3}, [1.0, 2.0], np.array([1.5]))
        cache.put("rsi", {"period": 14}, [1.0, 2.0], np.array([50.0]))
        count = cache.invalidate()
        assert count == 2
        assert cache.size == 0

    def test_clear(self):
        cache = IndicatorCache(max_size=10)
        cache.put("sma", {"period": 3}, [1.0, 2.0], np.array([1.5]))
        cache.clear()
        assert cache.size == 0


# ── Batch Processor Tests ───────────────────────────────────────────


class TestBatchProcessor:
    def test_compute_simple_indicator(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        processor = IndicatorBatchProcessor(registry=registry)
        result = processor.compute("sma", price_array, {"period": 10}, warmup=5)
        assert isinstance(result, np.ndarray)
        assert len(result) > 0
        assert not np.any(np.isnan(result))

    def test_compute_unknown_indicator(self, price_array):
        processor = IndicatorBatchProcessor()
        with pytest.raises(KeyError):
            processor.compute("ghost_indicator", price_array)

    def test_compute_with_warmup(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        processor = IndicatorBatchProcessor(registry=registry)
        no_warmup = processor.compute("sma", price_array, {"period": 10}, warmup=0)
        with_warmup = processor.compute("sma", price_array, {"period": 10}, warmup=20)
        assert len(with_warmup) < len(no_warmup)

    def test_compute_multiple(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        processor = IndicatorBatchProcessor(registry=registry)
        configs = {"sma": {"period": 10}, "rsi": {"period": 14}}
        results = processor.compute_multiple(configs, price_array, warmup=5)
        assert set(results.keys()) == {"sma", "rsi"}

    def test_compute_multiple_skips_unknown(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        processor = IndicatorBatchProcessor(registry=registry)
        configs = {"sma": {"period": 10}, "ghost": {}}
        results = processor.compute_multiple(configs, price_array)
        assert "sma" in results
        assert "ghost" not in results

    def test_compute_macd(self, price_array):
        processor = IndicatorBatchProcessor()
        results = processor.compute_macd(price_array, fast=12, slow=26, signal=9)
        assert "macd_line" in results
        assert "signal_line" in results
        assert "histogram" in results
        assert len(results["macd_line"]) > 0

    def test_compute_bb(self, price_array):
        processor = IndicatorBatchProcessor()
        results = processor.compute_bb(price_array, period=20, num_std=2.0)
        assert "middle" in results
        assert "upper" in results
        assert "lower" in results
        assert len(results["middle"]) > 0

    def test_warmup_indicator(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        processor = IndicatorBatchProcessor(registry=registry)
        ind = processor.warmup_indicator("sma", price_array[:30], {"period": 10})
        assert ind.is_ready
        assert ind.value is not None

    def test_with_cache(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        cache = IndicatorCache(max_size=50)
        processor = IndicatorBatchProcessor(registry=registry, cache=cache)

        r1 = processor.compute("sma", price_array, {"period": 10})
        r2 = processor.compute("sma", price_array, {"period": 10})

        np.testing.assert_array_equal(r1, r2)
        assert cache.size >= 1

    def test_cache_invalidation_between_runs(self, price_array):
        registry = IndicatorRegistry.instance()
        registry.discover_builtins()

        cache = IndicatorCache(max_size=50)
        processor = IndicatorBatchProcessor(registry=registry, cache=cache)

        processor.compute("sma", price_array[:50], {"period": 10})
        assert cache.size >= 1
        cache.invalidate("sma")
        assert cache.get("sma", {"period": 10}, price_array[:50].tolist()) is None
