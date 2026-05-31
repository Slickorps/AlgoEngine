"""Portfolio management for AlgoEngine"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..trading.models import Position, Trade, OrderSide
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("portfolio")


@dataclass
class PortfolioSnapshot:
    """Portfolio state at a point in time"""
    timestamp: datetime
    cash: Decimal
    positions_value: Decimal
    total_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    
    @property
    def leverage(self) -> float:
        """Portfolio leverage (positions / equity)"""
        if self.total_value == 0:
            return 0.0
        return float(self.positions_value) / float(self.total_value)


class Portfolio:
    """Manage portfolio cash and positions"""
    
    def __init__(self, initial_cash: Decimal = Decimal("100000.00")) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._positions: Dict[Symbol, Position] = {}
        self._trades: List[Trade] = []
        self._snapshots: List[PortfolioSnapshot] = []
        self._start_time: datetime = datetime.now()
        
        logger.info(f"Portfolio initialized with ${initial_cash}")
    
    @property
    def cash(self) -> Decimal:
        """Available cash"""
        return self._cash
    
    @property
    def initial_cash(self) -> Decimal:
        """Initial capital"""
        return self._initial_cash
    
    def get_position(self, symbol: Symbol) -> Optional[Position]:
        """Get position for symbol"""
        return self._positions.get(symbol)
    
    def get_all_positions(self) -> List[Position]:
        """Get all positions with non-zero quantity"""
        return [p for p in self._positions.values() if p.quantity > 0]
    
    @property
    def positions_value(self) -> Decimal:
        """Total market value of all positions"""
        return sum(
            p.market_value for p in self._positions.values()
            if p.current_price is not None
        )
    
    @property
    def total_value(self) -> Decimal:
        """Total portfolio value (cash + positions)"""
        return self._cash + self.positions_value
    
    @property
    def total_return(self) -> Decimal:
        """Total return since inception"""
        return self.total_value - self._initial_cash
    
    @property
    def total_return_percent(self) -> float:
        """Total return percentage"""
        if self._initial_cash == 0:
            return 0.0
        return float(self.total_return) / float(self._initial_cash) * 100
    
    @property
    def unrealized_pnl(self) -> Decimal:
        """Unrealized profit/loss from open positions"""
        return sum(p.unrealized_pnl for p in self._positions.values())
    
    @property
    def realized_pnl(self) -> Decimal:
        """Realized profit/loss from closed trades"""
        return sum(t.net_pnl for t in self._trades)
    
    def update_position(self, position: Position) -> None:
        """Update or add a position"""
        if position.quantity > 0:
            self._positions[position.symbol] = position
        else:
            # Remove closed position
            self._positions.pop(position.symbol, None)
    
    def update_cash(self, amount: Decimal) -> None:
        """Update cash balance (positive for inflow, negative for outflow)"""
        self._cash += amount
        if self._cash < 0:
            logger.warning(f"Portfolio cash negative: ${self._cash}")
    
    def record_trade(self, trade: Trade) -> None:
        """Record a completed trade"""
        self._trades.append(trade)
        
        # Update cash
        trade_value = trade.quantity * trade.exit_price
        if trade.side == OrderSide.BUY:  # Was long, now closing
            self._cash += trade_value  # Receive cash from selling
        else:  # Was short
            self._cash -= trade_value  # Pay to cover short
        
        # Subtract costs
        self._cash -= trade.commission + trade.slippage
        
        logger.info(
            f"Trade recorded: {trade.trade_id} {trade.symbol.ticker} "
            f"PnL: ${trade.net_pnl:.2f}"
        )
    
    def take_snapshot(self) -> PortfolioSnapshot:
        """Record current portfolio state"""
        snapshot = PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=self._cash,
            positions_value=self.positions_value,
            total_value=self.total_value,
            unrealized_pnl=self.unrealized_pnl,
            realized_pnl=self.realized_pnl
        )
        self._snapshots.append(snapshot)
        return snapshot
    
    def get_snapshots(self) -> List[PortfolioSnapshot]:
        """Get all recorded snapshots"""
        return self._snapshots.copy()
    
    def get_buying_power(self, margin_requirement: float = 1.0) -> Decimal:
        """Get available buying power considering margin"""
        return self._cash * Decimal(str(1.0 / margin_requirement))
    
    def get_exposure_by_symbol(self) -> Dict[Symbol, Decimal]:
        """Get exposure percentage by symbol"""
        total = self.total_value
        if total == 0:
            return {}
        
        return {
            symbol: (p.market_value / total)
            for symbol, p in self._positions.items()
            if p.market_value > 0
        }
    
    def get_sector_exposure(self) -> Dict[str, Decimal]:
        """Get exposure by sector (requires sector data)"""
        # Placeholder - would need sector info per symbol
        return {}
    
    def get_summary(self) -> Dict:
        """Get portfolio summary"""
        positions = self.get_all_positions()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'initial_cash': float(self._initial_cash),
            'cash': float(self._cash),
            'positions_value': float(self.positions_value),
            'total_value': float(self.total_value),
            'total_return': float(self.total_return),
            'total_return_pct': self.total_return_percent,
            'unrealized_pnl': float(self.unrealized_pnl),
            'realized_pnl': float(self.realized_pnl),
            'position_count': len(positions),
            'trade_count': len(self._trades)
        }
