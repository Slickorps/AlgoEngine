"""Example plugin demonstrating a custom trading strategy loaded via PluginLoader.

Place this entire directory inside the configured ``plugins_dir``
(or use it directly with ``PluginLoader(plugins_dir="examples")``).
"""

from decimal import Decimal
from typing import Any, Dict, Optional

from src.algorithms.indicators import SMA, RSI
from src.data.models import Bar
from src.engine.events import EventBus
from src.plugins.interface import (
    IPlugin,
    PluginDependency,
    PluginMetadata,
    PluginType,
    PluginState,
)
from src.portfolio.portfolio import Portfolio


class ExampleMovingAverageStrategy(IPlugin):
    """A simple SMA crossover strategy delivered as an engine plugin."""

    def __init__(self) -> None:
        self._state = PluginState.DISCOVERED
        self._fast_sma: Optional[SMA] = None
        self._slow_sma: Optional[SMA] = None
        self._rsi: Optional[RSI] = None
        self._portfolio: Optional[Portfolio] = None
        self._event_bus: Optional[EventBus] = None
        self._position: Decimal = Decimal("0")
        self._symbol = "AAPL"

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="example-moving-average",
            version="1.0.0",
            description="SMA crossover strategy as a loadable plugin",
            author="AlgoEngine Team",
            plugin_type=PluginType.STRATEGY,
            tags=["demo", "sma", "example"],
            provides={"strategy.SMA_CROSSOVER"},
            dependencies=[
                PluginDependency(
                    name="base-indicators",
                    version_min="1.0.0",
                    required=False,
                )
            ],
        )

    def on_load(self) -> None:
        self._fast_sma = SMA(period=10)
        self._slow_sma = SMA(period=30)
        self._rsi = RSI(period=14)

    def on_init(self, config: Dict[str, Any]) -> None:
        self._symbol = config.get("symbol", "AAPL")
        self._portfolio = config.get("portfolio")
        self._event_bus = config.get("event_bus")
        fast_period = config.get("fast_period", 10)
        slow_period = config.get("slow_period", 30)
        if fast_period != 10:
            self._fast_sma = SMA(period=fast_period)
        if slow_period != 30:
            self._slow_sma = SMA(period=slow_period)

    def validate(self) -> bool:
        return self._fast_sma is not None and self._slow_sma is not None

    def on_start(self) -> None:
        self._position = Decimal("0")

    def on_bar(self, bar: Bar) -> None:
        if self._fast_sma is None or self._slow_sma is None:
            return

        fast_val = self._fast_sma.update(bar.close)
        slow_val = self._slow_sma.update(bar.close)

        if not (self._fast_sma.is_ready and self._slow_sma.is_ready):
            return

        if fast_val > slow_val and self._position == 0:
            self._position = self._calculate_size(bar.close)
            self._emit_signal("BUY", bar)

        elif fast_val < slow_val and self._position > 0:
            self._emit_signal("SELL", bar)
            self._position = Decimal("0")

    def _calculate_size(self, price: Decimal) -> Decimal:
        if self._portfolio is None:
            return Decimal("100")
        value = self._portfolio.total_value * Decimal("0.1")
        return Decimal(int(value / price))

    def _emit_signal(self, direction: str, bar: Bar) -> None:
        if self._event_bus is not None:
            self._event_bus.emit(
                "strategy_signal",
                {
                    "strategy": self.metadata().name,
                    "direction": direction,
                    "symbol": str(bar.symbol) if hasattr(bar, "symbol") else self._symbol,
                    "price": str(bar.close),
                    "timestamp": str(bar.timestamp),
                },
            )

    def on_pause(self) -> None:
        pass

    def on_resume(self) -> None:
        pass

    def on_stop(self) -> None:
        self._position = Decimal("0")

    def on_unload(self) -> None:
        self._fast_sma = None
        self._slow_sma = None
        self._rsi = None
        self._portfolio = None
        self._event_bus = None

    @property
    def state(self) -> PluginState:
        return self._state

    @state.setter
    def state(self, value: PluginState) -> None:
        self._state = value
