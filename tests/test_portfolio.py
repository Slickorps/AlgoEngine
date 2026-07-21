"""Tests for portfolio management"""

import pytest
from decimal import Decimal
from datetime import datetime

from src.portfolio.portfolio import Portfolio, PortfolioSnapshot
from src.trading.models import Trade, OrderSide, Position
from src.data.models import Symbol


class TestPortfolio:
    """Test Portfolio class"""
    
    @pytest.fixture
    def portfolio(self):
        """Create portfolio with $100k initial cash"""
        return Portfolio(initial_cash=Decimal("100000.00"))
    
    def test_portfolio_initialization(self, portfolio):
        """Test portfolio initialization"""
        assert portfolio.cash == Decimal("100000.00")
        assert portfolio.initial_cash == Decimal("100000.00")
        assert portfolio.total_value == Decimal("100000.00")
    
    def test_update_position(self, portfolio):
        """Test updating a position"""
        symbol = Symbol(ticker="AAPL")
        position = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00")
        )
        
        portfolio.update_position(position)
        
        retrieved = portfolio.get_position(symbol)
        assert retrieved == position
        assert portfolio.positions_value == Decimal("16000.00")  # 100 * 160
    
    def test_record_trade_updates_cash(self, portfolio):
        """Test that recording a trade updates cash"""
        trade = Trade(
            trade_id="TRADE_001",
            symbol=Symbol(ticker="AAPL"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("160.00"),
            realized_pnl=Decimal("1000.00"),
            commission=Decimal("5.00"),
            slippage=Decimal("1.00")
        )
        
        initial_cash = portfolio.cash
        portfolio.record_trade(trade)
        
        # Cash should increase by sale value minus costs
        expected_cash_change = trade.quantity * trade.exit_price - trade.commission - trade.slippage
        assert portfolio.cash == initial_cash + expected_cash_change
    
    def test_total_return_calculation(self, portfolio):
        """Test total return calculation"""
        # Initial value = 100000
        # Add a position worth 5000
        position = Position(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00")
        )
        portfolio.update_position(position)
        
        # Cash decreased by position cost
        portfolio.update_cash(Decimal("-15000.00"))
        
        # Position increases in value
        position.current_price = Decimal("160.00")
        portfolio.update_position(position)
        
        # Total value = 85000 cash + 16000 position = 101000
        assert portfolio.total_value == Decimal("101000.00")
        assert portfolio.total_return == Decimal("1000.00")
        assert portfolio.total_return_percent == 1.0
    
    def test_take_snapshot(self, portfolio):
        """Test portfolio snapshot"""
        portfolio.take_snapshot()
        
        snapshots = portfolio.get_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].cash == portfolio.cash
        assert snapshots[0].total_value == portfolio.total_value
    
    def test_get_buying_power(self, portfolio):
        """Test buying power calculation"""
        buying_power = portfolio.get_buying_power(margin_requirement=0.5)
        assert buying_power == Decimal("200000.00")  # 2x with 50% margin
    
    def test_get_summary(self, portfolio):
        """Test portfolio summary"""
        summary = portfolio.get_summary()
        
        assert summary['initial_cash'] == 100000.0
        assert summary['cash'] == 100000.0
        assert summary['position_count'] == 0
        assert summary['trade_count'] == 0


class TestPortfolioSnapshot:
    """Test PortfolioSnapshot class"""
    
    def test_snapshot_creation(self):
        """Test creating a snapshot"""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=Decimal("50000.00"),
            positions_value=Decimal("50000.00"),
            total_value=Decimal("100000.00"),
            unrealized_pnl=Decimal("5000.00"),
            realized_pnl=Decimal("2000.00")
        )
        
        assert snapshot.leverage == 0.5  # 50% in positions
    
    def test_leverage_calculation(self):
        """Test leverage with different exposures"""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=Decimal("10000.00"),
            positions_value=Decimal("90000.00"),
            total_value=Decimal("100000.00"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0")
        )
        
        assert snapshot.leverage == 0.9  # 90% in positions


class TestPortfolioEdgeCases:
    """Edge case and uncovered branch tests for Portfolio"""

    @pytest.fixture
    def portfolio(self):
        return Portfolio(initial_cash=Decimal("100000.00"))

    def test_update_position_removes_when_zero_quantity(self, portfolio):
        symbol = Symbol(ticker="AAPL")
        position = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00"),
        )
        portfolio.update_position(position)
        assert portfolio.get_position(symbol) is not None

        position.quantity = Decimal("0")
        portfolio.update_position(position)
        assert portfolio.get_position(symbol) is None

    def test_get_position_returns_none_for_unknown_symbol(self, portfolio):
        unknown = Symbol(ticker="ZZZZ")
        assert portfolio.get_position(unknown) is None

    def test_total_value_with_multiple_positions(self, portfolio):
        aapl = Symbol(ticker="AAPL")
        tsla = Symbol(ticker="TSLA")

        portfolio.update_position(Position(
            symbol=aapl, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00"),
        ))
        portfolio.update_position(Position(
            symbol=tsla, side=OrderSide.BUY,
            quantity=Decimal("50"), avg_entry_price=Decimal("200.00"),
            current_price=Decimal("220.00"),
        ))
        portfolio.update_cash(Decimal("-15000.00"))  # paid for AAPL
        portfolio.update_cash(Decimal("-10000.00"))  # paid for TSLA

        expected_positions_value = Decimal("100") * Decimal("160.00") + Decimal("50") * Decimal("220.00")
        expected_total = portfolio.cash + expected_positions_value

        assert portfolio.positions_value == Decimal("27000.00")  # 16000 + 11000
        assert portfolio.total_value == portfolio.cash + Decimal("27000.00")

    def test_update_price_changes_position_values(self, portfolio):
        symbol = Symbol(ticker="AAPL")
        position = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
        )
        portfolio.update_position(position)
        portfolio.update_cash(Decimal("-15000.00"))

        assert portfolio.positions_value == Decimal("15000.00")
        assert portfolio.unrealized_pnl == Decimal("0")

        position.current_price = Decimal("170.00")
        portfolio.update_position(position)

        assert portfolio.positions_value == Decimal("17000.00")
        assert portfolio.unrealized_pnl == Decimal("2000.00")

    def test_get_summary_with_positions_and_trades(self, portfolio):
        symbol = Symbol(ticker="AAPL")
        portfolio.update_position(Position(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00"),
        ))
        trade = Trade(
            trade_id="T1",
            symbol=symbol,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            entry_price=Decimal("140.00"),
            exit_price=Decimal("155.00"),
            realized_pnl=Decimal("150.00"),
            commission=Decimal("1.00"),
            slippage=Decimal("0.50"),
        )
        portfolio.record_trade(trade)

        summary = portfolio.get_summary()

        assert summary['position_count'] == 1
        assert summary['trade_count'] == 1
        assert summary['total_value'] > 0
        assert isinstance(summary['timestamp'], str)

    def test_snapshot_comparison(self, portfolio):
        portfolio.take_snapshot()

        symbol = Symbol(ticker="AAPL")
        portfolio.update_position(Position(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00"),
        ))
        portfolio.update_cash(Decimal("-15000.00"))

        portfolio.take_snapshot()

        snapshots = portfolio.get_snapshots()
        assert len(snapshots) == 2
        assert snapshots[0].total_value != snapshots[1].total_value
        assert snapshots[0].positions_value == Decimal("0")
        assert snapshots[1].positions_value == Decimal("16000.00")

    def test_record_trade_short_side(self, portfolio):
        trade = Trade(
            trade_id="T_SHORT",
            symbol=Symbol(ticker="TSLA"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            side=OrderSide.SELL,
            quantity=Decimal("50"),
            entry_price=Decimal("200.00"),
            exit_price=Decimal("180.00"),
            realized_pnl=Decimal("1000.00"),
            commission=Decimal("2.00"),
            slippage=Decimal("0.50"),
        )
        initial_cash = portfolio.cash
        portfolio.record_trade(trade)

        trade_value = trade.quantity * trade.exit_price
        expected = initial_cash - trade_value - trade.commission - trade.slippage
        assert portfolio.cash == expected

    def test_unrealized_pnl_multiple_positions(self, portfolio):
        aapl = Symbol(ticker="AAPL")
        tsla = Symbol(ticker="TSLA")

        portfolio.update_position(Position(
            symbol=aapl, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("170.00"),
        ))
        portfolio.update_position(Position(
            symbol=tsla, side=OrderSide.BUY,
            quantity=Decimal("50"), avg_entry_price=Decimal("200.00"),
            current_price=Decimal("190.00"),
        ))

        aapl_pnl = Decimal("100") * (Decimal("170.00") - Decimal("150.00"))
        tsla_pnl = Decimal("50") * (Decimal("190.00") - Decimal("200.00"))
        assert portfolio.unrealized_pnl == aapl_pnl + tsla_pnl

    def test_get_exposure_by_symbol(self, portfolio):
        aapl = Symbol(ticker="AAPL")
        tsla = Symbol(ticker="TSLA")

        portfolio.update_position(Position(
            symbol=aapl, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
        ))
        portfolio.update_position(Position(
            symbol=tsla, side=OrderSide.BUY,
            quantity=Decimal("50"), avg_entry_price=Decimal("200.00"),
            current_price=Decimal("200.00"),
        ))
        portfolio.update_cash(Decimal("-25000.00"))

        exposure = portfolio.get_exposure_by_symbol()

        total = portfolio.total_value
        assert aapl in exposure
        assert tsla in exposure
        assert exposure[aapl] == Decimal("15000.00") / total
        assert exposure[tsla] == Decimal("10000.00") / total

    def test_get_exposure_by_symbol_zero_total(self):
        portfolio = Portfolio(initial_cash=Decimal("0"))
        assert portfolio.get_exposure_by_symbol() == {}

    def test_leverage_zero_total_value(self, portfolio):
        portfolio.update_cash(Decimal("-100000.00"))
        portfolio.take_snapshot()
        snapshots = portfolio.get_snapshots()
        assert snapshots[0].leverage == 0.0

    def test_total_return_percent_zero_initial(self):
        portfolio = Portfolio(initial_cash=Decimal("0"))
        assert portfolio.total_return_percent == 0.0
