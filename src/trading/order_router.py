"""Order routing system for AlgoEngine

Intelligently distributes orders across multiple broker adapters using
configurable routing strategies, broker selection algorithms, order splitting,
and performance tracking.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional,
)

from .models import (
    Order,
)
from .execution_engine import BrokerAdapter
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("trading.order_router")


# ---------------------------------------------------------------------------
# Routing strategy enum
# ---------------------------------------------------------------------------

class RoutingStrategy(Enum):
    """Available order routing strategies"""
    COST_BASED = auto()       # Route to broker with lowest commission
    LATENCY_BASED = auto()    # Route to broker with lowest latency
    FILL_RATE_BASED = auto()  # Route to broker with highest fill rate
    SPLIT = auto()            # Split order across multiple brokers
    FIXED = auto()            # Always route to a fixed broker
    ROUND_ROBIN = auto()      # Cycle through brokers
    VOLUME_WEIGHTED = auto()  # Weight by historical fill volume


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RouterConfig:
    """Configuration for the order router"""
    default_strategy: RoutingStrategy = RoutingStrategy.COST_BASED
    fallback_strategy: RoutingStrategy = RoutingStrategy.SPLIT

    # Split thresholds
    min_split_quantity: Decimal = Decimal("100")     # Order sizes below this are not split
    max_split_parts: int = 5                         # Maximum parts when splitting
    split_equal: bool = True                         # Split equally vs proportionally

    # Failure / timeout
    broker_timeout: float = 5.0                      # Per-broker timeout (seconds)
    max_retries: int = 2                             # Retry attempts per broker
    failover: bool = True                            # Automatically failover on error

    # Round-robin state
    round_robin_index: int = 0                       # Internal pointer for round-robin

    # Fixed broker
    fixed_broker_name: Optional[str] = None          # Used when strategy == FIXED

    # Cost caps
    max_commission_pct: Decimal = Decimal("0.1")     # Max commission as % of order value
    max_slippage_pct: Decimal = Decimal("0.05")      # Max allowable slippage %

    # Performance window
    metrics_window_seconds: float = 300.0            # Rolling window for broker metrics


@dataclass
class BrokerMetrics:
    """Performance metrics for a single broker"""
    name: str = ""
    total_orders: int = 0
    filled_orders: int = 0
    rejected_orders: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    total_commission: Decimal = Decimal("0")
    avg_commission_per_order: Decimal = Decimal("0")
    fill_rate: float = 0.0
    last_updated: Optional[datetime] = None
    is_connected: bool = False

    def record_submit(self, latency_ms: float) -> None:
        """Record an order submission with its latency"""
        self.total_orders += 1
        self.total_latency_ms += latency_ms
        self.avg_latency_ms = (
            self.total_latency_ms / self.total_orders
            if self.total_orders else 0.0
        )
        self.last_updated = datetime.now()

    def record_fill(self, commission: Decimal) -> None:
        """Record a filled order"""
        self.filled_orders += 1
        self.total_commission += commission
        self.avg_commission_per_order = (
            self.total_commission / self.filled_orders
            if self.filled_orders else Decimal("0")
        )
        self.fill_rate = (
            self.filled_orders / self.total_orders
            if self.total_orders else 0.0
        )

    def record_rejection(self) -> None:
        """Record a rejected order"""
        self.rejected_orders += 1
        self.fill_rate = (
            self.filled_orders / self.total_orders
            if self.total_orders else 0.0
        )

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as a dictionary"""
        return {
            "name": self.name,
            "total_orders": self.total_orders,
            "filled_orders": self.filled_orders,
            "rejected_orders": self.rejected_orders,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "avg_commission": float(self.avg_commission_per_order),
            "fill_rate": round(self.fill_rate, 4),
            "is_connected": self.is_connected,
            "last_updated": (
                self.last_updated.isoformat() if self.last_updated else None
            ),
        }


@dataclass
class RoutingRule:
    """Conditional routing rule"""
    conditions: Dict[str, Any]           # Key-value conditions to match
    strategy: RoutingStrategy            # Strategy to apply when matched
    priority: int = 0                    # Higher = evaluated first
    description: str = ""

    def matches(self, context: Dict[str, Any]) -> bool:
        """Check if this rule matches the given context"""
        for key, value in self.conditions.items():
            ctx_value = context.get(key)
            if ctx_value != value:
                return False
        return True


@dataclass
class RoutingResult:
    """Result of a routing operation"""
    orders: List[Order]
    broker_name: str
    strategy_used: RoutingStrategy
    success: bool = True
    error_message: Optional[str] = None
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Order split utility
# ---------------------------------------------------------------------------

def _split_order(
    order: Order,
    num_parts: int,
    equal: bool = True,
    weights: Optional[List[float]] = None,
) -> List[Order]:
    """Split a single order into multiple sub-orders.

    Parameters
    ----------
    order : Order
        The original order to split.
    num_parts : int
        Number of sub-orders to create.
    equal : bool
        If True, split quantities equally. Otherwise use weights.
    weights : list[float] or None
        Proportional weights (sum should equal 1.0). Required if equal=False.

    Returns
    -------
    list[Order]
        Sub-orders, each with a unique order_id.
    """
    if num_parts <= 1:
        return [order]

    parts: List[Order] = []

    if equal:
        part_qty = order.quantity / num_parts
        for i in range(num_parts):
            part = _copy_order_with_quantity(order, part_qty)
            part.tags["split_part"] = f"{i + 1}/{num_parts}"
            part.tags["parent_order_id"] = order.order_id
            parts.append(part)
    else:
        if not weights or len(weights) != num_parts:
            raise ValueError(
                "weights must be provided with length equal to num_parts "
                "when equal=False"
            )
        total_w = sum(weights)
        remaining = order.quantity
        for i in range(num_parts - 1):
            qty = order.quantity * Decimal(str(weights[i] / total_w))
            qty = max(qty, Decimal("1"))
            part = _copy_order_with_quantity(order, qty)
            part.tags["split_part"] = f"{i + 1}/{num_parts}"
            part.tags["parent_order_id"] = order.order_id
            parts.append(part)
            remaining -= qty
        # Last part gets the remainder
        last = _copy_order_with_quantity(order, max(remaining, Decimal("0")))
        last.tags["split_part"] = f"{num_parts}/{num_parts}"
        last.tags["parent_order_id"] = order.order_id
        parts.append(last)

    return parts


def _copy_order_with_quantity(order: Order, quantity: Decimal) -> Order:
    """Create a shallow copy of an order with a new quantity and ID."""
    copy = Order(
        symbol=order.symbol,
        side=order.side,
        quantity=quantity,
        order_type=order.order_type,
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        time_in_force=order.time_in_force,
        client_order_id=order.client_order_id,
        strategy_id=order.strategy_id,
    )
    # Carry over tags
    copy.tags = dict(order.tags)
    return copy


# ---------------------------------------------------------------------------
# Order router
# ---------------------------------------------------------------------------

class OrderRouter:
    """Intelligent order routing across multiple broker adapters.

    Supports configurable strategies including cost-based, latency-based,
    fill-rate-based, split, round-robin, and fixed routing.

    Example usage::

        router = OrderRouter()
        router.register_broker("oanda", oanda_broker)
        router.register_broker("sim", simulated_broker)

        results = await router.route_order(my_order)
    """

    def __init__(
        self,
        config: Optional[RouterConfig] = None,
    ) -> None:
        self._config = config or RouterConfig()
        self._brokers: Dict[str, BrokerAdapter] = {}
        self._metrics: Dict[str, BrokerMetrics] = {}
        self._rules: List[RoutingRule] = []

        # Event hooks
        self._on_route_callbacks: List[Callable[[RoutingResult], None]] = []
        self._on_error_callbacks: List[Callable[[str, Exception], None]] = []

        # Internal lock for safe metric updates
        self._lock = asyncio.Lock()

        logger.info(
            f"OrderRouter initialized (strategy={self._config.default_strategy.name})"
        )

    # ------------------------------------------------------------------
    # Broker management
    # ------------------------------------------------------------------

    def register_broker(
        self,
        name: str,
        broker: BrokerAdapter,
        initial_metrics: Optional[BrokerMetrics] = None,
    ) -> None:
        """Register a new broker for routing."""
        self._brokers[name] = broker
        self._metrics[name] = initial_metrics or BrokerMetrics(name=name)
        logger.info(
            f"Broker registered: {name} ({type(broker).__name__})"
        )

    def unregister_broker(self, name: str) -> None:
        """Remove a broker from routing."""
        self._brokers.pop(name, None)
        self._metrics.pop(name, None)
        logger.info(f"Broker unregistered: {name}")

    def get_broker_names(self) -> List[str]:
        """Return list of registered broker names."""
        return list(self._brokers.keys())

    def get_broker(self, name: str) -> Optional[BrokerAdapter]:
        """Get a specific broker adapter."""
        return self._brokers.get(name)

    # ------------------------------------------------------------------
    # Routing rules
    # ------------------------------------------------------------------

    def add_rule(self, rule: RoutingRule) -> None:
        """Add a conditional routing rule.

        Rules are evaluated in priority order (highest first) before the
        default strategy is applied.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: -r.priority)
        logger.info(
            f"Routing rule added: {rule.description} "
            f"(priority={rule.priority})"
        )

    def remove_rule(self, rule: RoutingRule) -> None:
        """Remove a routing rule."""
        if rule in self._rules:
            self._rules.remove(rule)

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def update_metrics(self, broker_name: str, metrics: BrokerMetrics) -> None:
        """Update broker performance metrics."""
        if broker_name in self._metrics:
            self._metrics[broker_name] = metrics

    def update_connectivity(self, broker_name: str, connected: bool) -> None:
        """Mark a broker as connected / disconnected."""
        if broker_name in self._metrics:
            self._metrics[broker_name].is_connected = connected

    def get_metrics(self, broker_name: Optional[str] = None) -> Dict[str, Any]:
        """Get metrics for a specific broker or all brokers.

        Returns
        -------
        dict
            Broker metrics keyed by broker name.
        """
        if broker_name:
            m = self._metrics.get(broker_name)
            return {broker_name: m.to_dict()} if m else {}
        return {name: m.to_dict() for name, m in self._metrics.items()}

    def _get_available_brokers(self) -> List[str]:
        """Return list of connected broker names."""
        return [
            name
            for name, m in self._metrics.items()
            if m.is_connected or self._brokers[name].is_connected()
        ]

    # ------------------------------------------------------------------
    # Routing logic
    # ------------------------------------------------------------------

    async def route_order(
        self,
        order: Order,
        context: Optional[Dict[str, Any]] = None,
        strategy: Optional[RoutingStrategy] = None,
    ) -> RoutingResult:
        """Route a single order to the best broker.

        Parameters
        ----------
        order : Order
            Order to route.
        context : dict or None
            Additional context for rule matching (symbol, account, etc.).
        strategy : RoutingStrategy or None
            Override the default strategy. Rules still take precedence.

        Returns
        -------
        RoutingResult
        """
        start = time.monotonic()

        try:
            # 1. Check conditional rules
            ctx = context or {}
            strategy = strategy or self._config.default_strategy

            for rule in self._rules:
                if rule.matches(ctx):
                    strategy = rule.strategy
                    logger.debug(
                        f"Rule matched: {rule.description} → "
                        f"{strategy.name}"
                    )
                    break

            # 2. Apply strategy
            if self._brokers:
                result = await self._apply_strategy(order, strategy)
            else:
                return RoutingResult(
                    orders=[order],
                    broker_name="none",
                    strategy_used=strategy,
                    success=False,
                    error_message="No brokers registered",
                )

            # 3. Notify
            elapsed = (time.monotonic() - start) * 1000
            result.latency_ms = elapsed

            for cb in self._on_route_callbacks:
                try:
                    cb(result)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Route callback error: {exc}")

            return result

        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            result = RoutingResult(
                orders=[order],
                broker_name="none",
                strategy_used=strategy if "strategy" in locals() else self._config.default_strategy,
                success=False,
                error_message=str(exc),
                latency_ms=elapsed,
            )
            for cb in self._on_error_callbacks:
                try:
                    cb("route_order", exc)
                except Exception:  # noqa: BLE001
                    pass
            return result

    async def route_batch(
        self,
        orders: List[Order],
        symbol: Optional[Symbol] = None,
        strategy: Optional[RoutingStrategy] = None,
    ) -> List[RoutingResult]:
        """Route a batch of orders concurrently.

        Returns
        -------
        list[RoutingResult]
            One result per order, in the same order as input.
        """
        tasks = [
            asyncio.create_task(
                self.route_order(
                    order,
                    context={"symbol": str(order.symbol)},
                    strategy=strategy,
                )
            )
            for order in orders
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[RoutingResult] = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                out.append(
                    RoutingResult(
                        orders=[orders[i]],
                        broker_name="none",
                        strategy_used=strategy or self._config.default_strategy,
                        success=False,
                        error_message=str(res),
                    )
                )
            else:
                out.append(res)
        return out

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    async def _apply_strategy(
        self,
        order: Order,
        strategy: RoutingStrategy,
    ) -> RoutingResult:
        """Apply the selected routing strategy."""
        available = self._get_available_brokers()
        if not available:
            return RoutingResult(
                orders=[order],
                broker_name="none",
                strategy_used=strategy,
                success=False,
                error_message="No connected brokers available",
            )

        if strategy == RoutingStrategy.COST_BASED:
            return self._route_cost_based(order, available)
        elif strategy == RoutingStrategy.LATENCY_BASED:
            return self._route_latency_based(order, available)
        elif strategy == RoutingStrategy.FILL_RATE_BASED:
            return self._route_fill_rate_based(order, available)
        elif strategy == RoutingStrategy.SPLIT:
            return self._route_split(order, available)
        elif strategy == RoutingStrategy.FIXED:
            return self._route_fixed(order, available)
        elif strategy == RoutingStrategy.ROUND_ROBIN:
            return self._route_round_robin(order, available)
        elif strategy == RoutingStrategy.VOLUME_WEIGHTED:
            return self._route_volume_weighted(order, available)
        else:
            return RoutingResult(
                orders=[order],
                broker_name="none",
                strategy_used=strategy,
                success=False,
                error_message=f"Unknown strategy: {strategy}",
            )

    # ---- Individual strategies ----------------------------------------

    def _route_cost_based(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Route to broker with lowest average commission per order."""
        best = min(
            available,
            key=lambda n: float(self._metrics[n].avg_commission_per_order),
        )
        logger.info(f"Cost-based route → {best}")
        return RoutingResult(orders=[order], broker_name=best, strategy_used=RoutingStrategy.COST_BASED)

    def _route_latency_based(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Route to broker with lowest average latency."""
        best = min(
            available,
            key=lambda n: self._metrics[n].avg_latency_ms,
        )
        logger.info(f"Latency-based route → {best}")
        return RoutingResult(orders=[order], broker_name=best, strategy_used=RoutingStrategy.LATENCY_BASED)

    def _route_fill_rate_based(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Route to broker with highest fill rate."""
        best = max(
            available,
            key=lambda n: self._metrics[n].fill_rate,
        )
        logger.info(f"Fill-rate-based route → {best}")
        return RoutingResult(orders=[order], broker_name=best, strategy_used=RoutingStrategy.FILL_RATE_BASED)

    def _route_round_robin(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Cycle through brokers in order."""
        idx = self._config.round_robin_index % len(available)
        self._config.round_robin_index = (idx + 1) % len(available)
        best = available[idx]
        logger.info(f"Round-robin route → {best}")
        return RoutingResult(orders=[order], broker_name=best, strategy_used=RoutingStrategy.ROUND_ROBIN)

    def _route_fixed(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Route to a pre-configured fixed broker."""
        name = self._config.fixed_broker_name
        if name and name in available:
            return RoutingResult(orders=[order], broker_name=name, strategy_used=RoutingStrategy.FIXED)

        # Fallback to first available
        fallback = available[0]
        logger.warning(
            f"Fixed broker '{name}' not available, falling back to {fallback}"
        )
        return RoutingResult(
            orders=[order],
            broker_name=fallback,
            strategy_used=RoutingStrategy.FIXED,
            success=True,
        )

    def _route_volume_weighted(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Weight brokers by their historical fill volume (filled_orders)."""
        total_filled = sum(
            self._metrics[n].filled_orders for n in available
        )
        if total_filled == 0:
            # Equal distribution if no history
            return self._route_split(order, available)

        weights = [
            self._metrics[n].filled_orders / total_filled
            for n in available
        ]
        parts = _split_order(
            order,
            num_parts=len(available),
            equal=False,
            weights=weights,
        )
        return RoutingResult(
            orders=parts,
            broker_name=",".join(available),
            strategy_used=RoutingStrategy.VOLUME_WEIGHTED,
        )

    def _route_split(
        self, order: Order, available: List[str]
    ) -> RoutingResult:
        """Split order across available brokers."""
        n = len(available)
        if (
            n == 1
            or order.quantity < self._config.min_split_quantity
        ):
            # Don't split small orders
            return RoutingResult(
                orders=[order],
                broker_name=available[0],
                strategy_used=RoutingStrategy.SPLIT,
            )

        parts = _split_order(
            order,
            num_parts=min(n, self._config.max_split_parts),
            equal=self._config.split_equal,
        )
        logger.info(
            f"Split order {order.order_id} into {len(parts)} parts"
        )
        return RoutingResult(
            orders=parts,
            broker_name=",".join(available),
            strategy_used=RoutingStrategy.SPLIT,
        )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def on_route(self, callback: Callable[[RoutingResult], None]) -> None:
        """Register a callback invoked after each routing decision."""
        self._on_route_callbacks.append(callback)

    def on_error(self, callback: Callable[[str, Exception], None]) -> None:
        """Register a callback invoked on routing errors."""
        self._on_error_callbacks.append(callback)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def check_all_brokers(self) -> Dict[str, bool]:
        """Check connectivity for all registered brokers."""
        results: Dict[str, bool] = {}
        for name, broker in self._brokers.items():
            try:
                connected = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, broker.is_connected,
                    ),
                    timeout=self._config.broker_timeout,
                )
                results[name] = connected
                self._metrics[name].is_connected = connected
            except Exception:
                results[name] = False
                self._metrics[name].is_connected = False
        return results

    def get_routing_summary(self) -> Dict[str, Any]:
        """Return a summary of routing state."""
        return {
            "registered_brokers": len(self._brokers),
            "connected_brokers": len(self._get_available_brokers()),
            "rules_count": len(self._rules),
            "default_strategy": self._config.default_strategy.name,
            "fallback_strategy": self._config.fallback_strategy.name,
            "metrics": self.get_metrics(),
        }