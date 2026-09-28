"""Tests for the sample trading strategies."""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from src.algorithms.sample_strategies import (
    SMAStrategy,
    RSIStrategy,
    MACDStrategy,
    BollingerBandsStrategy,
)
from src.algorithms.strategy import StrategyConfig
from src.portfolio.portfolio import Portfolio
from src.data.models import Symbol, Bar
from src.engine.events import EventBus


@pytest.fixture
def symbol():
    return Symbol(ticker="AAPL", security_type="EQUITY")


@pytest.fixture
def portfolio():
    return Portfolio(initial_cash=Decimal("100000"))


@pytest.fixture
def bus():
    return EventBus()


def make_bar(symbol, close, i=0):
    c = Decimal(str(close))
    return Bar(
        symbol=symbol, timestamp=datetime(2023, 1, 1) + timedelta(days=i),
        open=c, high=c, low=c, close=c, volume=Decimal("100"),
    )


def make_bars(symbol, prices):
    return [make_bar(symbol, p, i) for i, p in enumerate(prices)]


class FakeIndicator:
    """Deterministic stand-in for indicators exposing is_ready/value."""

    def __init__(self, ready=True, returns=None, **attrs):
        self.is_ready = ready
        self._returns = returns
        for key, val in attrs.items():
            setattr(self, key, val)

    def update(self, price):
        return self._returns


class FakeBands:
    def __init__(self, ready=True, percent_b=0.5, upper=None, lower=None):
        self.is_ready = ready
        self._percent_b = percent_b
        self.upper = upper
        self.lower = lower

    def update(self, price):
        pass

    def percent_b(self, price):
        return self._percent_b


def record_orders(strategy):
    calls = []
    strategy.buy_market = lambda sym, qty: calls.append(("buy", qty))
    strategy.sell_market = lambda sym, qty: calls.append(("sell", qty))
    return calls


class TestSMAStrategy:
    def _make(self, symbol, portfolio, bus, fast=2, slow=3):
        cfg = StrategyConfig(
            name="sma", symbols=[symbol],
            parameters={"fast_period": fast, "slow_period": slow},
        )
        strategy = SMAStrategy(cfg, portfolio, bus)
        strategy.start()
        return strategy

    def test_buy_on_golden_cross(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._fast_sma = FakeIndicator(True, Decimal("10"))
        s._slow_sma = FakeIndicator(True, Decimal("5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "buy"

    def test_no_action_before_ready(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._fast_sma = FakeIndicator(False, Decimal("10"))
        s._slow_sma = FakeIndicator(False, Decimal("5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_bearish_entry_is_noop(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._fast_sma = FakeIndicator(True, Decimal("5"))
        s._slow_sma = FakeIndicator(True, Decimal("10"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_exit_long_on_death_cross(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("10")
        s._fast_sma = FakeIndicator(True, Decimal("5"))
        s._slow_sma = FakeIndicator(True, Decimal("10"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == [("sell", Decimal("10"))]

    def test_cover_short_on_golden_cross(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("-5")
        s._fast_sma = FakeIndicator(True, Decimal("10"))
        s._slow_sma = FakeIndicator(True, Decimal("5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == [("buy", Decimal("5"))]

    def test_holding_no_cross_no_action(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("10")
        s._fast_sma = FakeIndicator(True, Decimal("10"))
        s._slow_sma = FakeIndicator(True, Decimal("5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_calculate_quantity(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        assert s._calculate_quantity(Decimal("100")) >= 1

    def test_get_summary(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        summary = s.get_summary()
        assert summary["strategy_type"] == "SMA_Crossover"
        assert summary["fast_period"] == 2
        assert summary["in_position"] is False

    def test_live_data_flow(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        record_orders(s)
        for bar in make_bars(symbol, range(1, 40)):
            s._on_bar(bar)
        assert s.get_summary()["in_position"] in (True, False)


class TestRSIStrategy:
    def _make(self, symbol, portfolio, bus, period=3):
        cfg = StrategyConfig(
            name="rsi", symbols=[symbol],
            parameters={"rsi_period": period, "oversold": 30, "overbought": 70},
        )
        strategy = RSIStrategy(cfg, portfolio, bus)
        strategy.start()
        return strategy

    def test_buy_on_oversold(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._rsi = FakeIndicator(True, Decimal("20"), value=Decimal("20"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "buy"

    def test_sell_on_overbought(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("10")
        s._rsi = FakeIndicator(True, Decimal("80"), value=Decimal("80"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "sell"

    def test_no_action_before_ready(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._rsi = FakeIndicator(False, None, value=None)
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_update_none_returns(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._rsi = FakeIndicator(True, None, value=None)
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_neutral_rsi_no_action(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._rsi = FakeIndicator(True, Decimal("50"), value=Decimal("50"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_calculate_quantity(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        assert s._calculate_quantity(Decimal("50")) >= 1

    def test_get_summary(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        summary = s.get_summary()
        assert summary["strategy_type"] == "RSI_MeanReversion"
        assert summary["rsi_period"] == 3

    def test_live_data_flow(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        record_orders(s)
        for bar in make_bars(symbol, [50 - i * 0.5 for i in range(40)]):
            s._on_bar(bar)
        assert "current_rsi" in s.get_summary()


class TestMACDStrategy:
    def _make(self, symbol, portfolio, bus, fast=2, slow=4, signal=2):
        cfg = StrategyConfig(
            name="macd", symbols=[symbol],
            parameters={"fast_period": fast, "slow_period": slow, "signal_period": signal},
        )
        strategy = MACDStrategy(cfg, portfolio, bus)
        strategy.start()
        return strategy

    def test_buy_on_positive_histogram(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._macd = FakeIndicator(True, None, histogram=Decimal("1.5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "buy"

    def test_sell_on_negative_histogram(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("10")
        s._macd = FakeIndicator(True, None, histogram=Decimal("-1.5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "sell"

    def test_no_action_before_ready(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._macd = FakeIndicator(False, None, histogram=Decimal("1"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_none_histogram_returns(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._macd = FakeIndicator(True, None, histogram=None)
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_calculate_quantity(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        assert s._calculate_quantity(Decimal("100")) >= 1

    def test_get_summary(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        summary = s.get_summary()
        assert summary["strategy_type"] == "MACD_Trend"

    def test_live_data_flow(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        record_orders(s)
        for bar in make_bars(symbol, [100 + (i % 5) for i in range(40)]):
            s._on_bar(bar)
        assert "histogram" in s.get_summary()


class TestBollingerBandsStrategy:
    def _make(self, symbol, portfolio, bus, period=3):
        cfg = StrategyConfig(
            name="bb", symbols=[symbol],
            parameters={"bb_period": period, "num_std": 2.0},
        )
        strategy = BollingerBandsStrategy(cfg, portfolio, bus)
        strategy.start()
        return strategy

    def test_buy_near_lower_band(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._bb = FakeBands(ready=True, percent_b=Decimal("0.05"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "buy"

    def test_sell_near_upper_band(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("10")
        s._bb = FakeBands(ready=True, percent_b=Decimal("0.95"))
        s.on_bar(make_bar(symbol, 100))
        assert calls and calls[0][0] == "sell"

    def test_no_action_before_ready(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._bb = FakeBands(ready=False, percent_b=Decimal("0.05"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_none_percent_b_returns(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s._bb = FakeBands(ready=True, percent_b=None)
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_mid_band_no_action(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        calls = record_orders(s)
        s.get_position = lambda sym: Decimal("0")
        s._bb = FakeBands(ready=True, percent_b=Decimal("0.5"))
        s.on_bar(make_bar(symbol, 100))
        assert calls == []

    def test_calculate_quantity(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        assert s._calculate_quantity(Decimal("100")) >= 1

    def test_get_summary(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        summary = s.get_summary()
        assert summary["strategy_type"] == "BollingerBands_MeanReversion"

    def test_live_data_flow(self, symbol, portfolio, bus):
        s = self._make(symbol, portfolio, bus)
        record_orders(s)
        for bar in make_bars(symbol, [100 + (i % 7) for i in range(40)]):
            s._on_bar(bar)
        assert "percent_b" in s.get_summary()
