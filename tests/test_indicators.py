"""Tests for technical indicators"""

from decimal import Decimal

from src.algorithms.indicators import SMA, EMA, RSI, MACD, BollingerBands, ATR, IndicatorManager


class TestSMA:
    """Test Simple Moving Average"""
    
    def test_sma_calculation(self):
        """Test SMA calculation"""
        sma = SMA(period=3)
        
        prices = [Decimal("10"), Decimal("20"), Decimal("30")]
        for p in prices:
            result = sma.update(p)
        
        assert result is not None
        assert result == Decimal("20")  # (10+20+30)/3
    
    def test_sma_not_ready(self):
        """Test SMA not ready with insufficient data"""
        sma = SMA(period=5)
        
        sma.update(Decimal("10"))
        sma.update(Decimal("20"))
        
        assert not sma.is_ready
        assert sma.value is None
    
    def test_sma_is_ready(self):
        """Test SMA becomes ready"""
        sma = SMA(period=2)
        
        sma.update(Decimal("10"))
        assert not sma.is_ready
        
        sma.update(Decimal("20"))
        assert sma.is_ready


class TestEMA:
    """Test Exponential Moving Average"""
    
    def test_ema_calculation(self):
        """Test EMA calculation"""
        ema = EMA(period=3)
        
        prices = [Decimal("10"), Decimal("20"), Decimal("30"), Decimal("35")]
        for p in prices:
            ema.update(p)
        
        assert ema.is_ready
        assert ema.value is not None
        # EMA should be closer to recent prices than SMA (30)
        assert float(ema.value) > 25


class TestRSI:
    """Test Relative Strength Index"""
    
    def test_rsi_calculation(self):
        """Test RSI calculation"""
        rsi = RSI(period=5)
        
        # Rising prices
        prices = [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13"), Decimal("14")]
        for p in prices:
            rsi.update(p)
        
        assert rsi.is_ready
        assert rsi.value is not None
        # RSI should be high for rising prices
        assert float(rsi.value) > 50
    
    def test_rsi_oversold(self):
        """Test RSI with falling prices"""
        rsi = RSI(period=5)
        
        # Falling prices
        prices = [Decimal("20"), Decimal("19"), Decimal("18"), Decimal("17"), Decimal("16")]
        for p in prices:
            rsi.update(p)
        
        # RSI should be low for falling prices
        assert float(rsi.value) < 50
    
    def test_rsi_bounds(self):
        """Test RSI stays within 0-100 bounds"""
        rsi = RSI(period=5)
        
        prices = [Decimal("100")] * 10  # Constant price
        for p in prices:
            rsi.update(p)
        
        assert rsi.is_ready
        # RSI should be around 50 for constant prices
        assert 0 <= float(rsi.value) <= 100


class TestMACD:
    """Test MACD indicator"""
    
    def test_macd_calculation(self):
        """Test MACD calculation"""
        macd = MACD(fast_period=3, slow_period=6, signal_period=3)
        
        prices = [Decimal(str(p)) for p in range(10, 30)]
        for p in prices:
            macd.update(p)
        
        assert macd.is_ready
        assert macd.macd_line is not None
        assert macd.signal_line is not None
    
    def test_macd_histogram(self):
        """Test MACD histogram"""
        macd = MACD(fast_period=3, slow_period=6, signal_period=3)
        
        prices = [Decimal(str(p)) for p in range(10, 30)]
        for p in prices:
            macd.update(p)
        
        assert macd.histogram is not None
        # Histogram = MACD line - Signal line
        expected = macd.macd_line - macd.signal_line
        assert abs(float(macd.histogram) - float(expected)) < 0.001


class TestBollingerBands:
    """Test Bollinger Bands"""
    
    def test_bb_calculation(self):
        """Test Bollinger Bands calculation"""
        bb = BollingerBands(period=5)
        
        prices = [Decimal("100")] * 5
        for p in prices:
            bb.update(p)
        
        assert bb.is_ready
        assert bb.middle is not None
        assert bb.upper is not None
        assert bb.lower is not None
        # All bands should be equal for constant prices
        assert bb.upper == bb.lower == bb.middle
    
    def test_bb_percent_b(self):
        """Test %B calculation"""
        bb = BollingerBands(period=5, num_std=2.0)
        
        # Variable prices
        prices = [Decimal("100"), Decimal("110"), Decimal("100"), Decimal("110"), Decimal("100")]
        for p in prices:
            bb.update(p)
        
        percent_b = bb.percent_b(Decimal("105"))
        assert percent_b is not None
        # %B should be between 0 and 1 for price within bands
        assert 0 <= float(percent_b) <= 1


class TestATR:
    """Test Average True Range"""
    
    def test_atr_calculation(self):
        """Test ATR calculation"""
        atr = ATR(period=3)
        
        # OHLC data
        for i in range(5):
            high = Decimal(str(100 + i + 2))
            low = Decimal(str(100 + i - 2))
            close = Decimal(str(100 + i))
            atr.update_with_ohlc(high, low, close)
        
        assert atr.is_ready
        assert atr.value is not None
        assert float(atr.value) > 0


class TestIndicatorManager:
    """Test Indicator Manager"""
    
    def test_add_indicator(self):
        """Test adding indicator"""
        manager = IndicatorManager()
        sma = SMA(period=5)
        
        manager.add("SMA5", sma)
        
        retrieved = manager.get("SMA5")
        assert retrieved == sma
    
    def test_update_all(self):
        """Test updating all indicators"""
        manager = IndicatorManager()
        
        manager.add("SMA5", SMA(period=5))
        manager.add("SMA10", SMA(period=10))
        
        # Update multiple times
        for _ in range(10):
            results = manager.update_all(Decimal("100"))
        
        # Both should be ready after 10 updates
        assert all(v is not None for v in results.values())
    
    def test_get_all_values(self):
        """Test getting all indicator values"""
        manager = IndicatorManager()
        
        sma5 = SMA(period=5)
        sma10 = SMA(period=10)
        
        manager.add("SMA5", sma5)
        manager.add("SMA10", sma10)
        
        # Update and make ready
        for _ in range(10):
            manager.update_all(Decimal("100"))
        
        values = manager.get_all_values()
        assert "SMA5" in values
        assert "SMA10" in values
        assert values["SMA5"] == Decimal("100")
        assert values["SMA10"] == Decimal("100")
