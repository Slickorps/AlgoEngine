"""Tests for strategy manager"""

import pytest
from decimal import Decimal

from src.algorithms.strategy_manager import StrategyManager
from src.algorithms.strategy import StrategyConfig
from src.algorithms.sample_strategies import SMAStrategy
from src.portfolio.portfolio import Portfolio
from src.engine.events import EventBus
from src.data.models import Symbol


class TestStrategyManager:
    """Test StrategyManager class"""
    
    @pytest.fixture
    def manager(self):
        portfolio = Portfolio(initial_cash=Decimal("100000"))
        event_bus = EventBus()
        return StrategyManager(portfolio, event_bus)
    
    def test_register_strategy_class(self, manager):
        """Test registering strategy class"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        assert "SMA" in manager._strategy_classes
    
    def test_create_strategy(self, manager):
        """Test creating strategy from class"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        config = StrategyConfig(
            name="TestSMA",
            symbols=[Symbol(ticker="AAPL")],
            parameters={"fast_period": 10, "slow_period": 30}
        )
        
        strategy = manager.create_strategy("SMA", config)
        
        assert strategy is not None
        assert strategy.name == "TestSMA"
        assert strategy.strategy_id in manager._strategies
    
    def test_create_unknown_strategy(self, manager):
        """Test creating unknown strategy class"""
        config = StrategyConfig(
            name="Unknown",
            symbols=[Symbol(ticker="AAPL")]
        )
        
        strategy = manager.create_strategy("Unknown", config)
        
        assert strategy is None
    
    def test_get_strategy(self, manager):
        """Test getting strategy by ID"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        config = StrategyConfig(
            name="TestSMA",
            symbols=[Symbol(ticker="AAPL")]
        )
        
        strategy = manager.create_strategy("SMA", config)
        retrieved = manager.get_strategy(strategy.strategy_id)
        
        assert retrieved == strategy
    
    def test_get_all_strategies(self, manager):
        """Test getting all strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            manager.create_strategy("SMA", config)
        
        all_strategies = manager.get_all_strategies()
        assert len(all_strategies) == 3
    
    def test_remove_strategy(self, manager):
        """Test removing strategy"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        config = StrategyConfig(name="Test", symbols=[Symbol(ticker="AAPL")])
        strategy = manager.create_strategy("SMA", config)
        
        result = manager.remove_strategy(strategy.strategy_id)
        
        assert result is True
        assert manager.get_strategy(strategy.strategy_id) is None
    
    def test_start_all(self, manager):
        """Test starting all strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            manager.create_strategy("SMA", config)
        
        started = manager.start_all()
        
        assert started == 3
        assert len(manager.get_running_strategies()) == 3
    
    def test_stop_all(self, manager):
        """Test stopping all strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            strategy = manager.create_strategy("SMA", config)
            strategy.start()
        
        stopped = manager.stop_all()
        
        assert stopped == 3
        assert len(manager.get_running_strategies()) == 0
    
    def test_get_summary(self, manager):
        """Test getting manager summary"""
        manager.register_strategy_class("SMA", SMAStrategy)
        
        config = StrategyConfig(name="Test", symbols=[Symbol(ticker="AAPL")])
        manager.create_strategy("SMA", config)
        
        summary = manager.get_summary()
        
        assert summary['total_strategies'] == 1
        assert 'SMA' in summary['registered_classes']
