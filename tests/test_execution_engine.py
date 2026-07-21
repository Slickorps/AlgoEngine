"""Tests for execution engine"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from datetime import datetime

from src.trading.execution_engine import ExecutionEngine
from src.trading.models import (
    Order, OrderSide, OrderType, OrderStatus, Fill,
    CommissionModel, SlippageModel
)
from src.data.models import Symbol, Tick


class MockBrokerAdapter:
    """Mock broker adapter for testing - not inheriting ABC to avoid abstractmethod checks"""

    def __init__(self, connected: bool = True):
        self._connected = connected
        self.is_connected = MagicMock(return_value=connected)
        self.submit_order = AsyncMock(return_value=True)
        self.cancel_order = AsyncMock(return_value=True)
        self.connect = AsyncMock(return_value=True)
        self.disconnect = AsyncMock(return_value=None)


@pytest.fixture
def aapl():
    return Symbol(ticker="AAPL")


@pytest.fixture
def sample_order(aapl):
    return Order(
        symbol=aapl,
        side=OrderSide.BUY,
        quantity=Decimal("100"),
        order_type=OrderType.MARKET
    )


@pytest.fixture
def limit_order(aapl):
    return Order(
        symbol=aapl,
        side=OrderSide.BUY,
        quantity=Decimal("50"),
        order_type=OrderType.LIMIT,
        limit_price=Decimal("149.00")
    )


@pytest.fixture
def sample_tick(aapl):
    return Tick(
        symbol=aapl,
        timestamp=datetime.now(),
        bid_price=Decimal("150.00"),
        ask_price=Decimal("150.05"),
        bid_size=Decimal("500"),
        ask_size=Decimal("300")
    )


class TestExecutionEngineInit:
    """Test ExecutionEngine initialization"""

    def test_init_defaults(self):
        engine = ExecutionEngine()
        assert engine.order_manager is not None
        assert engine.position_manager is not None
        assert engine._broker is None

    def test_init_with_broker(self):
        broker = MockBrokerAdapter(connected=True)
        engine = ExecutionEngine(broker=broker)
        assert engine._broker is broker

    def test_init_with_custom_commission_model(self):
        model = CommissionModel(per_share=Decimal("0.01"), flat_fee=Decimal("2.50"))
        engine = ExecutionEngine(commission_model=model)
        assert engine._commission_model.per_share == Decimal("0.01")
        assert engine._commission_model.flat_fee == Decimal("2.50")

    def test_init_with_custom_slippage_model(self):
        model = SlippageModel(fixed_slippage=Decimal("0.05"), percentage=Decimal("0.1"))
        engine = ExecutionEngine(slippage_model=model)
        assert engine._slippage_model.fixed_slippage == Decimal("0.05")
        assert engine._slippage_model.percentage == Decimal("0.1")

    def test_init_wires_fill_callback(self):
        engine = ExecutionEngine()
        assert len(engine._order_manager._on_fill_callbacks) == 1

    def test_order_manager_property(self):
        engine = ExecutionEngine()
        om = engine.order_manager
        assert om is engine._order_manager

    def test_position_manager_property(self):
        engine = ExecutionEngine()
        pm = engine.position_manager
        assert pm is engine._position_manager


class TestSubmitOrder:
    """Test submit_order flow"""

    @pytest.mark.asyncio
    async def test_submit_order_no_broker(self, sample_order):
        engine = ExecutionEngine()
        result = await engine.submit_order(sample_order)
        assert result is True
        assert sample_order.status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_submit_order_with_connected_broker(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        engine = ExecutionEngine(broker=broker)
        result = await engine.submit_order(sample_order)
        assert result is True
        assert sample_order.status == OrderStatus.ACCEPTED
        broker.submit_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submit_order_broker_rejects(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        broker.submit_order = AsyncMock(return_value=False)
        engine = ExecutionEngine(broker=broker)
        result = await engine.submit_order(sample_order)
        assert result is False
        assert sample_order.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_submit_order_broker_raises_exception(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        broker.submit_order = AsyncMock(side_effect=ConnectionError("Broker offline"))
        engine = ExecutionEngine(broker=broker)
        result = await engine.submit_order(sample_order)
        assert result is False
        assert sample_order.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_submit_order_disconnected_broker(self, sample_order):
        broker = MockBrokerAdapter(connected=False)
        engine = ExecutionEngine(broker=broker)
        result = await engine.submit_order(sample_order)
        assert result is True
        assert sample_order.status == OrderStatus.PENDING
        broker.submit_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submit_order_registers_in_manager(self, sample_order):
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        retrieved = engine.order_manager.get_order(sample_order.order_id)
        assert retrieved is sample_order


class TestCancelOrder:
    """Test cancel_order flow"""

    @pytest.mark.asyncio
    async def test_cancel_order_without_broker(self, sample_order):
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        result = await engine.cancel_order(sample_order.order_id)
        assert result is True
        assert sample_order.is_cancelled

    @pytest.mark.asyncio
    async def test_cancel_order_with_broker(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        engine = ExecutionEngine(broker=broker)
        await engine.submit_order(sample_order)
        result = await engine.cancel_order(sample_order.order_id)
        assert result is True
        broker.cancel_order.assert_awaited_once_with(sample_order.order_id)

    @pytest.mark.asyncio
    async def test_cancel_order_unknown_id(self):
        engine = ExecutionEngine()
        result = await engine.cancel_order("NONEXISTENT")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self):
        engine = ExecutionEngine()
        aapl = Symbol(ticker="AAPL")
        for _ in range(3):
            order = Order(symbol=aapl, side=OrderSide.BUY, quantity=Decimal("100"))
            await engine.submit_order(order)
        cancelled = engine.cancel_all_orders()
        assert cancelled == 3


class TestOrderLifecycle:
    """Test complete order lifecycle through engine"""

    @patch('src.trading.execution_engine.get_event_bus')
    @pytest.mark.asyncio
    async def test_market_order_fill_in_simulation(self, mock_get_event_bus, sample_order, sample_tick):
        mock_bus = MagicMock()
        mock_get_event_bus.return_value = mock_bus
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        assert sample_order.status == OrderStatus.PENDING

        engine.process_tick(sample_tick)

        assert sample_order.is_filled
        assert sample_order.filled_quantity == Decimal("100")
        assert sample_order.avg_fill_price is not None

    @patch('src.trading.execution_engine.get_event_bus')
    @pytest.mark.asyncio
    async def test_limit_order_buy_fills_when_ask_at_or_below(self, mock_get_event_bus, limit_order, sample_tick):
        mock_bus = MagicMock()
        mock_get_event_bus.return_value = mock_bus
        engine = ExecutionEngine()
        await engine.submit_order(limit_order)
        # tick ask_price is 150.05, limit is 149.00 - should NOT fill
        engine.process_tick(sample_tick)
        assert not limit_order.is_filled

        # tick with ask at limit - should fill
        fill_tick = Tick(
            symbol=limit_order.symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("148.90"),
            ask_price=Decimal("149.00"),
            bid_size=Decimal("500"),
            ask_size=Decimal("300")
        )
        engine.process_tick(fill_tick)
        assert limit_order.is_filled

    @patch('src.trading.execution_engine.get_event_bus')
    @pytest.mark.asyncio
    async def test_order_removed_from_active_after_fill(self, mock_get_event_bus, sample_order, sample_tick):
        mock_bus = MagicMock()
        mock_get_event_bus.return_value = mock_bus
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        assert len(engine.order_manager.get_active_orders()) == 1

        engine.process_tick(sample_tick)
        assert len(engine.order_manager.get_active_orders()) == 0

    @patch('src.trading.execution_engine.get_event_bus')
    @pytest.mark.asyncio
    async def test_position_created_on_fill(self, mock_get_event_bus, sample_order, sample_tick):
        mock_bus = MagicMock()
        mock_get_event_bus.return_value = mock_bus
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        engine.process_tick(sample_tick)

        position = engine.position_manager.get_position(sample_order.symbol)
        assert position is not None
        assert position.quantity == Decimal("100")

    @pytest.mark.asyncio
    async def test_submit_then_cancel_lifecycle(self, sample_order):
        engine = ExecutionEngine()
        await engine.submit_order(sample_order)
        assert sample_order.status == OrderStatus.PENDING

        await engine.cancel_order(sample_order.order_id)
        assert sample_order.is_cancelled
        assert len(engine.order_manager.get_active_orders()) == 0


class TestErrorHandling:
    """Test error handling in execution engine"""

    @pytest.mark.asyncio
    async def test_broker_exception_on_submit(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        broker.submit_order = AsyncMock(side_effect=RuntimeError("Unexpected failure"))
        engine = ExecutionEngine(broker=broker)
        result = await engine.submit_order(sample_order)
        assert result is False
        assert sample_order.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_broker_exception_on_cancel(self, sample_order):
        broker = MockBrokerAdapter(connected=True)
        broker.cancel_order = AsyncMock(side_effect=ConnectionError("Disconnected"))
        engine = ExecutionEngine(broker=broker)
        await engine.submit_order(sample_order)
        # Cancel should still succeed locally even if broker fails
        result = await engine.cancel_order(sample_order.order_id)
        assert result is True
        assert sample_order.is_cancelled

    @pytest.mark.asyncio
    async def test_fill_unknown_order_no_error(self):
        engine = ExecutionEngine()
        fill = Fill(
            order_id="NONEXISTENT",
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        engine._order_manager.process_fill("NONEXISTENT", fill)
        # Should not raise

    @patch('src.trading.execution_engine.get_event_bus')
    def test_process_tick_no_broker_required(self, mock_get_event_bus, aapl):
        mock_bus = MagicMock()
        mock_get_event_bus.return_value = mock_bus
        broker = MockBrokerAdapter(connected=True)
        engine = ExecutionEngine(broker=broker)
        tick = Tick(
            symbol=aapl,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("500"),
            ask_size=Decimal("300")
        )
        engine.process_tick(tick)
        assert engine._last_prices.get(aapl) is tick

    def test_get_statistics(self, aapl):
        engine = ExecutionEngine()
        stats = engine.get_statistics()
        assert 'orders' in stats
        assert 'positions' in stats
        assert 'total_pnl' in stats
        assert stats['orders']['total_orders'] == 0
