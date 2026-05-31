"""Simulated broker adapter for backtesting"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict
import random

from ..trading.execution_engine import BrokerAdapter
from ..trading.models import Order, OrderStatus, Fill, OrderType
from ..data.models import Tick
from ..utils.logger import get_logger

logger = get_logger("adapters.simulated_broker")


class SimulatedBroker(BrokerAdapter):
    """Simulated broker for backtesting"""
    
    def __init__(
        self,
        fill_probability: float = 1.0,
        latency_ms: float = 0,
        partial_fill_probability: float = 0.0
    ) -> None:
        self._connected = False
        self._fill_probability = fill_probability
        self._latency_ms = latency_ms
        self._partial_fill_probability = partial_fill_probability
        
        self._orders: Dict[str, Order] = {}
        self._pending_fills: asyncio.Queue = asyncio.Queue()
        self._fill_task: Optional[asyncio.Task] = None
    
    async def connect(self) -> bool:
        """Connect to simulated broker"""
        self._connected = True
        self._fill_task = asyncio.create_task(self._fill_loop())
        logger.info("Connected to simulated broker")
        return True
    
    async def disconnect(self) -> None:
        """Disconnect from simulated broker"""
        self._connected = False
        if self._fill_task:
            self._fill_task.cancel()
            try:
                await self._fill_task
            except asyncio.CancelledError:
                pass
        logger.info("Disconnected from simulated broker")
    
    def is_connected(self) -> bool:
        """Check connection status"""
        return self._connected
    
    async def submit_order(self, order: Order) -> bool:
        """Submit order to simulated broker"""
        if not self._connected:
            return False
        
        self._orders[order.order_id] = order
        order.status = OrderStatus.ACCEPTED
        order.submitted_at = datetime.now()
        
        # Queue for simulated fill processing
        await self._pending_fills.put(order)
        
        logger.debug(f"Order submitted to simulated broker: {order.order_id}")
        return True
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel order at simulated broker"""
        order = self._orders.get(order_id)
        if not order:
            return False
        
        if order.is_active:
            order.cancel()
            logger.debug(f"Order cancelled in simulated broker: {order_id}")
            return True
        
        return False
    
    async def _fill_loop(self) -> None:
        """Process pending fills with simulated latency"""
        while self._connected:
            try:
                order = await asyncio.wait_for(
                    self._pending_fills.get(),
                    timeout=1.0
                )
                
                if order.is_active:
                    # Simulate latency
                    if self._latency_ms > 0:
                        await asyncio.sleep(self._latency_ms / 1000)
                    
                    # Simulate fill probability
                    if random.random() <= self._fill_probability:
                        await self._simulate_fill(order)
                        
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in fill loop: {e}")
    
    async def _simulate_fill(self, order: Order) -> None:
        """Simulate order fill"""
        # Determine fill quantity
        fill_quantity = order.remaining_quantity
        
        # Simulate partial fill
        if random.random() <= self._partial_fill_probability:
            fill_quantity = fill_quantity * Decimal(str(random.uniform(0.3, 0.7)))
            fill_quantity = fill_quantity.quantize(Decimal("0.01"))
        
        # Get current price (would come from market data in real implementation)
        fill_price = await self._get_fill_price(order)
        
        if fill_price is None:
            logger.warning(f"Cannot fill order {order.order_id}: no price available")
            return
        
        # Create fill
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_quantity,
            fill_price=fill_price,
            fill_time=datetime.now()
        )
        
        # Update order (this would normally come from broker callback)
        order.update_fill(fill_quantity, fill_price)
        
        logger.info(
            f"Simulated fill: {order.order_id} {fill_quantity} @ {fill_price}"
        )
    
    async def _get_fill_price(self, order: Order) -> Optional[Decimal]:
        """Get simulated fill price"""
        # In a real implementation, this would use current market data
        # For simulation, we use a default price or the order's limit price
        
        if order.order_type == OrderType.LIMIT and order.limit_price:
            return order.limit_price
        
        # Return a simulated price (would be from market data)
        return Decimal("100.00")  # Placeholder
    
    def inject_market_data(self, tick: Tick) -> None:
        """Inject market data for fill simulation"""
        # This would be called by the data feed to provide current prices
        # for realistic fill simulation
        pass
