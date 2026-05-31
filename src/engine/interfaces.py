"""Core interfaces for AlgoEngine"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Any, Dict, List, Optional

import pandas as pd


class OrderType(Enum):
    """Order types supported by the engine"""
    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()
    TRAILING_STOP = auto()


class OrderStatus(Enum):
    """Order status values"""
    PENDING = auto()
    SUBMITTED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()


class PositionSide(Enum):
    """Position side"""
    LONG = auto()
    SHORT = auto()
    FLAT = auto()


@dataclass
class Symbol:
    """Trading symbol representation"""
    ticker: str
    security_type: str = "EQUITY"  # EQUITY, FOREX, FUTURE, OPTION, CRYPTO, CFD
    exchange: str = ""
    currency: str = "USD"
    
    def __hash__(self) -> int:
        return hash((self.ticker, self.security_type, self.exchange))
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Symbol):
            return False
        return (self.ticker, self.security_type, self.exchange) == (
            other.ticker, other.security_type, other.exchange
        )


@dataclass
class Tick:
    """Market tick data"""
    symbol: Symbol
    timestamp: datetime
    bid_price: Decimal
    ask_price: Decimal
    bid_size: Decimal
    ask_size: Decimal
    last_price: Optional[Decimal] = None
    volume: Optional[Decimal] = None


@dataclass
class Bar:
    """OHLCV bar data"""
    symbol: Symbol
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class Order:
    """Order representation"""
    id: str
    symbol: Symbol
    order_type: OrderType
    side: str  # BUY or SELL
    quantity: Decimal
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    filled_quantity: Decimal = Decimal('0')
    average_fill_price: Optional[Decimal] = None
    tags: Dict[str, Any] = None
    
    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = {}


@dataclass
class OrderEvent:
    """Order event data"""
    order_id: str
    symbol: Symbol
    status: OrderStatus
    timestamp: datetime
    filled_quantity: Decimal = Decimal('0')
    fill_price: Optional[Decimal] = None
    commission: Decimal = Decimal('0')
    message: str = ""


@dataclass
class Position:
    """Position data"""
    symbol: Symbol
    side: PositionSide
    quantity: Decimal
    avg_entry_price: Decimal
    unrealized_pnl: Decimal = Decimal('0')
    realized_pnl: Decimal = Decimal('0')
    market_price: Optional[Decimal] = None


class IAlgorithm(ABC):
    """Interface for trading algorithms"""
    
    @abstractmethod
    def initialize(self) -> None:
        """Initialize the algorithm"""
        pass
    
    @abstractmethod
    def on_data(self, data: Any) -> None:
        """Handle new market data"""
        pass
    
    @abstractmethod
    def on_order_event(self, event: OrderEvent) -> None:
        """Handle order events"""
        pass
    
    @abstractmethod
    def on_position_changed(self, position: Position) -> None:
        """Handle position changes"""
        pass
    
    @abstractmethod
    def on_warmup_finished(self) -> None:
        """Called when warmup period is complete"""
        pass
    
    @abstractmethod
    def on_end_of_day(self) -> None:
        """Called at end of trading day"""
        pass
    
    @abstractmethod
    def terminate(self, message: str = "") -> None:
        """Terminate the algorithm"""
        pass
    
    @property
    @abstractmethod
    def is_warming_up(self) -> bool:
        """Check if algorithm is in warmup period"""
        pass


class IDataFeed(ABC):
    """Interface for data feed providers"""
    
    @abstractmethod
    async def connect(self) -> None:
        """Connect to data source"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from data source"""
        pass
    
    @abstractmethod
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """Subscribe to market data for symbols"""
        pass
    
    @abstractmethod
    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        """Unsubscribe from market data"""
        pass
    
    @abstractmethod
    def get_history(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: str = "minute"
    ) -> pd.DataFrame:
        """Get historical data"""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to data source"""
        pass


class ITransactionHandler(ABC):
    """Interface for transaction handling"""
    
    @abstractmethod
    def process_order(self, order: Order) -> OrderEvent:
        """Process an order and return the result event"""
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str) -> OrderEvent:
        """Cancel an existing order"""
        pass
    
    @abstractmethod
    def update_order(self, order: Order) -> OrderEvent:
        """Update an existing order"""
        pass
    
    @abstractmethod
    def get_open_orders(self, symbol: Optional[Symbol] = None) -> List[Order]:
        """Get list of open orders"""
        pass
    
    @abstractmethod
    def get_order_by_id(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        pass


class IResultHandler(ABC):
    """Interface for handling algorithm results"""
    
    @abstractmethod
    def log_message(self, message: str, level: str = "INFO") -> None:
        """Log a message"""
        pass
    
    @abstractmethod
    def debug_message(self, message: str) -> None:
        """Debug message"""
        pass
    
    @abstractmethod
    def error_message(self, message: str, traceback: str = "") -> None:
        """Error message"""
        pass
    
    @abstractmethod
    def runtime_statistic(self, key: str, value: Any) -> None:
        """Store runtime statistic"""
        pass
    
    @abstractmethod
    def order_event(self, event: OrderEvent) -> None:
        """Handle order event"""
        pass
    
    @abstractmethod
    def save_results(self, name: str, result: Any) -> None:
        """Save algorithm results"""
        pass
    
    @abstractmethod
    def set_algorithm(self, algorithm: IAlgorithm) -> None:
        """Set the algorithm instance"""
        pass
    
    @abstractmethod
    def exit(self, exit_code: int = 0) -> None:
        """Exit the algorithm"""
        pass


class IPortfolio(ABC):
    """Interface for portfolio management"""
    
    @abstractmethod
    def get_cash(self, currency: str = "USD") -> Decimal:
        """Get available cash in specified currency"""
        pass
    
    @abstractmethod
    def get_total_portfolio_value(self) -> Decimal:
        """Get total portfolio value"""
        pass
    
    @abstractmethod
    def get_position(self, symbol: Symbol) -> Optional[Position]:
        """Get position for a symbol"""
        pass
    
    @abstractmethod
    def get_all_positions(self) -> List[Position]:
        """Get all positions"""
        pass
    
    @abstractmethod
    def get_unrealized_profit(self) -> Decimal:
        """Get total unrealized profit/loss"""
        pass
    
    @abstractmethod
    def get_realized_profit(self) -> Decimal:
        """Get total realized profit/loss"""
        pass
    
    @abstractmethod
    def process_fill(self, fill: OrderEvent) -> None:
        """Process an order fill"""
        pass
    
    @abstractmethod
    def set_cash(self, cash: Decimal, currency: str = "USD") -> None:
        """Set cash amount"""
        pass


class IExecutionModel(ABC):
    """Interface for execution models"""
    
    @abstractmethod
    def execute(self, portfolio: IPortfolio, orders: List[Order]) -> List[Order]:
        """Execute orders"""
        pass
    
    @abstractmethod
    def on_order_event(self, event: OrderEvent) -> None:
        """Handle order events"""
        pass


class IRiskManager(ABC):
    """Interface for risk management"""
    
    @abstractmethod
    def manage_risk(self, portfolio: IPortfolio, orders: List[Order]) -> List[Order]:
        """Apply risk management rules to orders"""
        pass
    
    @abstractmethod
    def on_position_changed(self, position: Position) -> None:
        """Handle position changes"""
        pass
    
    @abstractmethod
    def is_within_limits(self, portfolio: IPortfolio) -> bool:
        """Check if portfolio is within risk limits"""
        pass
