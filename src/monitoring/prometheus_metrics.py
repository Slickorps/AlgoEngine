"""Prometheus metrics collector for the AlgoEngine Python engine.

Exposes business-level trading metrics in Prometheus-compatible format
for scraping by the Prometheus server or the Go monitoring agent.
"""

import time
from datetime import datetime
from typing import Optional, Callable, Dict, Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    CollectorRegistry,
    REGISTRY,
)

from ..utils.logger import get_logger

logger = get_logger("monitoring.prometheus")


class EngineMetrics:
    """Collects and exposes AlgoEngine trading metrics in Prometheus format.

    Metrics tracked:
      - Order counts by side and type
      - Fill activity and value
      - Position P&L and exposure
      - Strategy state
      - Event processing latency
      - Tick/bar data throughput
    """

    def __init__(
        self,
        registry: Optional[CollectorRegistry] = None,
        namespace: str = "algoengine",
        subsystem: str = "core",
    ) -> None:
        self._registry = registry or REGISTRY
        self._namespace = namespace
        self._subsystem = subsystem
        self._start_time = time.time()

        # ── Order Metrics ────────────────────────────────────────
        self.orders_submitted = Counter(
            name=f"{namespace}_{subsystem}_orders_submitted_total",
            documentation="Total number of orders submitted",
            labelnames=["side", "order_type"],
            registry=self._registry,
        )
        self.orders_filled = Counter(
            name=f"{namespace}_{subsystem}_orders_filled_total",
            documentation="Total number of orders filled",
            labelnames=["side"],
            registry=self._registry,
        )
        self.orders_cancelled = Counter(
            name=f"{namespace}_{subsystem}_orders_cancelled_total",
            documentation="Total number of orders cancelled",
            labelnames=["reason"],
            registry=self._registry,
        )
        self.orders_rejected = Counter(
            name=f"{namespace}_{subsystem}_orders_rejected_total",
            documentation="Total number of orders rejected by risk checks",
            labelnames=["reason"],
            registry=self._registry,
        )
        self.active_orders = Gauge(
            name=f"{namespace}_{subsystem}_active_orders",
            documentation="Number of currently active (pending) orders",
            registry=self._registry,
        )

        # ── Fill Metrics ─────────────────────────────────────────
        self.fill_value = Counter(
            name=f"{namespace}_{subsystem}_fill_value_total",
            documentation="Total value of filled orders in quote currency",
            labelnames=["side"],
            registry=self._registry,
        )
        self.fill_count = Counter(
            name=f"{namespace}_{subsystem}_fill_count_total",
            documentation="Total number of fills",
            labelnames=["side"],
            registry=self._registry,
        )

        # ── Position Metrics ─────────────────────────────────────
        self.position_count = Gauge(
            name=f"{namespace}_{subsystem}_position_count",
            documentation="Current number of open positions",
            registry=self._registry,
        )
        self.net_exposure = Gauge(
            name=f"{namespace}_{subsystem}_net_exposure",
            documentation="Net portfolio exposure in quote currency",
            registry=self._registry,
        )
        self.unrealized_pnl = Gauge(
            name=f"{namespace}_{subsystem}_unrealized_pnl",
            documentation="Current unrealized profit/loss across all positions",
            registry=self._registry,
        )

        # ── Strategy Metrics ─────────────────────────────────────
        self.strategy_signals = Counter(
            name=f"{namespace}_{subsystem}_strategy_signals_total",
            documentation="Total number of trading signals generated",
            labelnames=["strategy", "direction"],
            registry=self._registry,
        )
        self.strategy_state = Gauge(
            name=f"{namespace}_{subsystem}_strategy_state",
            documentation="Current strategy state (1=running, 0=stopped, -1=paused)",
            labelnames=["strategy"],
            registry=self._registry,
        )

        # ── Data Metrics ─────────────────────────────────────────
        self.ticks_received = Counter(
            name=f"{namespace}_{subsystem}_ticks_received_total",
            documentation="Total number of ticks received",
            labelnames=["symbol"],
            registry=self._registry,
        )
        self.bars_received = Counter(
            name=f"{namespace}_{subsystem}_bars_received_total",
            documentation="Total number of bars received",
            labelnames=["symbol", "resolution"],
            registry=self._registry,
        )
        self.data_gaps = Counter(
            name=f"{namespace}_{subsystem}_data_gaps_total",
            documentation="Total number of data gaps detected",
            labelnames=["symbol"],
            registry=self._registry,
        )
        self.data_latency = Histogram(
            name=f"{namespace}_{subsystem}_data_latency_seconds",
            documentation="Data ingestion latency from source to engine",
            labelnames=["symbol", "data_type"],
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0],
            registry=self._registry,
        )

        # ── Engine Health Metrics ────────────────────────────────
        self.engine_running = Gauge(
            name=f"{namespace}_{subsystem}_running",
            documentation="Whether the engine is running (1=yes, 0=no)",
            registry=self._registry,
        )
        self.engine_uptime = Gauge(
            name=f"{namespace}_{subsystem}_uptime_seconds",
            documentation="Engine uptime in seconds",
            registry=self._registry,
        )
        self.engine_info = Info(
            name=f"{namespace}_{subsystem}_info",
            documentation="Engine version information",
            registry=self._registry,
        )
        self.engine_errors = Counter(
            name=f"{namespace}_{subsystem}_errors_total",
            documentation="Total number of engine errors",
            labelnames=["component", "error_type"],
            registry=self._registry,
        )

        # ── Event Processing Metrics ─────────────────────────────
        self.event_processed = Counter(
            name=f"{namespace}_{subsystem}_events_processed_total",
            documentation="Total number of events processed",
            labelnames=["event_type"],
            registry=self._registry,
        )
        self.event_latency = Histogram(
            name=f"{namespace}_{subsystem}_event_latency_seconds",
            documentation="Event processing latency",
            labelnames=["event_type"],
            buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
            registry=self._registry,
        )
        self.event_queue_size = Gauge(
            name=f"{namespace}_{subsystem}_event_queue_size",
            documentation="Current event queue backlog size",
            registry=self._registry,
        )

        # ── HTTP Callbacks (for external metrics collection) ────
        self._on_metrics_collected: Optional[Callable[[], Dict[str, Any]]] = None

        logger.info("EngineMetrics initialized with Prometheus registry")

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self, version: str = "1.0.0", mode: str = "unknown") -> None:
        self._start_time = time.time()
        self.engine_running.set(1)
        self.engine_uptime.set(0)
        self.engine_info.info({"version": version, "mode": mode})
        logger.info(f"Prometheus metrics started (version={version}, mode={mode})")

    def stop(self) -> None:
        self.engine_running.set(0)
        logger.info("Prometheus metrics stopped")

    def tick(self) -> None:
        self.engine_uptime.set(time.time() - self._start_time)

    # ── Order Tracking ─────────────────────────────────────────

    def record_order_submitted(self, side: str, order_type: str) -> None:
        self.orders_submitted.labels(side=side, order_type=order_type).inc()
        self.active_orders.inc()

    def record_order_filled(self, side: str) -> None:
        self.orders_filled.labels(side=side).inc()
        self.active_orders.dec()

    def record_order_cancelled(self, reason: str = "user") -> None:
        self.orders_cancelled.labels(reason=reason).inc()
        self.active_orders.dec()

    def record_order_rejected(self, reason: str = "risk_limit") -> None:
        self.orders_rejected.labels(reason=reason).inc()
        self.active_orders.dec()

    def record_fill(self, side: str, value: float) -> None:
        self.fill_count.labels(side=side).inc()
        self.fill_value.labels(side=side).inc(value)

    # ── Position Tracking ──────────────────────────────────────

    def record_positions(self, count: int, net_exposure: float, pnl: float) -> None:
        self.position_count.set(count)
        self.net_exposure.set(net_exposure)
        self.unrealized_pnl.set(pnl)

    # ── Strategy Tracking ──────────────────────────────────────

    def record_signal(self, strategy: str, direction: str) -> None:
        self.strategy_signals.labels(strategy=strategy, direction=direction).inc()

    def set_strategy_state(self, strategy: str, state: int) -> None:
        self.strategy_state.labels(strategy=strategy).set(state)

    # ── Data Tracking ──────────────────────────────────────────

    def record_tick(self, symbol: str) -> None:
        self.ticks_received.labels(symbol=symbol).inc()

    def record_bar(self, symbol: str, resolution: str) -> None:
        self.bars_received.labels(symbol=symbol, resolution=resolution).inc()

    def record_data_gap(self, symbol: str) -> None:
        self.data_gaps.labels(symbol=symbol).inc()

    def record_data_latency(
        self, symbol: str, data_type: str, latency_seconds: float
    ) -> None:
        self.data_latency.labels(symbol=symbol, data_type=data_type).observe(
            latency_seconds
        )

    # ── Error Tracking ─────────────────────────────────────────

    def record_error(self, component: str, error_type: str) -> None:
        self.engine_errors.labels(component=component, error_type=error_type).inc()

    # ── Event Tracking ─────────────────────────────────────────

    def record_event(self, event_type: str) -> None:
        self.event_processed.labels(event_type=event_type).inc()

    def record_event_latency(self, event_type: str, latency_seconds: float) -> None:
        self.event_latency.labels(event_type=event_type).observe(latency_seconds)

    def set_event_queue_size(self, size: int) -> None:
        self.event_queue_size.set(size)

    # ── External Callback ──────────────────────────────────────

    def set_metrics_collector(
        self, callback: Callable[[], Dict[str, Any]]
    ) -> None:
        self._on_metrics_collected = callback

    # ── Serialization ──────────────────────────────────────────

    def get_metrics_text(self) -> bytes:
        self.tick()
        if self._on_metrics_collected:
            try:
                extra = self._on_metrics_collected()
                for name, value in extra.items():
                    if isinstance(value, (int, float)):
                        g = Gauge(
                            f"{self._namespace}_{self._subsystem}_{name}",
                            documentation=f"Dynamic metric: {name}",
                            registry=self._registry,
                        )
                        g.set(float(value))
            except Exception:
                logger.warning("Metrics collector callback failed", exc_info=True)
        return generate_latest(self._registry)

    def get_metrics_json(self) -> Dict[str, Any]:
        self.tick()
        metrics: Dict[str, Any] = {
            "engine_running": bool(self.engine_running._value.get()),
            "uptime_seconds": time.time() - self._start_time,
            "active_orders": self.active_orders._value.get(),
            "position_count": self.position_count._value.get(),
            "net_exposure": self.net_exposure._value.get(),
            "unrealized_pnl": self.unrealized_pnl._value.get(),
            "event_queue_size": self.event_queue_size._value.get(),
            "timestamp": datetime.utcnow().isoformat(),
        }
        if self._on_metrics_collected:
            try:
                metrics.update(self._on_metrics_collected())
            except Exception:
                pass
        return metrics


_engine_metrics: Optional[EngineMetrics] = None


def get_engine_metrics() -> EngineMetrics:
    global _engine_metrics
    if _engine_metrics is None:
        _engine_metrics = EngineMetrics()
    return _engine_metrics


async def start_metrics_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    path: str = "/metrics",
    engine_metrics: Optional[EngineMetrics] = None,
):
    """Start a minimal HTTP server exposing Prometheus metrics.

    This is an optional lightweight server separate from the main FastAPI app.
    The main engine should ideally integrate metrics into the existing FastAPI
    endpoints instead of starting a separate server.
    """
    from aiohttp import web

    metrics = engine_metrics or get_engine_metrics()
    metrics.start()

    async def metrics_handler(request: web.Request) -> web.Response:
        return web.Response(
            body=metrics.get_metrics_text(),
            content_type="text/plain; version=0.0.4",
        )

    app = web.Application()
    app.router.add_get(path, metrics_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info(f"Metrics server listening on {host}:{port}{path}")
    return runner
