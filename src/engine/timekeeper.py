"""Time management for AlgoEngine"""

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum, auto

from pytz import timezone

from ..utils.logger import get_logger

logger = get_logger("timekeeper")


class TimeMode(Enum):
    """Time operation mode"""
    LIVE = auto()
    BACKTEST = auto()
    PAPER = auto()


@dataclass
class Schedule:
    """Scheduled task"""
    id: str
    callback: Callable[[], None]
    next_run: datetime
    interval: Optional[timedelta] = None
    timezone: str = "UTC"
    enabled: bool = True


class TimeKeeper:
    """Time management for backtesting and live trading"""
    
    def __init__(
        self,
        timezone_str: str = "UTC",
        is_backtest: bool = False,
        mode: TimeMode = TimeMode.LIVE
    ) -> None:
        self._timezone = timezone(timezone_str)
        self._mode = mode
        self._is_backtest = is_backtest or (mode == TimeMode.BACKTEST)
        self._current_time: datetime = datetime.now(self._timezone)
        self._start_time: datetime = self._current_time
        self._schedules: Dict[str, Schedule] = {}
        self._running: bool = False
        self._time_changed_callbacks: List[Callable[[datetime], None]] = []
    
    @property
    def mode(self) -> TimeMode:
        """Get time operation mode"""
        return self._mode
    
    def set_current_time(self, time: datetime) -> None:
        """Set current time (backtest mode only)"""
        if self._is_backtest:
            self._current_time = time if time.tzinfo else self._timezone.localize(time)
            self._notify_time_changed()
        else:
            raise RuntimeError("Cannot set time in live trading mode")
        
    @property
    def current_time(self) -> datetime:
        """Get current time"""
        if not self._is_backtest:
            self._current_time = datetime.now(self._timezone)
        return self._current_time
    
    @current_time.setter
    def current_time(self, value: datetime) -> None:
        """Set current time (backtest mode only)"""
        if self._is_backtest:
            self._current_time = value if value.tzinfo else self._timezone.localize(value)
            self._notify_time_changed()
        else:
            raise RuntimeError("Cannot set time in live trading mode")
    
    def advance_time(self, delta: timedelta) -> None:
        """Advance time by delta (backtest mode only)"""
        if self._is_backtest:
            self._current_time += delta
            self._notify_time_changed()
        else:
            raise RuntimeError("Cannot advance time in live trading mode")
    
    def set_time(self, time: datetime) -> None:
        """Set time to specific value (backtest mode only)"""
        if self._is_backtest:
            self._current_time = time if time.tzinfo else self._timezone.localize(time)
            self._notify_time_changed()
        else:
            raise RuntimeError("Cannot set time in live trading mode")
    
    def on_time_changed(self, callback: Callable[[datetime], None]) -> None:
        """Register time change callback"""
        self._time_changed_callbacks.append(callback)
    
    def _notify_time_changed(self) -> None:
        """Notify all time change listeners"""
        for callback in self._time_changed_callbacks:
            try:
                callback(self._current_time)
            except Exception as e:
                logger.error(f"Error in time changed callback: {e}")
    
    def schedule(
        self,
        schedule_id: str,
        callback: Callable[[], None],
        run_at: datetime,
        interval: Optional[timedelta] = None,
        timezone_str: str = "UTC"
    ) -> Schedule:
        """Schedule a one-time or recurring task"""
        tz = timezone(timezone_str)
        if run_at.tzinfo is None:
            run_at = tz.localize(run_at)
        else:
            run_at = run_at.astimezone(tz)
        
        schedule = Schedule(
            id=schedule_id,
            callback=callback,
            next_run=run_at,
            interval=interval,
            timezone=timezone_str,
            enabled=True
        )
        self._schedules[schedule_id] = schedule
        logger.debug(f"Scheduled task {schedule_id} at {run_at}")
        return schedule
    
    def unschedule(self, schedule_id: str) -> None:
        """Remove a scheduled task"""
        if schedule_id in self._schedules:
            del self._schedules[schedule_id]
            logger.debug(f"Unscheduled task {schedule_id}")
    
    def is_market_open(
        self,
        market_hours: Optional[Dict[str, tuple]] = None,
        market_timezone: str = "US/Eastern"
    ) -> bool:
        """Check if market is open"""
        now = self.current_time
        market_tz = timezone(market_timezone)
        market_time = now.astimezone(market_tz)
        
        weekday = market_time.weekday()
        if weekday >= 5:  # Saturday and Sunday
            return False
        
        if market_hours:
            open_time, close_time = market_hours.get(
                weekday,
                (market_time.replace(hour=9, minute=30, second=0, microsecond=0),
                 market_time.replace(hour=16, minute=0, second=0, microsecond=0))
            )
            return open_time <= market_time <= close_time
        
        # Default market hours: 9:30 AM - 4:00 PM
        market_open = market_time.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = market_time.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= market_time <= market_close
    
    def get_next_market_open(
        self,
        market_timezone: str = "US/Eastern",
        market_open_time: tuple = (9, 30)
    ) -> datetime:
        """Get next market open time"""
        now = self.current_time
        market_tz = timezone(market_timezone)
        market_time = now.astimezone(market_tz)
        
        # Find next weekday
        days_ahead = 0
        while (market_time + timedelta(days=days_ahead)).weekday() >= 5:
            days_ahead += 1
        
        next_open = (market_time + timedelta(days=days_ahead)).replace(
            hour=market_open_time[0],
            minute=market_open_time[1],
            second=0,
            microsecond=0
        )
        
        # If current time is after market open, move to next day
        if days_ahead == 0 and market_time.time() > next_open.time():
            next_open += timedelta(days=1)
            while next_open.weekday() >= 5:
                next_open += timedelta(days=1)
        
        return next_open
    
    async def run(self) -> None:
        """Run the time keeper loop"""
        self._running = True
        logger.info("Time keeper started")
        
        while self._running:
            now = self.current_time
            
            # Execute scheduled tasks
            for schedule_id, schedule in list(self._schedules.items()):
                if schedule.enabled and schedule.next_run <= now:
                    try:
                        schedule.callback()
                    except Exception as e:
                        logger.error(f"Error in scheduled task {schedule_id}: {e}")
                    
                    if schedule.interval:
                        schedule.next_run += schedule.interval
                    else:
                        self.unschedule(schedule_id)
            
            await asyncio.sleep(1)
        
        logger.info("Time keeper stopped")
    
    def stop(self) -> None:
        """Stop the time keeper"""
        self._running = False
    
    @property
    def is_backtest(self) -> bool:
        """Check if running in backtest mode"""
        return self._is_backtest
    
    @property
    def timezone(self) -> timezone:
        """Get the timezone"""
        return self._timezone
    
    def to_timezone(self, dt: datetime, target_tz: str) -> datetime:
        """Convert datetime to target timezone"""
        target = timezone(target_tz)
        if dt.tzinfo is None:
            dt = self._timezone.localize(dt)
        return dt.astimezone(target)
