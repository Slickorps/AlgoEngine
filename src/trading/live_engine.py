"""
Live trading engine for AlgoEngine - connects real broker accounts
and provides real-time risk management for production trading.
"""

import asyncio
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type
)

from .models import (
    Order, OrderType, OrderSide, OrderStatus, Fill, Trade,
    Position, CommissionModel, SlippageModel, TimeInForce
)
from .order_manager import OrderManager
from .position_manager import PositionManager
from .execution_engine import ExecutionEngine, BrokerAdapter
from ..data.models import Symbol, Tick, Bar, MarketData
from ..engine.events import Event, EventBus, EventType, get_event_bus
from ..engine.interfaces import IPortfolio
from ..risk.risk_manager import RiskManager, RiskContext
from ..utils.logger import get_logger
from ..utils.error_handler import (
    CircuitBreaker,
    CircuitBreakerConfig,
    RetryConfig,
    retry_async,
)

logger = get_logger("trading.live_engine")


class LiveTradingMode(Enum):
    """Operating modes for live trading"""
    PAPER = "paper"              # Simulated trades with live data
    LIVE = "live"                # Real trades with real money
    WARMUP = "warmup"            # Warmup mode - no trading
    STANDBY = "standby"          # Connected but not trading


class EngineHealth(Enum):
    """Health status of the live engine"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    OFFLINE = "offline"


@dataclass
class LiveEngineConfig:
    """Configuration for live trading engine"""
    broker_timeout: float = 10.0          # Broker API timeout in seconds
    max_retry_attempts: int = 3            # Max retry for broker operations
    health_check_interval: float = 5.0     # Health check frequency in seconds
    position_sync_interval: float = 30.0   # Position sync frequency
    order_sync_interval: float = 10.0      # Order sync frequency
    max_pending_orders: int = 50           # Max pending orders allowed
    max_daily_trades: int = 100            # Daily trade limit
    max_daily_volume: Decimal = Decimal("1000000")  # Daily volume limit
    circuit_breaker_loss_pct: float = 5.0  # Circuit breaker trigger %
    circuit_breaker_cooldown: float = 300.0  # Cooldown period in seconds
    allow_partial_fills: bool = True       # Allow partial order fills
    auto_reconnect: bool = True            # Auto-reconnect on disconnect
    reconnect_max_attempts: int = 10       # Max reconnect attempts
    reconnect_delay: float = 1.0           # Initial reconnect delay
    reconnect_backoff: float = 2.0         # Exponential backoff factor

    # Risk limits
    max_position_size_pct: float = 10.0    # Max position as % of portfolio
    max_total_exposure_pct: float = 100.0  # Max total exposure
    max_drawdown_pct: float = 20.0         # Max drawdown before halt
    max_concentration_pct: float = 25.0    # Max single symbol exposure
    min_cash_buffer: Decimal = Decimal("1000")  # Minimum cash buffer


@dataclass
class LiveTradingStats:
    """Statistics for live trading session"""
    session_start: datetime = field(default_factory=datetime.now)
    total_orders_submitted: int = 0
    total_orders_filled: int = 0
    total_orders_rejected: int = 0
    total_orders_cancelled: int = 0
    total_trades: int = 0
    total_volume: Decimal = Decimal("0")
    total_commission: Decimal = Decimal("0")
    total_slippage: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    peak_portfolio_value: Decimal = Decimal("0")
    current_drawdown_pct: float = 0.0
    connection_drops: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None
    is_circuit_breaker_active: bool = False
    circuit_breaker_triggered_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to dictionary"""
        return {
            "session_start": self.session_start.isoformat(),
            "total_orders_submitted": self.total_orders_submitted,
            "total_orders_filled": self.total_orders_filled,
            "total_orders_rejected": self.total_orders_rejected,
            "total_orders_cancelled": self.total_orders_cancelled,
            "total_trades": self.total_trades,
            "total_volume": float(self.total_volume),
            "total_commission": float(self.total_commission),
            "total_slippage": float(self.total_slippage),
            "net_pnl": float(self.net_pnl),
            "peak_portfolio_value": float(self.peak_portfolio_value),
            "current_drawdown_pct": self.current_drawdown_pct,
            "connection_drops": self.connection_drops,
            "is_circuit_breaker_active": self.is_circuit_breaker_active,
            "last_error": self.last_error
        }


class LiveTradeLogger:
    """
    Tracks and logs all live trade activity.
    Provides audit trail and real-time monitoring.
    """

    def __init__(self) -> None:
        self._trade_log: List[Dict[str, Any]] = []
        self._order_log: List[Dict[str, Any]] = []
        self._error_log: List[Dict[str, Any]] = []
        self._max_log_entries: int = 10000

    def log_order_submitted(self, order: Order) -> None:
        """Log order submission"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "ORDER_SUBMITTED",
            "order_id": order.order_id,
            "symbol": str(order.symbol),
            "side": order.side.name,
            "type": order.order_type.name,
            "quantity": float(order.quantity),
            "limit_price": float(order.limit_price) if order.limit_price else None,
            "stop_price": float(order.stop_price) if order.stop_price else None
        }
        self._add_to_log(self._order_log, entry)
        logger.info(f"LIVE ORDER: {order.side.name} {order.quantity} "
                    f"{order.symbol.ticker} @ {order.order_type.name}")

    def log_order_fill(self, order: Order, fill: Fill) -> None:
        """Log order fill"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "ORDER_FILLED",
            "order_id": order.order_id,
            "fill_id": fill.fill_id,
            "symbol": str(order.symbol),
            "side": order.side.name,
            "quantity": float(fill.quantity),
            "fill_price": float(fill.fill_price),
            "commission": float(fill.commission),
            "slippage": float(fill.slippage),
            "filled_quantity": float(order.filled_quantity),
            "remaining_quantity": float(order.remaining_quantity)
        }
        self._add_to_log(self._trade_log, entry)
        logger.info(f"LIVE FILL: {fill.side.name} {fill.quantity} "
                    f"{fill.symbol.ticker} @ {fill.fill_price} "
                    f"(Commission: {fill.commission})")

    def log_order_rejected(self, order: Order, reason: str) -> None:
        """Log order rejection"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "ORDER_REJECTED",
            "order_id": order.order_id,
            "symbol": str(order.symbol),
            "reason": reason
        }
        self._add_to_log(self._order_log, entry)
        logger.warning(f"LIVE REJECTED: {order.side.name} {order.quantity} "
                       f"{order.symbol.ticker} - {reason}")

    def log_error(self, source: str, error: Exception, context: str = "") -> None:
        """Log error event"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "context": context
        }
        self._add_to_log(self._error_log, entry)
        logger.error(f"LIVE ERROR [{source}]: {error}")

    def _add_to_log(self, log_list: list, entry: Dict[str, Any]) -> None:
        """Add entry to log with size management"""
        log_list.append(entry)
        if len(log_list) > self._max_log_entries:
            log_list.pop(0)

    def get_recent_trades(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get most recent trade entries"""
        return self._trade_log[-count:]

    def get_recent_orders(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get most recent order entries"""
        return self._order_log[-count:]

    def get_recent_errors(self, count: int = 20) -> List[Dict[str, Any]]:
        """Get most recent error entries"""
        return self._error_log[-count:]

    def clear_logs(self) -> None:
        """Clear all logs"""
        self._trade_log.clear()
        self._order_log.clear()
        self._error_log.clear()


class LiveEngine:
    """
    Live trading engine for production use.
    Manages broker connection, order execution, and risk controls.
    """

    def __init__(
        self,
        portfolio: IPortfolio,
        broker: Optional[BrokerAdapter] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        config: Optional[LiveEngineConfig] = None,
        mode: LiveTradingMode = LiveTradingMode.PAPER
    ) -> None:
        self._portfolio = portfolio
        self._broker = broker
        self._execution_engine = execution_engine or ExecutionEngine(broker=broker)
        self._config = config or LiveEngineConfig()
        self._mode = mode
        self._event_bus = get_event_bus()

        # Core components
        self._risk_manager: Optional[RiskManager] = None
        self._trade_logger = LiveTradeLogger()
        self._circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=5,
                cooldown_seconds=self._config.circuit_breaker_cooldown,
            ),
            name="live_engine",
        )
        self._stats = LiveTradingStats()

        # State management
        self._running: bool = False
        self._health = EngineHealth.OFFLINE
        self._connected: bool = False
        self._daily_trade_count: int = 0
        self._daily_volume: Decimal = Decimal("0")
        self._daily_reset_time: Optional[datetime] = None
        self._pending_orders: Dict[str, Order] = {}
        self._symbol_subscriptions: Set[Symbol] = set()
        self._last_health_check: Optional[datetime] = None

        # Background tasks
        self._tasks: List[asyncio.Task] = []
        self._maintenance_lock = asyncio.Lock()

        # Callbacks
        self._on_order_handlers: List[Callable[[Order], None]] = []
        self._on_fill_handlers: List[Callable[[Fill], None]] = []
        self._on_error_handlers: List[Callable[[Exception], None]] = []
        self._on_health_change: List[Callable[[EngineHealth], None]] = []
        self._on_circuit_breaker: List[Callable[[str], None]] = []

        # Fire callbacks when the unified circuit breaker changes state
        self._circuit_breaker.on_open.append(self._on_cb_open)

        logger.info(f"LiveEngine initialized in {mode.value} mode")

    def _on_cb_open(self, name: str) -> None:
        """Handle circuit breaker opening — fire registered callbacks."""
        self._stats.is_circuit_breaker_active = True
        self._stats.circuit_breaker_triggered_at = datetime.now()
        for handler in self._on_circuit_breaker:
            try:
                handler(name)
            except Exception as e:
                logger.error(f"Error in circuit breaker handler: {e}")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> LiveTradingMode:
        """Get current trading mode"""
        return self._mode

    @property
    def health(self) -> EngineHealth:
        """Get engine health status"""
        return self._health

    @property
    def stats(self) -> LiveTradingStats:
        """Get trading statistics"""
        return self._stats

    @property
    def is_running(self) -> bool:
        """Check if engine is running"""
        return self._running

    @property
    def is_connected(self) -> bool:
        """Check if broker is connected"""
        return self._connected

    @property
    def order_manager(self) -> OrderManager:
        """Get order manager"""
        return self._execution_engine.order_manager

    @property
    def position_manager(self) -> PositionManager:
        """Get position manager"""
        return self._execution_engine.position_manager

    @property
    def trade_logger(self) -> LiveTradeLogger:
        """Get trade logger"""
        return self._trade_logger

    # ------------------------------------------------------------------
    # Lifecycle Management
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the live trading engine"""
        if self._running:
            logger.warning("LiveEngine is already running")
            return True

        logger.info(f"Starting live engine in {self._mode.value} mode")

        try:
            self._running = True
            self._stats.session_start = datetime.now()

            # Connect to broker
            if not await self._connect_broker():
                self._running = False
                return False

            # Start maintenance tasks
            self._tasks = [
                asyncio.create_task(self._health_check_loop()),
                asyncio.create_task(self._position_sync_loop()),
                asyncio.create_task(self._order_sync_loop()),
                asyncio.create_task(self._daily_reset_loop())
            ]

            self._health = EngineHealth.HEALTHY
            self._event_bus.emit(
                Event(
                    event_type=EventType.START,
                    timestamp=datetime.now(),
                    data={"mode": self._mode.value}
                )
            )

            logger.info(f"Live engine started successfully in {self._mode.value} mode")
            return True

        except Exception as e:
            self._health = EngineHealth.OFFLINE
            self._running = False
            self._trade_logger.log_error("engine.start", e)
            logger.error(f"Failed to start live engine: {e}")
            return False

    async def stop(self) -> None:
        """Stop the live trading engine"""
        if not self._running:
            return

        logger.info("Stopping live engine...")
        self._running = False

        # Cancel all background tasks
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Cancel pending orders
        if self._config.auto_reconnect and self._pending_orders:
            logger.info(f"Cancelling {len(self._pending_orders)} pending orders...")
            for order in list(self._pending_orders.values()):
                await self._cancel_order(order)

        # Disconnect broker
        await self._disconnect_broker()

        self._health = EngineHealth.OFFLINE
        self._event_bus.emit(
            Event(
                event_type=EventType.STOP,
                timestamp=datetime.now(),
                data={"mode": self._mode.value}
            )
        )

        logger.info("Live engine stopped")

    def pause(self) -> None:
        """Pause trading (no new orders)"""
        if self._running and self._mode == LiveTradingMode.LIVE:
            self._mode = LiveTradingMode.STANDBY
            logger.info("Trading paused - engine in STANDBY mode")
            self._event_bus.emit(
                Event(
                    event_type=EventType.PAUSE,
                    timestamp=datetime.now()
                )
            )

    def resume(self) -> None:
        """Resume trading"""
        if self._running and self._mode == LiveTradingMode.STANDBY:
            self._mode = LiveTradingMode.LIVE
            logger.info("Trading resumed - engine in LIVE mode")
            self._event_bus.emit(
                Event(
                    event_type=EventType.RESUME,
                    timestamp=datetime.now()
                )
            )

    def set_risk_manager(self, risk_manager: RiskManager) -> None:
        """Set risk manager instance"""
        self._risk_manager = risk_manager
        logger.info("Risk manager attached to live engine")

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    async def _connect_broker(self) -> bool:
        """Connect to the broker"""
        if not self._broker:
            if self._mode == LiveTradingMode.LIVE:
                logger.error("No broker configured for LIVE mode")
                return False
            logger.info("No broker - running in PAPER mode")
            self._connected = True
            return True

        try:
            logger.info(f"Connecting to broker...")
            connected = await asyncio.wait_for(
                self._broker.connect(),
                timeout=self._config.broker_timeout
            )

            if connected:
                self._connected = True
                self._health = EngineHealth.HEALTHY
                logger.info("Connected to broker")
            else:
                self._connected = False
                self._health = EngineHealth.CRITICAL
                logger.error("Failed to connect to broker")

            return connected

        except asyncio.TimeoutError:
            logger.error(f"Broker connection timeout ({self._config.broker_timeout}s)")
            self._health = EngineHealth.CRITICAL
            self._connected = False
            return False

        except Exception as e:
            self._trade_logger.log_error("broker.connect", e)
            self._health = EngineHealth.CRITICAL
            self._connected = False
            return False

    async def _disconnect_broker(self) -> None:
        """Disconnect from broker"""
        if self._broker:
            try:
                await self._broker.disconnect()
                logger.info("Disconnected from broker")
            except Exception as e:
                self._trade_logger.log_error("broker.disconnect", e)
        self._connected = False

    async def _reconnect_broker(self) -> bool:
        """Attempt to reconnect to broker (uses unified retry with backoff)."""
        if not self._config.auto_reconnect or not self._broker:
            return False

        logger.info("Attempting broker reconnection...")
        self._stats.connection_drops += 1

        try:
            await retry_async(
                self._do_single_reconnect,
                config=RetryConfig(
                    max_attempts=self._config.reconnect_max_attempts,
                    base_delay=self._config.reconnect_delay,
                    backoff_factor=self._config.reconnect_backoff,
                    max_delay=60.0,
                    jitter=True,
                ),
                circuit_breaker=self._circuit_breaker,
                context="broker.reconnect",
            )
            logger.info("Reconnected to broker")
            return True
        except Exception:
            logger.error("All reconnection attempts failed")
            self._health = EngineHealth.OFFLINE
            return False

    async def _do_single_reconnect(self) -> bool:
        """Perform a single broker connection attempt (raises on failure)."""
        if await self._connect_broker():
            return True
        raise ConnectionError("Broker connection attempt returned False")

    # ------------------------------------------------------------------
    # Order Execution
    # ------------------------------------------------------------------

    async def submit_order(
        self,
        order: Order,
        check_risk: bool = True
    ) -> Tuple[bool, str]:
        """
        Submit an order to the live engine.

        Args:
            order: Order to submit
            check_risk: Whether to perform risk checks

        Returns:
            Tuple of (success: bool, message: str)
        """
        if not self._running:
            return False, "Engine is not running"

        if self._mode == LiveTradingMode.WARMUP:
            return False, "Engine is in warmup mode"

        if self._circuit_breaker.is_active:
            return False, "Circuit breaker is active - trading paused"

        if self._health in [EngineHealth.CRITICAL, EngineHealth.OFFLINE]:
            return False, f"Engine health is {self._health.value}"

        # Check daily limits
        if self._daily_trade_count >= self._config.max_daily_trades:
            return False, f"Daily trade limit ({self._config.max_daily_trades}) reached"

        new_volume = order.quantity * (order.limit_price or Decimal("1"))
        if self._daily_volume + new_volume > self._config.max_daily_volume:
            return False, f"Daily volume limit ({self._config.max_daily_volume}) would be exceeded"

        # Check pending order limit
        if len(self._pending_orders) >= self._config.max_pending_orders:
            return False, f"Max pending orders ({self._config.max_pending_orders}) reached"

        # Risk check
        if check_risk and self._risk_manager and self._mode == LiveTradingMode.LIVE:
            price = order.limit_price or Decimal("100")  # Use estimated price
            passed, reason = self._risk_manager.check_order(order, price)
            if not passed:
                self._trade_logger.log_order_rejected(order, reason)
                self._stats.total_orders_rejected += 1
                return False, reason

        try:
            # Submit to execution engine
            success = await self._execution_engine.submit_order(order)

            if success:
                self._pending_orders[order.order_id] = order
                self._stats.total_orders_submitted += 1
                self._daily_trade_count += 1
                self._daily_volume += new_volume

                # Log the order
                self._trade_logger.log_order_submitted(order)

                # Emit event
                self._event_bus.emit(
                    Event(
                        event_type=EventType.ORDER_SUBMITTED,
                        timestamp=datetime.now(),
                        data={
                            "order_id": order.order_id,
                            "symbol": str(order.symbol),
                            "side": order.side.name,
                            "quantity": float(order.quantity),
                            "order_type": order.order_type.name
                        }
                    )
                )

                # Notify callbacks
                for handler in self._on_order_handlers:
                    try:
                        handler(order)
                    except Exception as e:
                        logger.error(f"Error in order handler: {e}")

                return True, "Order submitted successfully"

            else:
                self._trade_logger.log_order_rejected(order, "Broker rejected order")
                self._stats.total_orders_rejected += 1
                return False, "Broker rejected the order"

        except Exception as e:
            self._trade_logger.log_error("order.submit", e, str(order))
            self._circuit_breaker.record_failure()
            self._stats.total_orders_rejected += 1
            return False, f"Error submitting order: {e}"

    async def _cancel_order(self, order: Order) -> bool:
        """Cancel a single order"""
        try:
            success = await self._execution_engine.cancel_order(order.order_id)
            if success:
                self._pending_orders.pop(order.order_id, None)
                self._stats.total_orders_cancelled += 1

                self._event_bus.emit(
                    Event(
                        event_type=EventType.ORDER_CANCELLED,
                        timestamp=datetime.now(),
                        data={
                            "order_id": order.order_id,
                            "symbol": str(order.symbol)
                        }
                    )
                )

            return success

        except Exception as e:
            self._trade_logger.log_error("order.cancel", e, order.order_id)
            return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID"""
        order = self._pending_orders.get(order_id)
        if not order:
            order = self.order_manager.get_order(order_id)
        if not order:
            logger.warning(f"Order {order_id} not found for cancellation")
            return False

        return await self._cancel_order(order)

    async def cancel_all_orders(
        self,
        symbol: Optional[Symbol] = None
    ) -> int:
        """Cancel all pending orders"""
        orders_to_cancel = [
            o for o in self._pending_orders.values()
            if symbol is None or o.symbol == symbol
        ]

        cancelled = 0
        for order in orders_to_cancel:
            if await self._cancel_order(order):
                cancelled += 1

        logger.info(f"Cancelled {cancelled} orders")
        return cancelled

    # ------------------------------------------------------------------
    # Market Data Processing
    # ------------------------------------------------------------------

    def process_market_data(self, data: MarketData) -> None:
        """Process incoming market data"""
        if not self._running:
            return

        try:
            if isinstance(data, Tick):
                self._execution_engine.process_tick(data)

                # Update circuit breaker with current portfolio state
                current_value = self._portfolio.total_value
                if current_value > self._stats.peak_portfolio_value:
                    self._stats.peak_portfolio_value = current_value

                # Calculate drawdown
                if self._stats.peak_portfolio_value > 0:
                    drawdown = (
                        (self._stats.peak_portfolio_value - current_value) /
                        self._stats.peak_portfolio_value * 100
                    )
                    self._stats.current_drawdown_pct = float(drawdown)

                    # Check circuit breaker – trip if drawdown exceeds threshold
                    if (
                        self._stats.current_drawdown_pct
                        >= self._config.circuit_breaker_loss_pct
                    ):
                        self._circuit_breaker.trip(
                            f"Drawdown {self._stats.current_drawdown_pct:.2f}% "
                            f"exceeds {self._config.circuit_breaker_loss_pct}% threshold"
                        )

            elif isinstance(data, Bar):
                # Handle bar data for strategy updates
                pass

        except Exception as e:
            self._trade_logger.log_error("market_data", e)

    def on_fill(self, fill: Fill) -> None:
        """Handle order fill from execution engine"""
        try:
            order = self._pending_orders.pop(fill.order_id, None)
            if order is None:
                order = self.order_manager.get_order(fill.order_id)

            if order:
                self._trade_logger.log_order_fill(order, fill)
                self._stats.total_orders_filled += 1
                self._stats.total_volume += fill.quantity

                # Update costs
                self._stats.total_commission += fill.commission
                self._stats.total_slippage += fill.slippage

                # Emit fill event
                self._event_bus.emit(
                    Event(
                        event_type=EventType.ORDER_FILLED,
                        timestamp=datetime.now(),
                        data={
                            "order_id": fill.order_id,
                            "fill_id": fill.fill_id,
                            "symbol": str(fill.symbol),
                            "side": fill.side.name,
                            "quantity": float(fill.quantity),
                            "fill_price": float(fill.fill_price),
                            "commission": float(fill.commission),
                            "slippage": float(fill.slippage)
                        }
                    )
                )

                # Notify handlers
                for handler in self._on_fill_handlers:
                    try:
                        handler(fill)
                    except Exception as e:
                        logger.error(f"Error in fill handler: {e}")

        except Exception as e:
            self._trade_logger.log_error("on_fill", e, str(fill))

    # ------------------------------------------------------------------
    # Risk Control
    # ------------------------------------------------------------------

    def _check_order_risk(
        self,
        order: Order,
        price: Decimal
    ) -> Tuple[bool, str]:
        """Perform risk checks for an order"""
        portfolio_value = self._portfolio.total_value

        # Check drawdown limit
        if self._stats.current_drawdown_pct >= self._config.max_drawdown_pct:
            return False, (
                f"Trading halted: drawdown {self._stats.current_drawdown_pct:.2f}% "
                f"exceeds {self._config.max_drawdown_pct}% limit"
            )

        # Check position size
        order_value = order.quantity * price
        if portfolio_value > 0:
            position_pct = float(order_value) / float(portfolio_value) * 100
            if position_pct > self._config.max_position_size_pct:
                return False, (
                    f"Position size {position_pct:.2f}% exceeds "
                    f"{self._config.max_position_size_pct}% limit"
                )

        # Check cash buffer
        if self._mode == LiveTradingMode.LIVE:
            cash = self._portfolio.get_cash()
            if cash < self._config.min_cash_buffer:
                return False, (
                    f"Cash ${cash:.2f} below minimum buffer "
                    f"${self._config.min_cash_buffer}"
                )

        return True, "Risk check passed"

    # ------------------------------------------------------------------
    # Background Tasks
    # ------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Periodic health check of broker connection"""
        while self._running:
            try:
                await asyncio.sleep(self._config.health_check_interval)
                await self._perform_health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def _perform_health_check(self) -> None:
        """Check broker connection health"""
        previous_health = self._health

        if self._broker:
            try:
                is_connected = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, self._broker.is_connected
                    ),
                    timeout=5.0
                )

                if is_connected:
                    self._health = EngineHealth.HEALTHY
                    self._connected = True
                else:
                    self._health = EngineHealth.DEGRADED
                    self._connected = False

                    # Attempt reconnection
                    if self._config.auto_reconnect:
                        await self._reconnect_broker()

            except asyncio.TimeoutError:
                self._health = EngineHealth.DEGRADED
                logger.warning("Health check timeout")
            except Exception as e:
                self._health = EngineHealth.DEGRADED
                self._trade_logger.log_error("health_check", e)
        else:
            self._health = EngineHealth.HEALTHY
            self._connected = True

        # Notify on health change
        if previous_health != self._health:
            for handler in self._on_health_change:
                try:
                    handler(self._health)
                except Exception as e:
                    logger.error(f"Error in health change handler: {e}")

                    self._event_bus.emit(
                        Event(
                            event_type=EventType.WEBSOCKET_MESSAGE,
                            timestamp=datetime.now(),
                            data={
                                "type": "health_change",
                                "previous": previous_health.value,
                                "current": self._health.value
                            }
                        )
                    )

    async def _position_sync_loop(self) -> None:
        """Periodic position synchronization with broker"""
        while self._running:
            try:
                await asyncio.sleep(self._config.position_sync_interval)
                await self._sync_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Position sync error: {e}")

    async def _sync_positions(self) -> None:
        """Synchronize positions with broker"""
        if not self._connected or not self._broker:
            return

        try:
            # Get positions from broker
            broker_positions = await self._broker.get_positions()

            # Compare with local positions and update
            for broker_pos in broker_positions:
                local_pos = self.position_manager.get_position(broker_pos.symbol)
                if self._positions_differ(local_pos, broker_pos):
                    self._reconcile_position(broker_pos)

            logger.debug(f"Position sync completed: {len(broker_positions)} positions")

        except Exception as e:
            self._trade_logger.log_error("position_sync", e)

    async def _order_sync_loop(self) -> None:
        """Periodic order status synchronization with broker"""
        while self._running:
            try:
                await asyncio.sleep(self._config.order_sync_interval)
                await self._sync_orders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order sync error: {e}")

    async def _sync_orders(self) -> None:
        """Synchronize order status with broker"""
        if not self._connected or not self._broker:
            return

        try:
            if len(self._pending_orders) == 0:
                return

            broker_orders = await self._broker.get_orders()
            broker_order_map = {o.broker_order_id: o for o in broker_orders if o.broker_order_id}

            for order_id, local_order in list(self._pending_orders.items()):
                broker_order = broker_order_map.get(order_id)
                if broker_order:
                    # Update local order status
                    if broker_order.status != local_order.status:
                        logger.info(
                            f"Order {order_id} status changed: "
                            f"{local_order.status.name} -> {broker_order.status.name}"
                        )
                        local_order.status = broker_order.status

                        if broker_order.status in [
                            OrderStatus.FILLED,
                            OrderStatus.CANCELLED,
                            OrderStatus.REJECTED,
                            OrderStatus.EXPIRED
                        ]:
                            self._pending_orders.pop(order_id, None)

            logger.debug(f"Order sync completed: {len(self._pending_orders)} pending")

        except Exception as e:
            self._trade_logger.log_error("order_sync", e)

    async def _daily_reset_loop(self) -> None:
        """Daily reset of trade counters"""
        while self._running:
            try:
                now = datetime.now()
                next_reset = (
                    now.replace(hour=0, minute=0, second=0, microsecond=0)
                    + timedelta(days=1)
                )
                sleep_seconds = (next_reset - now).total_seconds()
                await asyncio.sleep(sleep_seconds)

                # Reset daily counters
                self._daily_trade_count = 0
                self._daily_volume = Decimal("0")
                self._daily_reset_time = datetime.now()

                logger.info("Daily trade counters reset")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Daily reset error: {e}")

    # ------------------------------------------------------------------
    # Position Reconciliation
    # ------------------------------------------------------------------

    def _positions_differ(
        self,
        local_pos: Optional[Position],
        broker_pos: Any
    ) -> bool:
        """Check if local and broker positions differ"""
        if local_pos is None and broker_pos is not None:
            return True
        if local_pos is not None and broker_pos is None:
            return True
        if local_pos is None and broker_pos is None:
            return False

        # Compare key fields (simplified)
        if hasattr(broker_pos, 'quantity'):
            if local_pos.quantity != broker_pos.quantity:
                return True
        if hasattr(broker_pos, 'avg_entry_price'):
            if local_pos.avg_entry_price != broker_pos.avg_entry_price:
                return True

        return False

    def _reconcile_position(self, broker_pos: Any) -> None:
        """Reconcile position difference"""
        logger.warning(
            f"Position mismatch detected for {broker_pos.symbol}: "
            f"reconciling with broker state"
        )

        # Log the discrepancy
        self._trade_logger.log_error(
            "position_reconciliation",
            Exception("Position mismatch"),
            f"Symbol: {broker_pos.symbol.ticker}, "
            f"Broker Qty: {getattr(broker_pos, 'quantity', 'N/A')}"
        )

        # In production, would update local position to match broker
        # This is a simplified version

    # ------------------------------------------------------------------
    # Callback Management
    # ------------------------------------------------------------------

    def on_order(self, handler: Callable[[Order], None]) -> None:
        """Register order handler"""
        self._on_order_handlers.append(handler)

    def on_fill(self, handler: Callable[[Fill], None]) -> None:
        """Register fill handler"""
        self._on_fill_handlers.append(handler)

    def on_error(self, handler: Callable[[Exception], None]) -> None:
        """Register error handler"""
        self._on_error_handlers.append(handler)

    def on_health_change(self, handler: Callable[[EngineHealth], None]) -> None:
        """Register health change handler"""
        self._on_health_change.append(handler)

    def on_circuit_breaker(self, handler: Callable[[str], None]) -> None:
        """Register circuit breaker handler"""
        self._on_circuit_breaker.append(handler)

    # ------------------------------------------------------------------
    # Utility Methods
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive engine status"""
        return {
            "mode": self._mode.value,
            "health": self._health.value,
            "running": self._running,
            "connected": self._connected,
            "circuit_breaker_active": self._circuit_breaker.is_active,
            "pending_orders": len(self._pending_orders),
            "daily_trade_count": self._daily_trade_count,
            "daily_trade_limit": self._config.max_daily_trades,
            "daily_volume": float(self._daily_volume),
            "stats": self._stats.to_dict(),
            "portfolio": self._portfolio.get_summary(),
            "uptime": str(datetime.now() - self._stats.session_start)
        }

    def get_recent_activity(
        self,
        count: int = 20
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get recent trading activity"""
        return {
            "trades": self._trade_logger.get_recent_trades(count),
            "orders": self._trade_logger.get_recent_orders(count),
            "errors": self._trade_logger.get_recent_errors(count)
        }


# ------------------------------------------------------------------
# Factory Function
# ------------------------------------------------------------------

def create_live_engine(
    portfolio: IPortfolio,
    broker: Optional[BrokerAdapter] = None,
    mode: str = "paper",
    **kwargs: Any
) -> LiveEngine:
    """
    Create a live trading engine.

    Args:
        portfolio: Portfolio instance
        broker: Optional broker adapter
        mode: Trading mode ('paper', 'live', 'warmup', 'standby')
        **kwargs: Additional LiveEngineConfig parameters

    Returns:
        Configured LiveEngine instance
    """
    mode_map = {
        "paper": LiveTradingMode.PAPER,
        "live": LiveTradingMode.LIVE,
        "warmup": LiveTradingMode.WARMUP,
        "standby": LiveTradingMode.STANDBY
    }

    trading_mode = mode_map.get(mode.lower(), LiveTradingMode.PAPER)

    config = LiveEngineConfig(**{
        k: v for k, v in kwargs.items()
        if hasattr(LiveEngineConfig, k)
    })

    engine = LiveEngine(
        portfolio=portfolio,
        broker=broker,
        config=config,
        mode=trading_mode
    )

    return engine