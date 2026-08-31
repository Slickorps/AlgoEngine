"""Tests for backtest parameter optimization."""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from src.backtesting.optimization import ParameterOptimizer, OptimizationResult
from src.backtesting.engine import BacktestConfig
from src.backtesting.results import BacktestResults
from src.algorithms.sample_strategies import SMAStrategy
from src.data.models import Symbol, Bar


@pytest.fixture
def config():
    return BacktestConfig(
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 1, 10),
        symbols=[Symbol(ticker="AAPL")],
        initial_cash=Decimal("100000"),
    )


@pytest.fixture
def data():
    symbol = Symbol(ticker="AAPL")
    bars = []
    base = datetime(2023, 1, 1)
    price = 100.0
    for i in range(10):
        bars.append(Bar(
            symbol=symbol,
            timestamp=base + timedelta(days=i),
            open=Decimal(str(price)),
            high=Decimal(str(price + 2)),
            low=Decimal(str(price - 2)),
            close=Decimal(str(price)),
            volume=Decimal("1000"),
        ))
        price += 1
    return {symbol: bars}


def make_result(parameters, score, total_return=5.0):
    results = BacktestResults(
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 1, 10),
        initial_cash=Decimal("100000"),
        final_value=Decimal("105000"),
    )
    return OptimizationResult(parameters=parameters, results=results, score=score)


class TestOptimizationResult:
    def test_to_dict(self):
        result = make_result({"fast_period": 5}, 1.5)
        data = result.to_dict()
        assert data["parameters"] == {"fast_period": 5}
        assert data["score"] == 1.5
        assert "summary" in data


class TestParameterOptimizer:
    def test_initialization(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        assert optimizer._strategy_class == SMAStrategy
        assert optimizer._config is config

    def test_generate_parameter_combinations(self, config):
        grid = {"fast_period": [5, 10], "slow_period": [20, 30]}
        optimizer = ParameterOptimizer(SMAStrategy, config, grid)
        combos = optimizer._generate_parameter_combinations()
        assert len(combos) == 4

    def test_generate_parameter_combinations_single(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        combos = optimizer._generate_parameter_combinations()
        assert combos == [{"fast_period": 5}]

    def test_set_scoring_function(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer.set_scoring_function(lambda r: 42.0)
        assert optimizer._scoring_function(None) == 42.0

    async def test_optimize_runs_backtests(self, config, data):
        grid = {"fast_period": [5, 10], "slow_period": [20]}
        optimizer = ParameterOptimizer(SMAStrategy, config, grid)
        best = await optimizer.optimize(data)

        assert best is not None
        assert len(optimizer.get_all_results()) == 2
        assert best.parameters["fast_period"] in [5, 10]

    async def test_optimize_empty_grid(self, config, data):
        optimizer = ParameterOptimizer(SMAStrategy, config, {})
        best = await optimizer.optimize(data)
        assert best is not None

    def test_get_all_results_returns_copy(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer._results = [make_result({"fast_period": 5}, 1.0)]
        results = optimizer.get_all_results()
        results.append(make_result({"fast_period": 10}, 2.0))
        assert len(optimizer.get_all_results()) == 1

    def test_get_top_results(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer._results = [
            make_result({"fast_period": 5}, 1.0),
            make_result({"fast_period": 10}, 3.0),
            make_result({"fast_period": 15}, 2.0),
        ]
        top = optimizer.get_top_results(2)
        assert [r.score for r in top] == [3.0, 2.0]

    def test_get_results_dataframe(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer._results = [
            make_result({"fast_period": 5}, 1.5, total_return=10.0),
            make_result({"fast_period": 10}, 2.5, total_return=20.0),
        ]
        df = optimizer.get_results_dataframe()
        assert len(df) == 2
        assert "fast_period" in df.columns
        assert "score" in df.columns

    def test_save_results(self, config, tmp_path):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer._results = [
            make_result({"fast_period": 5}, 1.5),
            make_result({"fast_period": 10}, 2.5),
        ]
        filepath = str(tmp_path / "results.json")
        optimizer.save_results(filepath)

        import json
        with open(filepath) as f:
            data = json.load(f)
        assert data["config"]["strategy"] == "SMAStrategy"
        assert len(data["results"]) == 2

    def test_analyze_parameter_sensitivity(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5, 10]})
        optimizer._results = [
            make_result({"fast_period": 5}, 1.0, total_return=10.0),
            make_result({"fast_period": 5}, 2.0, total_return=20.0),
            make_result({"fast_period": 10}, 3.0, total_return=30.0),
        ]
        analysis = optimizer.analyze_parameter_sensitivity("fast_period")

        assert 5 in analysis
        assert 10 in analysis
        assert analysis[5]["avg_score"] == 1.5
        assert analysis[5]["count"] == 2
        assert analysis[10]["avg_score"] == 3.0

    def test_analyze_parameter_sensitivity_missing_param(self, config):
        optimizer = ParameterOptimizer(SMAStrategy, config, {"fast_period": [5]})
        optimizer._results = [make_result({"fast_period": 5}, 1.0)]
        analysis = optimizer.analyze_parameter_sensitivity("nonexistent")
        assert analysis == {}
