"""Order management for AlgoEngine"""

from collections import defaultdict
from typing import Dict, List, Optional, Callable, Any

from .models import Order, Fill
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("trading.order_manager")


class OrderManager:
    """Manage order lifecycle and tracking"""
    
    def __init__(self) -> None:
        self._orders: Dict[str, Order] = {}
        self._orders_by_symbol: Dict[Symbol, List[str]] = defaultdict(list)
        self._active_orders: set = set()
        
        # Callbacks
        self._on_order_callbacks: List[Callable[[Order], None]] = []
        self._on_fill_callbacks: List[Callable[[Fill], None]] = []
        self._on_cancel_callbacks: List[Callable[[Order], None]] = []
    
    def register_order(self, order: Order) -> None:
        """Register a new order"""
        self._orders[order.order_id] = order
        self._orders_by_symbol[order.symbol].append(order.order_id)
        self._active_orders.add(order.order_id)
        
        logger.info(f"Order registered: {order.order_id} {order.side.name} {order.quantity} {order.symbol.ticker}")
        
        # Notify callbacks
        for callback in self._on_order_callbacks:
            try:
                callback(order)
            except Exception as e:
                logger.error(f"Error in order callback: {e}")
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID"""
        return self._orders.get(order_id)
    
    def get_orders_by_symbol(self, symbol: Symbol) -> List[Order]:
        """Get all orders for a symbol"""
        order_ids = self._orders_by_symbol.get(symbol, [])
        return [self._orders[oid] for oid in order_ids if oid in self._orders]
    
    def get_active_orders(self, symbol: Optional[Symbol] = None) -> List[Order]:
        """Get all active orders"""
        orders = []
        for order_id in self._active_orders:
            order = self._orders.get(order_id)
            if order and (symbol is None or order.symbol == symbol):
                orders.append(order)
        return orders
    
    def process_fill(self, order_id: str, fill: Fill) -> None:
        """Process a fill for an order"""
        order = self._orders.get(order_id)
        if not order:
            logger.error(f"Fill received for unknown order: {order_id}")
            return
        
        # Update order
        order.update_fill(fill.quantity, fill.fill_price)
        
        if not order.is_active:
            self._active_orders.discard(order_id)
        
        logger.info(
            f"Fill processed: {fill.fill_id} {fill.quantity} @ {fill.fill_price} "
            f"({order.filled_quantity}/{order.quantity})"
        )
        
        # Notify callbacks
        for callback in self._on_fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                logger.error(f"Error in fill callback: {e}")
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        order = self._orders.get(order_id)
        if not order:
            logger.error(f"Cannot cancel unknown order: {order_id}")
            return False
        
        if not order.is_active:
            logger.warning(f"Order {order_id} is not active, cannot cancel")
            return False
        
        order.cancel()
        self._active_orders.discard(order_id)
        
        logger.info(f"Order cancelled: {order_id}")
        
        # Notify callbacks
        for callback in self._on_cancel_callbacks:
            try:
                callback(order)
            except Exception as e:
                logger.error(f"Error in cancel callback: {e}")
        
        return True
    
    def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int:
        """Cancel all active orders"""
        orders_to_cancel = self.get_active_orders(symbol)
        cancelled_count = 0
        
        for order in orders_to_cancel:
            if self.cancel_order(order.order_id):
                cancelled_count += 1
        
        return cancelled_count
    
    def on_order(self, callback: Callable[[Order], None]) -> None:
        """Register order update callback"""
        self._on_order_callbacks.append(callback)
    
    def on_fill(self, callback: Callable[[Fill], None]) -> None:
        """Register fill callback"""
        self._on_fill_callbacks.append(callback)
    
    def on_cancel(self, callback: Callable[[Order], None]) -> None:
        """Register cancel callback"""
        self._on_cancel_callbacks.append(callback)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get order statistics"""
        total_orders = len(self._orders)
        active_orders = len(self._active_orders)
        filled_orders = sum(1 for o in self._orders.values() if o.is_filled)
        cancelled_orders = sum(1 for o in self._orders.values() if o.is_cancelled)
        
        return {
            'total_orders': total_orders,
            'active_orders': active_orders,
            'filled_orders': filled_orders,
            'cancelled_orders': cancelled_orders,
            'symbols_traded': len(self._orders_by_symbol)
        }
