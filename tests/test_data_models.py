"""Tests for data models"""

from datetime import datetime
from decimal import Decimal

from src.data.models import (
    Symbol, Tick, Bar, Quote, Trade, DataType, OrderBook, OrderBookLevel
)


class TestSymbol:
    """Test Symbol class"""
    
    def test_symbol_creation(self):
        """Test creating a symbol"""
        sym = Symbol(ticker="AAPL", security_type="EQUITY", exchange="NASDAQ")
        assert sym.ticker == "AAPL"
        assert sym.security_type == "EQUITY"
        assert sym.exchange == "NASDAQ"
        assert sym.currency == "USD"  # Default
    
    def test_symbol_equality(self):
        """Test symbol equality"""
        sym1 = Symbol(ticker="AAPL", security_type="EQUITY")
        sym2 = Symbol(ticker="AAPL", security_type="EQUITY")
        sym3 = Symbol(ticker="MSFT", security_type="EQUITY")
        
        assert sym1 == sym2
        assert sym1 != sym3
        assert hash(sym1) == hash(sym2)
    
    def test_symbol_string(self):
        """Test symbol string representation"""
        sym1 = Symbol(ticker="AAPL", exchange="NASDAQ")
        sym2 = Symbol(ticker="AAPL")
        
        assert str(sym1) == "AAPL.NASDAQ"
        assert str(sym2) == "AAPL"


class TestTick:
    """Test Tick class"""
    
    def test_tick_creation(self):
        """Test creating a tick"""
        symbol = Symbol(ticker="AAPL")
        tick = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("200")
        )
        
        assert tick.symbol == symbol
        assert tick.bid_price == Decimal("150.00")
        assert tick.ask_price == Decimal("150.05")
    
    def test_tick_spread(self):
        """Test tick spread calculation"""
        tick = Tick(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        
        assert tick.spread == Decimal("0.05")
    
    def test_tick_mid_price(self):
        """Test tick mid price calculation"""
        tick = Tick(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("100.00"),
            ask_price=Decimal("100.10"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        
        assert tick.mid_price == Decimal("100.05")


class TestBar:
    """Test Bar class"""
    
    def test_bar_creation(self):
        """Test creating a bar"""
        bar = Bar(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            open=Decimal("150.00"),
            high=Decimal("155.00"),
            low=Decimal("148.00"),
            close=Decimal("152.00"),
            volume=Decimal("1000000")
        )
        
        assert bar.open == Decimal("150.00")
        assert bar.high == Decimal("155.00")
        assert bar.low == Decimal("148.00")
        assert bar.close == Decimal("152.00")
        assert bar.volume == Decimal("1000000")
    
    def test_bar_validation(self):
        """Test bar OHLC validation"""
        # High should be adjusted if less than open/close
        bar = Bar(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            open=Decimal("150.00"),
            high=Decimal("149.00"),  # Lower than open
            low=Decimal("148.00"),
            close=Decimal("152.00"),
            volume=Decimal("1000")
        )
        
        assert bar.high == Decimal("152.00")  # Adjusted to close


class TestQuote:
    """Test Quote class"""
    
    def test_quote_creation(self):
        """Test creating a quote"""
        quote = Quote(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            bid_size=Decimal("500"),
            ask_price=Decimal("150.05"),
            ask_size=Decimal("300")
        )
        
        assert quote.data_type == DataType.QUOTE
        assert quote.bid_price == Decimal("150.00")


class TestTrade:
    """Test Trade class"""
    
    def test_trade_creation(self):
        """Test creating a trade"""
        trade = Trade(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            price=Decimal("150.00"),
            size=Decimal("100"),
            side="BUY",
            trade_id="12345"
        )
        
        assert trade.data_type == DataType.TRADE
        assert trade.price == Decimal("150.00")
        assert trade.side == "BUY"


class TestOrderBook:
    """Test OrderBook class"""
    
    def test_orderbook_creation(self):
        """Test creating order book"""
        bids = [
            OrderBookLevel(price=Decimal("100.00"), size=Decimal("100"), order_count=5),
            OrderBookLevel(price=Decimal("99.95"), size=Decimal("200"), order_count=3),
        ]
        asks = [
            OrderBookLevel(price=Decimal("100.05"), size=Decimal("150"), order_count=4),
            OrderBookLevel(price=Decimal("100.10"), size=Decimal("300"), order_count=6),
        ]
        
        ob = OrderBook(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bids=bids,
            asks=asks
        )
        
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2
        assert ob.data_type == DataType.ORDER_BOOK
