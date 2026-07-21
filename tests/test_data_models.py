"""Tests for data models"""

from datetime import datetime
from decimal import Decimal

from src.data.models import (
    Symbol, Tick, Bar, Quote, Trade, DataType, Resolution,
    OrderBook, OrderBookLevel, FundamentalData, News
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
    
    def test_orderbook_empty(self):
        """Test order book with empty bids and asks"""
        ob = OrderBook(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bids=[],
            asks=[]
        )
        
        assert len(ob.bids) == 0
        assert len(ob.asks) == 0
        assert isinstance(ob, OrderBook)
    
    def test_orderbook_bid_ask_imbalance(self):
        """Test order book with asymmetric levels"""
        bids = [OrderBookLevel(price=Decimal("100.00"), size=Decimal("500"))]
        asks = [
            OrderBookLevel(price=Decimal("100.05"), size=Decimal("100"), order_count=2),
            OrderBookLevel(price=Decimal("100.10"), size=Decimal("200")),
            OrderBookLevel(price=Decimal("100.15"), size=Decimal("150"), order_count=1),
        ]
        ob = OrderBook(
            symbol=Symbol(ticker="MSFT"),
            timestamp=datetime.now(),
            bids=bids,
            asks=asks
        )
        
        assert len(ob.bids) == 1
        assert len(ob.asks) == 3
        assert ob.bids[0].price == Decimal("100.00")


class TestFundamentalData:
    """Test FundamentalData class"""
    
    def test_fundamental_data_creation(self):
        """Test creating fundamental data"""
        fd = FundamentalData(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            pe_ratio=28.5,
            eps=Decimal("6.14"),
            market_cap=Decimal("2800000000000"),
            dividend_yield=0.005,
            book_value=Decimal("3.50")
        )
        
        assert fd.data_type == DataType.FUNDAMENTAL
        assert fd.pe_ratio == 28.5
        assert fd.eps == Decimal("6.14")
        assert fd.market_cap == Decimal("2800000000000")
    
    def test_fundamental_data_defaults(self):
        """Test fundamental data with all defaults"""
        fd = FundamentalData(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now()
        )
        
        assert fd.pe_ratio is None
        assert fd.eps is None
        assert fd.market_cap is None
        assert fd.dividend_yield is None
        assert fd.book_value is None


class TestNews:
    """Test News class"""
    
    def test_news_creation(self):
        """Test creating news item"""
        news = News(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            headline="Apple Reports Record Earnings",
            content="Apple Inc. reported quarterly earnings that exceeded analyst expectations...",
            source="Reuters",
            sentiment=0.8
        )
        
        assert news.data_type == DataType.NEWS
        assert news.headline == "Apple Reports Record Earnings"
        assert news.source == "Reuters"
        assert news.sentiment == 0.8
    
    def test_news_negative_sentiment(self):
        """Test news with negative sentiment"""
        news = News(
            symbol=Symbol(ticker="TSLA"),
            timestamp=datetime.now(),
            headline="Tesla Misses Delivery Targets",
            content="Tesla reported Q3 deliveries below analyst estimates...",
            source="Bloomberg",
            sentiment=-0.6
        )
        
        assert news.sentiment == -0.6
        assert news.symbol.ticker == "TSLA"
    
    def test_news_default_sentiment(self):
        """Test news with default sentiment"""
        news = News(
            symbol=Symbol(ticker="MSFT"),
            timestamp=datetime.now(),
            headline="Market Update",
            content="General market conditions..."
        )
        
        assert news.sentiment is None
        assert news.source == ""


class TestMarketDataTypeUnion:
    """Test MarketData type union"""
    
    def test_tick_is_marketdata(self):
        """Test Tick is assignable to MarketData"""
        from src.data.models import MarketData
        tick = Tick(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("200")
        )
        data: MarketData = tick
        assert data.symbol.ticker == "AAPL"
    
    def test_orderbook_is_marketdata(self):
        """Test OrderBook is assignable to MarketData"""
        from src.data.models import MarketData
        ob = OrderBook(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now(),
            bids=[],
            asks=[]
        )
        data: MarketData = ob
        assert data.data_type == DataType.ORDER_BOOK
    
    def test_fundamental_is_marketdata(self):
        """Test FundamentalData is assignable to MarketData"""
        from src.data.models import MarketData
        fd = FundamentalData(
            symbol=Symbol(ticker="AAPL"),
            timestamp=datetime.now()
        )
        data: MarketData = fd
        assert data.data_type == DataType.FUNDAMENTAL


class TestResolutionEnum:
    """Test Resolution enum values"""
    
    def test_resolution_values(self):
        """Test all resolution enum values"""
        assert Resolution.TICK.value == "tick"
        assert Resolution.SECOND.value == "second"
        assert Resolution.MINUTE.value == "minute"
        assert Resolution.HOUR.value == "hour"
        assert Resolution.DAILY.value == "daily"
        assert Resolution.WEEKLY.value == "weekly"
        assert Resolution.MONTHLY.value == "monthly"
    
    def test_resolution_count(self):
        """Test total number of resolutions"""
        assert len(Resolution) == 7
