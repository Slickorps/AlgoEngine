"""Data models for AlgoEngine"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from enum import Enum, auto


class DataType(Enum):
    """Types of market data"""
    TICK = auto()
    BAR = auto()
    QUOTE = auto()
    TRADE = auto()
    ORDER_BOOK = auto()
    FUNDAMENTAL = auto()
    NEWS = auto()


class Resolution(Enum):
    """Data resolutions"""
    TICK = "tick"
    SECOND = "second"
    MINUTE = "minute"
    HOUR = "hour"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
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
    
    def __str__(self) -> str:
        return f"{self.ticker}.{self.exchange}" if self.exchange else self.ticker


@dataclass
class BaseData:
    """Base class for all market data"""
    symbol: Symbol
    timestamp: datetime
    data_type: DataType = field(default=DataType.TICK)
    
    def __post_init__(self) -> None:
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


@dataclass
class Tick(BaseData):
    """Market tick data"""
    bid_price: Decimal = Decimal("0")
    ask_price: Decimal = Decimal("0")
    bid_size: Decimal = Decimal("0")
    ask_size: Decimal = Decimal("0")
    last_price: Optional[Decimal] = None
    last_size: Optional[Decimal] = None
    data_type: DataType = field(default=DataType.TICK)
    
    @property
    def spread(self) -> Decimal:
        """Get bid-ask spread"""
        return self.ask_price - self.bid_price
    
    @property
    def mid_price(self) -> Decimal:
        """Get mid price"""
        return (self.bid_price + self.ask_price) / 2


@dataclass
class Bar(BaseData):
    """OHLCV bar data"""
    open: Decimal = Decimal("0")
    high: Decimal = Decimal("0")
    low: Decimal = Decimal("0")
    close: Decimal = Decimal("0")
    volume: Decimal = Decimal("0")
    resolution: Resolution = Resolution.MINUTE
    data_type: DataType = field(default=DataType.BAR)
    
    def __post_init__(self) -> None:
        super().__post_init__()
        # Ensure high is highest and low is lowest
        if self.high < max(self.open, self.close):
            self.high = max(self.open, self.close)
        if self.low > min(self.open, self.close):
            self.low = min(self.open, self.close)


@dataclass
class Quote(BaseData):
    """Quote data (L1)"""
    bid_price: Decimal = Decimal("0")
    bid_size: Decimal = Decimal("0")
    ask_price: Decimal = Decimal("0")
    ask_size: Decimal = Decimal("0")
    data_type: DataType = field(default=DataType.QUOTE)


@dataclass
class Trade(BaseData):
    """Trade/transaction data"""
    price: Decimal = Decimal("0")
    size: Decimal = Decimal("0")
    side: str = ""  # BUY or SELL
    trade_id: str = ""
    data_type: DataType = field(default=DataType.TRADE)


@dataclass
class OrderBookLevel:
    """Single level in order book"""
    price: Decimal
    size: Decimal
    order_count: Optional[int] = None


@dataclass
class OrderBook(BaseData):
    """L2 Order book data"""
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    data_type: DataType = field(default=DataType.ORDER_BOOK)


@dataclass
class FundamentalData(BaseData):
    """Fundamental data"""
    pe_ratio: Optional[float] = None
    eps: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None
    dividend_yield: Optional[float] = None
    book_value: Optional[Decimal] = None
    data_type: DataType = field(default=DataType.FUNDAMENTAL)


@dataclass
class News(BaseData):
    """News/sentiment data"""
    headline: str = ""
    content: str = ""
    source: str = ""
    sentiment: Optional[float] = None  # -1 to 1
    data_type: DataType = field(default=DataType.NEWS)


# Type alias for any market data type
MarketData = Tick | Bar | Quote | Trade | OrderBook | FundamentalData | News
