"""Tests for performance metrics"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta

from src.portfolio.metrics import PerformanceMetrics, PerformanceCalculator
from src.portfolio.portfolio import PortfolioSnapshot


class TestPerformanceCalculator:
    """Test PerformanceCalculator"""
    
    @pytest.fixture
    def sample_snapshots(self):
        """Create sample portfolio snapshots"""
        base_time = datetime.now()
        snapshots = []
        
        # Create 10 days of equity curve
        values = [100000, 101000, 102000, 101500, 103000,  
                  102000, 104000, 103500, 105000, 106000]
        
        for i, value in enumerate(values):
            snapshots.append(PortfolioSnapshot(
                timestamp=base_time + timedelta(days=i),
                cash=Decimal(str(value * 0.1)),
                positions_value=Decimal(str(value * 0.9)),
                total_value=Decimal(str(value)),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0")
            ))
        
        return snapshots
    
    def test_calculate_returns(self):
        """Test return calculation"""
        equity_curve = [100, 110, 105, 115]
        returns = PerformanceCalculator.calculate_returns(equity_curve)
        
        assert len(returns) == 3
        assert returns[0] == pytest.approx(0.10, abs=0.01)  # 10% gain
        assert returns[1] == pytest.approx(-0.045, abs=0.01)  # ~-4.5% loss
        assert returns[2] == pytest.approx(0.095, abs=0.01)  # ~9.5% gain
    
    def test_calculate_sharpe_ratio(self):
        """Test Sharpe ratio calculation"""
        returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        sharpe = PerformanceCalculator.calculate_sharpe_ratio(returns)
        
        # Sharpe should be positive for positive returns
        assert sharpe > 0
    
    def test_calculate_max_drawdown(self):
        """Test max drawdown calculation"""
        equity_curve = [100, 110, 105, 115, 100, 120, 90, 130]
        
        max_dd, duration = PerformanceCalculator.calculate_max_drawdown(equity_curve)
        
        # Max drawdown is from 120 to 90 = 25%
        assert max_dd > 0
        assert duration > 0
    
    def test_calculate_metrics(self, sample_snapshots):
        """Test comprehensive metrics calculation"""
        metrics = PerformanceCalculator.calculate_metrics(sample_snapshots)
        
        assert metrics is not None
        assert metrics.total_return > 0  # 6% gain (100k to 106k)
        assert metrics.volatility >= 0
        assert metrics.sharpe_ratio is not None
        assert metrics.max_drawdown >= 0
    
    def test_calculate_var(self):
        """Test Value at Risk calculation"""
        returns = [0.05, -0.02, 0.03, -0.05, 0.01, -0.03, 0.02]
        var = PerformanceCalculator.calculate_var(returns, confidence_level=0.95)
        
        # VaR should be negative (potential loss)
        assert var < 0
    
    def test_empty_returns(self):
        """Test handling of empty returns"""
        sharpe = PerformanceCalculator.calculate_sharpe_ratio([])
        assert sharpe == 0.0
        
        returns = PerformanceCalculator.calculate_returns([100])
        assert returns == []
    
    def test_single_snapshot(self):
        """Test metrics with single snapshot"""
        snapshots = [PortfolioSnapshot(
            timestamp=datetime.now(),
            cash=Decimal("100000"),
            positions_value=Decimal("0"),
            total_value=Decimal("100000"),
            unrealized_pnl=Decimal("0"),
            realized_pnl=Decimal("0")
        )]
        
        metrics = PerformanceCalculator.calculate_metrics(snapshots)
        assert metrics is None  # Not enough data


class TestPerformanceMetrics:
    """Test PerformanceMetrics dataclass"""
    
    def test_metrics_creation(self):
        """Test creating performance metrics"""
        metrics = PerformanceMetrics(
            total_return=10.5,
            annualized_return=25.0,
            volatility=15.0,
            sharpe_ratio=1.5,
            max_drawdown=5.0,
            max_drawdown_duration=10,
            win_rate=0.6,
            profit_factor=2.0,
            avg_trade_return=1.5,
            calmar_ratio=5.0
        )
        
        assert metrics.total_return == 10.5
        assert metrics.sharpe_ratio == 1.5
        assert metrics.calmar_ratio == 5.0


class TestEdgeCases:
    """Edge case and uncovered branch tests for metrics"""

    def test_calculate_var_different_confidence_levels(self):
        returns = [0.05, -0.02, 0.03, -0.05, 0.01, -0.03, 0.02, -0.04, 0.06, -0.01]
        var_95 = PerformanceCalculator.calculate_var(returns, confidence_level=0.95)
        var_99 = PerformanceCalculator.calculate_var(returns, confidence_level=0.99)
        assert var_95 < 0
        assert var_99 <= var_95  # 99% VaR should be more negative

    def test_calculate_var_empty(self):
        var = PerformanceCalculator.calculate_var([])
        assert var == 0.0

    def test_sharpe_ratio_zero_volatility(self):
        returns = [0.01, 0.01, 0.01, 0.01]
        sharpe = PerformanceCalculator.calculate_sharpe_ratio(returns)
        assert sharpe == 0.0

    def test_sharpe_ratio_fewer_than_two(self):
        sharpe = PerformanceCalculator.calculate_sharpe_ratio([0.01])
        assert sharpe == 0.0

    def test_calculate_returns_two_elements(self):
        equity = [100, 110]
        returns = PerformanceCalculator.calculate_returns(equity)
        assert len(returns) == 1
        assert returns[0] == pytest.approx(0.10)

    def test_calculate_returns_single_element(self):
        returns = PerformanceCalculator.calculate_returns([100])
        assert returns == []

    def test_calculate_max_drawdown_single_element(self):
        dd, dur = PerformanceCalculator.calculate_max_drawdown([100])
        assert dd == 0.0
        assert dur == 0

    def test_calculate_max_drawdown_empty(self):
        dd, dur = PerformanceCalculator.calculate_max_drawdown([])
        assert dd == 0.0
        assert dur == 0

    def test_calculate_beta_valid(self):
        strategy = [0.01, 0.02, -0.005, 0.015, 0.003]
        benchmark = [0.005, 0.015, -0.002, 0.01, 0.002]
        beta = PerformanceCalculator.calculate_beta(strategy, benchmark)
        assert beta != 0.0

    def test_calculate_beta_length_mismatch(self):
        beta = PerformanceCalculator.calculate_beta([0.01, 0.02], [0.01])
        assert beta == 0.0

    def test_calculate_beta_fewer_than_two(self):
        beta = PerformanceCalculator.calculate_beta([0.01], [0.01])
        assert beta == 0.0

    def test_calculate_beta_zero_benchmark_variance(self):
        benchmark = [0.02, 0.02, 0.02, 0.02]
        strategy = [0.01, 0.02, -0.005, 0.015]
        beta = PerformanceCalculator.calculate_beta(strategy, benchmark)
        assert beta == 0.0

    def test_calculate_alpha_valid(self):
        strategy = [0.001, 0.002, -0.001, 0.0015, 0.0005]
        benchmark = [0.0005, 0.0015, -0.0002, 0.001, 0.0002]
        alpha = PerformanceCalculator.calculate_alpha(strategy, benchmark)
        assert isinstance(alpha, float)

    def test_calculate_metrics_zero_days(self):
        now = datetime.now()
        snapshots = [
            PortfolioSnapshot(
                timestamp=now,
                cash=Decimal("90000"),
                positions_value=Decimal("10000"),
                total_value=Decimal("100000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
            PortfolioSnapshot(
                timestamp=now,
                cash=Decimal("90000"),
                positions_value=Decimal("11000"),
                total_value=Decimal("101000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
        ]
        metrics = PerformanceCalculator.calculate_metrics(snapshots)
        assert metrics is not None
        assert metrics.annualized_return == metrics.total_return

    def test_calculate_metrics_no_drawdown(self):
        now = datetime.now()
        snapshots = [
            PortfolioSnapshot(
                timestamp=now,
                cash=Decimal("90000"),
                positions_value=Decimal("10000"),
                total_value=Decimal("100000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
            PortfolioSnapshot(
                timestamp=now + timedelta(days=1),
                cash=Decimal("91000"),
                positions_value=Decimal("10000"),
                total_value=Decimal("101000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
            PortfolioSnapshot(
                timestamp=now + timedelta(days=2),
                cash=Decimal("92000"),
                positions_value=Decimal("10000"),
                total_value=Decimal("102000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
        ]
        metrics = PerformanceCalculator.calculate_metrics(snapshots)
        assert metrics is not None
        assert metrics.max_drawdown == 0.0
        assert metrics.calmar_ratio == 0.0


class TestTradeMetrics:
    """Tests for trade-based performance metrics"""

    def _make_trade(self, net_pnl, trade_id="T1"):
        from src.trading.models import Trade, OrderSide
        from src.data.models import Symbol

        pnl = Decimal(str(net_pnl))
        return Trade(
            trade_id=trade_id,
            symbol=Symbol(ticker="AAPL"),
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            entry_price=Decimal("100"),
            exit_price=Decimal("100"),
            realized_pnl=pnl,
        )

    def test_calculate_trade_metrics_empty(self):
        win_rate, profit_factor, avg = PerformanceCalculator.calculate_trade_metrics(None)
        assert win_rate == 0.0
        assert profit_factor == 0.0
        assert avg == 0.0

    def test_calculate_trade_metrics_empty_list(self):
        win_rate, profit_factor, avg = PerformanceCalculator.calculate_trade_metrics([])
        assert win_rate == 0.0
        assert profit_factor == 0.0
        assert avg == 0.0

    def test_calculate_trade_metrics_win_and_loss(self):
        trades = [self._make_trade(100, "T1"), self._make_trade(-50, "T2")]

        win_rate, profit_factor, avg = PerformanceCalculator.calculate_trade_metrics(trades)

        assert win_rate == 50.0
        assert profit_factor == pytest.approx(2.0)
        assert avg == pytest.approx(25.0)

    def test_calculate_trade_metrics_all_wins_no_loss(self):
        trades = [self._make_trade(100, "T1"), self._make_trade(50, "T2")]

        win_rate, profit_factor, avg = PerformanceCalculator.calculate_trade_metrics(trades)

        assert win_rate == 100.0
        assert profit_factor == pytest.approx(150.0)
        assert avg == pytest.approx(75.0)

    def test_calculate_trade_metrics_all_losses(self):
        trades = [self._make_trade(-100, "T1"), self._make_trade(-50, "T2")]

        win_rate, profit_factor, avg = PerformanceCalculator.calculate_trade_metrics(trades)

        assert win_rate == 0.0
        assert profit_factor == 0.0
        assert avg == pytest.approx(-75.0)

    def test_calculate_metrics_with_trades(self):
        now = datetime.now()
        snapshots = [
            PortfolioSnapshot(
                timestamp=now,
                cash=Decimal("100000"),
                positions_value=Decimal("0"),
                total_value=Decimal("100000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
            PortfolioSnapshot(
                timestamp=now + timedelta(days=1),
                cash=Decimal("101000"),
                positions_value=Decimal("0"),
                total_value=Decimal("101000"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
        ]
        trades = [self._make_trade(100, "T1"), self._make_trade(-50, "T2")]

        metrics = PerformanceCalculator.calculate_metrics(snapshots, trades=trades)

        assert metrics is not None
        assert metrics.win_rate == 50.0
        assert metrics.profit_factor == pytest.approx(2.0)
        assert metrics.avg_trade_return == pytest.approx(25.0)
