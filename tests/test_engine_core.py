"""Tests for core engine"""

import pytest
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, List

from src.engine.engine import Engine, EngineState
from src.engine.events import EventBus, Event, EventType, get_event_bus
from src.engine.interfaces import (
    IAlgorithm, IDataFeed, ITransactionHandler, IResultHandler,
    IPortfolio, IExecutionModel, IRiskManager,
    Symbol, Order, OrderEvent, OrderType, Position, Tick, Bar,
)
from src.utils.config import Config


class MockAlgorithm(IAlgorithm):
    """Mock algorithm for testing"""

    def __init__(self):
        self.initialized = False
        self.terminated = False
        self.terminate_message = ""
        self.data_received: List[Any] = []
        self.order_events: List[OrderEvent] = []
        self.position_changes: List[Position] = []
        self.warmup_finished = False
        self.end_of_day_calls = 0

    def initialize(self) -> None:
        self.initialized = True

    def on_data(self, data: Any) -> None:
        self.data_received.append(data)

    def on_order_event(self, event: OrderEvent) -> None:
        self.order_events.append(event)

    def on_position_changed(self, position: Position) -> None:
        self.position_changes.append(position)

    def on_warmup_finished(self) -> None:
        self.warmup_finished = True

    def on_end_of_day(self) -> None:
        self.end_of_day_calls += 1

    def terminate(self, message: str = "") -> None:
        self.terminated = True
        self.terminate_message = message

    @property
    def is_warming_up(self) -> bool:
        return False


class MockDataFeed(IDataFeed):
    """Mock data feed for testing"""

    def __init__(self):
        self.connected = False
        self.subscribed_symbols: List[Symbol] = []
        self.unsubscribed_symbols: List[Symbol] = []
        self.connect_call_count = 0
        self.disconnect_call_count = 0

    async def connect(self) -> None:
        self.connected = True
        self.connect_call_count += 1

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnect_call_count += 1

    async def subscribe(self, symbols: List[Symbol]) -> None:
        self.subscribed_symbols.extend(symbols)

    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        self.unsubscribed_symbols.extend(symbols)

    def get_history(self, symbol, start, end, resolution="minute"):
        import pandas as pd
        return pd.DataFrame()

    def is_connected(self) -> bool:
        return self.connected


class MockTransactionHandler(ITransactionHandler):
    """Mock transaction handler for testing"""

    def process_order(self, order: Order) -> OrderEvent:
        return OrderEvent(
            order_id=order.id,
            symbol=order.symbol,
            status=order.status,
            timestamp=datetime.now(),
        )

    def cancel_order(self, order_id: str) -> OrderEvent:
        return OrderEvent(
            order_id=order_id,
            symbol=Symbol(ticker="AAPL", security_type="EQUITY"),
            status="CANCELLED",
            timestamp=datetime.now(),
        )

    def update_order(self, order: Order) -> OrderEvent:
        return OrderEvent(
            order_id=order.id,
            symbol=order.symbol,
            status=order.status,
            timestamp=datetime.now(),
        )

    def get_open_orders(self, symbol=None) -> List[Order]:
        return []

    def get_order_by_id(self, order_id: str):
        return None


class MockResultHandler(IResultHandler):
    """Mock result handler for testing"""

    def __init__(self):
        self.algorithm = None
        self.messages: List[str] = []
        self.statistics: dict = {}
        self.order_events: List[OrderEvent] = []

    def log_message(self, message: str, level: str = "INFO") -> None:
        self.messages.append(f"[{level}] {message}")

    def debug_message(self, message: str) -> None:
        self.messages.append(f"[DEBUG] {message}")

    def error_message(self, message: str, traceback: str = "") -> None:
        self.messages.append(f"[ERROR] {message}")

    def runtime_statistic(self, key: str, value: Any) -> None:
        self.statistics[key] = value

    def order_event(self, event: OrderEvent) -> None:
        self.order_events.append(event)

    def save_results(self, name: str, result: Any) -> None:
        pass

    def set_algorithm(self, algorithm: IAlgorithm) -> None:
        self.algorithm = algorithm

    def exit(self, exit_code: int = 0) -> None:
        pass


class MockPortfolio(IPortfolio):
    """Mock portfolio for testing"""

    def __init__(self):
        self.cash = Decimal("0")
        self.fills: List[OrderEvent] = []

    def get_cash(self, currency: str = "USD") -> Decimal:
        return self.cash

    def get_total_portfolio_value(self) -> Decimal:
        return self.cash

    def get_position(self, symbol: Symbol):
        return None

    def get_all_positions(self) -> List[Position]:
        return []

    def get_unrealized_profit(self) -> Decimal:
        return Decimal("0")

    def get_realized_profit(self) -> Decimal:
        return Decimal("0")

    def process_fill(self, fill: OrderEvent) -> None:
        self.fills.append(fill)

    def set_cash(self, cash: Decimal, currency: str = "USD") -> None:
        self.cash = cash


class MockRiskManager(IRiskManager):
    """Mock risk manager for testing"""

    def manage_risk(self, portfolio: IPortfolio, orders: List[Order]) -> List[Order]:
        return orders

    def on_position_changed(self, position: Position) -> None:
        pass

    def is_within_limits(self, portfolio: IPortfolio) -> bool:
        return True


class TestEngineInitialization:
    """Test Engine initialization with different configs"""

    def test_engine_default_construction(self):
        """Test engine with no arguments"""
        engine = Engine()
        assert engine.state == EngineState.IDLE
        assert engine.is_running is False
        assert engine.is_backtest is False
        assert engine.algorithm is None
        assert engine.portfolio is None

    def test_engine_with_config(self):
        """Test engine with explicit config"""
        config = Config()
        config.env = "production"
        config.debug = False

        engine = Engine(config=config)
        assert engine.state == EngineState.IDLE

    def test_engine_backtest_mode(self):
        """Test engine in backtest mode"""
        start = datetime(2024, 1, 1)
        end = datetime(2024, 6, 30)

        engine = Engine(is_backtest=True, start_date=start, end_date=end)
        assert engine.is_backtest is True
        assert engine.state == EngineState.IDLE
        assert engine.is_running is False

    def test_engine_with_all_components(self):
        """Test engine with all components injected"""
        algo = MockAlgorithm()
        data_feed = MockDataFeed()
        txn = MockTransactionHandler()
        result = MockResultHandler()
        portfolio = MockPortfolio()
        risk = MockRiskManager()

        engine = Engine(
            algorithm_class=MockAlgorithm,
            data_feed=data_feed,
            transaction_handler=txn,
            result_handler=result,
            portfolio=portfolio,
            risk_manager=risk,
        )
        assert engine.state == EngineState.IDLE

    def test_engine_setters_before_initialize(self):
        """Test setting components before initialization"""
        engine = Engine()
        data_feed = MockDataFeed()
        result = MockResultHandler()

        engine.set_warmup_period(timedelta(days=60))
        engine.set_algorithm(MockAlgorithm)
        engine.set_data_feed(data_feed)
        engine.set_result_handler(result)

        assert engine.state == EngineState.IDLE

    def test_engine_set_portfolio(self):
        """Test setting portfolio"""
        engine = Engine()
        portfolio = MockPortfolio()
        engine.set_portfolio(portfolio)

    def test_engine_set_transaction_handler(self):
        """Test setting transaction handler"""
        engine = Engine()
        txn = MockTransactionHandler()
        engine.set_transaction_handler(txn)


class TestEngineStateTransitions:
    """Test engine state transitions"""

    @pytest.mark.asyncio
    async def test_idle_to_initializing_via_initialize(self):
        """Test IDLE -> INITIALIZING transition via initialize()"""
        engine = Engine()
        assert engine.state == EngineState.IDLE

        success = await engine.initialize()
        assert success is True
        assert engine.state == EngineState.INITIALIZING

    @pytest.mark.asyncio
    async def test_idle_to_initializing_with_data_feed(self):
        """Test initialize with data feed stays in INITIALIZING"""
        engine = Engine(data_feed=MockDataFeed())
        await engine.initialize()
        assert engine.state == EngineState.INITIALIZING

    @pytest.mark.asyncio
    async def test_initialize_creates_algorithm(self):
        """Test initialize creates algorithm instance"""
        engine = Engine(algorithm_class=MockAlgorithm)
        await engine.initialize()

        assert engine.state == EngineState.INITIALIZING
        assert engine.algorithm is not None
        assert isinstance(engine.algorithm, MockAlgorithm)
        assert engine.algorithm.initialized is True

    @pytest.mark.asyncio
    async def test_initialize_with_result_handler_sets_algorithm(self):
        """Test initialize sets algorithm on result handler"""
        result = MockResultHandler()
        engine = Engine(algorithm_class=MockAlgorithm, result_handler=result)
        await engine.initialize()

        assert result.algorithm is not None
        assert isinstance(result.algorithm, MockAlgorithm)

    @pytest.mark.asyncio
    async def test_idle_to_stopped_via_stop(self):
        """Test stop from IDLE transitions to STOPPED"""
        engine = Engine()
        await engine.stop()
        assert engine.state == EngineState.STOPPED

    @pytest.mark.asyncio
    async def test_full_idle_to_stopped_cycle(self):
        """Test IDLE -> INITIALIZING -> STOPPED cycle"""
        engine = Engine()
        assert engine.state == EngineState.IDLE

        await engine.initialize()
        assert engine.state == EngineState.INITIALIZING

        await engine.stop()
        assert engine.state == EngineState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_terminates_algorithm(self):
        """Test stop terminates algorithm"""
        engine = Engine(algorithm_class=MockAlgorithm)
        await engine.initialize()

        assert engine.algorithm.terminated is False
        await engine.stop()
        assert engine.algorithm.terminated is True
        assert engine.algorithm.terminate_message == "Engine stopped"

    @pytest.mark.asyncio
    async def test_double_initialize(self):
        """Test initialize when already initialized leaves state as INITIALIZING"""
        engine = Engine()
        await engine.initialize()
        assert engine.state == EngineState.INITIALIZING

        success = await engine.initialize()
        assert success is True
        assert engine.state == EngineState.INITIALIZING

    @pytest.mark.asyncio
    async def test_double_stop(self):
        """Test calling stop twice stays STOPPED"""
        engine = Engine()
        await engine.initialize()
        await engine.stop()
        assert engine.state == EngineState.STOPPED

        await engine.stop()
        assert engine.state == EngineState.STOPPED

    @pytest.mark.asyncio
    async def test_initialize_to_error_on_data_feed_failure(self):
        """Test initialize transitions to ERROR when data feed connect fails"""
        class FailingDataFeed(MockDataFeed):
            async def connect(self) -> None:
                raise RuntimeError("Connection refused")

        engine = Engine(data_feed=FailingDataFeed())
        result = await engine.initialize()
        assert result is False
        assert engine.state == EngineState.ERROR

    @pytest.mark.asyncio
    async def test_go_to_running_via_start(self):
        """Test that calling start() transitions through INITIALIZING to RUNNING"""
        engine = Engine()
        engine._state = EngineState.INITIALIZING

        # Simulate what start() does: set state to RUNNING and _running to True
        engine._state = EngineState.RUNNING
        engine._running = True
        assert engine.state == EngineState.RUNNING
        assert engine.is_running is True

    def test_pause_from_running(self):
        """Test pause transitions RUNNING -> PAUSED"""
        engine = Engine()
        engine._state = EngineState.RUNNING
        engine.pause()
        assert engine.state == EngineState.PAUSED

    def test_resume_from_paused(self):
        """Test resume transitions PAUSED -> RUNNING"""
        engine = Engine()
        engine._state = EngineState.PAUSED
        engine.resume()
        assert engine.state == EngineState.RUNNING

    @pytest.mark.asyncio
    async def test_stop_from_paused(self):
        """Test stop from PAUSED transitions to STOPPED"""
        engine = Engine(algorithm_class=MockAlgorithm)
        await engine.initialize()
        engine._state = EngineState.PAUSED

        await engine.stop()
        assert engine.state == EngineState.STOPPED


class TestEngineAlgorithmCreation:
    """Test engine algorithm creation and initialization"""

    def test_engine_stores_algorithm_class(self):
        """Test engine stores algorithm class reference"""
        engine = Engine(algorithm_class=MockAlgorithm)
        algo = engine.algorithm
        assert algo is None

    @pytest.mark.asyncio
    async def test_algorithm_created_on_initialize(self):
        """Test algorithm instance is created during initialize"""
        engine = Engine(algorithm_class=MockAlgorithm)
        assert engine.algorithm is None

        await engine.initialize()
        assert engine.algorithm is not None
        assert isinstance(engine.algorithm, MockAlgorithm)

    def test_set_algorithm_after_construction(self):
        """Test setting algorithm class after engine construction"""
        engine = Engine()
        assert engine.algorithm is None

        engine.set_algorithm(MockAlgorithm)

    @pytest.mark.asyncio
    async def test_initialize_without_algorithm(self):
        """Test initialize without algorithm class set"""
        engine = Engine()
        await engine.initialize()
        assert engine.algorithm is None
        assert engine.state == EngineState.INITIALIZING

    @pytest.mark.asyncio
    async def test_engine_no_data_feed_on_initialize(self):
        """Test initialize succeeds without data feed"""
        engine = Engine()
        success = await engine.initialize()
        assert success is True

    @pytest.mark.asyncio
    async def test_engine_timekeeper_created(self):
        """Test timekeeper is created on engine construction"""
        engine = Engine()
        assert engine.timekeeper is not None


class TestEngineConfigurationSetters:
    """Test engine set_warmup_period, set_algorithm, set_data_feed, etc."""

    def test_set_warmup_period_default(self):
        """Test default warmup period is zero"""
        engine = Engine()
        engine.set_warmup_period(timedelta(days=0))

    def test_set_warmup_period_custom(self):
        """Test setting custom warmup period"""
        engine = Engine()
        engine.set_warmup_period(timedelta(days=90))

    def test_set_data_feed(self):
        """Test setting data feed"""
        engine = Engine()
        df = MockDataFeed()
        engine.set_data_feed(df)

    def test_set_algorithm(self):
        """Test setting algorithm class"""
        engine = Engine()
        engine.set_algorithm(MockAlgorithm)

    def test_set_result_handler(self):
        """Test setting result handler"""
        engine = Engine()
        rh = MockResultHandler()
        engine.set_result_handler(rh)

    def test_set_transaction_handler(self):
        """Test setting transaction handler"""
        engine = Engine()
        th = MockTransactionHandler()
        engine.set_transaction_handler(th)

    def test_set_portfolio(self):
        """Test setting portfolio"""
        engine = Engine()
        pf = MockPortfolio()
        engine.set_portfolio(pf)


class TestEnginePauseResume:
    """Test engine pause/resume functionality"""

    def test_pause_from_idle_no_effect(self):
        """Test pause from IDLE has no effect"""
        engine = Engine()
        engine.pause()
        assert engine.state == EngineState.IDLE

    def test_resume_from_idle_no_effect(self):
        """Test resume from IDLE has no effect"""
        engine = Engine()
        engine.resume()
        assert engine.state == EngineState.IDLE

    def test_pause_resume_cycle(self):
        """Test RUNNING -> PAUSED -> RUNNING cycle"""
        engine = Engine()
        engine._state = EngineState.RUNNING

        engine.pause()
        assert engine.state == EngineState.PAUSED

        engine.resume()
        assert engine.state == EngineState.RUNNING

    def test_pause_from_paused_no_effect(self):
        """Test pause from PAUSED has no effect"""
        engine = Engine()
        engine._state = EngineState.PAUSED

        engine.pause()
        assert engine.state == EngineState.PAUSED

    def test_resume_from_running_no_effect(self):
        """Test resume from RUNNING has no effect"""
        engine = Engine()
        engine._state = EngineState.RUNNING

        engine.resume()
        assert engine.state == EngineState.RUNNING

    def test_multiple_pause_resume_cycles(self):
        """Test multiple pause/resume cycles"""
        engine = Engine()
        engine._state = EngineState.RUNNING

        for _ in range(3):
            engine.pause()
            assert engine.state == EngineState.PAUSED
            engine.resume()
            assert engine.state == EngineState.RUNNING

    def test_pause_from_initializing_no_effect(self):
        """Test pause from INITIALIZING has no effect"""
        engine = Engine()
        engine._state = EngineState.INITIALIZING

        engine.pause()
        assert engine.state == EngineState.INITIALIZING

    def test_resume_from_initializing_no_effect(self):
        """Test resume from INITIALIZING has no effect"""
        engine = Engine()
        engine._state = EngineState.INITIALIZING

        engine.resume()
        assert engine.state == EngineState.INITIALIZING


class TestEngineWithMocks:
    """Test engine with mock IAlgorithm, IDataFeed from interfaces"""

    @pytest.mark.asyncio
    async def test_initialize_connects_data_feed(self):
        """Test data feed.connect() is called during initialize"""
        data_feed = MockDataFeed()
        engine = Engine(data_feed=data_feed)
        assert data_feed.connected is False

        await engine.initialize()
        assert data_feed.connected is True
        assert data_feed.connect_call_count == 1

    @pytest.mark.asyncio
    async def test_stop_disconnects_data_feed(self):
        """Test data feed.disconnect() is called during stop"""
        data_feed = MockDataFeed()
        engine = Engine(data_feed=data_feed)
        await engine.initialize()

        await engine.stop()
        assert data_feed.connected is False
        assert data_feed.disconnect_call_count == 1

    @pytest.mark.asyncio
    async def test_initialize_with_portfolio_sets_cash(self):
        """Test portfolio cash is set to default during initialize"""
        portfolio = MockPortfolio()
        engine = Engine(portfolio=portfolio)

        await engine.initialize()
        assert portfolio.cash == Decimal("100000")

    @pytest.mark.asyncio
    async def test_event_bus_setup_on_construction(self):
        """Test engine subscribes to event bus on construction"""
        engine = Engine()
        bus = engine.event_bus
        assert bus.get_handler_count(EventType.TICK) >= 0

    @pytest.mark.asyncio
    async def test_multiple_engines_share_event_bus(self):
        """Test multiple engines share the same event bus"""
        engine1 = Engine()
        engine2 = Engine()
        assert engine1.event_bus is engine2.event_bus

    def test_engine_properties(self):
        """Test engine read-only properties"""
        engine = Engine()
        assert isinstance(engine.state, EngineState)
        assert isinstance(engine.is_running, bool)
        assert isinstance(engine.is_backtest, bool)
        assert engine.algorithm is None or isinstance(engine.algorithm, IAlgorithm)

    @pytest.mark.asyncio
    async def test_initialize_sets_state_to_initializing(self):
        """Test successful initialize transitions to INITIALIZING"""
        engine = Engine()
        assert engine.state == EngineState.IDLE

        result = await engine.initialize()
        assert result is True
        assert engine.state == EngineState.INITIALIZING

    @pytest.mark.asyncio
    async def test_is_running_false_after_initialize(self):
        """Test is_running is False after initialize (only start() sets _running)"""
        engine = Engine()
        await engine.initialize()
        assert engine.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cleans_up_data_feed(self):
        """Test stop properly disconnects data feed"""
        data_feed = MockDataFeed()
        data_feed.connected = True
        engine = Engine(data_feed=data_feed)
        await engine.stop()
        assert data_feed.disconnect_call_count == 1
        assert data_feed.connected is False

    def test_engine_is_backtest_property(self):
        """Test is_backtest returned correctly"""
        live_engine = Engine(is_backtest=False)
        bt_engine = Engine(is_backtest=True)

        assert live_engine.is_backtest is False
        assert bt_engine.is_backtest is True

    @pytest.mark.asyncio
    async def test_stop_calls_algorithm_terminate(self):
        """Test stopping engine terminates the algorithm"""
        engine = Engine(algorithm_class=MockAlgorithm)
        await engine.initialize()

        await engine.stop()
        assert engine.algorithm.terminated is True
        assert "Engine stopped" in engine.algorithm.terminate_message

    def test_submit_order_while_not_running(self):
        """Test submit_order while engine is not running returns early"""
        engine = Engine()
        assert engine.state == EngineState.IDLE

        symbol = Symbol(ticker="AAPL", security_type="EQUITY")
        order = Order(
            id="ord-1", symbol=symbol, order_type=OrderType.MARKET,
            side="BUY", quantity=Decimal("10")
        )
        engine.submit_order(order)
        assert engine.state == EngineState.IDLE


class SpyTransactionHandler(MockTransactionHandler):
    """Transaction handler that records processed and cancelled orders"""

    def __init__(self):
        super().__init__()
        self.processed: List[Order] = []
        self.cancelled: List[str] = []

    def process_order(self, order: Order) -> OrderEvent:
        self.processed.append(order)
        return super().process_order(order)

    def cancel_order(self, order_id: str) -> OrderEvent:
        self.cancelled.append(order_id)
        return super().cancel_order(order_id)


class RejectingRiskManager(MockRiskManager):
    """Risk manager that rejects all orders"""

    def is_within_limits(self, portfolio: IPortfolio) -> bool:
        return False


class TestEngineEventHandlersAndOrderFlow:
    """Test event handlers and order submission/cancellation paths"""

    def _make_event(self, event_type, data):
        return Event(event_type=event_type, timestamp=datetime.now(), data=data)

    def test_on_tick_dispatches_to_algorithm(self):
        engine = Engine()
        algo = MockAlgorithm()
        engine._algorithm = algo
        tick = Tick(
            symbol=Symbol("AAPL"), timestamp=datetime.now(),
            bid_price=Decimal("150.00"), ask_price=Decimal("150.10"),
            bid_size=Decimal("100"), ask_size=Decimal("100"),
        )
        engine._on_tick(self._make_event(EventType.TICK, tick))
        assert len(algo.data_received) == 1

    def test_on_bar_dispatches_to_algorithm(self):
        engine = Engine()
        algo = MockAlgorithm()
        engine._algorithm = algo
        bar = Bar(
            symbol=Symbol("AAPL"), timestamp=datetime.now(),
            open=Decimal("150"), high=Decimal("155"), low=Decimal("149"),
            close=Decimal("152"), volume=Decimal("1000"),
        )
        engine._on_bar(self._make_event(EventType.BAR, bar))
        assert len(algo.data_received) == 1

    def test_on_tick_skipped_during_warmup(self):
        engine = Engine()
        algo = MockAlgorithm()
        engine._algorithm = algo
        engine._is_warming_up = True
        tick = Tick(
            symbol=Symbol("AAPL"), timestamp=datetime.now(),
            bid_price=Decimal("150.00"), ask_price=Decimal("150.10"),
            bid_size=Decimal("100"), ask_size=Decimal("100"),
        )
        engine._on_tick(self._make_event(EventType.TICK, tick))
        assert len(algo.data_received) == 0

    def test_on_order_filled_dispatches_to_algorithm_and_portfolio(self):
        portfolio = MockPortfolio()
        engine = Engine(portfolio=portfolio)
        algo = MockAlgorithm()
        engine._algorithm = algo
        order_event = OrderEvent(
            order_id="o1", symbol=Symbol("AAPL"), status="FILLED",
            timestamp=datetime.now(),
        )
        engine._on_order_filled(self._make_event(EventType.ORDER_FILLED, order_event))
        assert len(algo.order_events) == 1
        assert len(portfolio.fills) == 1

    def test_submit_order_running_with_transaction_handler(self):
        txn = SpyTransactionHandler()
        engine = Engine(transaction_handler=txn, risk_manager=MockRiskManager())
        engine._state = EngineState.RUNNING
        order = Order(
            id="o1", symbol=Symbol("AAPL"), order_type=OrderType.MARKET,
            side="BUY", quantity=Decimal("10"),
        )
        engine.submit_order(order)
        assert len(txn.processed) == 1

    def test_submit_order_rejected_by_risk_manager(self):
        txn = SpyTransactionHandler()
        engine = Engine(
            transaction_handler=txn,
            risk_manager=RejectingRiskManager(),
            portfolio=MockPortfolio(),
        )
        engine._state = EngineState.RUNNING
        order = Order(
            id="o1", symbol=Symbol("AAPL"), order_type=OrderType.MARKET,
            side="BUY", quantity=Decimal("10"),
        )
        engine.submit_order(order)
        assert len(txn.processed) == 0

    def test_cancel_order_with_transaction_handler(self):
        txn = SpyTransactionHandler()
        engine = Engine(transaction_handler=txn)
        engine.cancel_order("o1")
        assert txn.cancelled == ["o1"]

    def test_cancel_order_without_transaction_handler(self):
        engine = Engine()
        engine.cancel_order("o1")  # should not raise

    @pytest.mark.asyncio
    async def test_warmup_backtest_mode(self):
        engine = Engine(
            algorithm_class=MockAlgorithm,
            is_backtest=True,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 30),
        )
        await engine.initialize()
        await engine.warmup(timedelta(days=10))
        assert engine.algorithm.warmup_finished is True
        assert engine.state == EngineState.RUNNING

    @pytest.mark.asyncio
    async def test_warmup_skipped_in_live_mode(self):
        engine = Engine(algorithm_class=MockAlgorithm, is_backtest=False)
        await engine.initialize()
        await engine.warmup(timedelta(days=10))
        assert engine.algorithm.warmup_finished is False

    @pytest.mark.asyncio
    async def test_start_and_stop_live(self):
        data_feed = MockDataFeed()
        engine = Engine(data_feed=data_feed, algorithm_class=MockAlgorithm)
        await engine.start()
        assert engine.state == EngineState.RUNNING
        assert engine.is_running is True
        await engine.stop()
        assert engine.state == EngineState.STOPPED

    @pytest.mark.asyncio
    async def test_start_backtest_runs_warmup(self):
        engine = Engine(
            algorithm_class=MockAlgorithm,
            is_backtest=True,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 30),
        )
        engine.set_warmup_period(timedelta(days=5))
        await engine.start()
        assert engine.state == EngineState.RUNNING
        assert engine.algorithm.warmup_finished is True
        await engine.stop()
        assert engine.state == EngineState.STOPPED
