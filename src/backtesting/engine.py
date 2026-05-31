"""Backtest engine for AlgoEngine"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Dict, Optional, Type, Any

from ..algorithms.strategy import Strategy, StrategyConfig
from ..algorithms.strategy_manager import StrategyManager
from ..portfolio.portfolio import Portfolio
from ..engine.events import EventBus, EventType
from ..engine.timekeeper import TimeKeeper, TimeMode
from ..data.models import Symbol, Bar, Resolution
from ..adapters.simulated_broker import SimulatedBroker
from ..trading.execution_engine import ExecutionEngine
from ..trading.order_manager import OrderManager
from ..trading.position_manager import PositionManager
from ..utils.logger import get_logger
from .results import BacktestResults

logger = get_logger("backtesting.engine")


@dataclass
class BacktestConfig:
    """Backtest configuration"""
    start_date: datetime
    end_date: datetime
    symbols: List[Symbol]
    resolution: Resolution = Resolution.MINUTE
    initial_cash: Decimal = Decimal("100000.00")
    commission_per_share: Decimal = Decimal("0.005")
    slippage_percent: float = 0.001  # 0.1%
    fill_probability: float = 1.0  # Always fill in backtest
    
    def __post_init__(self):
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")


class BacktestEngine:
    """Engine for running strategy backtests"""
    
    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        
        # Initialize core components
        self._event_bus = EventBus()
        self._time_keeper = TimeKeeper(mode=TimeMode.BACKTEST)
        
        # Initialize portfolio
        self._portfolio = Portfolio(initial_cash=config.initial_cash)
        
        # Initialize trading components
        self._order_manager = OrderManager()
        self._position_manager = PositionManager()
        
        # Initialize simulated broker
        self._broker = SimulatedBroker(
            fill_probability=config.fill_probability,
            latency_ms=0  # No latency in backtest
        )
        
        # Initialize execution engine
        self._execution_engine = ExecutionEngine()
        self._execution_engine._broker = self._broker
        
        # Initialize strategy manager
        self._strategy_manager = StrategyManager(
            portfolio=self._portfolio,
            event_bus=self._event_bus
        )
        
        # Data storage
        self._historical_data: Dict[Symbol, List[Bar]] = {}
        self._current_index: Dict[Symbol, int] = {}
        
        # Results
        self._results: Optional[BacktestResults] = None
        
        logger.info(f"BacktestEngine initialized for {len(config.symbols)} symbols")
    
    def load_historical_data(
        self,
        symbol: Symbol,
        data: List[Bar]
    ) -> None:
        """Load historical data for a symbol"""
        # Filter data by date range
        filtered = [
            bar for bar in data
            if self._config.start_date <= bar.timestamp <= self._config.end_date
        ]
        
        # Sort by timestamp
        filtered.sort(key=lambda x: x.timestamp)
        
        self._historical_data[symbol] = filtered
        self._current_index[symbol] = 0
        
        logger.info(f"Loaded {len(filtered)} bars for {symbol.ticker}")
    
    def register_strategy(
        self,
        name: str,
        strategy_class: Type[Strategy]
    ) -> None:
        """Register a strategy class"""
        self._strategy_manager.register_strategy_class(name, strategy_class)
        logger.info(f"Registered strategy: {name}")
    
    def add_strategy(
        self,
        strategy_name: str,
        parameters: Optional[Dict[str, Any]] = None
    ) -> Optional[Strategy]:
        """Add a strategy to the backtest"""
        config = StrategyConfig(
            name=strategy_name,
            symbols=self._config.symbols,
            parameters=parameters or {}
        )
        
        strategy = self._strategy_manager.create_strategy(strategy_name, config)
        return strategy
    
    async def run(self) -> BacktestResults:
        """Run the backtest"""
        logger.info("Starting backtest...")
        
        # Connect broker
        await self._broker.connect()
        
        # Start execution engine
        await self._execution_engine.start()
        
        # Start all strategies
        self._strategy_manager.start_all()
        
        # Get all timestamps from data
        all_timestamps = self._get_all_timestamps()
        
        if not all_timestamps:
            logger.warning("No historical data loaded")
            return BacktestResults(
                start_date=self._config.start_date,
                end_date=self._config.end_date,
                initial_cash=self._config.initial_cash
            )
        
        # Run simulation
        for timestamp in all_timestamps:
            self._time_keeper.set_current_time(timestamp)
            
            # Process bars for this timestamp
            await self._process_timestamp(timestamp)
            
            # Take portfolio snapshot
            self._portfolio.take_snapshot()
        
        # Stop everything
        self._strategy_manager.stop_all()
        await self._execution_engine.stop()
        await self._broker.disconnect()
        
        # Generate results
        self._results = self._generate_results()
        
        logger.info("Backtest completed")
        return self._results
    
    def _get_all_timestamps(self) -> List[datetime]:
        """Get all unique timestamps from historical data"""
        timestamps = set()
        
        for symbol, bars in self._historical_data.items():
            for bar in bars:
                timestamps.add(bar.timestamp)
        
        return sorted(list(timestamps))
    
    async def _process_timestamp(self, timestamp: datetime) -> None:
        """Process all data for a given timestamp"""
        for symbol in self._config.symbols:
            bars = self._historical_data.get(symbol, [])
            index = self._current_index.get(symbol, 0)
            
            if index < len(bars) and bars[index].timestamp == timestamp:
                bar = bars[index]
                self._current_index[symbol] = index + 1
                
                # Emit bar event
                self._event_bus.emit(EventType.BAR, bar)
                
                # Update position prices
                position = self._position_manager.get_position(symbol)
                if position:
                    position.update_price(bar.close, timestamp)
    
    def _generate_results(self) -> BacktestResults:
        """Generate backtest results"""
        snapshots = self._portfolio.get_snapshots()
        trades = self._position_manager.get_all_trades()
        
        return BacktestResults(
            start_date=self._config.start_date,
            end_date=self._config.end_date,
            initial_cash=self._config.initial_cash,
            final_value=self._portfolio.total_value,
            snapshots=snapshots,
            trades=trades
        )
    
    def get_results(self) -> Optional[BacktestResults]:
        """Get backtest results"""
        return self._results
    
    def reset(self) -> None:
        """Reset the engine for a new backtest"""
        self._portfolio = Portfolio(initial_cash=self._config.initial_cash)
        self._current_index = {s: 0 for s in self._config.symbols}
        self._results = None
        self._historical_data.clear()
        logger.info("BacktestEngine reset")
