"""Tests for risk management"""

import pytest
from decimal import Decimal

from src.risk.risk_manager import RiskManager, RiskContext, RiskRule, RiskRuleType
from src.portfolio.portfolio import Portfolio
from src.trading.models import Order, OrderSide, OrderType, Position
from src.data.models import Symbol


class TestRiskManager:
    """Test RiskManager class"""
    
    @pytest.fixture
    def portfolio(self):
        """Create portfolio with $100k"""
        return Portfolio(initial_cash=Decimal("100000.00"))
    
    @pytest.fixture
    def risk_manager(self, portfolio):
        """Create risk manager"""
        return RiskManager(portfolio)
    
    def test_position_size_limit(self, risk_manager, portfolio):
        """Test position size limit check"""
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("10000"),  # Way too many shares
            order_type=OrderType.MARKET
        )
        
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        
        assert not passed
        assert "Position size" in reason
    
    def test_buying_power_check(self, risk_manager, portfolio):
        """Test insufficient buying power check"""
        # Increase limits so only buying power check fails
        risk_manager._max_position_size_percent = 200.0  # Allow large positions
        risk_manager._max_concentration_percent = 200.0  # Allow concentration
        risk_manager._max_total_exposure_percent = 200.0  # Allow exposure
        
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("800"),  # $120k value, exceeds $100k cash
            order_type=OrderType.MARKET
        )
        
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        
        assert not passed
        assert "Insufficient buying power" in reason
    
    def test_valid_order_passes(self, risk_manager, portfolio):
        """Test that valid order passes risk checks"""
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("50"),  # $7.5k = 7.5% of portfolio, under 10% limit
            order_type=OrderType.MARKET
        )
        
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        
        assert passed
        assert reason == "Risk check passed"
    
    def test_calculate_position_size(self, risk_manager):
        """Test position size calculation"""
        symbol = Symbol(ticker="AAPL")
        price = Decimal("150.00")
        
        shares = risk_manager.calculate_position_size(
            symbol,
            price,
            risk_per_trade_percent=1.0,
            stop_loss_percent=2.0
        )
        
        # Risk per trade = 1% of 100k = $1000
        # Risk per share = 2% of $150 = $3
        # Shares = $1000 / $3 = 333.33, rounded down to 333
        assert shares == Decimal("333")
    
    def test_drawdown_limit(self, risk_manager, portfolio):
        """Test trading halt on excessive drawdown"""
        # Simulate 30% drawdown
        portfolio.update_cash(Decimal("-30000.00"))  # Now at $70k
        
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            order_type=OrderType.MARKET
        )
        
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        
        assert not passed
        assert "drawdown" in reason.lower()
    
    def test_concentration_limit(self, risk_manager, portfolio):
        """Test concentration limit"""
        # Set concentration limit to 10% (lower than position size limit)
        risk_manager._max_concentration_percent = 10.0
        risk_manager._max_position_size_percent = 50.0  # Allow larger positions
        
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),  # $15k = 15% of portfolio, passes 50% position limit but fails 10% concentration
            order_type=OrderType.MARKET
        )
        
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        
        assert not passed
        assert "Concentration" in reason
    
    def test_get_risk_summary(self, risk_manager, portfolio):
        """Test risk summary generation"""
        summary = risk_manager.get_risk_summary()
        
        assert 'current_drawdown' in summary
        assert 'trading_halted' in summary
        assert 'cash_available' in summary
        assert summary['cash_available'] == 100000.0


class TestRiskContext:
    """Test RiskContext"""
    
    def test_risk_context_creation(self):
        """Test creating risk context"""
        portfolio = Portfolio()
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.MARKET
        )
        
        context = RiskContext(
            portfolio=portfolio,
            order=order,
            symbol=order.symbol,
            proposed_quantity=order.quantity,
            current_price=Decimal("150.00")
        )
        
        assert context.portfolio == portfolio
        assert context.order == order
        assert context.proposed_quantity == Decimal("100")


class TestRiskManagerEdgeCases:
    """Edge case and uncovered branch tests for RiskManager"""

    @pytest.fixture
    def portfolio(self):
        return Portfolio(initial_cash=Decimal("100000.00"))

    @pytest.fixture
    def risk_manager(self, portfolio):
        return RiskManager(portfolio)

    def test_calculate_position_size_zero_risk_per_share(self, risk_manager):
        symbol = Symbol(ticker="AAPL")
        shares = risk_manager.calculate_position_size(
            symbol, Decimal("150.00"),
            risk_per_trade_percent=1.0,
            stop_loss_percent=0.0,
        )
        assert shares == Decimal("0")

    def test_calculate_position_size_with_fractional_shares(self, risk_manager):
        symbol = Symbol(ticker="AAPL")
        shares = risk_manager.calculate_position_size(
            symbol, Decimal("150.00"),
            risk_per_trade_percent=0.5,
            stop_loss_percent=5.0,
        )
        assert shares >= 0

    def test_get_risk_summary_with_positions(self, risk_manager, portfolio):
        portfolio.update_position(Position(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00"),
        ))

        summary = risk_manager.get_risk_summary()

        assert summary['total_exposure'] > 0
        assert summary['max_concentration'] > 0
        assert summary['trading_halted'] is False

    def test_get_risk_summary_after_drawdown(self, risk_manager, portfolio):
        portfolio.update_cash(Decimal("-25000.00"))
        risk_manager._max_drawdown_percent = 20.0

        summary = risk_manager.get_risk_summary()

        assert summary['current_drawdown'] == 25.0
        assert summary['trading_halted'] is True

    def test_concentration_with_existing_position(self, risk_manager, portfolio):
        risk_manager._max_concentration_percent = 15.0
        risk_manager._max_position_size_percent = 50.0

        symbol = Symbol(ticker="AAPL")
        portfolio.update_position(Position(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("60"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
        ))

        order = Order(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("20"), order_type=OrderType.MARKET,
        )

        passed, reason = risk_manager.check_order(order, Decimal("150.00"))

        # 60 + 20 = 80 shares * $150 = $12000 = 12% < 15% => passes
        assert passed
        assert reason == "Risk check passed"

    def test_concentration_exceeds_with_existing_position(self, risk_manager, portfolio):
        risk_manager._max_concentration_percent = 10.0
        risk_manager._max_position_size_percent = 50.0

        symbol = Symbol(ticker="AAPL")
        portfolio.update_position(Position(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("60"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
        ))

        order = Order(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("20"), order_type=OrderType.MARKET,
        )

        passed, reason = risk_manager.check_order(order, Decimal("150.00"))

        # 60 + 20 = 80 shares * $150 = $12000 = 12% > 10% => fails
        assert not passed
        assert "Concentration" in reason

    def test_concentration_sell_side_uses_existing_only(self, risk_manager, portfolio):
        risk_manager._max_concentration_percent = 10.0
        risk_manager._max_position_size_percent = 50.0

        symbol = Symbol(ticker="AAPL")
        portfolio.update_position(Position(
            symbol=symbol, side=OrderSide.BUY,
            quantity=Decimal("50"), avg_entry_price=Decimal("150.00"),
            current_price=Decimal("150.00"),
        ))

        order = Order(
            symbol=symbol, side=OrderSide.SELL,
            quantity=Decimal("100"), order_type=OrderType.MARKET,
        )

        passed, reason = risk_manager.check_order(order, Decimal("150.00"))

        # For SELL, total_quantity = current_quantity only = 50 * $150 = $7500 = 7.5% < 10% => passes
        assert passed

    def test_drawdown_zero_initial_cash(self):
        portfolio = Portfolio(initial_cash=Decimal("0"))
        rm = RiskManager(portfolio)
        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), order_type=OrderType.MARKET,
        )
        passed, reason = rm.check_order(order, Decimal("150.00"))
        # Buying power fails because cash is 0, but drawdown check should return True
        assert not passed
        assert "Insufficient buying power" in reason

    def test_position_size_zero_portfolio_value(self, risk_manager, portfolio):
        # Drive portfolio value to 0
        portfolio.update_cash(Decimal("-100000.00"))
        assert portfolio.total_value == Decimal("0")

        result = risk_manager._check_position_size(RiskContext(
            portfolio=portfolio,
            proposed_quantity=Decimal("100"),
            current_price=Decimal("150.00"),
        ))
        assert result == (True, "")

    def test_concentration_zero_portfolio_value(self, risk_manager, portfolio):
        portfolio.update_cash(Decimal("-100000.00"))
        risk_manager._max_drawdown_percent = 200.0

        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("100"), order_type=OrderType.MARKET,
        )
        result = risk_manager.check_order(order, Decimal("150.00"))
        # Portfolio value is 0, concentration limit returns True (early return)
        # But buying power check will fail since cash is 0
        assert not result[0]
        assert "Insufficient buying power" in result[1]

    def test_exposure_zero_portfolio_value(self, risk_manager, portfolio):
        portfolio.update_cash(Decimal("-100000.00"))
        result = risk_manager._check_exposure_limit(RiskContext(
            portfolio=portfolio,
            proposed_quantity=Decimal("100"),
            current_price=Decimal("150.00"),
        ))
        assert result == (True, "")

    def test_buying_power_no_price(self, risk_manager, portfolio):
        result = risk_manager._check_buying_power(RiskContext(
            portfolio=portfolio,
            proposed_quantity=Decimal("100"),
            current_price=None,
        ))
        assert result == (True, "")

        result2 = risk_manager._check_buying_power(RiskContext(
            portfolio=portfolio,
            proposed_quantity=None,
            current_price=Decimal("150.00"),
        ))
        assert result2 == (True, "")

    def test_add_rule_and_check(self, risk_manager, portfolio):
        class AlwaysFailRule(RiskRule):
            def check(self, context):
                return False, "Always fails"

        rule = AlwaysFailRule(rule_type=RiskRuleType.POSITION_SIZE_LIMIT, enabled=True)
        risk_manager.add_rule(rule)

        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), order_type=OrderType.MARKET,
        )
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        assert not passed
        assert reason == "Always fails"

    def test_add_rule_disabled_not_checked(self, risk_manager, portfolio):
        class AlwaysFailRule(RiskRule):
            def check(self, context):
                return False, "Always fails"

        rule = AlwaysFailRule(rule_type=RiskRuleType.POSITION_SIZE_LIMIT, enabled=False)
        risk_manager.add_rule(rule)

        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), order_type=OrderType.MARKET,
        )
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        assert passed

    def test_add_custom_check_passes(self, risk_manager, portfolio):
        risk_manager.add_custom_check(lambda ctx: (True, "custom ok"))

        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), order_type=OrderType.MARKET,
        )
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        assert passed
        assert reason == "Risk check passed"

    def test_add_custom_check_fails(self, risk_manager, portfolio):
        risk_manager.add_custom_check(lambda ctx: (False, "custom veto"))

        order = Order(
            symbol=Symbol(ticker="AAPL"), side=OrderSide.BUY,
            quantity=Decimal("10"), order_type=OrderType.MARKET,
        )
        passed, reason = risk_manager.check_order(order, Decimal("150.00"))
        assert not passed
        assert reason == "custom veto"

    def test_max_position_size_percent_property(self, risk_manager):
        assert risk_manager.max_position_size_percent == 10.0
        risk_manager.max_position_size_percent = 15.0
        assert risk_manager.max_position_size_percent == 15.0

    def test_max_drawdown_percent_property(self, risk_manager):
        assert risk_manager.max_drawdown_percent == 20.0
        risk_manager.max_drawdown_percent = 25.0
        assert risk_manager.max_drawdown_percent == 25.0
