"""Tests for strategy manager"""

import pytest
from decimal import Decimal

from src.algorithms.strategy_manager import StrategyManager
from src.algorithms.strategy import StrategyConfig, StrategyState
from src.algorithms.sample_strategies import SMAStrategy, MACDStrategy
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

    def test_add_strategy(self, manager):
        """Test adding an existing strategy instance to manager"""
        manager.register_strategy_class("SMA", SMAStrategy)

        config = StrategyConfig(name="ExternalSMA", symbols=[Symbol(ticker="AAPL")])
        strategy = manager.create_strategy("SMA", config)

        new_manager = StrategyManager(manager._portfolio, manager._event_bus)
        new_manager.add_strategy(strategy)

        assert strategy.strategy_id in new_manager._strategies
        assert new_manager.get_strategy(strategy.strategy_id) == strategy
        assert len(new_manager.get_all_strategies()) == 1

    def test_remove_nonexistent_strategy(self, manager):
        """Test removing a strategy that does not exist returns False"""
        result = manager.remove_strategy("NONEXISTENT_ID")

        assert result is False

    def test_get_running_strategies_after_pause(self, manager):
        """Test get_running_strategies excludes paused strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)

        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            manager.create_strategy("SMA", config)

        manager.start_all()

        all_strategies = manager.get_all_strategies()
        manager.pause_strategy(all_strategies[1].strategy_id)

        running = manager.get_running_strategies()
        assert len(running) == 2
        for s in running:
            assert s.is_running

    def test_pause_all(self, manager):
        """Test pause_all pauses all running strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)

        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            manager.create_strategy("SMA", config)

        manager.start_all()
        assert len(manager.get_running_strategies()) == 3

        paused = manager.pause_all()

        assert paused == 3
        assert len(manager.get_running_strategies()) == 0

        for s in manager.get_all_strategies():
            assert s.state == StrategyState.PAUSED

    def test_resume_all(self, manager):
        """Test resume_all resumes all paused strategies"""
        manager.register_strategy_class("SMA", SMAStrategy)

        for i in range(3):
            config = StrategyConfig(
                name=f"Strategy{i}",
                symbols=[Symbol(ticker="AAPL")]
            )
            manager.create_strategy("SMA", config)

        manager.start_all()
        manager.pause_all()
        assert len(manager.get_running_strategies()) == 0

        resumed = 0
        for s in manager.get_all_strategies():
            if manager.resume_strategy(s.strategy_id):
                resumed += 1

        assert resumed == 3
        assert len(manager.get_running_strategies()) == 3

    def test_get_summary_with_multiple_strategies(self, manager):
        """Test get_summary with multiple strategies of different types"""
        manager.register_strategy_class("SMA", SMAStrategy)
        manager.register_strategy_class("MACD", MACDStrategy)

        config1 = StrategyConfig(
            name="SMA1", symbols=[Symbol(ticker="AAPL")],
            parameters={"fast_period": 5}
        )
        config2 = StrategyConfig(
            name="MACD1", symbols=[Symbol(ticker="GOOGL")],
            parameters={"fast_period": 12, "slow_period": 26}
        )

        s1 = manager.create_strategy("SMA", config1)
        s2 = manager.create_strategy("MACD", config2)
        s1.start()

        summary = manager.get_summary()

        assert summary['total_strategies'] == 2
        assert summary['running_strategies'] == 1
        assert len(summary['strategies']) == 2
        assert s1.strategy_id in summary['strategies']
        assert s2.strategy_id in summary['strategies']
        assert summary['strategies'][s1.strategy_id]['state'] == 'RUNNING'
        assert summary['strategies'][s2.strategy_id]['state'] == 'INITIALIZED'
        assert 'SMA' in summary['registered_classes']
        assert 'MACD' in summary['registered_classes']
