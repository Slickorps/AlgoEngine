"""Tests for order manager"""

import pytest
from decimal import Decimal
from datetime import datetime

from src.trading.order_manager import OrderManager
from src.trading.models import Order, OrderSide, OrderType, Fill
from src.data.models import Symbol


class TestOrderManager:
    """Test OrderManager class"""
    
    @pytest.fixture
    def manager(self):
        """Create order manager instance"""
        return OrderManager()
    
    @pytest.fixture
    def sample_order(self):
        """Create sample order"""
        return Order(
            symbol=Symbol(ticker="AAPL"),
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            order_type=OrderType.MARKET
        )
    
    def test_register_order(self, manager, sample_order):
        """Test registering an order"""
        manager.register_order(sample_order)
        
        retrieved = manager.get_order(sample_order.order_id)
        assert retrieved == sample_order
    
    def test_get_active_orders(self, manager, sample_order):
        """Test getting active orders"""
        manager.register_order(sample_order)
        
        active = manager.get_active_orders()
        assert len(active) == 1
        assert active[0] == sample_order
    
    def test_process_fill(self, manager, sample_order):
        """Test processing a fill"""
        manager.register_order(sample_order)
        
        fill = Fill(
            order_id=sample_order.order_id,
            symbol=sample_order.symbol,
            side=sample_order.side,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        
        manager.process_fill(sample_order.order_id, fill)
        
        assert sample_order.filled_quantity == Decimal("100")
        assert sample_order.is_filled
    
    def test_cancel_order(self, manager, sample_order):
        """Test cancelling an order"""
        manager.register_order(sample_order)
        
        result = manager.cancel_order(sample_order.order_id)
        
        assert result is True
        assert sample_order.is_cancelled
    
    def test_cancel_all_orders(self, manager):
        """Test cancelling all orders"""
        for i in range(3):
            order = Order(
                symbol=Symbol(ticker="AAPL"),
                side=OrderSide.BUY,
                quantity=Decimal("100")
            )
            manager.register_order(order)
        
        cancelled = manager.cancel_all_orders()
        
        assert cancelled == 3
    
    def test_order_callback(self, manager, sample_order):
        """Test order callback registration"""
        callback_called = []
        
        def on_order(order):
            callback_called.append(order.order_id)
        
        manager.on_order(on_order)
        manager.register_order(sample_order)
        
        assert len(callback_called) == 1
        assert callback_called[0] == sample_order.order_id
    
    def test_fill_callback(self, manager, sample_order):
        """Test fill callback registration"""
        callback_called = []
        
        def on_fill(fill):
            callback_called.append(fill.order_id)
        
        manager.on_fill(on_fill)
        manager.register_order(sample_order)
        
        fill = Fill(
            order_id=sample_order.order_id,
            symbol=sample_order.symbol,
            side=sample_order.side,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        manager.process_fill(sample_order.order_id, fill)
        
        assert len(callback_called) == 1
    
    def test_cancel_all_orders_by_symbol(self, manager):
        """Test cancel_all_orders filtered by symbol"""
        aapl = Symbol(ticker="AAPL")
        msft = Symbol(ticker="MSFT")
        for _ in range(2):
            manager.register_order(Order(symbol=aapl, side=OrderSide.BUY, quantity=Decimal("100")))
        for _ in range(3):
            manager.register_order(Order(symbol=msft, side=OrderSide.SELL, quantity=Decimal("50")))
        
        cancelled = manager.cancel_all_orders(symbol=aapl)
        
        assert cancelled == 2
        assert len(manager.get_active_orders()) == 3
    
    def test_get_active_orders_after_fill(self, manager, sample_order):
        """Test active orders are removed after full fill"""
        manager.register_order(sample_order)
        assert len(manager.get_active_orders()) == 1
        
        fill = Fill(
            order_id=sample_order.order_id,
            symbol=sample_order.symbol,
            side=sample_order.side,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        manager.process_fill(sample_order.order_id, fill)
        
        assert len(manager.get_active_orders()) == 0
    
    def test_get_active_orders_with_symbol_filter(self, manager):
        """Test get_active_orders returns only matching symbol"""
        aapl = Symbol(ticker="AAPL")
        msft = Symbol(ticker="MSFT")
        manager.register_order(Order(symbol=aapl, side=OrderSide.BUY, quantity=Decimal("100")))
        manager.register_order(Order(symbol=aapl, side=OrderSide.SELL, quantity=Decimal("50")))
        manager.register_order(Order(symbol=msft, side=OrderSide.BUY, quantity=Decimal("200")))
        
        aapl_active = manager.get_active_orders(symbol=aapl)
        msft_active = manager.get_active_orders(symbol=msft)
        
        assert len(aapl_active) == 2
        assert len(msft_active) == 1
        assert msft_active[0].symbol.ticker == "MSFT"
    
    def test_register_multiple_orders_statistics(self, manager):
        """Test statistics with multiple registered orders"""
        aapl = Symbol(ticker="AAPL")
        for i in range(5):
            manager.register_order(Order(symbol=aapl, side=OrderSide.BUY, quantity=Decimal("100")))
        
        stats = manager.get_statistics()
        assert stats['total_orders'] == 5
        assert stats['active_orders'] == 5
        assert stats['filled_orders'] == 0
        assert stats['symbols_traded'] == 1
    
    def test_on_fill_callback_multiple_handlers(self, manager, sample_order):
        """Test multiple on_fill callbacks all fire"""
        manager.register_order(sample_order)
        results = []
        
        def handler_a(fill):
            results.append("a")
        
        def handler_b(fill):
            results.append("b")
        
        manager.on_fill(handler_a)
        manager.on_fill(handler_b)
        
        fill = Fill(
            order_id=sample_order.order_id,
            symbol=sample_order.symbol,
            side=sample_order.side,
            quantity=Decimal("100"),
            fill_price=Decimal("150.00"),
            fill_time=datetime.now()
        )
        manager.process_fill(sample_order.order_id, fill)
        
        assert results == ["a", "b"]
    
    def test_on_order_callback_with_error_resilience(self, manager, sample_order):
        """Test on_order continues calling handlers after one raises"""
        results = []
        
        def good_handler(order):
            results.append(order.order_id)
        
        def bad_handler(order):
            raise RuntimeError("callback failure")
        
        def second_good_handler(order):
            results.append("second")
        
        manager.on_order(good_handler)
        manager.on_order(bad_handler)
        manager.on_order(second_good_handler)
        manager.register_order(sample_order)
        
        assert len(results) == 2
        assert sample_order.order_id in results
        assert "second" in results
