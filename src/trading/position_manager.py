"""Position management for AlgoEngine"""

from typing import Dict, List, Optional
from decimal import Decimal

from .models import Position, OrderSide, Fill, Trade
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("trading.position_manager")


class PositionManager:
    """Manage trading positions"""
    
    def __init__(self) -> None:
        self._positions: Dict[Symbol, Position] = {}
        self._trades: List[Trade] = []
        self._trade_counter: int = 0
    
    def get_position(self, symbol: Symbol) -> Optional[Position]:
        """Get position for symbol"""
        return self._positions.get(symbol)
    
    def get_all_positions(self) -> List[Position]:
        """Get all current positions"""
        return [p for p in self._positions.values() if p.quantity > 0]
    
    def get_long_positions(self) -> List[Position]:
        """Get all long positions"""
        return [p for p in self._positions.values() if p.is_long]
    
    def get_short_positions(self) -> List[Position]:
        """Get all short positions"""
        return [p for p in self._positions.values() if p.is_short]
    
    def update_price(self, symbol: Symbol, price: Decimal, timestamp) -> None:
        """Update market price for a position"""
        position = self._positions.get(symbol)
        if position:
            position.update_price(price, timestamp)
    
    def process_fill(self, fill: Fill) -> Optional[Trade]:
        """Process a fill and update positions"""
        symbol = fill.symbol
        position = self._positions.get(symbol)
        
        if position is None:
            # Create new position
            position = Position(
                symbol=symbol,
                side=fill.side,
                quantity=Decimal("0"),
                avg_entry_price=None
            )
            self._positions[symbol] = position
        
        # Check if this reduces the position
        position_side = OrderSide.BUY if position.is_long else OrderSide.SELL
        is_reducing = (position.quantity > 0) and (fill.side != position_side)
        
        trade = None
        if is_reducing:
            # Calculate realized P&L and create trade record
            realized_pnl = self._calculate_realized_pnl(position, fill)
            
            # Create trade record
            self._trade_counter += 1
            trade = Trade(
                trade_id=f"TRADE_{self._trade_counter}",
                symbol=symbol,
                entry_time=position.opened_at if position.opened_at else fill.fill_time,
                exit_time=fill.fill_time,
                side=position.side,
                quantity=fill.quantity,
                entry_price=position.avg_entry_price if position.avg_entry_price else fill.fill_price,
                exit_price=fill.fill_price,
                realized_pnl=realized_pnl,
                commission=fill.commission,
                slippage=fill.slippage,
                exit_order_id=fill.order_id
            )
            self._trades.append(trade)
            
            logger.info(
                f"Trade completed: {trade.trade_id} {symbol.ticker} "
                f"PnL: {realized_pnl}"
            )
            
            # Reduce position
            position.reduce(fill.quantity, fill.fill_price, fill.fill_time)
            
            # If position fully closed, remove it
            if position.quantity <= 0:
                del self._positions[symbol]
        else:
            # Add to existing position
            position.add_fill(fill)
            logger.info(
                f"Position increased: {symbol.ticker} {position.quantity} @ {position.avg_entry_price}"
            )
        
        return trade
    
    def _calculate_realized_pnl(self, position: Position, fill: Fill) -> Decimal:
        """Calculate realized P&L for a reducing fill"""
        if position.avg_entry_price is None:
            return Decimal("0")
        
        if position.is_long:
            return fill.quantity * (fill.fill_price - position.avg_entry_price)
        else:
            return fill.quantity * (position.avg_entry_price - fill.fill_price)
    
    def get_total_exposure(self) -> Decimal:
        """Get total market exposure"""
        return sum(
            abs(p.market_value) for p in self._positions.values()
            if p.current_price is not None
        )
    
    def get_unrealized_pnl(self) -> Decimal:
        """Get total unrealized P&L"""
        return sum(p.unrealized_pnl for p in self._positions.values())
    
    def get_realized_pnl(self) -> Decimal:
        """Get total realized P&L from closed trades"""
        return sum(t.net_pnl for t in self._trades)
    
    def get_trades(self, symbol: Optional[Symbol] = None) -> List[Trade]:
        """Get all trades or trades for specific symbol"""
        if symbol:
            return [t for t in self._trades if t.symbol == symbol]
        return self._trades.copy()
    
    def get_position_summary(self) -> Dict:
        """Get summary of all positions"""
        positions = self.get_all_positions()
        
        return {
            'total_positions': len(positions),
            'long_positions': len([p for p in positions if p.is_long]),
            'short_positions': len([p for p in positions if p.is_short]),
            'total_exposure': float(self.get_total_exposure()),
            'unrealized_pnl': float(self.get_unrealized_pnl()),
            'realized_pnl': float(self.get_realized_pnl()),
            'total_trades': len(self._trades)
        }
