"""Tests for trading models"""

from datetime import datetime
from decimal import Decimal

from src.trading.models import (
    OrderType, OrderSide, OrderStatus, Order, Fill, Position, Trade, CommissionModel, SlippageModel
)
from src.data.models import Symbol


class TestOrder:
    """Test Order class"""
    
    def test_order_creation(self):
        """Test creating an order"""
        symbol = Symbol(ticker="AAPL")
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.LIMIT,
            limit_price=Decimal("150.00")
        )
        
        assert order.symbol == symbol
        assert order.side == OrderSide.BUY
        assert order.quantity == Decimal("100")
        assert order.order_type == OrderType.LIMIT
        assert order.limit_price == Decimal("150.00")
        assert order.status == OrderStatus.SUBMITTED
        assert order.remaining_quantity == Decimal("100")
    
    def test_order_fill_update(self):
        """Test updating order with fill"""
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.MARKET
        )
        
        # Partial fill
        order.update_fill(Decimal("50"), Decimal("150.00"))
        
        assert order.filled_quantity == Decimal("50")
        assert order.remaining_quantity == Decimal("50")
        assert order.status == OrderStatus.PARTIALLY_FILLED
        assert order.avg_fill_price == Decimal("150.00")
    
    def test_order_complete_fill(self):
        """Test complete order fill"""
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.MARKET
        )
        
        order.update_fill(Decimal("100"), Decimal("150.00"))
        
        assert order.filled_quantity == Decimal("100")
        assert order.remaining_quantity <= 0
        assert order.is_filled
        assert order.filled_at is not None
    
    def test_order_cancel(self):
        """Test order cancellation"""
        order = Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100")
        )
        
        order.cancel()
        
        assert order.is_cancelled
        assert order.cancelled_at is not None


class TestPosition:
    """Test Position class"""
    
    def test_position_creation(self):
        """Test creating a position"""
        position = Position(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00")
        )
        
        assert position.is_long
        assert not position.is_short
        assert position.quantity == Decimal("100")
    
    def test_position_unrealized_pnl(self):
        """Test unrealized P&L calculation"""
        position = Position(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("160.00")
        )
        
        assert position.unrealized_pnl == Decimal("1000.00")  # 100 * (160 - 150)
    
    def test_position_add_fill(self):
        """Test adding fill to position"""
        position = Position(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("0")
        )
        
        fill = Fill(
            order_id="ORD001",
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        
        position.add_fill(fill)
        
        assert position.quantity == Decimal("100")
        assert position.avg_entry_price == Decimal("150.00")


class TestCommissionModel:
    """Test CommissionModel"""
    
    def test_per_share_commission(self):
        """Test per-share commission calculation"""
        model = CommissionModel(per_share=Decimal("0.01"))
        commission = model.calculate(Decimal("100"), Decimal("150.00"))
        
        assert commission == Decimal("1.00")  # 100 shares * $0.01
    
    def test_min_commission(self):
        """Test minimum commission"""
        model = CommissionModel(
            per_share=Decimal("0.001"),
            min_per_order=Decimal("1.00")
        )
        commission = model.calculate(Decimal("100"), Decimal("150.00"))
        
        assert commission == Decimal("1.00")  # Minimum applies
    
    def test_flat_fee(self):
        """Test flat fee commission"""
        model = CommissionModel(flat_fee=Decimal("5.00"))
        commission = model.calculate(Decimal("100"), Decimal("150.00"))
        
        assert commission == Decimal("5.00")


class TestSlippageModel:
    """Test SlippageModel"""
    
    def test_fixed_slippage(self):
        """Test fixed slippage"""
        model = SlippageModel(fixed_slippage=Decimal("0.01"))
        slippage = model.calculate(Decimal("100.00"), Decimal("100"), Decimal("1000"))
        
        assert slippage == Decimal("0.01")
    
    def test_percentage_slippage(self):
        """Test percentage slippage"""
        model = SlippageModel(percentage=Decimal("0.1"))  # 0.1%
        slippage = model.calculate(Decimal("100.00"), Decimal("100"), Decimal("1000"))
        
        assert slippage == Decimal("0.1")  # 100 * 0.1%


class TestTrade:
    """Test Trade class"""
    
    def test_trade_creation(self):
        """Test creating a trade record"""
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
        
        assert trade.gross_pnl == Decimal("1006.00")
        assert trade.net_pnl == Decimal("994.00")
