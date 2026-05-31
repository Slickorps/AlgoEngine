"""Execution engine for AlgoEngine"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from .models import Order, OrderType, OrderStatus, Fill, OrderSide, CommissionModel, SlippageModel
from .order_manager import OrderManager
from .position_manager import PositionManager
from ..data.models import Symbol, Tick
from ..engine.events import EventType, get_event_bus
from ..utils.logger import get_logger

logger = get_logger("trading.execution")


class BrokerAdapter(ABC):
    """Base class for broker adapters"""
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to broker"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from broker"""
        pass
    
    @abstractmethod
    async def submit_order(self, order: Order) -> bool:
        """Submit order to broker"""
        pass
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order at broker"""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check connection status"""
        pass


class ExecutionEngine:
    """Execute orders and process fills"""
    
    def __init__(
        self,
        broker: Optional[BrokerAdapter] = None,
        commission_model: Optional[CommissionModel] = None,
        slippage_model: Optional[SlippageModel] = None
    ) -> None:
        self._broker = broker
        self._commission_model = commission_model or CommissionModel()
        self._slippage_model = slippage_model or SlippageModel()
        
        self._order_manager = OrderManager()
        self._position_manager = PositionManager()
        
        self._event_bus = get_event_bus()
        self._last_prices: dict = {}
        
        # Wire up order manager callbacks
        self._order_manager.on_fill(self._on_fill)
    
    @property
    def order_manager(self) -> OrderManager:
        """Get order manager"""
        return self._order_manager
    
    @property
    def position_manager(self) -> PositionManager:
        """Get position manager"""
        return self._position_manager
    
    async def submit_order(self, order: Order) -> bool:
        """Submit an order for execution"""
        # Register order
        self._order_manager.register_order(order)
        
        # Route to broker if available
        if self._broker and self._broker.is_connected():
            try:
                success = await self._broker.submit_order(order)
                if success:
                    order.status = OrderStatus.ACCEPTED
                    order.submitted_at = datetime.now()
                else:
                    order.status = OrderStatus.REJECTED
                    logger.error(f"Order rejected by broker: {order.order_id}")
                return success
            except Exception as e:
                logger.error(f"Error submitting order: {e}")
                order.status = OrderStatus.REJECTED
                return False
        else:
            # No broker - simulated execution
            logger.warning(f"No broker connected, order {order.order_id} pending")
            order.status = OrderStatus.PENDING
            return True
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        # Cancel at broker first
        if self._broker and self._broker.is_connected():
            try:
                await self._broker.cancel_order(order_id)
            except Exception as e:
                logger.error(f"Error cancelling order at broker: {e}")
        
        # Update local state
        return self._order_manager.cancel_order(order_id)
    
    def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int:
        """Cancel all orders"""
        return self._order_manager.cancel_all_orders(symbol)
    
    def process_tick(self, tick: Tick) -> None:
        """Process market tick for pending orders"""
        symbol = tick.symbol
        self._last_prices[symbol] = tick
        
        # Update position prices
        self._position_manager.update_price(symbol, tick.mid_price, tick.timestamp)
        
        # Check pending orders for this symbol
        if not self._broker:  # Only process in simulation mode
            self._process_pending_orders(symbol, tick)
    
    def _process_pending_orders(self, symbol: Symbol, tick: Tick) -> None:
        """Process pending orders against market data"""
        pending_orders = [
            o for o in self._order_manager.get_active_orders(symbol)
            if o.status == OrderStatus.PENDING
        ]
        
        for order in pending_orders:
            fill = self._try_fill_order(order, tick)
            if fill:
                self._order_manager.process_fill(order.order_id, fill)
    
    def _try_fill_order(self, order: Order, tick: Tick) -> Optional[Fill]:
        """Try to fill an order against current market data"""
        if order.order_type == OrderType.MARKET:
            # Market orders fill immediately at mid price
            fill_price = tick.mid_price
            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.remaining_quantity,
                fill_price=fill_price,
                fill_time=tick.timestamp
            )
            return self._apply_costs(fill, tick)
        
        elif order.order_type == OrderType.LIMIT:
            # Limit orders fill if price is better than limit
            if order.side == OrderSide.BUY and tick.ask_price <= order.limit_price:
                fill_price = min(tick.ask_price, order.limit_price)
                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.remaining_quantity,
                    fill_price=fill_price,
                    fill_time=tick.timestamp
                )
                return self._apply_costs(fill, tick)
            
            elif order.side == OrderSide.SELL and tick.bid_price >= order.limit_price:
                fill_price = max(tick.bid_price, order.limit_price)
                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.remaining_quantity,
                    fill_price=fill_price,
                    fill_time=tick.timestamp
                )
                return self._apply_costs(fill, tick)
        
        elif order.order_type == OrderType.STOP:
            # Stop orders trigger when price reaches stop level
            if order.side == OrderSide.BUY and tick.ask_price >= order.stop_price:
                fill_price = tick.ask_price
                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.remaining_quantity,
                    fill_price=fill_price,
                    fill_time=tick.timestamp
                )
                return self._apply_costs(fill, tick)
            
            elif order.side == OrderSide.SELL and tick.bid_price <= order.stop_price:
                fill_price = tick.bid_price
                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.remaining_quantity,
                    fill_price=fill_price,
                    fill_time=tick.timestamp
                )
                return self._apply_costs(fill, tick)
        
        return None
    
    def _apply_costs(self, fill: Fill, tick: Tick) -> Fill:
        """Apply commission and slippage to fill"""
        # Calculate commission
        fill.commission = self._commission_model.calculate(fill.quantity, fill.fill_price)
        
        # Calculate slippage
        volume = tick.bid_size + tick.ask_size
        slippage = self._slippage_model.calculate(fill.fill_price, fill.quantity, volume)
        fill.slippage = slippage
        
        # Adjust fill price for slippage
        if fill.side == OrderSide.BUY:
            fill.fill_price += slippage  # Pay more when buying
        else:
            fill.fill_price -= slippage  # Get less when selling
        
        return fill
    
    def _on_fill(self, fill: Fill) -> None:
        """Handle order fill"""
        # Update positions
        trade = self._position_manager.process_fill(fill)
        
        # Emit event
        self._event_bus.emit(
            type=EventType.FILL,
            timestamp=fill.fill_time,
            data={'fill': fill, 'trade': trade},
            symbol=str(fill.symbol)
        )
        
        logger.info(
            f"Fill processed: {fill.fill_id} {fill.symbol.ticker} "
            f"{fill.side.name} {fill.quantity} @ {fill.fill_price}"
        )
    
    def get_statistics(self) -> dict:
        """Get execution statistics"""
        return {
            'orders': self._order_manager.get_statistics(),
            'positions': self._position_manager.get_position_summary(),
            'total_pnl': float(
                self._position_manager.get_realized_pnl() +
                self._position_manager.get_unrealized_pnl()
            )
        }
