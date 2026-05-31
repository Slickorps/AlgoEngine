"""Tests for strategy base class"""

import pytest
from decimal import Decimal

from src.algorithms.strategy import Strategy, StrategyConfig, StrategyState
from src.portfolio.portfolio import Portfolio
from src.engine.events import EventBus
from src.data.models import Symbol


class MockStrategy(Strategy):
    """Mock implementation of Strategy for testing"""
    
    def __init__(self, config, portfolio, event_bus):
        super().__init__(config, portfolio, event_bus)
        self.bar_count = 0
        self.tick_count = 0
        self.fill_count = 0
    
    def on_bar(self, bar):
        self.bar_count += 1
    
    def on_tick(self, tick):
        self.tick_count += 1
    
    def on_fill(self, fill):
        self.fill_count += 1


class TestStrategyBase:
    """Test Strategy base class"""
    
    @pytest.fixture
    def event_bus(self):
        return EventBus()
    
    @pytest.fixture
    def portfolio(self):
        return Portfolio(initial_cash=Decimal("100000"))
    
    @pytest.fixture
    def strategy_config(self):
        return StrategyConfig(
            name="TestStrategy",
            symbols=[Symbol(ticker="AAPL")],
            parameters={"param1": 100}
        )
    
    @pytest.fixture
    def strategy(self, strategy_config, portfolio, event_bus):
        return MockStrategy(strategy_config, portfolio, event_bus)
    
    def test_strategy_initialization(self, strategy):
        """Test strategy initialization"""
        assert strategy.name == "TestStrategy"
        assert strategy.state == StrategyState.INITIALIZED
        assert strategy.strategy_id.startswith("STRAT_")
        assert Symbol(ticker="AAPL") in strategy.symbols
    
    def test_strategy_start(self, strategy):
        """Test starting strategy"""
        strategy.start()
        
        assert strategy.state == StrategyState.RUNNING
        assert strategy.is_running
    
    def test_strategy_pause(self, strategy):
        """Test pausing strategy"""
        strategy.start()
        strategy.pause()
        
        assert strategy.state == StrategyState.PAUSED
        assert not strategy.is_running
    
    def test_strategy_resume(self, strategy):
        """Test resuming strategy"""
        strategy.start()
        strategy.pause()
        strategy.resume()
        
        assert strategy.state == StrategyState.RUNNING
        assert strategy.is_running
    
    def test_strategy_stop(self, strategy):
        """Test stopping strategy"""
        strategy.start()
        strategy.stop()
        
        assert strategy.state == StrategyState.STOPPED
        assert not strategy.is_running
    
    def test_parameter_access(self, strategy):
        """Test parameter access"""
        assert strategy.param("param1") == 100
        assert strategy.param("nonexistent", "default") == "default"
        
        strategy.set_param("new_param", 50)
        assert strategy.param("new_param") == 50
    
    def test_get_summary(self, strategy):
        """Test strategy summary"""
        summary = strategy.get_summary()
        
        assert summary['name'] == "TestStrategy"
        assert 'strategy_id' in summary
        assert 'state' in summary
        assert 'symbols' in summary


class TestStrategyConfig:
    """Test StrategyConfig"""
    
    def test_config_creation(self):
        """Test creating config"""
        config = StrategyConfig(
            name="MyStrategy",
            symbols=[Symbol(ticker="AAPL")],
            parameters={"fast": 10, "slow": 20}
        )
        
        assert config.name == "MyStrategy"
        assert config.get("fast") == 10
        assert config.get("slow") == 20
        assert config.get("missing", 50) == 50
    
    def test_config_enabled(self):
        """Test config enabled flag"""
        config = StrategyConfig(
            name="MyStrategy",
            symbols=[Symbol(ticker="AAPL")],
            enabled=False
        )
        
        assert not config.enabled
