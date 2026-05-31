"""Strategy base class for AlgoEngine"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Any, List, Optional, Callable
from enum import Enum, auto
import itertools

from ..data.models import Symbol, Tick, Bar
from ..trading.models import Order, OrderSide, OrderType, Fill
from ..portfolio.portfolio import Portfolio
from ..engine.events import EventBus, EventType
from ..utils.logger import get_logger

logger = get_logger("algorithms.strategy")

# Global counter for strategy IDs
_strategy_counter = itertools.count(1)


class StrategyState(Enum):
    """Strategy lifecycle states"""
    INITIALIZED = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPED = auto()


@dataclass
class StrategyConfig:
    """Strategy configuration"""
    name: str
    symbols: List[Symbol]
    parameters: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get parameter value"""
        return self.parameters.get(key, default)


class Strategy(ABC):
    """Base class for trading strategies"""
    
    def __init__(
        self,
        config: StrategyConfig,
        portfolio: Portfolio,
        event_bus: EventBus
    ) -> None:
        self._strategy_id = f"STRAT_{next(_strategy_counter)}"
        self._config = config
        self._portfolio = portfolio
        self._event_bus = event_bus
        self._state = StrategyState.INITIALIZED
        
        # Data storage
        self._data: Dict[Symbol, List[Bar]] = {s: [] for s in config.symbols}
        self._latest_ticks: Dict[Symbol, Tick] = {}
        
        # Event handlers
        self._order_handlers: List[Callable[[Order], None]] = []
        self._fill_handlers: List[Callable[[Fill], None]] = []
        
        logger.info(f"Strategy {self._strategy_id} ({config.name}) initialized")
    
    @property
    def strategy_id(self) -> str:
        """Unique strategy identifier"""
        return self._strategy_id
    
    @property
    def name(self) -> str:
        """Strategy name"""
        return self._config.name
    
    @property
    def state(self) -> StrategyState:
        """Current strategy state"""
        return self._state
    
    @property
    def is_running(self) -> bool:
        """Check if strategy is running"""
        return self._state == StrategyState.RUNNING
    
    @property
    def symbols(self) -> List[Symbol]:
        """Symbols this strategy trades"""
        return self._config.symbols
    
    def start(self) -> None:
        """Start the strategy"""
        if self._state == StrategyState.STOPPED:
            logger.warning(f"Cannot restart stopped strategy {self._strategy_id}")
            return
        
        self._state = StrategyState.RUNNING
        self.on_start()
        
        # Subscribe to events
        self._subscribe_to_events()
        
        logger.info(f"Strategy {self._strategy_id} started")
    
    def pause(self) -> None:
        """Pause the strategy"""
        if self._state == StrategyState.RUNNING:
            self._state = StrategyState.PAUSED
            self.on_pause()
            logger.info(f"Strategy {self._strategy_id} paused")
    
    def resume(self) -> None:
        """Resume the strategy"""
        if self._state == StrategyState.PAUSED:
            self._state = StrategyState.RUNNING
            self.on_resume()
            logger.info(f"Strategy {self._strategy_id} resumed")
    
    def stop(self) -> None:
        """Stop the strategy"""
        self._state = StrategyState.STOPPED
        self.on_stop()
        
        # Unsubscribe from events
        self._unsubscribe_from_events()
        
        logger.info(f"Strategy {self._strategy_id} stopped")
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to market data events"""
        for symbol in self._config.symbols:
            self._event_bus.subscribe(
                EventType.BAR,
                lambda event, s=symbol: self._on_bar(event) if event.symbol == s else None
            )
            self._event_bus.subscribe(
                EventType.TICK,
                lambda event, s=symbol: self._on_tick(event) if event.symbol == s else None
            )
    
    def _unsubscribe_from_events(self) -> None:
        """Unsubscribe from events (simplified - real implementation would track subscriptions)"""
        pass
    
    def _on_bar(self, bar: Bar) -> None:
        """Internal bar handler"""
        if not self.is_running:
            return
        
        # Store bar data
        if bar.symbol in self._data:
            self._data[bar.symbol].append(bar)
            # Keep only last 1000 bars
            if len(self._data[bar.symbol]) > 1000:
                self._data[bar.symbol].pop(0)
        
        # Call user handler
        self.on_bar(bar)
    
    def _on_tick(self, tick: Tick) -> None:
        """Internal tick handler"""
        if not self.is_running:
            return
        
        self._latest_ticks[tick.symbol] = tick
        self.on_tick(tick)
    
    def _on_fill(self, fill: Fill) -> None:
        """Internal fill handler"""
        self.on_fill(fill)
        
        # Notify custom handlers
        for handler in self._fill_handlers:
            handler(fill)
    
    # --- Lifecycle hooks (override in subclasses) ---
    
    def on_start(self) -> None:
        """Called when strategy starts"""
        pass
    
    def on_pause(self) -> None:
        """Called when strategy pauses"""
        pass
    
    def on_resume(self) -> None:
        """Called when strategy resumes"""
        pass
    
    def on_stop(self) -> None:
        """Called when strategy stops"""
        pass
    
    @abstractmethod
    def on_bar(self, bar: Bar) -> None:
        """Called on each new bar - implement in subclass"""
        pass
    
    def on_tick(self, tick: Tick) -> None:
        """Called on each tick - override if needed"""
        pass
    
    def on_fill(self, fill: Fill) -> None:
        """Called on order fill - override if needed"""
        pass
    
    # --- Trading methods ---
    
    def submit_order(
        self,
        symbol: Symbol,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[Decimal] = None,
        stop_price: Optional[Decimal] = None
    ) -> Optional[Order]:
        """Submit an order from this strategy"""
        if not self.is_running:
            logger.warning(f"Strategy {self._strategy_id} not running, cannot submit order")
            return None
        
        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            strategy_id=self._strategy_id
        )
        
        # Emit order event
        self._event_bus.emit(EventType.ORDER, order)
        
        logger.info(
            f"Strategy {self._strategy_id} submitted {side.name} order: "
            f"{quantity} {symbol.ticker}"
        )
        
        return order
    
    def buy_market(self, symbol: Symbol, quantity: Decimal) -> Optional[Order]:
        """Submit market buy order"""
        return self.submit_order(symbol, OrderSide.BUY, quantity, OrderType.MARKET)
    
    def sell_market(self, symbol: Symbol, quantity: Decimal) -> Optional[Order]:
        """Submit market sell order"""
        return self.submit_order(symbol, OrderSide.SELL, quantity, OrderType.MARKET)
    
    def buy_limit(
        self,
        symbol: Symbol,
        quantity: Decimal,
        limit_price: Decimal
    ) -> Optional[Order]:
        """Submit limit buy order"""
        return self.submit_order(
            symbol, OrderSide.BUY, quantity, OrderType.LIMIT, limit_price
        )
    
    def sell_limit(
        self,
        symbol: Symbol,
        quantity: Decimal,
        limit_price: Decimal
    ) -> Optional[Order]:
        """Submit limit sell order"""
        return self.submit_order(
            symbol, OrderSide.SELL, quantity, OrderType.LIMIT, limit_price
        )
    
    # --- Data access ---
    
    def get_bars(self, symbol: Symbol, n: int = 100) -> List[Bar]:
        """Get last n bars for symbol"""
        bars = self._data.get(symbol, [])
        return bars[-n:] if bars else []
    
    def get_latest_price(self, symbol: Symbol) -> Optional[Decimal]:
        """Get latest price for symbol"""
        if symbol in self._latest_ticks:
            return self._latest_ticks[symbol].price
        
        bars = self._data.get(symbol, [])
        if bars:
            return bars[-1].close
        
        return None
    
    def get_position(self, symbol: Symbol) -> Decimal:
        """Get current position quantity for symbol"""
        position = self._portfolio.get_position(symbol)
        return position.quantity if position else Decimal("0")
    
    # --- Event registration ---
    
    def on_order(self, handler: Callable[[Order], None]) -> None:
        """Register order event handler"""
        self._order_handlers.append(handler)
    
    def on_fill_event(self, handler: Callable[[Fill], None]) -> None:
        """Register fill event handler"""
        self._fill_handlers.append(handler)
    
    # --- Parameter access ---
    
    def param(self, key: str, default: Any = None) -> Any:
        """Get strategy parameter"""
        return self._config.get(key, default)
    
    def set_param(self, key: str, value: Any) -> None:
        """Set strategy parameter"""
        self._config.parameters[key] = value
    
    def get_summary(self) -> Dict[str, Any]:
        """Get strategy summary"""
        return {
            'strategy_id': self._strategy_id,
            'name': self._config.name,
            'state': self._state.name,
            'symbols': [s.ticker for s in self._config.symbols],
            'parameters': self._config.parameters.copy()
        }
