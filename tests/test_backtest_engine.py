"""Tests for backtest engine"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from src.backtesting.engine import BacktestEngine, BacktestConfig
from src.backtesting.results import BacktestResults
from src.algorithms.sample_strategies import SMAStrategy
from src.data.models import Symbol, Bar


class TestBacktestConfig:
    """Test BacktestConfig"""
    
    def test_config_creation(self):
        """Test creating backtest config"""
        config = BacktestConfig(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 12, 31),
            symbols=[Symbol(ticker="AAPL")],
            initial_cash=Decimal("100000")
        )
        
        assert config.start_date == datetime(2023, 1, 1)
        assert config.end_date == datetime(2023, 12, 31)
        assert config.initial_cash == Decimal("100000")
    
    def test_invalid_date_range(self):
        """Test that invalid date range raises error"""
        with pytest.raises(ValueError):
            BacktestConfig(
                start_date=datetime(2023, 12, 31),
                end_date=datetime(2023, 1, 1),
                symbols=[Symbol(ticker="AAPL")]
            )


class TestBacktestEngine:
    """Test BacktestEngine"""
    
    @pytest.fixture
    def config(self):
        return BacktestConfig(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            symbols=[Symbol(ticker="AAPL")],
            initial_cash=Decimal("100000")
        )
    
    @pytest.fixture
    def engine(self, config):
        return BacktestEngine(config)
    
    def test_engine_initialization(self, engine):
        """Test engine initialization"""
        assert engine._config is not None
        assert engine._portfolio is not None
        assert engine._strategy_manager is not None
    
    def test_register_strategy(self, engine):
        """Test registering strategy class"""
        engine.register_strategy("SMA", SMAStrategy)
        
        assert "SMA" in engine._strategy_manager._strategy_classes
    
    def test_add_strategy(self, engine):
        """Test adding strategy"""
        engine.register_strategy("SMA", SMAStrategy)
        
        strategy = engine.add_strategy("SMA", {"fast_period": 5, "slow_period": 10})
        
        assert strategy is not None
        assert strategy.name == "SMA"
    
    def test_load_historical_data(self, engine):
        """Test loading historical data"""
        symbol = Symbol(ticker="AAPL")
        
        bars = [
            Bar(
                symbol=symbol,
                timestamp=datetime(2023, 1, 1) + timedelta(days=i),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("105"),
                volume=1000
            )
            for i in range(5)
        ]
        
        engine.load_historical_data(symbol, bars)
        
        assert symbol in engine._historical_data
        assert len(engine._historical_data[symbol]) == 5


class TestBacktestResults:
    """Test BacktestResults"""
    
    @pytest.fixture
    def results(self):
        return BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 12, 31),
            initial_cash=Decimal("100000"),
            final_value=Decimal("110000")
        )
    
    def test_total_return_calculation(self, results):
        """Test total return calculation"""
        assert results.total_return == Decimal("10000")
        assert results.total_return_percent == 10.0
    
    def test_no_trades(self, results):
        """Test results with no trades"""
        assert results.total_trades == 0
        assert results.win_rate == 0.0
        assert results.avg_trade_return == Decimal("0")
    
    def test_win_rate_calculation(self):
        """Test win rate calculation with trades"""
        from src.trading.models import Trade, OrderSide
        
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 12, 31),
            initial_cash=Decimal("100000"),
            trades=[
                Trade(
                    trade_id="T1",
                    symbol=Symbol(ticker="AAPL"),
                    entry_time=datetime.now(),
                    exit_time=datetime.now(),
                    side=OrderSide.BUY,
                    quantity=Decimal("100"),
                    entry_price=Decimal("100"),
                    exit_price=Decimal("110"),
                    realized_pnl=Decimal("1000"),
                    commission=Decimal("5"),
                    slippage=Decimal("1")
                ),
                Trade(
                    trade_id="T2",
                    symbol=Symbol(ticker="AAPL"),
                    entry_time=datetime.now(),
                    exit_time=datetime.now(),
                    side=OrderSide.BUY,
                    quantity=Decimal("100"),
                    entry_price=Decimal("110"),
                    exit_price=Decimal("105"),
                    realized_pnl=Decimal("-500"),
                    commission=Decimal("5"),
                    slippage=Decimal("1")
                )
            ]
        )
        
        assert results.total_trades == 2
        assert results.winning_trades == 1
        assert results.losing_trades == 1
        assert results.win_rate == 50.0
    
    def test_get_summary(self, results):
        """Test summary generation"""
        summary = results.get_summary()
        
        assert "period" in summary
        assert "returns" in summary
        assert "trades" in summary
        
        assert summary["returns"]["total_return_pct"] == 10.0
        assert summary["trades"]["total"] == 0
    
    def test_to_dict(self, results):
        """Test conversion to dictionary"""
        data = results.to_dict()
        
        assert "start_date" in data
        assert "initial_cash" in data
        assert "total_return" in data
        assert "equity_curve" in data
