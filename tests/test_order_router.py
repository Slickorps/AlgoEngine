"""Tests for order routing system"""

import pytest
from decimal import Decimal

from src.trading.models import Order, OrderSide, OrderType
from src.trading.execution_engine import BrokerAdapter
from src.trading.order_router import (
    OrderRouter,
    RoutingStrategy,
    RouterConfig,
    BrokerMetrics,
    RoutingRule,
    RoutingResult,
    _split_order,
    _copy_order_with_quantity,
)
from src.data.models import Symbol


# ---------------------------------------------------------------------------
# Mock broker adapter for testing
# ---------------------------------------------------------------------------

class MockBroker(BrokerAdapter):
    """Mock broker adapter for testing"""

    def __init__(self, name: str = "mock", connected: bool = True):
        self._connected = connected
        self._submitted: list = []
        self._name = name

    @property
    def mock_submitted(self) -> list:
        return self._submitted

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    async def submit_order(self, order: Order) -> bool:
        self._submitted.append(order)
        return True

    async def cancel_order(self, order_id: str) -> bool:
        return True

    def is_connected(self) -> bool:
        return self._connected


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL")


@pytest.fixture
def sample_order(symbol):
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("1000"),
        order_type=OrderType.MARKET,
    )


@pytest.fixture
def small_order(symbol):
    return Order(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        order_type=OrderType.MARKET,
    )


@pytest.fixture
def broker_a():
    return MockBroker(name="broker_a", connected=True)


@pytest.fixture
def broker_b():
    return MockBroker(name="broker_b", connected=True)


@pytest.fixture
def broker_c():
    return MockBroker(name="broker_c", connected=True)


@pytest.fixture
def disconnected_broker():
    return MockBroker(name="offline", connected=False)


@pytest.fixture
def router():
    return OrderRouter()


@pytest.fixture
def router_with_brokers(router, broker_a, broker_b):
    router.register_broker("broker_a", broker_a)
    router.register_broker("broker_b", broker_b)
    router.update_connectivity("broker_a", True)
    router.update_connectivity("broker_b", True)
    return router


# ---------------------------------------------------------------------------
# RoutingStrategy tests
# ---------------------------------------------------------------------------

class TestRoutingStrategy:
    """Test RoutingStrategy enum"""

    def test_all_strategies_exist(self):
        strategies = list(RoutingStrategy)
        assert RoutingStrategy.COST_BASED in strategies
        assert RoutingStrategy.LATENCY_BASED in strategies
        assert RoutingStrategy.FILL_RATE_BASED in strategies
        assert RoutingStrategy.SPLIT in strategies
        assert RoutingStrategy.FIXED in strategies
        assert RoutingStrategy.ROUND_ROBIN in strategies
        assert RoutingStrategy.VOLUME_WEIGHTED in strategies


# ---------------------------------------------------------------------------
# RouterConfig tests
# ---------------------------------------------------------------------------

class TestRouterConfig:
    """Test RouterConfig dataclass"""

    def test_default_values(self):
        config = RouterConfig()
        assert config.default_strategy == RoutingStrategy.COST_BASED
        assert config.fallback_strategy == RoutingStrategy.SPLIT
        assert config.min_split_quantity == Decimal("100")
        assert config.max_split_parts == 5
        assert config.broker_timeout == 5.0
        assert config.max_retries == 2

    def test_custom_values(self):
        config = RouterConfig(
            default_strategy=RoutingStrategy.LATENCY_BASED,
            min_split_quantity=Decimal("50"),
            max_split_parts=3,
        )
        assert config.default_strategy == RoutingStrategy.LATENCY_BASED
        assert config.min_split_quantity == Decimal("50")
        assert config.max_split_parts == 3


# ---------------------------------------------------------------------------
# BrokerMetrics tests
# ---------------------------------------------------------------------------

class TestBrokerMetrics:
    """Test BrokerMetrics dataclass"""

    def test_initial_state(self):
        metrics = BrokerMetrics(name="test_broker")
        assert metrics.name == "test_broker"
        assert metrics.total_orders == 0
        assert metrics.filled_orders == 0
        assert metrics.fill_rate == 0.0

    def test_record_submit(self):
        metrics = BrokerMetrics(name="test")
        metrics.record_submit(latency_ms=150.0)
        assert metrics.total_orders == 1
        assert metrics.avg_latency_ms == 150.0
        assert metrics.last_updated is not None

    def test_record_multiple_submits_avg_latency(self):
        metrics = BrokerMetrics(name="test")
        metrics.record_submit(latency_ms=100.0)
        metrics.record_submit(latency_ms=200.0)
        assert metrics.total_orders == 2
        assert metrics.avg_latency_ms == 150.0

    def test_record_fill_updates_rate(self):
        metrics = BrokerMetrics(name="test")
        metrics.record_submit(latency_ms=100.0)
        metrics.record_submit(latency_ms=100.0)
        metrics.record_fill(commission=Decimal("5"))
        assert metrics.filled_orders == 1
        assert metrics.fill_rate == 0.5
        assert metrics.avg_commission_per_order == Decimal("5")

    def test_record_rejection(self):
        metrics = BrokerMetrics(name="test")
        metrics.record_submit(latency_ms=100.0)
        metrics.record_rejection()
        assert metrics.rejected_orders == 1
        assert metrics.fill_rate == 0.0

    def test_to_dict(self):
        metrics = BrokerMetrics(name="test")
        metrics.record_submit(latency_ms=100.0)
        d = metrics.to_dict()
        assert d["name"] == "test"
        assert d["total_orders"] == 1
        assert d["avg_latency_ms"] == 100.0
        assert d["is_connected"] is False


# ---------------------------------------------------------------------------
# RoutingRule tests
# ---------------------------------------------------------------------------

class TestRoutingRule:
    """Test RoutingRule dataclass"""

    def test_matches_single_condition(self):
        rule = RoutingRule(
            conditions={"symbol": "AAPL"},
            strategy=RoutingStrategy.SPLIT,
        )
        assert rule.matches({"symbol": "AAPL"})
        assert not rule.matches({"symbol": "MSFT"})

    def test_matches_multiple_conditions(self):
        rule = RoutingRule(
            conditions={"symbol": "AAPL", "order_type": "MARKET"},
            strategy=RoutingStrategy.FIXED,
        )
        assert rule.matches({"symbol": "AAPL", "order_type": "MARKET"})
        assert not rule.matches({"symbol": "AAPL", "order_type": "LIMIT"})
        assert not rule.matches({"symbol": "MSFT", "order_type": "MARKET"})

    def test_matches_empty_context(self):
        rule = RoutingRule(
            conditions={"symbol": "AAPL"},
            strategy=RoutingStrategy.LATENCY_BASED,
        )
        assert not rule.matches({})

    def test_matches_missing_key(self):
        rule = RoutingRule(
            conditions={"volume": 1000},
            strategy=RoutingStrategy.COST_BASED,
        )
        assert not rule.matches({"symbol": "AAPL"})


# ---------------------------------------------------------------------------
# Order split tests
# ---------------------------------------------------------------------------

class TestOrderSplit:
    """Test _split_order and _copy_order_with_quantity"""

    def test_split_equal(self, sample_order):
        parts = _split_order(sample_order, num_parts=4, equal=True)
        assert len(parts) == 4
        expected = Decimal("250")
        for p in parts:
            assert p.quantity == expected
            assert p.tags["parent_order_id"] == sample_order.order_id
            assert "split_part" in p.tags

    def test_split_no_split_needed(self, sample_order):
        parts = _split_order(sample_order, num_parts=1)
        assert len(parts) == 1
        assert parts[0] is sample_order

    def test_split_zero_or_negative(self, sample_order):
        parts = _split_order(sample_order, num_parts=0)
        assert len(parts) == 1

    def test_split_with_weights(self, sample_order):
        weights = [0.5, 0.3, 0.2]
        parts = _split_order(sample_order, num_parts=3, equal=False, weights=weights)
        assert len(parts) == 3
        total_qty = sum((p.quantity for p in parts), Decimal("0"))
        assert abs(float(total_qty - sample_order.quantity)) < 0.01

    def test_split_weights_wrong_length(self, sample_order):
        with pytest.raises(ValueError, match="weights must be provided"):
            _split_order(sample_order, num_parts=3, equal=False, weights=[0.5])

    def test_copy_order_with_quantity(self, sample_order):
        copy = _copy_order_with_quantity(sample_order, Decimal("500"))
        assert copy.quantity == Decimal("500")
        assert copy.symbol == sample_order.symbol
        assert copy.side == sample_order.side
        assert copy.order_id != sample_order.order_id
        assert copy.tags == sample_order.tags


# ---------------------------------------------------------------------------
# RoutingResult tests
# ---------------------------------------------------------------------------

class TestRoutingResult:
    """Test RoutingResult dataclass"""

    def test_defaults(self, sample_order):
        result = RoutingResult(
            orders=[sample_order],
            broker_name="test_broker",
            strategy_used=RoutingStrategy.COST_BASED,
        )
        assert result.success is True
        assert result.error_message is None
        assert result.latency_ms == 0.0

    def test_failure_result(self, sample_order):
        result = RoutingResult(
            orders=[sample_order],
            broker_name="none",
            strategy_used=RoutingStrategy.FIXED,
            success=False,
            error_message="Broker unavailable",
        )
        assert result.success is False
        assert result.error_message == "Broker unavailable"


# ---------------------------------------------------------------------------
# OrderRouter broker management tests
# ---------------------------------------------------------------------------

class TestOrderRouterBrokerManagement:
    """Test broker registration and management"""

    def test_register_broker(self, router, broker_a):
        router.register_broker("broker_a", broker_a)
        assert "broker_a" in router.get_broker_names()
        assert router.get_broker("broker_a") is broker_a

    def test_unregister_broker(self, router, broker_a):
        router.register_broker("broker_a", broker_a)
        router.unregister_broker("broker_a")
        assert "broker_a" not in router.get_broker_names()
        assert router.get_broker("broker_a") is None

    def test_register_multiple_brokers(self, router, broker_a, broker_b, broker_c):
        router.register_broker("a", broker_a)
        router.register_broker("b", broker_b)
        router.register_broker("c", broker_c)
        assert len(router.get_broker_names()) == 3

    def test_register_with_initial_metrics(self, router, broker_a):
        metrics = BrokerMetrics(name="broker_a", total_orders=5, filled_orders=3)
        router.register_broker("broker_a", broker_a, initial_metrics=metrics)
        m = router.get_metrics("broker_a")
        assert m["broker_a"]["total_orders"] == 5


# ---------------------------------------------------------------------------
# OrderRouter routing rules tests
# ---------------------------------------------------------------------------

class TestOrderRouterRules:
    """Test routing rule management"""

    def test_add_rule(self, router):
        rule = RoutingRule(
            conditions={"symbol": "AAPL"},
            strategy=RoutingStrategy.SPLIT,
            priority=10,
            description="Split AAPL orders",
        )
        router.add_rule(rule)
        assert router._rules[0] is rule

    def test_remove_rule(self, router):
        rule = RoutingRule(
            conditions={}, strategy=RoutingStrategy.FIXED
        )
        router.add_rule(rule)
        router.remove_rule(rule)
        assert len(router._rules) == 0

    def test_rule_priority_ordering(self, router):
        low = RoutingRule(conditions={}, strategy=RoutingStrategy.COST_BASED, priority=1)
        high = RoutingRule(conditions={}, strategy=RoutingStrategy.FIXED, priority=10)
        mid = RoutingRule(conditions={}, strategy=RoutingStrategy.SPLIT, priority=5)
        router.add_rule(low)
        router.add_rule(high)
        router.add_rule(mid)
        # Should be sorted highest priority first
        assert router._rules[0].priority == 10
        assert router._rules[1].priority == 5
        assert router._rules[2].priority == 1


# ---------------------------------------------------------------------------
# OrderRouter metrics tests
# ---------------------------------------------------------------------------

class TestOrderRouterMetrics:
    """Test metrics management"""

    def test_update_metrics(self, router, broker_a):
        router.register_broker("broker_a", broker_a)
        new_metrics = BrokerMetrics(name="broker_a", total_orders=10)
        router.update_metrics("broker_a", new_metrics)
        assert router._metrics["broker_a"].total_orders == 10

    def test_update_metrics_unknown_broker(self, router):
        # Should not raise
        router.update_metrics("nonexistent", BrokerMetrics(name="x"))

    def test_update_connectivity(self, router, broker_a):
        router.register_broker("broker_a", broker_a)
        router.update_connectivity("broker_a", True)
        assert router._metrics["broker_a"].is_connected
        router.update_connectivity("broker_a", False)
        assert not router._metrics["broker_a"].is_connected

    def test_get_metrics_all(self, router, broker_a, broker_b):
        router.register_broker("a", broker_a)
        router.register_broker("b", broker_b)
        all_metrics = router.get_metrics()
        assert "a" in all_metrics
        assert "b" in all_metrics

    def test_get_metrics_specific(self, router, broker_a):
        router.register_broker("a", broker_a)
        metrics = router.get_metrics("a")
        assert "a" in metrics

    def test_get_metrics_nonexistent(self, router):
        assert router.get_metrics("nope") == {}


# ---------------------------------------------------------------------------
# OrderRouter routing logic tests (sync strategies)
# ---------------------------------------------------------------------------

class TestOrderRouterRouting:
    """Test routing strategies"""

    @pytest.mark.asyncio
    async def test_route_no_brokers_registered(self, router, sample_order):
        result = await router.route_order(sample_order)
        assert result.success is False
        assert "No brokers registered" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_route_no_connected_brokers(self, router, sample_order, disconnected_broker):
        router.register_broker("offline", disconnected_broker)
        router.update_connectivity("offline", False)
        result = await router.route_order(sample_order)
        assert result.success is False
        assert "No connected brokers" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_route_cost_based(self, router_with_brokers, sample_order, broker_a, broker_b):
        # broker_a has lower commission => should be selected
        router_with_brokers._metrics["broker_a"].avg_commission_per_order = Decimal("1")
        router_with_brokers._metrics["broker_b"].avg_commission_per_order = Decimal("5")

        result = await router_with_brokers.route_order(
            sample_order, strategy=RoutingStrategy.COST_BASED
        )
        assert result.success
        assert result.broker_name == "broker_a"
        assert result.strategy_used == RoutingStrategy.COST_BASED

    @pytest.mark.asyncio
    async def test_route_latency_based(self, router_with_brokers, sample_order):
        router_with_brokers._metrics["broker_a"].avg_latency_ms = 200.0
        router_with_brokers._metrics["broker_b"].avg_latency_ms = 50.0

        result = await router_with_brokers.route_order(
            sample_order, strategy=RoutingStrategy.LATENCY_BASED
        )
        assert result.success
        assert result.broker_name == "broker_b"

    @pytest.mark.asyncio
    async def test_route_fill_rate_based(self, router_with_brokers, sample_order):
        router_with_brokers._metrics["broker_a"].fill_rate = 0.95
        router_with_brokers._metrics["broker_b"].fill_rate = 0.60

        result = await router_with_brokers.route_order(
            sample_order, strategy=RoutingStrategy.FILL_RATE_BASED
        )
        assert result.success
        assert result.broker_name == "broker_a"

    @pytest.mark.asyncio
    async def test_route_fixed(self, router_with_brokers, sample_order):
        config = RouterConfig(fixed_broker_name="broker_b")
        r = OrderRouter(config=config)
        r.register_broker("broker_a", MockBroker("broker_a"))
        r.register_broker("broker_b", MockBroker("broker_b"))
        r.update_connectivity("broker_a", True)
        r.update_connectivity("broker_b", True)

        result = await r.route_order(sample_order, strategy=RoutingStrategy.FIXED)
        assert result.success
        assert result.broker_name == "broker_b"

    @pytest.mark.asyncio
    async def test_route_fixed_fallback(self, router_with_brokers, sample_order):
        config = RouterConfig(fixed_broker_name="nonexistent")
        r = OrderRouter(config=config)
        r.register_broker("broker_a", MockBroker("broker_a"))
        r.update_connectivity("broker_a", True)

        result = await r.route_order(sample_order, strategy=RoutingStrategy.FIXED)
        assert result.success
        assert result.broker_name == "broker_a"  # fallback to first available

    @pytest.mark.asyncio
    async def test_route_round_robin(self, router_with_brokers, sample_order):
        r = router_with_brokers
        # First call → broker_a
        result1 = await r.route_order(sample_order, strategy=RoutingStrategy.ROUND_ROBIN)
        assert result1.broker_name == "broker_a"
        # Second call → broker_b
        result2 = await r.route_order(sample_order, strategy=RoutingStrategy.ROUND_ROBIN)
        assert result2.broker_name == "broker_b"
        # Third call → back to broker_a
        result3 = await r.route_order(sample_order, strategy=RoutingStrategy.ROUND_ROBIN)
        assert result3.broker_name == "broker_a"

    @pytest.mark.asyncio
    async def test_route_split_single_broker(self, router, sample_order, broker_a):
        router.register_broker("a", broker_a)
        router.update_connectivity("a", True)
        result = await router.route_order(sample_order, strategy=RoutingStrategy.SPLIT)
        assert result.success
        assert result.broker_name == "a"
        assert len(result.orders) == 1

    @pytest.mark.asyncio
    async def test_route_split_multiple_brokers(self, router, sample_order, broker_a, broker_b):
        router.register_broker("a", broker_a)
        router.register_broker("b", broker_b)
        router.update_connectivity("a", True)
        router.update_connectivity("b", True)

        result = await router.route_order(sample_order, strategy=RoutingStrategy.SPLIT)
        assert result.success
        assert len(result.orders) == 2
        total_qty = sum((o.quantity for o in result.orders), Decimal("0"))
        assert abs(float(total_qty - sample_order.quantity)) < 0.01

    @pytest.mark.asyncio
    async def test_route_split_small_order_not_split(self, router, small_order, broker_a, broker_b):
        router.register_broker("a", broker_a)
        router.register_broker("b", broker_b)
        router.update_connectivity("a", True)
        router.update_connectivity("b", True)

        result = await router.route_order(small_order, strategy=RoutingStrategy.SPLIT)
        assert len(result.orders) == 1

    @pytest.mark.asyncio
    async def test_route_volume_weighted(self, router, sample_order, broker_a, broker_b):
        router.register_broker("a", broker_a)
        router.register_broker("b", broker_b)
        router.update_connectivity("a", True)
        router.update_connectivity("b", True)
        router._metrics["a"].filled_orders = 7
        router._metrics["b"].filled_orders = 3

        result = await router.route_order(sample_order, strategy=RoutingStrategy.VOLUME_WEIGHTED)
        assert result.success
        assert len(result.orders) == 2
        assert result.strategy_used == RoutingStrategy.VOLUME_WEIGHTED

    @pytest.mark.asyncio
    async def test_rule_overrides_strategy(self, router_with_brokers, sample_order):
        router_with_brokers.add_rule(
            RoutingRule(
                conditions={"symbol": "AAPL"},
                strategy=RoutingStrategy.SPLIT,
                priority=100,
                description="Split rule for AAPL",
            )
        )
        result = await router_with_brokers.route_order(
            sample_order,
            context={"symbol": "AAPL"},
            strategy=RoutingStrategy.COST_BASED,
        )
        assert result.strategy_used == RoutingStrategy.SPLIT

    @pytest.mark.asyncio
    async def test_route_callback_fires(self, router_with_brokers, sample_order):
        callback_results = []

        def on_route(result):
            callback_results.append(result)

        router_with_brokers.on_route(on_route)
        await router_with_brokers.route_order(sample_order)
        assert len(callback_results) == 1
        assert isinstance(callback_results[0], RoutingResult)


# ---------------------------------------------------------------------------
# OrderRouter batch routing tests
# ---------------------------------------------------------------------------

class TestOrderRouterBatch:
    """Test batch routing"""

    @pytest.mark.asyncio
    async def test_route_batch(self, router_with_brokers, sample_order, small_order):
        orders = [sample_order, small_order]
        results = await router_with_brokers.route_batch(orders)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, RoutingResult)

    @pytest.mark.asyncio
    async def test_route_batch_empty(self, router_with_brokers):
        results = await router_with_brokers.route_batch([])
        assert results == []


# ---------------------------------------------------------------------------
# OrderRouter summary and connectivity tests
# ---------------------------------------------------------------------------

class TestOrderRouterSummary:
    """Test summary and connectivity checks"""

    @pytest.mark.asyncio
    async def test_check_all_brokers(self, router, broker_a, disconnected_broker):
        router.register_broker("online", broker_a)
        router.register_broker("offline", disconnected_broker)
        results = await router.check_all_brokers()
        assert results["online"] is True
        assert results["offline"] is False

    def test_get_routing_summary(self, router, broker_a, disconnected_broker):
        router.register_broker("a", broker_a)
        router.register_broker("offline", disconnected_broker)

        summary = router.get_routing_summary()
        assert summary["registered_brokers"] == 2
        assert summary["connected_brokers"] == 1  # only broker_a, disconnected_broker is offline
        assert summary["rules_count"] == 0
        assert summary["default_strategy"] == "COST_BASED"
        assert "metrics" in summary


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestOrderRouterEdgeCases:
    """Test edge cases and error handling"""

    @pytest.mark.asyncio
    async def test_unknown_strategy(self, router_with_brokers, sample_order):
        """route_order with invalid strategy should fail cleanly.
        
        Uses a private helper to bypass the enum type guard.
        """
        result = await router_with_brokers._apply_strategy(
            sample_order, "INVALID_STRATEGY"  # type: ignore
        )
        assert result.success is False
        assert result.error_message is not None

    @pytest.mark.asyncio
    async def test_error_callback_fires(self, router, sample_order, monkeypatch):
        # Register a broker but inject an error in _apply_strategy

        original = router._apply_strategy
        async def faulty_strategy(order, strategy):
            raise RuntimeError("Simulated failure")

        router._apply_strategy = faulty_strategy  # type: ignore
        router.register_broker("a", MockBroker("a"))
        router.update_connectivity("a", True)

        error_calls = []
        router.on_error(lambda ctx, exc: error_calls.append((ctx, exc)))
        result = await router.route_order(sample_order)

        assert result.success is False
        assert len(error_calls) == 1

    def test__get_available_brokers_empty(self, router):
        assert router._get_available_brokers() == []

    def test_config_failover_default(self):
        config = RouterConfig()
        assert config.failover is True


# ---------------------------------------------------------------------------
# Integration smoke test: router + real-like Order
# ---------------------------------------------------------------------------

class TestOrderRouterIntegration:
    """Smoke tests that mimic typical usage patterns"""

    @pytest.mark.asyncio
    async def test_typical_usage_flow(self, symbol):
        # Setup
        router = OrderRouter(
            config=RouterConfig(
                default_strategy=RoutingStrategy.LATENCY_BASED,
            )
        )
        oanda = MockBroker("oanda", connected=True)
        sim = MockBroker("sim", connected=True)
        router.register_broker("oanda", oanda)
        router.register_broker("sim", sim)
        router.update_connectivity("oanda", True)
        router.update_connectivity("sim", True)

        # Tune metrics
        router._metrics["oanda"].record_submit(50.0)
        router._metrics["oanda"].record_submit(60.0)
        router._metrics["sim"].record_submit(200.0)

        # Create order
        order = Order(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=Decimal("500"),
            order_type=OrderType.MARKET,
        )

        # Route
        result = await router.route_order(order)
        assert result.success
        # oanda has lower avg latency (55 vs 200) → selected
        assert result.broker_name == "oanda"
        assert result.strategy_used == RoutingStrategy.LATENCY_BASED

    @pytest.mark.asyncio
    async def test_split_then_submit_to_brokers(self, symbol):
        """Simulate split routing then submitting to each broker"""
        router = OrderRouter()
        a = MockBroker("a")
        b = MockBroker("b")
        router.register_broker("a", a)
        router.register_broker("b", b)
        router.update_connectivity("a", True)
        router.update_connectivity("b", True)

        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("1000"),
            order_type=OrderType.MARKET,
        )

        result = await router.route_order(order, strategy=RoutingStrategy.SPLIT)
        assert result.success
        assert len(result.orders) >= 1

    @pytest.mark.asyncio
    async def test_rule_based_symbol_routing(self, symbol):
        """Specific symbol rules override defaults"""
        router = OrderRouter(
            config=RouterConfig(default_strategy=RoutingStrategy.COST_BASED)
        )
        a = MockBroker("a")
        b = MockBroker("b")
        router.register_broker("a", a)
        router.register_broker("b", b)
        router.update_connectivity("a", True)
        router.update_connectivity("b", True)

        router.add_rule(
            RoutingRule(
                conditions={"symbol": "AAPL"},
                strategy=RoutingStrategy.SPLIT,
                priority=100,
                description="Always split AAPL",
            )
        )

        aapl_order = Order(
            symbol=symbol,  # AAPL
            side=OrderSide.BUY,
            quantity=Decimal("1000"),
        )
        msft_order = Order(
            symbol=Symbol(ticker="MSFT"),
            side=OrderSide.BUY,
            quantity=Decimal("1000"),
        )

        result_aapl = await router.route_order(aapl_order, context={"symbol": "AAPL"})
        result_msft = await router.route_order(msft_order, context={"symbol": "MSFT"})

        assert result_aapl.strategy_used == RoutingStrategy.SPLIT
        assert result_msft.strategy_used == RoutingStrategy.COST_BASED