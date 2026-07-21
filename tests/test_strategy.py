"""Tests for strategy base class"""

import pytest
from decimal import Decimal

from src.algorithms.strategy import Strategy, StrategyConfig, StrategyState
from src.algorithms.sample_strategies import SMAStrategy, RSIStrategy
from src.portfolio.portfolio import Portfolio
from src.engine.events import EventBus
from src.data.models import Symbol
from src.trading.models import OrderSide, OrderType


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


class TestStrategyTrading:
    """Test Strategy trading methods"""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def portfolio(self):
        return Portfolio(initial_cash=Decimal("100000"))

    @pytest.fixture
    def symbol(self):
        return Symbol(ticker="AAPL")

    @pytest.fixture
    def strategy_config(self, symbol):
        return StrategyConfig(
            name="Trader",
            symbols=[symbol],
            parameters={"param1": 100}
        )

    @pytest.fixture
    def strategy(self, strategy_config, portfolio, event_bus):
        s = MockStrategy(strategy_config, portfolio, event_bus)
        s.start()
        return s

    def test_buy_market(self, strategy, symbol):
        """Test buy_market calls submit_order with BUY MARKET args"""
        from unittest.mock import MagicMock
        mock_order = MagicMock()
        strategy.submit_order = MagicMock(return_value=mock_order)

        result = strategy.buy_market(symbol, Decimal("10"))

        strategy.submit_order.assert_called_once_with(
            symbol, OrderSide.BUY, Decimal("10"), OrderType.MARKET
        )
        assert result is mock_order

    def test_sell_market(self, strategy, symbol):
        """Test sell_market calls submit_order with SELL MARKET args"""
        from unittest.mock import MagicMock
        mock_order = MagicMock()
        strategy.submit_order = MagicMock(return_value=mock_order)

        result = strategy.sell_market(symbol, Decimal("5"))

        strategy.submit_order.assert_called_once_with(
            symbol, OrderSide.SELL, Decimal("5"), OrderType.MARKET
        )
        assert result is mock_order

    def test_buy_limit(self, strategy, symbol):
        """Test buy_limit calls submit_order with BUY LIMIT args"""
        from unittest.mock import MagicMock
        mock_order = MagicMock()
        strategy.submit_order = MagicMock(return_value=mock_order)

        result = strategy.buy_limit(symbol, Decimal("10"), Decimal("150.00"))

        strategy.submit_order.assert_called_once_with(
            symbol, OrderSide.BUY, Decimal("10"), OrderType.LIMIT, Decimal("150.00")
        )
        assert result is mock_order

    def test_sell_limit(self, strategy, symbol):
        """Test sell_limit calls submit_order with SELL LIMIT args"""
        from unittest.mock import MagicMock
        mock_order = MagicMock()
        strategy.submit_order = MagicMock(return_value=mock_order)

        result = strategy.sell_limit(symbol, Decimal("20"), Decimal("200.00"))

        strategy.submit_order.assert_called_once_with(
            symbol, OrderSide.SELL, Decimal("20"), OrderType.LIMIT, Decimal("200.00")
        )
        assert result is mock_order

    def test_submit_order_when_paused(self, strategy_config, portfolio, event_bus, symbol):
        """Test submit_order returns None when strategy is paused"""
        strategy = MockStrategy(strategy_config, portfolio, event_bus)

        order = strategy.buy_market(symbol, Decimal("10"))

        assert order is None

    def test_get_position_with_no_holding(self, strategy, symbol):
        """Test get_position returns 0 when no position"""
        position = strategy.get_position(symbol)

        assert position == Decimal("0")

    def test_get_position_with_holding(self, strategy, symbol, portfolio):
        """Test get_position returns existing position quantity"""
        from src.trading.models import Position

        pos = Position(symbol=symbol, side=OrderSide.BUY, quantity=Decimal("50"))
        portfolio.update_position(pos)

        result = strategy.get_position(symbol)

        assert result == Decimal("50")

    def test_param_method(self, strategy):
        """Test param method returns config values"""
        assert strategy.param("param1") == 100
        assert strategy.param("nonexistent") is None
        assert strategy.param("nonexistent", 42) == 42

    def test_param_method_after_set_param(self, strategy):
        """Test param reflects set_param changes"""
        strategy.set_param("dynamic", "hello")
        assert strategy.param("dynamic") == "hello"

        strategy.set_param("param1", 999)
        assert strategy.param("param1") == 999

    def test_buying_power_via_portfolio(self, portfolio):
        """Test buying power calculation"""
        power = portfolio.get_buying_power()
        assert power == Decimal("100000")

        power_with_margin = portfolio.get_buying_power(margin_requirement=0.5)
        assert power_with_margin == Decimal("200000")

    def test_strategy_id_uniqueness(self, strategy_config, portfolio, event_bus):
        """Test each strategy gets a unique ID"""
        s1 = MockStrategy(strategy_config, portfolio, event_bus)
        s2 = MockStrategy(strategy_config, portfolio, event_bus)

        assert s1.strategy_id != s2.strategy_id
        assert s1.strategy_id.startswith("STRAT_")


class TestSampleStrategies:
    """Test sample strategies"""

    @pytest.fixture
    def portfolio(self):
        return Portfolio(initial_cash=Decimal("100000"))

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def symbol(self):
        return Symbol(ticker="AAPL")

    def test_sma_strategy_quantity_calculation(self, symbol, portfolio, event_bus):
        """Test SMA strategy position sizing"""
        config = StrategyConfig(
            name="SMA_Test",
            symbols=[symbol],
            parameters={"fast_period": 5, "slow_period": 20, "position_size": 0.2}
        )
        strategy = SMAStrategy(config, portfolio, event_bus)

        quantity = strategy._calculate_quantity(Decimal("150.00"))

        assert quantity > 0
        position_value = portfolio.total_value * Decimal("0.2")
        expected_quantity = int(position_value / Decimal("150.00"))
        assert quantity == Decimal(max(expected_quantity, 1))

    def test_rsi_strategy_quantity_calculation(self, symbol, portfolio, event_bus):
        """Test RSI strategy position sizing"""
        config = StrategyConfig(
            name="RSI_Test",
            symbols=[symbol],
            parameters={"rsi_period": 14, "oversold": 30, "overbought": 70, "position_size": 0.15}
        )
        strategy = RSIStrategy(config, portfolio, event_bus)

        quantity = strategy._calculate_quantity(Decimal("200.00"))

        assert quantity > 0
        position_value = portfolio.total_value * Decimal("0.15")
        expected_quantity = int(position_value / Decimal("200.00"))
        assert quantity == Decimal(max(expected_quantity, 1))


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
