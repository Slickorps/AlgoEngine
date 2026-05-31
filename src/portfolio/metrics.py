"""Portfolio performance metrics for AlgoEngine"""

from dataclasses import dataclass
from typing import List, Optional, Tuple
import math

import numpy as np

from .portfolio import PortfolioSnapshot
from ..utils.logger import get_logger

logger = get_logger("portfolio.metrics")


@dataclass
class PerformanceMetrics:
    """Calculated performance metrics"""
    # Returns
    total_return: float  # Total return percentage
    annualized_return: float  # Annualized return
    
    # Risk
    volatility: float  # Annualized standard deviation
    sharpe_ratio: float  # Sharpe ratio (assuming risk-free rate of 0)
    max_drawdown: float  # Maximum drawdown percentage
    max_drawdown_duration: int  # Max drawdown duration in days
    
    # Trade metrics
    win_rate: float  # Percentage of winning trades
    profit_factor: float  # Gross profit / Gross loss
    avg_trade_return: float  # Average return per trade
    
    # Calmar ratio
    calmar_ratio: float  # Annualized return / Max drawdown


class PerformanceCalculator:
    """Calculate portfolio performance metrics"""
    
    @staticmethod
    def calculate_returns(equity_curve: List[float]) -> List[float]:
        """Calculate periodic returns from equity curve"""
        if len(equity_curve) < 2:
            return []
        
        returns = []
        for i in range(1, len(equity_curve)):
            ret = (equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
            returns.append(ret)
        
        return returns
    
    @staticmethod
    def calculate_sharpe_ratio(
        returns: List[float],
        risk_free_rate: float = 0.0,
        periods_per_year: int = 252
    ) -> float:
        """Calculate Sharpe ratio"""
        if not returns or len(returns) < 2:
            return 0.0
        
        excess_returns = [r - risk_free_rate for r in returns]
        mean_return = np.mean(excess_returns)
        std_return = np.std(excess_returns, ddof=1)
        
        if std_return == 0:
            return 0.0
        
        # Annualize
        sharpe = (mean_return * periods_per_year) / (std_return * math.sqrt(periods_per_year))
        return sharpe
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> Tuple[float, int]:
        """Calculate maximum drawdown and its duration"""
        if not equity_curve or len(equity_curve) < 2:
            return 0.0, 0
        
        peak = equity_curve[0]
        max_drawdown = 0.0
        peak_idx = 0
        max_dd_start = 0
        max_dd_end = 0
        
        for i, value in enumerate(equity_curve):
            if value > peak:
                peak = value
                peak_idx = i
            
            drawdown = (peak - value) / peak
            
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_dd_start = peak_idx
                max_dd_end = i
        
        duration = max_dd_end - max_dd_start
        return max_drawdown * 100, duration  # Return as percentage
    
    @classmethod
    def calculate_metrics(
        cls,
        snapshots: List[PortfolioSnapshot],
        risk_free_rate: float = 0.0
    ) -> Optional[PerformanceMetrics]:
        """Calculate all performance metrics from snapshots"""
        if len(snapshots) < 2:
            return None
        
        # Extract equity curve
        equity_curve = [float(s.total_value) for s in snapshots]
        
        # Calculate returns
        returns = cls.calculate_returns(equity_curve)
        
        if not returns:
            return None
        
        # Total return
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100
        
        # Annualized return
        start_time = snapshots[0].timestamp
        end_time = snapshots[-1].timestamp
        days = (end_time - start_time).days
        
        if days > 0:
            years = days / 365.25
            annualized_return = ((equity_curve[-1] / equity_curve[0]) ** (1/years) - 1) * 100
        else:
            annualized_return = total_return
        
        # Volatility (annualized)
        if len(returns) > 1:
            volatility = np.std(returns, ddof=1) * math.sqrt(252) * 100
        else:
            volatility = 0.0
        
        # Sharpe ratio
        sharpe = cls.calculate_sharpe_ratio(returns, risk_free_rate)
        
        # Max drawdown
        max_dd, dd_duration = cls.calculate_max_drawdown(equity_curve)
        
        # Trade metrics (placeholder - would need actual trade data)
        win_rate = 0.0
        profit_factor = 0.0
        avg_trade_return = 0.0
        
        # Calmar ratio
        if max_dd > 0:
            calmar = annualized_return / max_dd
        else:
            calmar = 0.0
        
        return PerformanceMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            volatility=volatility,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_duration=dd_duration,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_return=avg_trade_return,
            calmar_ratio=calmar
        )
    
    @staticmethod
    def calculate_var(
        returns: List[float],
        confidence_level: float = 0.95
    ) -> float:
        """Calculate Value at Risk"""
        if not returns:
            return 0.0
        
        return np.percentile(returns, (1 - confidence_level) * 100)
    
    @staticmethod
    def calculate_beta(
        strategy_returns: List[float],
        benchmark_returns: List[float]
    ) -> float:
        """Calculate beta relative to benchmark"""
        if len(strategy_returns) != len(benchmark_returns) or len(strategy_returns) < 2:
            return 0.0
        
        covariance = np.cov(strategy_returns, benchmark_returns)[0][1]
        benchmark_variance = np.var(benchmark_returns)
        
        if benchmark_variance == 0:
            return 0.0
        
        return covariance / benchmark_variance
    
    @staticmethod
    def calculate_alpha(
        strategy_returns: List[float],
        benchmark_returns: List[float],
        risk_free_rate: float = 0.0
    ) -> float:
        """Calculate alpha (Jensen's alpha)"""
        beta = PerformanceCalculator.calculate_beta(strategy_returns, benchmark_returns)
        
        strategy_mean = np.mean(strategy_returns)
        benchmark_mean = np.mean(benchmark_returns)
        
        alpha = strategy_mean - (risk_free_rate + beta * (benchmark_mean - risk_free_rate))
        return alpha * 252  # Annualize
