"""Backtest results for AlgoEngine"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Any
from collections import defaultdict


from ..portfolio.portfolio import PortfolioSnapshot
from ..trading.models import Trade
from ..portfolio.metrics import PerformanceCalculator


@dataclass
class TradeRecord:
    """Record of a trade during backtest"""
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    commission: Decimal
    slippage: Decimal
    
    @property
    def gross_pnl(self) -> Decimal:
        """Gross profit/loss"""
        return self.realized_pnl + self.commission + self.slippage
    
    @property
    def net_pnl(self) -> Decimal:
        """Net profit/loss after costs"""
        return self.realized_pnl - self.commission - self.slippage


@dataclass
class BacktestResults:
    """Complete backtest results"""
    # Time period
    start_date: datetime
    end_date: datetime
    
    # Financial results
    initial_cash: Decimal
    final_value: Decimal = field(default_factory=lambda: Decimal("0"))
    
    # Data
    snapshots: List[PortfolioSnapshot] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.final_value:
            if self.snapshots:
                self.final_value = self.snapshots[-1].total_value
            else:
                self.final_value = self.initial_cash
    
    @property
    def total_return(self) -> Decimal:
        """Total return amount"""
        return self.final_value - self.initial_cash
    
    @property
    def total_return_percent(self) -> float:
        """Total return percentage"""
        if self.initial_cash == 0:
            return 0.0
        return float(self.total_return) / float(self.initial_cash) * 100
    
    @property
    def total_trades(self) -> int:
        """Total number of trades"""
        return len(self.trades)
    
    @property
    def winning_trades(self) -> int:
        """Number of winning trades"""
        return sum(1 for t in self.trades if t.net_pnl > 0)
    
    @property
    def losing_trades(self) -> int:
        """Number of losing trades"""
        return sum(1 for t in self.trades if t.net_pnl < 0)
    
    @property
    def win_rate(self) -> float:
        """Win rate percentage"""
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100
    
    @property
    def avg_trade_return(self) -> Decimal:
        """Average return per trade"""
        if self.total_trades == 0:
            return Decimal("0")
        return sum(t.net_pnl for t in self.trades) / self.total_trades
    
    @property
    def max_drawdown(self) -> float:
        """Maximum drawdown percentage"""
        if not self.snapshots:
            return 0.0
        
        equity_curve = [float(s.total_value) for s in self.snapshots]
        max_dd, _ = PerformanceCalculator.calculate_max_drawdown(equity_curve)
        return max_dd
    
    @property
    def sharpe_ratio(self) -> float:
        """Sharpe ratio"""
        if len(self.snapshots) < 2:
            return 0.0
        
        equity_curve = [float(s.total_value) for s in self.snapshots]
        returns = PerformanceCalculator.calculate_returns(equity_curve)
        
        if not returns:
            return 0.0
        
        return PerformanceCalculator.calculate_sharpe_ratio(returns)
    
    @property
    def profit_factor(self) -> float:
        """Profit factor (gross profit / gross loss)"""
        gross_profit = sum(float(t.net_pnl) for t in self.trades if t.net_pnl > 0)
        gross_loss = abs(sum(float(t.net_pnl) for t in self.trades if t.net_pnl < 0))
        
        if gross_loss == 0:
            return gross_profit if gross_profit > 0 else 0.0
        
        return gross_profit / gross_loss
    
    def get_equity_curve(self) -> List[float]:
        """Get equity curve values"""
        return [float(s.total_value) for s in self.snapshots]
    
    def get_drawdown_series(self) -> List[float]:
        """Get drawdown series (in percentage)"""
        if not self.snapshots:
            return []
        
        equity = self.get_equity_curve()
        peak = equity[0]
        drawdowns = []
        
        for value in equity:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100
            drawdowns.append(dd)
        
        return drawdowns
    
    def get_trades_by_symbol(self) -> Dict[str, List[Trade]]:
        """Get trades grouped by symbol"""
        grouped = defaultdict(list)
        for trade in self.trades:
            grouped[trade.symbol].append(trade)
        return dict(grouped)
    
    def get_monthly_returns(self) -> Dict[str, float]:
        """Get returns by month"""
        monthly = defaultdict(lambda: {"start": None, "end": None})
        
        for snapshot in self.snapshots:
            month_key = snapshot.timestamp.strftime("%Y-%m")
            
            if monthly[month_key]["start"] is None:
                monthly[month_key]["start"] = snapshot.total_value
            monthly[month_key]["end"] = snapshot.total_value
        
        returns = {}
        for month, values in monthly.items():
            if values["start"] and values["end"] and values["start"] > 0:
                ret = (float(values["end"]) / float(values["start"]) - 1) * 100
                returns[month] = ret
        
        return returns
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive summary"""
        return {
            "period": {
                "start": self.start_date.isoformat(),
                "end": self.end_date.isoformat(),
                "duration_days": (self.end_date - self.start_date).days
            },
            "returns": {
                "initial_capital": float(self.initial_cash),
                "final_value": float(self.final_value),
                "total_return": float(self.total_return),
                "total_return_pct": self.total_return_percent,
                "sharpe_ratio": self.sharpe_ratio,
                "max_drawdown_pct": self.max_drawdown
            },
            "trades": {
                "total": self.total_trades,
                "winning": self.winning_trades,
                "losing": self.losing_trades,
                "win_rate_pct": self.win_rate,
                "profit_factor": self.profit_factor,
                "avg_trade_return": float(self.avg_trade_return)
            }
        }
    
    def print_report(self) -> None:
        """Print formatted backtest report"""
        summary = self.get_summary()
        
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        
        print("\n📅 Period:")
        print(f"  Start: {summary['period']['start']}")
        print(f"  End:   {summary['period']['end']}")
        print(f"  Duration: {summary['period']['duration_days']} days")
        
        print("\n💰 Returns:")
        print(f"  Initial Capital: ${summary['returns']['initial_capital']:,.2f}")
        print(f"  Final Value:     ${summary['returns']['final_value']:,.2f}")
        print(f"  Total Return:    ${summary['returns']['total_return']:,.2f}")
        print(f"  Return %:         {summary['returns']['total_return_pct']:.2f}%")
        print(f"  Sharpe Ratio:    {summary['returns']['sharpe_ratio']:.2f}")
        print(f"  Max Drawdown:    {summary['returns']['max_drawdown_pct']:.2f}%")
        
        print("\n📊 Trades:")
        print(f"  Total Trades:    {summary['trades']['total']}")
        print(f"  Winning:         {summary['trades']['winning']}")
        print(f"  Losing:          {summary['trades']['losing']}")
        print(f"  Win Rate:        {summary['trades']['win_rate_pct']:.1f}%")
        print(f"  Profit Factor:   {summary['trades']['profit_factor']:.2f}")
        
        print("\n" + "=" * 60)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert results to dictionary"""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_cash": float(self.initial_cash),
            "final_value": float(self.final_value),
            "total_return": float(self.total_return),
            "total_return_percent": self.total_return_percent,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "equity_curve": self.get_equity_curve(),
            "monthly_returns": self.get_monthly_returns()
        }
