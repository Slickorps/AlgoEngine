"""Core engine for AlgoEngine"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Type

from .interfaces import (
    IAlgorithm, IDataFeed, ITransactionHandler, IResultHandler,
    IPortfolio, IExecutionModel, IRiskManager, Order, OrderEvent
)
from .events import EventBus, Event, EventType, get_event_bus
from .timekeeper import TimeKeeper
from ..utils.logger import get_logger
from ..utils.config import Config, get_config

logger = get_logger("engine")


class EngineState(Enum):
    """Engine states"""
    IDLE = "idle"
    INITIALIZING = "initializing"
    WARMUP = "warmup"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class Engine:
    """Main trading engine orchestrating all components"""
    
    def __init__(
        self,
        config: Optional[Config] = None,
        algorithm_class: Optional[Type[IAlgorithm]] = None,
        data_feed: Optional[IDataFeed] = None,
        transaction_handler: Optional[ITransactionHandler] = None,
        result_handler: Optional[IResultHandler] = None,
        portfolio: Optional[IPortfolio] = None,
        execution_model: Optional[IExecutionModel] = None,
        risk_manager: Optional[IRiskManager] = None,
        is_backtest: bool = False,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> None:
        self._config = config or get_config()
        self._algorithm_class = algorithm_class
        self._data_feed = data_feed
        self._transaction_handler = transaction_handler
        self._result_handler = result_handler
        self._portfolio = portfolio
        self._execution_model = execution_model
        self._risk_manager = risk_manager
        
        self._is_backtest = is_backtest
        self._start_date = start_date
        self._end_date = end_date
        
        self._state = EngineState.IDLE
        self._algorithm: Optional[IAlgorithm] = None
        self._event_bus = get_event_bus()
        self._timekeeper = TimeKeeper(
            timezone_str=self._config.timezone,
            is_backtest=is_backtest
        )
        
        self._warmup_period: timedelta = timedelta(days=0)
        self._current_warmup_time: Optional[datetime] = None
        self._is_warming_up: bool = False
        
        self._running: bool = False
        self._tasks: List[asyncio.Task] = []
        
        self._setup_event_handlers()
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers"""
        self._event_bus.subscribe(EventType.TICK, self._on_tick)
        self._event_bus.subscribe(EventType.BAR, self._on_bar)
        self._event_bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
    
    def _on_tick(self, event: Event) -> None:
        """Handle tick events"""
        if self._algorithm and not self._is_warming_up:
            self._algorithm.on_data(event.data)
    
    def _on_bar(self, event: Event) -> None:
        """Handle bar events"""
        if self._algorithm and not self._is_warming_up:
            self._algorithm.on_data(event.data)
    
    def _on_order_filled(self, event: Event) -> None:
        """Handle order fill events"""
        if isinstance(event.data, OrderEvent):
            if self._algorithm:
                self._algorithm.on_order_event(event.data)
            if self._portfolio:
                self._portfolio.process_fill(event.data)
            if self._risk_manager:
                # Update risk manager with new position if needed
                pass
    
    async def initialize(self) -> bool:
        """Initialize the engine"""
        try:
            self._state = EngineState.INITIALIZING
            logger.info("Initializing engine...")
            
            # Initialize data feed
            if self._data_feed:
                await self._data_feed.connect()
            
            # Initialize algorithm
            if self._algorithm_class:
                self._algorithm = self._algorithm_class()
                self._algorithm.initialize()
                if self._result_handler:
                    self._result_handler.set_algorithm(self._algorithm)
            
            # Set initial cash
            if self._portfolio:
                self._portfolio.set_cash(Decimal('100000'))  # Default starting cash
            
            logger.info("Engine initialized successfully")
            return True
            
        except Exception as e:
            self._state = EngineState.ERROR
            logger.error(f"Engine initialization failed: {e}")
            return False
    
    async def warmup(self, period: timedelta = timedelta(days=30)) -> None:
        """Run warmup period"""
        if not self._is_backtest:
            logger.warning("Warmup only applicable in backtest mode")
            return
        
        self._is_warming_up = True
        self._warmup_period = period
        self._state = EngineState.WARMUP
        
        logger.info(f"Starting warmup period: {period}")
        self._event_bus.emit(Event(
            event_type=EventType.WARMUP_STARTED,
            timestamp=self._timekeeper.current_time
        ))
        
        # Set warmup start time
        warmup_start = self._start_date - period if self._start_date else datetime.now() - period
        self._timekeeper.set_time(warmup_start)
        
        # Process historical data during warmup
        if self._data_feed and self._algorithm:
            # Load historical data for warmup
            pass
        
        self._is_warming_up = False
        self._state = EngineState.RUNNING
        
        self._event_bus.emit(Event(
            event_type=EventType.WARMUP_FINISHED,
            timestamp=self._timekeeper.current_time
        ))
        
        if self._algorithm:
            self._algorithm.on_warmup_finished()
        
        logger.info("Warmup completed")
    
    async def start(self) -> None:
        """Start the engine"""
        if self._state in [EngineState.RUNNING, EngineState.WARMUP]:
            logger.warning("Engine is already running")
            return
        
        if not await self.initialize():
            return
        
        if self._is_backtest:
            await self.warmup(self._warmup_period)
        
        self._state = EngineState.RUNNING
        self._running = True
        
        logger.info("Engine started")
        self._event_bus.emit(Event(
            event_type=EventType.START,
            timestamp=self._timekeeper.current_time
        ))
        
        # Start background tasks
        self._tasks.append(asyncio.create_task(self._timekeeper.run()))
        self._tasks.append(asyncio.create_task(self._event_bus.start()))
        
        if self._data_feed:
            self._tasks.append(asyncio.create_task(self._run_data_feed()))
    
    async def _run_data_feed(self) -> None:
        """Run data feed processing loop"""
        while self._running:
            try:
                if not self._data_feed.is_connected():
                    await self._data_feed.connect()
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Data feed error: {e}")
                await asyncio.sleep(5)  # Retry after delay
    
    def pause(self) -> None:
        """Pause the engine"""
        if self._state == EngineState.RUNNING:
            self._state = EngineState.PAUSED
            logger.info("Engine paused")
    
    def resume(self) -> None:
        """Resume the engine"""
        if self._state == EngineState.PAUSED:
            self._state = EngineState.RUNNING
            logger.info("Engine resumed")
    
    async def stop(self) -> None:
        """Stop the engine"""
        if self._state == EngineState.STOPPED:
            return
        
        self._state = EngineState.STOPPING
        self._running = False
        
        logger.info("Stopping engine...")
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        
        if self._data_feed:
            await self._data_feed.disconnect()
        
        if self._algorithm:
            self._algorithm.terminate("Engine stopped")
        
        self._event_bus.stop()
        self._timekeeper.stop()
        
        self._state = EngineState.STOPPED
        logger.info("Engine stopped")
        
        self._event_bus.emit(Event(
            event_type=EventType.STOP,
            timestamp=self._timekeeper.current_time
        ))
    
    def submit_order(self, order: Order) -> None:
        """Submit an order"""
        if self._state != EngineState.RUNNING:
            logger.warning("Cannot submit order - engine not running")
            return
        
        if self._risk_manager and not self._risk_manager.is_within_limits(self._portfolio):
            logger.warning("Order rejected - risk limits exceeded")
            return
        
        if self._transaction_handler:
            event = self._transaction_handler.process_order(order)
            self._event_bus.emit(Event(
                event_type=EventType.ORDER_SUBMITTED,
                timestamp=self._timekeeper.current_time,
                data=event
            ))
    
    def cancel_order(self, order_id: str) -> None:
        """Cancel an order"""
        if self._transaction_handler:
            event = self._transaction_handler.cancel_order(order_id)
            self._event_bus.emit(Event(
                event_type=EventType.ORDER_CANCELLED,
                timestamp=self._timekeeper.current_time,
                data=event
            ))
    
    @property
    def state(self) -> EngineState:
        """Get current engine state"""
        return self._state
    
    @property
    def is_running(self) -> bool:
        """Check if engine is running"""
        return self._running and self._state == EngineState.RUNNING
    
    @property
    def is_backtest(self) -> bool:
        """Check if in backtest mode"""
        return self._is_backtest
    
    @property
    def algorithm(self) -> Optional[IAlgorithm]:
        """Get the algorithm instance"""
        return self._algorithm
    
    @property
    def portfolio(self) -> Optional[IPortfolio]:
        """Get the portfolio instance"""
        return self._portfolio
    
    @property
    def timekeeper(self) -> TimeKeeper:
        """Get the time keeper"""
        return self._timekeeper
    
    @property
    def event_bus(self) -> EventBus:
        """Get the event bus"""
        return self._event_bus
    
    def set_algorithm(self, algorithm_class: Type[IAlgorithm]) -> None:
        """Set the algorithm class"""
        self._algorithm_class = algorithm_class
    
    def set_data_feed(self, data_feed: IDataFeed) -> None:
        """Set the data feed"""
        self._data_feed = data_feed
    
    def set_transaction_handler(self, handler: ITransactionHandler) -> None:
        """Set the transaction handler"""
        self._transaction_handler = handler
    
    def set_result_handler(self, handler: IResultHandler) -> None:
        """Set the result handler"""
        self._result_handler = handler
    
    def set_portfolio(self, portfolio: IPortfolio) -> None:
        """Set the portfolio"""
        self._portfolio = portfolio
    
    def set_warmup_period(self, period: timedelta) -> None:
        """Set the warmup period"""
        self._warmup_period = period
