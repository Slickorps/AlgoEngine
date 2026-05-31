"""Sample trading strategies for AlgoEngine"""

from decimal import Decimal
from typing import Optional

from .strategy import Strategy
from .indicators import SMA, RSI, MACD, BollingerBands
from ..data.models import Bar
from ..trading.models import OrderSide


class SMAStrategy(Strategy):
    """Simple Moving Average Crossover Strategy"""
    
    def __init__(self, config, portfolio, event_bus) -> None:
        super().__init__(config, portfolio, event_bus)
        
        # Get parameters
        self._fast_period = config.get("fast_period", 10)
        self._slow_period = config.get("slow_period", 30)
        
        # Initialize indicators
        self._fast_sma = SMA(self._fast_period)
        self._slow_sma = SMA(self._slow_period)
        
        # Track position state
        self._in_position = False
        self._position_side: Optional[OrderSide] = None
    
    def on_bar(self, bar: Bar) -> None:
        """Process new bar data"""
        # Update indicators
        fast_val = self._fast_sma.update(bar.close)
        slow_val = self._slow_sma.update(bar.close)
        
        # Wait for indicators to be ready
        if not (self._fast_sma.is_ready and self._slow_sma.is_ready):
            return
        
        # Get current position
        position = self.get_position(bar.symbol)
        self._in_position = position != 0
        
        if self._in_position:
            # Check for exit signal
            if fast_val and slow_val:
                if position > 0 and fast_val < slow_val:
                    # Exit long - sell
                    self.sell_market(bar.symbol, abs(position))
                elif position < 0 and fast_val > slow_val:
                    # Exit short - buy to cover
                    self.buy_market(bar.symbol, abs(position))
        else:
            # Check for entry signal
            if fast_val and slow_val:
                if fast_val > slow_val:
                    # Bullish crossover - buy
                    quantity = self._calculate_quantity(bar.close)
                    if quantity > 0:
                        self.buy_market(bar.symbol, quantity)
                elif fast_val < slow_val:
                    # Bearish crossover - sell short
                    quantity = self._calculate_quantity(bar.close)
                    if quantity > 0:
                        # For simplicity, we'll just not take short positions
                        # In a real system, you'd handle short selling
                        pass
    
    def _calculate_quantity(self, price: Decimal) -> Decimal:
        """Calculate position size"""
        # Get position sizing from config
        risk_pct = self.param("risk_per_trade", 0.02)  # 2% default
        position_pct = self.param("position_size", 0.1)  # 10% default
        
        portfolio_value = self._portfolio.total_value
        position_value = portfolio_value * Decimal(str(position_pct))
        
        quantity = int(position_value / price)
        return Decimal(max(quantity, 1))
    
    def get_summary(self) -> dict:
        """Get strategy summary"""
        summary = super().get_summary()
        summary.update({
            "strategy_type": "SMA_Crossover",
            "fast_period": self._fast_period,
            "slow_period": self._slow_period,
            "in_position": self._in_position
        })
        return summary


class RSIStrategy(Strategy):
    """RSI Mean Reversion Strategy"""
    
    def __init__(self, config, portfolio, event_bus) -> None:
        super().__init__(config, portfolio, event_bus)
        
        # Get parameters
        self._period = config.get("rsi_period", 14)
        self._oversold = config.get("oversold", 30)
        self._overbought = config.get("overbought", 70)
        
        # Initialize RSI
        self._rsi = RSI(self._period)
    
    def on_bar(self, bar: Bar) -> None:
        """Process new bar data"""
        # Update RSI
        rsi_val = self._rsi.update(bar.close)
        
        if not self._rsi.is_ready or rsi_val is None:
            return
        
        rsi_float = float(rsi_val)
        position = self.get_position(bar.symbol)
        
        # RSI oversold - buy signal
        if rsi_float < self._oversold and position == 0:
            quantity = self._calculate_quantity(bar.close)
            if quantity > 0:
                self.buy_market(bar.symbol, quantity)
        
        # RSI overbought - sell signal
        elif rsi_float > self._overbought and position > 0:
            self.sell_market(bar.symbol, position)
    
    def _calculate_quantity(self, price: Decimal) -> Decimal:
        """Calculate position size"""
        position_pct = self.param("position_size", 0.1)
        portfolio_value = self._portfolio.total_value
        position_value = portfolio_value * Decimal(str(position_pct))
        
        quantity = int(position_value / price)
        return Decimal(max(quantity, 1))
    
    def get_summary(self) -> dict:
        """Get strategy summary"""
        summary = super().get_summary()
        summary.update({
            "strategy_type": "RSI_MeanReversion",
            "rsi_period": self._period,
            "oversold": self._oversold,
            "overbought": self._overbought,
            "current_rsi": float(self._rsi.value) if self._rsi.value else None
        })
        return summary


class MACDStrategy(Strategy):
    """MACD Trend Following Strategy"""
    
    def __init__(self, config, portfolio, event_bus) -> None:
        super().__init__(config, portfolio, event_bus)
        
        # Get parameters
        fast = config.get("fast_period", 12)
        slow = config.get("slow_period", 26)
        signal = config.get("signal_period", 9)
        
        # Initialize MACD
        self._macd = MACD(fast, slow, signal)
    
    def on_bar(self, bar: Bar) -> None:
        """Process new bar data"""
        # Update MACD
        self._macd.update(bar.close)
        
        if not self._macd.is_ready:
            return
        
        histogram = self._macd.histogram
        if histogram is None:
            return
        
        position = self.get_position(bar.symbol)
        hist_float = float(histogram)
        
        # MACD histogram turns positive - bullish signal
        if hist_float > 0 and position == 0:
            quantity = self._calculate_quantity(bar.close)
            if quantity > 0:
                self.buy_market(bar.symbol, quantity)
        
        # MACD histogram turns negative - bearish signal
        elif hist_float < 0 and position > 0:
            self.sell_market(bar.symbol, position)
    
    def _calculate_quantity(self, price: Decimal) -> Decimal:
        """Calculate position size"""
        position_pct = self.param("position_size", 0.1)
        portfolio_value = self._portfolio.total_value
        position_value = portfolio_value * Decimal(str(position_pct))
        
        quantity = int(position_value / price)
        return Decimal(max(quantity, 1))
    
    def get_summary(self) -> dict:
        """Get strategy summary"""
        summary = super().get_summary()
        summary.update({
            "strategy_type": "MACD_Trend",
            "histogram": float(self._macd.histogram) if self._macd.histogram else None
        })
        return summary


class BollingerBandsStrategy(Strategy):
    """Bollinger Bands Mean Reversion Strategy"""
    
    def __init__(self, config, portfolio, event_bus) -> None:
        super().__init__(config, portfolio, event_bus)
        
        # Get parameters
        period = config.get("bb_period", 20)
        num_std = config.get("num_std", 2.0)
        
        # Initialize Bollinger Bands
        self._bb = BollingerBands(period, num_std)
    
    def on_bar(self, bar: Bar) -> None:
        """Process new bar data"""
        # Update Bollinger Bands
        self._bb.update(bar.close)
        
        if not self._bb.is_ready:
            return
        
        position = self.get_position(bar.symbol)
        
        # Get current price position within bands
        percent_b = self._bb.percent_b(bar.close)
        if percent_b is None:
            return
        
        percent_b_float = float(percent_b)
        
        # Price near lower band (< 0) - buy signal
        if percent_b_float < 0.1 and position == 0:
            quantity = self._calculate_quantity(bar.close)
            if quantity > 0:
                self.buy_market(bar.symbol, quantity)
        
        # Price near upper band (> 1) - sell signal
        elif percent_b_float > 0.9 and position > 0:
            self.sell_market(bar.symbol, position)
    
    def _calculate_quantity(self, price: Decimal) -> Decimal:
        """Calculate position size"""
        position_pct = self.param("position_size", 0.1)
        portfolio_value = self._portfolio.total_value
        position_value = portfolio_value * Decimal(str(position_pct))
        
        quantity = int(position_value / price)
        return Decimal(max(quantity, 1))
    
    def get_summary(self) -> dict:
        """Get strategy summary"""
        summary = super().get_summary()
        summary.update({
            "strategy_type": "BollingerBands_MeanReversion",
            "upper": float(self._bb.upper) if self._bb.upper else None,
            "lower": float(self._bb.lower) if self._bb.lower else None,
            "percent_b": float(self._bb.percent_b(self.get_latest_price(self._config.symbols[0]))
                               ) if self._bb.is_ready else None
        })
        return summary
