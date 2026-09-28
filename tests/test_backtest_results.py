"""Tests for backtest result aggregation and reporting."""

from datetime import datetime, timedelta
from decimal import Decimal

from src.backtesting.results import BacktestResults, TradeRecord
from src.portfolio.portfolio import PortfolioSnapshot
from src.trading.models import Trade, OrderSide
from src.data.models import Symbol


def make_snapshot(dt, value):
    return PortfolioSnapshot(
        timestamp=dt,
        cash=Decimal(str(value)),
        positions_value=Decimal("0"),
        total_value=Decimal(str(value)),
        unrealized_pnl=Decimal("0"),
        realized_pnl=Decimal("0"),
    )


def make_snapshots(values, start=None):
    base = start or datetime(2023, 1, 1)
    return [make_snapshot(base + timedelta(days=i), v) for i, v in enumerate(values)]


def make_trade(symbol="AAPL", realized=100, commission=0, slippage=0):
    return Trade(
        trade_id=f"t-{symbol}-{realized}",
        symbol=Symbol(ticker=symbol),
        entry_time=datetime(2023, 1, 1),
        exit_time=datetime(2023, 1, 2),
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        realized_pnl=Decimal(str(realized)),
        commission=Decimal(str(commission)),
        slippage=Decimal(str(slippage)),
    )


class TestTradeRecord:
    def test_gross_and_net_pnl(self):
        record = TradeRecord(
            trade_id="1", symbol="AAPL",
            entry_time=datetime(2023, 1, 1), exit_time=datetime(2023, 1, 2),
            side="BUY", quantity=Decimal("10"),
            entry_price=Decimal("100"), exit_price=Decimal("110"),
            realized_pnl=Decimal("100"),
            commission=Decimal("2"), slippage=Decimal("1"),
        )
        assert record.gross_pnl == Decimal("103")
        assert record.net_pnl == Decimal("97")


class TestFinalValue:
    def test_final_value_from_last_snapshot(self):
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
            snapshots=make_snapshots([100, 110, 120]),
        )
        assert results.final_value == Decimal("120")

    def test_final_value_falls_back_to_initial_cash(self):
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
        )
        assert results.final_value == Decimal("100")
        assert results.max_drawdown == 0.0
        assert results.sharpe_ratio == 0.0
        assert results.get_drawdown_series() == []

    def test_zero_initial_cash_return(self):
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("0"),
        )
        assert results.total_return_percent == 0.0


class TestTradeMetrics:
    def _results(self, trades, values=(100, 110)):
        return BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
            snapshots=make_snapshots(list(values)),
            trades=trades,
        )

    def test_trade_counts_and_win_rate(self):
        results = self._results([make_trade(realized=100), make_trade(realized=-50)])
        assert results.total_trades == 2
        assert results.winning_trades == 1
        assert results.losing_trades == 1
        assert results.win_rate == 50.0

    def test_avg_trade_return(self):
        results = self._results([make_trade(realized=100), make_trade(realized=-50)])
        assert results.avg_trade_return == Decimal("25")

    def test_profit_factor_with_losses(self):
        results = self._results([make_trade(realized=100), make_trade(realized=-50)])
        assert results.profit_factor == 2.0

    def test_profit_factor_only_wins(self):
        results = self._results([make_trade(realized=100)])
        assert results.profit_factor == 100.0

    def test_profit_factor_no_trades(self):
        results = self._results([])
        assert results.profit_factor == 0.0
        assert results.win_rate == 0.0
        assert results.avg_trade_return == Decimal("0")

    def test_net_pnl_after_costs(self):
        results = self._results([
            make_trade(realized=100, commission=10, slippage=5),
        ])
        assert results.winning_trades == 1
        assert results.avg_trade_return == Decimal("85")


class TestCurvesAndGrouping:
    def _results(self, values=(100, 90, 95)):
        return BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
            snapshots=make_snapshots(list(values)),
        )

    def test_equity_curve(self):
        assert self._results().get_equity_curve() == [100.0, 90.0, 95.0]

    def test_drawdown_series(self):
        series = self._results().get_drawdown_series()
        assert series[0] == 0.0
        assert abs(series[1] - 10.0) < 1e-9
        assert abs(series[2] - 5.0) < 1e-9

    def test_max_drawdown(self):
        assert abs(self._results().max_drawdown - 10.0) < 1e-9

    def test_sharpe_ratio_with_snapshots(self):
        results = self._results((100, 105, 103, 108))
        assert isinstance(results.sharpe_ratio, float)

    def test_trades_by_symbol(self):
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
            trades=[
                make_trade("AAPL", 10), make_trade("AAPL", 20), make_trade("MSFT", 5),
            ],
        )
        grouped = results.get_trades_by_symbol()
        assert set(grouped.keys()) == {"AAPL", "MSFT"}
        assert len(grouped["AAPL"]) == 2

    def test_monthly_returns(self):
        snapshots = [
            make_snapshot(datetime(2023, 1, 1), 100),
            make_snapshot(datetime(2023, 1, 20), 110),
            make_snapshot(datetime(2023, 2, 1), 120),
            make_snapshot(datetime(2023, 2, 25), 132),
        ]
        results = BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 3, 1),
            initial_cash=Decimal("100"),
            snapshots=snapshots,
        )
        monthly = results.get_monthly_returns()
        assert abs(monthly["2023-01"] - 10.0) < 1e-6
        assert abs(monthly["2023-02"] - 10.0) < 1e-6


class TestReporting:
    def _results(self):
        return BacktestResults(
            start_date=datetime(2023, 1, 1),
            end_date=datetime(2023, 1, 10),
            initial_cash=Decimal("100"),
            snapshots=make_snapshots([100, 110]),
            trades=[make_trade(realized=100), make_trade(realized=-50)],
        )

    def test_get_summary(self):
        summary = self._results().get_summary()
        assert summary["period"]["duration_days"] == 9
        assert summary["trades"]["total"] == 2
        assert summary["returns"]["initial_capital"] == 100.0

    def test_to_dict(self):
        data = self._results().to_dict()
        assert data["total_trades"] == 2
        assert "equity_curve" in data
        assert "monthly_returns" in data

    def test_print_report(self, capsys):
        self._results().print_report()
        captured = capsys.readouterr()
        assert "BACKTEST RESULTS" in captured.out
        assert "Total Trades" in captured.out
