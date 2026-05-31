"""Parameter optimization for backtesting"""

from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Callable
from itertools import product
import json

from .engine import BacktestEngine, BacktestConfig
from .results import BacktestResults
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("backtesting.optimization")


@dataclass
class OptimizationResult:
    """Result of a single optimization run"""
    parameters: Dict[str, Any]
    results: BacktestResults
    score: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "parameters": self.parameters,
            "score": self.score,
            "summary": self.results.get_summary()
        }


class ParameterOptimizer:
    """Optimize strategy parameters using grid search"""
    
    def __init__(
        self,
        strategy_class: type,
        config: BacktestConfig,
        parameter_grid: Dict[str, List[Any]]
    ) -> None:
        self._strategy_class = strategy_class
        self._config = config
        self._parameter_grid = parameter_grid
        self._results: List[OptimizationResult] = []
        
        # Default scoring function: Sharpe ratio
        self._scoring_function: Callable[[BacktestResults], float] = \
            lambda r: r.sharpe_ratio if r else 0.0
    
    def set_scoring_function(
        self,
        func: Callable[[BacktestResults], float]
    ) -> None:
        """Set custom scoring function"""
        self._scoring_function = func
    
    def _generate_parameter_combinations(self) -> List[Dict[str, Any]]:
        """Generate all parameter combinations from grid"""
        keys = list(self._parameter_grid.keys())
        values = [self._parameter_grid[k] for k in keys]
        
        combinations = []
        for combo in product(*values):
            param_dict = dict(zip(keys, combo))
            combinations.append(param_dict)
        
        return combinations
    
    async def optimize(self, data: Dict[Symbol, List[Any]]) -> OptimizationResult:
        """Run parameter optimization"""
        combinations = self._generate_parameter_combinations()
        total = len(combinations)
        
        logger.info(f"Starting optimization with {total} parameter combinations")
        
        best_result: Optional[OptimizationResult] = None
        
        for i, params in enumerate(combinations, 1):
            logger.info(f"Running backtest {i}/{total} with params: {params}")
            
            # Create engine
            engine = BacktestEngine(self._config)
            
            # Load data
            for symbol, bars in data.items():
                engine.load_historical_data(symbol, bars)
            
            # Register and add strategy
            engine.register_strategy("strategy", self._strategy_class)
            strategy = engine.add_strategy("strategy", params)
            
            if not strategy:
                logger.warning(f"Failed to create strategy with params: {params}")
                continue
            
            # Run backtest
            results = await engine.run()
            
            # Score results
            score = self._scoring_function(results)
            
            # Store result
            opt_result = OptimizationResult(
                parameters=params,
                results=results,
                score=score
            )
            self._results.append(opt_result)
            
            # Track best
            if best_result is None or score > best_result.score:
                best_result = opt_result
                logger.info(f"New best score: {score:.4f} with params: {params}")
        
        if best_result:
            logger.info(f"Optimization complete. Best score: {best_result.score:.4f}")
        
        return best_result
    
    def get_all_results(self) -> List[OptimizationResult]:
        """Get all optimization results"""
        return self._results.copy()
    
    def get_top_results(self, n: int = 10) -> List[OptimizationResult]:
        """Get top N results by score"""
        sorted_results = sorted(
            self._results,
            key=lambda x: x.score,
            reverse=True
        )
        return sorted_results[:n]
    
    def get_results_dataframe(self):
        """Get results as pandas DataFrame (if pandas available)"""
        try:
            import pandas as pd
            
            rows = []
            for result in self._results:
                row = {
                    **result.parameters,
                    "score": result.score,
                    "total_return": result.results.total_return_percent,
                    "sharpe_ratio": result.results.sharpe_ratio,
                    "max_drawdown": result.results.max_drawdown,
                    "win_rate": result.results.win_rate,
                    "total_trades": result.results.total_trades
                }
                rows.append(row)
            
            return pd.DataFrame(rows)
        except ImportError:
            logger.warning("pandas not available, returning None")
            return None
    
    def save_results(self, filepath: str) -> None:
        """Save optimization results to JSON"""
        data = {
            "config": {
                "strategy": self._strategy_class.__name__,
                "parameter_grid": {
                    k: [str(v) for v in vals]
                    for k, vals in self._parameter_grid.items()
                }
            },
            "results": [
                r.to_dict() for r in self.get_top_results(20)
            ]
        }
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info(f"Optimization results saved to {filepath}")
    
    def analyze_parameter_sensitivity(
        self,
        parameter: str
    ) -> Dict[Any, Dict[str, float]]:
        """Analyze sensitivity to a specific parameter"""
        sensitivity = {}
        
        for result in self._results:
            param_value = result.parameters.get(parameter)
            if param_value not in sensitivity:
                sensitivity[param_value] = {
                    "scores": [],
                    "returns": [],
                    "sharpe_ratios": []
                }
            
            sensitivity[param_value]["scores"].append(result.score)
            sensitivity[param_value]["returns"].append(
                result.results.total_return_percent
            )
            sensitivity[param_value]["sharpe_ratios"].append(
                result.results.sharpe_ratio
            )
        
        # Calculate averages
        analysis = {}
        for param_value, metrics in sensitivity.items():
            analysis[param_value] = {
                "avg_score": sum(metrics["scores"]) / len(metrics["scores"]),
                "avg_return": sum(metrics["returns"]) / len(metrics["returns"]),
                "avg_sharpe": sum(metrics["sharpe_ratios"]) / len(metrics["sharpe_ratios"]),
                "count": len(metrics["scores"])
            }
        
        return analysis
