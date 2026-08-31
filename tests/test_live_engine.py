"""Tests for the live trading engine."""

import pytest
from datetime import datetime
from decimal import Decimal

from src.trading.live_engine import (
    LiveEngine,
    LiveTradingMode,
    EngineHealth,
    LiveEngineConfig,
    LiveTradingStats,
    LiveTradeLogger,
    create_live_engine,
)
from src.trading.models import Order, OrderSide, OrderType, OrderStatus, Fill, Position
from src.trading.execution_engine import BrokerAdapter
from src.data.models import Symbol, Tick, Bar
from src.risk.risk_manager import RiskManager


class FakePortfolio:
    """Minimal portfolio for live engine tests."""

    def __init__(self, cash: str = "100000"):
        self.cash = Decimal(cash)
        self.initial_cash = Decimal(cash)
        self._total_value = Decimal(cash)

    @property
    def total_value(self) -> Decimal:
        return self._total_value

    @total_value.setter
    def total_value(self, value: Decimal) -> None:
        self._total_value = value

    def get_cash(self, currency: str = "USD") -> Decimal:
        return self.cash

    def get_summary(self) -> dict:
        return {"cash": float(self.cash), "total_value": float(self._total_value)}


class FakeBroker(BrokerAdapter):
    """Minimal broker adapter for live engine tests."""

    def __init__(self):
        self._connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.submitted = []
        self.cancelled = []
        self.positions = []
        self.orders = []

    async def connect(self) -> bool:
        self.connect_calls += 1
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def submit_order(self, order: Order) -> bool:
        self.submitted.append(order)
        return True

    async def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True

    async def get_positions(self):
        return self.positions

    async def get_orders(self):
        return self.orders


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL", security_type="EQUITY", exchange="NASDAQ")


def make_order(symbol, quantity="10", side=OrderSide.BUY):
    return Order(symbol=symbol, side=side, quantity=Decimal(quantity))


class TestLiveTradingStats:
    def test_to_dict(self):
        stats = LiveTradingStats()
        data = stats.to_dict()
        assert data["total_orders_submitted"] == 0
        assert data["total_volume"] == 0.0
        assert data["is_circuit_breaker_active"] is False
        assert "session_start" in data


class TestLiveTradeLogger:
    def test_log_order_submitted(self):
        logger = LiveTradeLogger()
        order = make_order(Symbol(ticker="AAPL"))
        logger.log_order_submitted(order)
        assert len(logger._order_log) == 1
        assert logger.get_recent_orders()[-1]["event"] == "ORDER_SUBMITTED"

    def test_log_order_fill(self):
        logger = LiveTradeLogger()
        order = make_order(Symbol(ticker="AAPL"))
        fill = Fill(
            order_id=order.order_id, symbol=order.symbol, side=OrderSide.BUY,
            quantity=Decimal("10"), fill_price=Decimal("150"), fill_time=datetime.now(),
        )
        logger.log_order_fill(order, fill)
        assert len(logger._trade_log) == 1
        assert logger.get_recent_trades()[-1]["event"] == "ORDER_FILLED"

    def test_log_order_rejected(self):
        logger = LiveTradeLogger()
        order = make_order(Symbol(ticker="AAPL"))
        logger.log_order_rejected(order, "risk")
        assert logger.get_recent_orders()[-1]["event"] == "ORDER_REJECTED"

    def test_log_error(self):
        logger = LiveTradeLogger()
        logger.log_error("test", ValueError("boom"))
        assert len(logger._error_log) == 1
        assert logger.get_recent_errors()[-1]["error_type"] == "ValueError"

    def test_log_size_management(self):
        logger = LiveTradeLogger()
        logger._max_log_entries = 3
        for i in range(5):
            logger.log_error("test", ValueError(str(i)))
        assert len(logger._error_log) == 3

    def test_clear_logs(self):
        logger = LiveTradeLogger()
        logger.log_error("test", ValueError("boom"))
        logger.clear_logs()
        assert logger.get_recent_errors() == []


class TestLiveEngineBasics:
    def test_init_defaults(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        assert engine.mode == LiveTradingMode.PAPER
        assert engine.health == EngineHealth.OFFLINE
        assert engine.is_running is False
        assert engine.is_connected is False
        assert engine.stats.total_orders_submitted == 0

    def test_properties(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        assert engine.order_manager is not None
        assert engine.position_manager is not None
        assert engine.trade_logger is not None

    def test_set_risk_manager(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        rm = RiskManager(FakePortfolio())
        engine.set_risk_manager(rm)
        assert engine._risk_manager is rm

    def test_callbacks_registered(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        received = []
        engine.on_order(lambda o: received.append(o))
        engine.on_fill(lambda f: received.append(f))
        engine.on_error(lambda e: received.append(e))
        engine.on_health_change(lambda h: received.append(h))
        engine.on_circuit_breaker(lambda n: received.append(n))
        assert len(engine._on_order_handlers) == 1
        assert len(engine._on_fill_handlers) == 1
        assert len(engine._on_error_handlers) == 1
        assert len(engine._on_health_change) == 1
        assert len(engine._on_circuit_breaker) == 1

    def test_get_status(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        status = engine.get_status()
        assert status["mode"] == "paper"
        assert status["health"] == "offline"
        assert status["running"] is False
        assert "stats" in status

    def test_get_recent_activity(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        activity = engine.get_recent_activity()
        assert "trades" in activity
        assert "orders" in activity
        assert "errors" in activity


class TestLiveEngineLifecycle:
    async def test_start_paper_mode_without_broker(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        result = await engine.start()
        assert result is True
        assert engine.is_running is True
        assert engine.is_connected is True
        await engine.stop()

    async def test_start_live_mode_without_broker_fails(self):
        engine = LiveEngine(portfolio=FakePortfolio(), mode=LiveTradingMode.LIVE)
        result = await engine.start()
        assert result is False
        assert engine.is_running is False

    async def test_start_already_running(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        await engine.start()
        assert await engine.start() is True
        await engine.stop()

    async def test_stop(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        await engine.start()
        await engine.stop()
        assert engine.is_running is False
        assert engine.health == EngineHealth.OFFLINE

    async def test_stop_when_not_running(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        await engine.stop()
        assert engine.is_running is False

    def test_pause_resume(self):
        engine = LiveEngine(portfolio=FakePortfolio(), mode=LiveTradingMode.LIVE)
        engine._running = True
        engine.pause()
        assert engine.mode == LiveTradingMode.STANDBY
        engine.resume()
        assert engine.mode == LiveTradingMode.LIVE

    def test_pause_only_in_live_mode(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        engine._running = True
        engine.pause()
        assert engine.mode == LiveTradingMode.PAPER


class TestLiveEngineConnection:
    async def test_connect_broker_success(self):
        broker = FakeBroker()
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        assert await engine._connect_broker() is True
        assert engine.is_connected is True
        assert broker.connect_calls == 1

    async def test_connect_broker_returns_false(self):
        broker = FakeBroker()

        async def fail_connect():
            return False

        broker.connect = fail_connect
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        assert await engine._connect_broker() is False
        assert engine.health == EngineHealth.CRITICAL

    async def test_connect_broker_timeout(self):
        import asyncio

        broker = FakeBroker()

        async def slow_connect():
            await asyncio.sleep(10)
            return True

        broker.connect = slow_connect
        engine = LiveEngine(
            portfolio=FakePortfolio(),
            broker=broker,
            config=LiveEngineConfig(broker_timeout=0.01),
        )
        assert await engine._connect_broker() is False
        assert engine.health == EngineHealth.CRITICAL

    async def test_disconnect_broker(self):
        broker = FakeBroker()
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        engine._connected = True
        await engine._disconnect_broker()
        assert broker.disconnect_calls == 1
        assert engine.is_connected is False


class TestLiveEngineOrderExecution:
    def _running_engine(self, broker=None):
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        engine._running = True
        engine._health = EngineHealth.HEALTHY
        return engine

    async def test_submit_order_not_running(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "not running" in msg

    async def test_submit_order_warmup_mode(self):
        engine = LiveEngine(portfolio=FakePortfolio(), mode=LiveTradingMode.WARMUP)
        engine._running = True
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "warmup" in msg

    async def test_submit_order_circuit_breaker_active(self):
        engine = self._running_engine()
        engine._circuit_breaker.trip("test")
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "Circuit breaker" in msg

    async def test_submit_order_daily_limit(self):
        engine = self._running_engine()
        engine._daily_trade_count = engine._config.max_daily_trades
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "Daily trade limit" in msg

    async def test_submit_order_volume_limit(self):
        engine = self._running_engine()
        engine._daily_volume = engine._config.max_daily_volume
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "volume limit" in msg

    async def test_submit_order_pending_limit(self):
        engine = self._running_engine()
        engine._pending_orders = {str(i): make_order(Symbol(ticker="AAPL")) for i in range(engine._config.max_pending_orders)}
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert "Max pending orders" in msg

    async def test_submit_order_success(self):
        broker = FakeBroker()
        engine = self._running_engine(broker)
        broker._connected = True
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is True
        assert engine.stats.total_orders_submitted == 1
        assert engine._daily_trade_count == 1

    async def test_submit_order_rejected_by_risk(self):
        portfolio = FakePortfolio()
        engine = LiveEngine(portfolio=portfolio, mode=LiveTradingMode.LIVE)
        engine._running = True
        engine._health = EngineHealth.HEALTHY
        engine._connected = True
        risk = RiskManager(portfolio)
        risk.max_drawdown_percent = 0.0
        engine.set_risk_manager(risk)
        ok, msg = await engine.submit_order(make_order(Symbol(ticker="AAPL")))
        assert ok is False
        assert engine.stats.total_orders_rejected == 1

    async def test_cancel_order_not_found(self):
        engine = self._running_engine()
        assert await engine.cancel_order("missing") is False

    async def test_cancel_order_success(self):
        engine = self._running_engine()
        order = make_order(Symbol(ticker="AAPL"))
        engine.order_manager.register_order(order)
        engine._pending_orders[order.order_id] = order
        assert await engine.cancel_order(order.order_id) is True
        assert order.order_id not in engine._pending_orders

    async def test_cancel_all_orders(self):
        engine = self._running_engine()
        for i in range(3):
            order = make_order(Symbol(ticker="AAPL"))
            engine.order_manager.register_order(order)
            engine._pending_orders[order.order_id] = order
        cancelled = await engine.cancel_all_orders()
        assert cancelled == 3


class TestLiveEngineMarketData:
    def test_process_market_data_not_running(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        tick = Tick(
            symbol=Symbol(ticker="AAPL"), timestamp=datetime.now(),
            bid_price=Decimal("150"), ask_price=Decimal("150.1"),
            bid_size=Decimal("1"), ask_size=Decimal("1"),
        )
        engine.process_market_data(tick)
        assert engine.stats.peak_portfolio_value == Decimal("0")

    def test_process_tick_updates_peak(self):
        portfolio = FakePortfolio()
        portfolio.total_value = Decimal("100000")
        engine = LiveEngine(portfolio=portfolio)
        engine._running = True
        tick = Tick(
            symbol=Symbol(ticker="AAPL"), timestamp=datetime.now(),
            bid_price=Decimal("150"), ask_price=Decimal("150.1"),
            bid_size=Decimal("1"), ask_size=Decimal("1"),
        )
        engine.process_market_data(tick)
        assert engine.stats.peak_portfolio_value == Decimal("100000")

    def test_process_tick_drawdown_trips_circuit_breaker(self):
        portfolio = FakePortfolio()
        portfolio.total_value = Decimal("100000")
        engine = LiveEngine(portfolio=portfolio)
        engine._running = True

        tick = Tick(
            symbol=Symbol(ticker="AAPL"), timestamp=datetime.now(),
            bid_price=Decimal("150"), ask_price=Decimal("150.1"),
            bid_size=Decimal("1"), ask_size=Decimal("1"),
        )
        engine.process_market_data(tick)

        portfolio.total_value = Decimal("90000")
        engine.process_market_data(tick)

        assert engine.stats.current_drawdown_pct > 0
        assert engine._circuit_breaker.is_active is True
        assert engine.stats.is_circuit_breaker_active is True

    def test_process_bar_no_error(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        engine._running = True
        bar = Bar(
            symbol=Symbol(ticker="AAPL"), timestamp=datetime.now(),
            open=Decimal("150"), high=Decimal("151"), low=Decimal("149"),
            close=Decimal("150"), volume=Decimal("100"),
        )
        engine.process_market_data(bar)


class TestLiveEngineOnFill:
    def test_handle_fill_updates_stats(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        order = make_order(Symbol(ticker="AAPL"))
        engine._pending_orders[order.order_id] = order
        fill = Fill(
            order_id=order.order_id, symbol=order.symbol, side=OrderSide.BUY,
            quantity=Decimal("10"), fill_price=Decimal("150"),
            fill_time=datetime.now(), commission=Decimal("1"), slippage=Decimal("0.5"),
        )
        engine.handle_fill(fill)
        assert engine.stats.total_orders_filled == 1
        assert engine.stats.total_volume == Decimal("10")
        assert engine.stats.total_commission == Decimal("1")
        assert engine.stats.total_slippage == Decimal("0.5")
        assert order.order_id not in engine._pending_orders

    def test_on_fill_registers_handler(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        received = []
        engine.on_fill(lambda f: received.append(f))
        assert len(engine._on_fill_handlers) == 1

    def test_handle_fill_unknown_order(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        fill = Fill(
            order_id="missing", symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), fill_price=Decimal("150"), fill_time=datetime.now(),
        )
        engine.handle_fill(fill)
        assert engine.stats.total_orders_filled == 0


class TestLiveEngineRisk:
    def test_check_order_risk_passed(self):
        portfolio = FakePortfolio()
        engine = LiveEngine(portfolio=portfolio)
        ok, reason = engine._check_order_risk(make_order(Symbol(ticker="AAPL")), Decimal("100"))
        assert ok is True

    def test_check_order_risk_position_size(self):
        portfolio = FakePortfolio("1000")
        engine = LiveEngine(portfolio=portfolio)
        order = make_order(Symbol(ticker="AAPL"), quantity="500")
        ok, reason = engine._check_order_risk(order, Decimal("100"))
        assert ok is False
        assert "Position size" in reason

    def test_check_order_risk_drawdown(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        engine._stats.current_drawdown_pct = 50.0
        ok, reason = engine._check_order_risk(make_order(Symbol(ticker="AAPL")), Decimal("100"))
        assert ok is False
        assert "drawdown" in reason


class TestLiveEngineSync:
    def test_positions_differ(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        symbol = Symbol(ticker="AAPL")
        pos = Position(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("10"))
        assert engine._positions_differ(None, pos) is True
        assert engine._positions_differ(pos, None) is True
        assert engine._positions_differ(None, None) is False
        assert engine._positions_differ(pos, pos) is False

        other = Position(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("20"))
        assert engine._positions_differ(pos, other) is True

    def test_reconcile_position(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        symbol = Symbol(ticker="AAPL")
        pos = Position(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("10"))
        engine._reconcile_position(pos)

    async def test_sync_positions_no_broker(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        await engine._sync_positions()

    async def test_sync_positions_with_broker(self):
        broker = FakeBroker()
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        engine._connected = True
        broker.positions = [
            Position(symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY, quantity=Decimal("10"))
        ]
        await engine._sync_positions()

    async def test_sync_orders_no_pending(self):
        broker = FakeBroker()
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        engine._connected = True
        await engine._sync_orders()

    async def test_sync_orders_status_change(self):
        broker = FakeBroker()
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        engine._connected = True
        order = make_order(Symbol(ticker="AAPL"))
        engine._pending_orders[order.order_id] = order

        filled = make_order(Symbol(ticker="AAPL"))
        filled.broker_order_id = order.order_id
        filled.status = OrderStatus.FILLED
        broker.orders = [filled]
        await engine._sync_orders()

        assert order.order_id not in engine._pending_orders
        assert order.status == OrderStatus.FILLED

    async def test_perform_health_check_without_broker(self):
        engine = LiveEngine(portfolio=FakePortfolio())
        engine._health = EngineHealth.OFFLINE
        await engine._perform_health_check()
        assert engine.health == EngineHealth.HEALTHY

    async def test_perform_health_check_connected_broker(self):
        broker = FakeBroker()
        broker._connected = True
        engine = LiveEngine(portfolio=FakePortfolio(), broker=broker)
        await engine._perform_health_check()
        assert engine.health == EngineHealth.HEALTHY


class TestCreateLiveEngine:
    def test_factory_default_mode(self):
        engine = create_live_engine(FakePortfolio())
        assert engine.mode == LiveTradingMode.PAPER

    def test_factory_live_mode(self):
        engine = create_live_engine(FakePortfolio(), mode="live")
        assert engine.mode == LiveTradingMode.LIVE

    def test_factory_unknown_mode_falls_back(self):
        engine = create_live_engine(FakePortfolio(), mode="bogus")
        assert engine.mode == LiveTradingMode.PAPER

    def test_factory_passes_config_kwargs(self):
        engine = create_live_engine(FakePortfolio(), max_daily_trades=5)
        assert engine._config.max_daily_trades == 5
