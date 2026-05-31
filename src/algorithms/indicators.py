"""Technical indicators for AlgoEngine"""

from abc import ABC, abstractmethod
from typing import Optional, Dict
from decimal import Decimal
from collections import deque

import numpy as np


class Indicator(ABC):
    """Base class for technical indicators"""
    
    def __init__(self, period: int = 14, name: str = "") -> None:
        self._period = period
        self._name = name or self.__class__.__name__
        self._values: deque = deque(maxlen=period * 2)
        self._result: Optional[Decimal] = None
    
    @property
    def name(self) -> str:
        """Indicator name"""
        return self._name
    
    @property
    def period(self) -> int:
        """Indicator period"""
        return self._period
    
    @property
    def value(self) -> Optional[Decimal]:
        """Current indicator value"""
        return self._result
    
    @property
    def is_ready(self) -> bool:
        """Check if indicator has enough data"""
        return len(self._values) >= self._period
    
    def update(self, price: Decimal) -> Optional[Decimal]:
        """Update indicator with new price"""
        self._values.append(float(price))
        
        if len(self._values) >= self._period:
            self._result = self._calculate()
            return self._result
        
        return None
    
    @abstractmethod
    def _calculate(self) -> Decimal:
        """Calculate indicator value - implement in subclass"""
        pass
    
    def reset(self) -> None:
        """Reset indicator state"""
        self._values.clear()
        self._result = None


class SMA(Indicator):
    """Simple Moving Average"""
    
    def __init__(self, period: int = 20) -> None:
        super().__init__(period, f"SMA{period}")
    
    def _calculate(self) -> Decimal:
        """Calculate SMA"""
        values = list(self._values)[-self._period:]
        return Decimal(str(np.mean(values)))


class EMA(Indicator):
    """Exponential Moving Average"""
    
    def __init__(self, period: int = 20) -> None:
        super().__init__(period, f"EMA{period}")
        self._ema: Optional[float] = None
    
    def _calculate(self) -> Decimal:
        """Calculate EMA"""
        values = list(self._values)[-self._period:]
        
        if self._ema is None:
            # Initialize with SMA
            self._ema = np.mean(values)
        else:
            # EMA formula: EMA = Price * k + EMA_prev * (1 - k)
            multiplier = 2.0 / (self._period + 1)
            self._ema = values[-1] * multiplier + self._ema * (1 - multiplier)
        
        return Decimal(str(self._ema))
    
    def reset(self) -> None:
        """Reset EMA state"""
        super().reset()
        self._ema = None


class RSI(Indicator):
    """Relative Strength Index"""
    
    def __init__(self, period: int = 14) -> None:
        super().__init__(period, f"RSI{period}")
        self._prev_price: Optional[float] = None
        self._avg_gain: float = 0.0
        self._avg_loss: float = 0.0
    
    def _calculate(self) -> Decimal:
        """Calculate RSI"""
        values = list(self._values)
        
        if len(values) < 2:
            return Decimal("50")
        
        current_price = values[-1]
        
        if self._prev_price is None:
            # First calculation - use simple averages
            gains = []
            losses = []
            for i in range(1, len(values)):
                change = values[i] - values[i-1]
                if change > 0:
                    gains.append(change)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(change))
            
            self._avg_gain = np.mean(gains[-self._period:]) if gains else 0.0001
            self._avg_loss = np.mean(losses[-self._period:]) if losses else 0.0001
        else:
            # Subsequent calculations - smoothed averages
            change = current_price - self._prev_price
            gain = max(change, 0)
            loss = abs(min(change, 0))
            
            # Smoothed averages
            alpha = 1.0 / self._period
            self._avg_gain = self._avg_gain * (1 - alpha) + gain * alpha
            self._avg_loss = self._avg_loss * (1 - alpha) + loss * alpha
        
        self._prev_price = current_price
        
        # RSI calculation
        if self._avg_loss == 0:
            return Decimal("100")
        
        rs = self._avg_gain / self._avg_loss
        rsi = 100.0 - (100.0 / (1 + rs))
        
        return Decimal(str(rsi))
    
    def reset(self) -> None:
        """Reset RSI state"""
        super().reset()
        self._prev_price = None
        self._avg_gain = 0.0
        self._avg_loss = 0.0


class MACD:
    """Moving Average Convergence Divergence"""
    
    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> None:
        self._fast = EMA(fast_period)
        self._slow = EMA(slow_period)
        self._signal_period = signal_period
        self._macd_line: Optional[Decimal] = None
        self._signal_line: Optional[Decimal] = None
        self._histogram: Optional[Decimal] = None
        self._signal_values: deque = deque(maxlen=signal_period * 2)
    
    @property
    def is_ready(self) -> bool:
        """Check if MACD is ready"""
        return self._fast.is_ready and self._slow.is_ready
    
    def update(self, price: Decimal) -> None:
        """Update MACD with new price"""
        self._fast.update(price)
        self._slow.update(price)
        
        if self._fast.is_ready and self._slow.is_ready:
            # Calculate MACD line
            fast_val = float(self._fast.value) if self._fast.value else 0
            slow_val = float(self._slow.value) if self._slow.value else 0
            macd_value = fast_val - slow_val
            self._macd_line = Decimal(str(macd_value))
            
            # Calculate signal line (EMA of MACD)
            self._signal_values.append(macd_value)
            if len(self._signal_values) >= self._signal_period:
                alpha = 2.0 / (self._signal_period + 1)
                signal_val = np.mean(list(self._signal_values)[:self._signal_period])
                # Simple EMA for signal
                self._signal_line = Decimal(str(np.mean(list(self._signal_values)[-self._signal_period:])))
            
            # Calculate histogram
            if self._signal_line:
                self._histogram = self._macd_line - self._signal_line
    
    @property
    def macd_line(self) -> Optional[Decimal]:
        """MACD line value"""
        return self._macd_line
    
    @property
    def signal_line(self) -> Optional[Decimal]:
        """Signal line value"""
        return self._signal_line
    
    @property
    def histogram(self) -> Optional[Decimal]:
        """MACD histogram"""
        return self._histogram


class BollingerBands:
    """Bollinger Bands"""
    
    def __init__(self, period: int = 20, num_std: float = 2.0) -> None:
        self._period = period
        self._num_std = num_std
        self._values: deque = deque(maxlen=period * 2)
        self._middle: Optional[Decimal] = None
        self._upper: Optional[Decimal] = None
        self._lower: Optional[Decimal] = None
    
    @property
    def is_ready(self) -> bool:
        """Check if Bollinger Bands are ready"""
        return len(self._values) >= self._period
    
    def update(self, price: Decimal) -> None:
        """Update Bollinger Bands"""
        self._values.append(float(price))
        
        if len(self._values) >= self._period:
            values = list(self._values)[-self._period:]
            mean = np.mean(values)
            std = np.std(values)
            
            self._middle = Decimal(str(mean))
            self._upper = Decimal(str(mean + std * self._num_std))
            self._lower = Decimal(str(mean - std * self._num_std))
    
    @property
    def middle(self) -> Optional[Decimal]:
        """Middle band (SMA)"""
        return self._middle
    
    @property
    def upper(self) -> Optional[Decimal]:
        """Upper band"""
        return self._upper
    
    @property
    def lower(self) -> Optional[Decimal]:
        """Lower band"""
        return self._lower
    
    def percent_b(self, price: Decimal) -> Optional[Decimal]:
        """Calculate %B for given price"""
        if not self.is_ready or self._upper == self._lower:
            return None
        
        upper = float(self._upper)
        lower = float(self._lower)
        p = float(price)
        
        percent_b = (p - lower) / (upper - lower)
        return Decimal(str(percent_b))


class ATR(Indicator):
    """Average True Range"""
    
    def __init__(self, period: int = 14) -> None:
        super().__init__(period, f"ATR{period}")
        self._prev_close: Optional[float] = None
        self._atr: float = 0.0
    
    def update_with_ohlc(self, high: Decimal, low: Decimal, close: Decimal) -> Optional[Decimal]:
        """Update ATR with OHLC data"""
        h, l, c = float(high), float(low), float(close)
        
        if self._prev_close is not None:
            tr1 = h - l  # High - Low
            tr2 = abs(h - self._prev_close)  # |High - Close_prev|
            tr3 = abs(l - self._prev_close)  # |Low - Close_prev|
            true_range = max(tr1, tr2, tr3)
        else:
            true_range = h - l
        
        self._values.append(true_range)
        self._prev_close = c
        
        if len(self._values) >= self._period:
            self._result = self._calculate()
            return self._result
        
        return None
    
    def _calculate(self) -> Decimal:
        """Calculate ATR"""
        values = list(self._values)[-self._period:]
        return Decimal(str(np.mean(values)))
    
    def reset(self) -> None:
        """Reset ATR state"""
        super().reset()
        self._prev_close = None
        self._atr = 0.0


class IndicatorManager:
    """Manage multiple indicators"""
    
    def __init__(self) -> None:
        self._indicators: Dict[str, Indicator] = {}
    
    def add(self, name: str, indicator: Indicator) -> None:
        """Add an indicator"""
        self._indicators[name] = indicator
    
    def get(self, name: str) -> Optional[Indicator]:
        """Get indicator by name"""
        return self._indicators.get(name)
    
    def update_all(self, price: Decimal) -> Dict[str, Optional[Decimal]]:
        """Update all indicators with new price"""
        results = {}
        for name, indicator in self._indicators.items():
            results[name] = indicator.update(price)
        return results
    
    def get_all_values(self) -> Dict[str, Optional[Decimal]]:
        """Get all indicator values"""
        return {
            name: indicator.value for name, indicator in self._indicators.items()
        }
    
    def reset_all(self) -> None:
        """Reset all indicators"""
        for indicator in self._indicators.values():
            indicator.reset()
