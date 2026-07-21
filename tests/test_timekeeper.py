"""Tests for time keeper"""

import pytest
from datetime import datetime, timedelta
from src.engine.timekeeper import TimeKeeper


class TestTimeKeeper:
    """Test time keeper functionality"""
    
    def test_backtest_mode(self):
        """Test backtest mode time setting"""
        tk = TimeKeeper(is_backtest=True)
        
        test_time = datetime(2023, 1, 1, 12, 0, 0)
        tk.set_time(test_time)
        
        assert tk.current_time.year == 2023
        assert tk.current_time.month == 1
        assert tk.current_time.day == 1
    
    def test_backtest_advance(self):
        """Test advancing time in backtest mode"""
        tk = TimeKeeper(is_backtest=True)
        
        start = datetime(2023, 1, 1)
        tk.set_time(start)
        tk.advance_time(timedelta(hours=1))
        
        assert tk.current_time.hour == 1
    
    def test_live_mode_cannot_set_time(self):
        """Test that time cannot be set in live mode"""
        tk = TimeKeeper(is_backtest=False)
        
        with pytest.raises(RuntimeError):
            tk.set_time(datetime(2023, 1, 1))
    
    def test_market_hours_check(self):
        """Test market hours checking"""
        tk = TimeKeeper(is_backtest=True, timezone_str="US/Eastern")
        
        # Monday at 10:00 AM
        monday_morning = datetime(2023, 6, 12, 10, 0, 0)
        tk.set_time(monday_morning)
        
        assert tk.is_market_open(market_timezone="US/Eastern")
        
        # Saturday
        saturday = datetime(2023, 6, 10, 12, 0, 0)
        tk.set_time(saturday)
        
        assert not tk.is_market_open(market_timezone="US/Eastern")
    
    def test_schedule_task(self):
        """Test scheduling a task"""
        tk = TimeKeeper(is_backtest=True)
        
        executed = []
        
        def task():
            executed.append(True)
        
        run_at = datetime(2023, 1, 1, 12, 0, 0)
        tk.schedule("test_task", task, run_at)
        
        # Set time after scheduled time
        tk.set_time(datetime(2023, 1, 1, 13, 0, 0))
        
        # In a real scenario, the task would be executed by run()
        # Here we just verify the schedule was created
        assert "test_task" in tk._schedules
    
    def test_unschedule_task(self):
        """Test unscheduling a task"""
        tk = TimeKeeper(is_backtest=True)
        
        def task():
            pass
        
        tk.schedule("test_task", task, datetime.now())
        tk.unschedule("test_task")
        
        assert "test_task" not in tk._schedules
    
    def test_timezone_conversion(self):
        """Test timezone conversion"""
        tk = TimeKeeper(timezone_str="UTC")
        
        utc_time = datetime(2023, 1, 1, 12, 0, 0)
        est_time = tk.to_timezone(utc_time, "US/Eastern")
        
        # EST is 5 hours behind UTC
        assert est_time.hour == 7

    def test_set_current_time_in_backtest(self):
        """Test set_current_time in backtest mode"""
        tk = TimeKeeper(is_backtest=True, timezone_str="UTC")
        test_time = datetime(2024, 6, 15, 9, 30, 0)
        tk.set_current_time(test_time)

        assert tk.current_time.year == 2024
        assert tk.current_time.month == 6
        assert tk.current_time.day == 15
        assert tk.current_time.hour == 9
        assert tk.current_time.minute == 30

    def test_set_current_time_in_live_mode_raises(self):
        """Test set_current_time raises in live mode"""
        tk = TimeKeeper(is_backtest=False)
        with pytest.raises(RuntimeError):
            tk.set_current_time(datetime(2024, 1, 1))

    def test_current_time_setter_in_backtest(self):
        """Test current_time property setter in backtest mode"""
        tk = TimeKeeper(is_backtest=True, timezone_str="UTC")
        tk.current_time = datetime(2025, 3, 20, 14, 0, 0)

        assert tk.current_time.year == 2025
        assert tk.current_time.month == 3
        assert tk.current_time.day == 20
        assert tk.current_time.hour == 14

    def test_current_time_setter_in_live_mode_raises(self):
        """Test current_time setter raises in live mode"""
        tk = TimeKeeper(is_backtest=False)
        with pytest.raises(RuntimeError):
            tk.current_time = datetime(2025, 1, 1)

    def test_advance_time_various_deltas(self):
        """Test advance_time with seconds, minutes, and days"""
        tk = TimeKeeper(is_backtest=True)
        tk.set_time(datetime(2023, 6, 1, 12, 0, 0))

        tk.advance_time(timedelta(seconds=30))
        assert tk.current_time.second == 30

        tk.advance_time(timedelta(minutes=45))
        assert tk.current_time.minute == 45

        tk.advance_time(timedelta(days=2))
        assert tk.current_time.day == 3

    def test_advance_time_in_live_mode_raises(self):
        """Test advance_time raises in live mode"""
        tk = TimeKeeper(is_backtest=False)
        with pytest.raises(RuntimeError):
            tk.advance_time(timedelta(hours=1))

    def test_on_time_changed_callback(self):
        """Test on_time_changed callback fires on set_time"""
        tk = TimeKeeper(is_backtest=True)
        received: list = []

        def on_change(new_time):
            received.append(new_time)

        tk.on_time_changed(on_change)
        test_time = datetime(2023, 7, 7, 8, 0, 0)
        tk.set_time(test_time)

        assert len(received) == 1
        assert received[0].year == 2023
        assert received[0].month == 7
        assert received[0].day == 7

    def test_schedule_recurring_interval(self):
        """Test scheduling a task with a recurring interval"""
        tk = TimeKeeper(is_backtest=True)
        run_at = datetime(2023, 1, 1, 10, 0, 0)
        s = tk.schedule("recurring", lambda: None, run_at, interval=timedelta(hours=2))

        assert s.interval == timedelta(hours=2)
        assert s.id == "recurring"
        assert s.enabled is True
        assert "recurring" in tk._schedules

    def test_unschedule_nonexistent_does_not_raise(self):
        """Test unscheduling a non-existent ID does not raise"""
        tk = TimeKeeper(is_backtest=True)
        tk.unschedule("nonexistent")
        # Should not raise; no assertion needed

    def test_scheduled_events_tracking(self):
        """Test _schedules dict reflects add/remove correctly"""
        tk = TimeKeeper(is_backtest=True)

        tk.schedule("a", lambda: None, datetime(2023, 1, 1, 10, 0, 0))
        tk.schedule("b", lambda: None, datetime(2023, 1, 1, 11, 0, 0))
        assert len(tk._schedules) == 2
        assert "a" in tk._schedules
        assert "b" in tk._schedules

        tk.unschedule("a")
        assert len(tk._schedules) == 1
        assert "a" not in tk._schedules
        assert "b" in tk._schedules

        tk.unschedule("b")
        assert len(tk._schedules) == 0

    def test_timezone_initialization_asia_shanghai(self):
        """Test TimeKeeper with Asia/Shanghai timezone"""
        tk = TimeKeeper(timezone_str="Asia/Shanghai")
        assert str(tk.timezone) == "Asia/Shanghai"

    def test_timezone_initialization_us_eastern(self):
        """Test TimeKeeper with US/Eastern timezone"""
        tk = TimeKeeper(timezone_str="US/Eastern")
        assert str(tk.timezone) == "US/Eastern"

    def test_to_timezone_with_naive_datetime(self):
        """Test to_timezone localizes naive datetime then converts"""
        tk = TimeKeeper(timezone_str="UTC")
        naive = datetime(2023, 5, 10, 20, 0, 0)
        shanghai = tk.to_timezone(naive, "Asia/Shanghai")

        # UTC 20:00 -> Shanghai next day 04:00
        assert shanghai.hour == 4
        assert shanghai.day == 11

    def test_set_time_with_timezone_aware_datetime(self):
        """Test set_time preserves tz-aware datetime"""
        from pytz import UTC
        tk = TimeKeeper(is_backtest=True, timezone_str="UTC")
        aware = datetime(2024, 8, 1, 15, 0, 0, tzinfo=UTC)
        tk.set_time(aware)

        assert tk.current_time.hour == 15
        assert tk.current_time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_run_stop_basic(self):
        """Test run() starts and stop() ends the loop"""
        tk = TimeKeeper(is_backtest=True)
        tk.set_time(datetime(2024, 1, 1, 12, 0, 0))

        import asyncio
        task = asyncio.create_task(tk.run())
        await asyncio.sleep(0.1)
        tk.stop()
        await asyncio.sleep(0.2)

        assert task.done()

    @pytest.mark.asyncio
    async def test_run_executes_due_scheduled_task(self):
        """Test run() fires a scheduled task whose time has passed"""
        tk = TimeKeeper(is_backtest=True)
        tk.set_time(datetime(2024, 1, 1, 10, 0, 0))

        executed = []

        def task():
            executed.append(True)

        # Schedule in the current backtest timeline
        tk.schedule("due_task", task, datetime(2023, 12, 31, 23, 59, 59))

        import asyncio
        task_coro = asyncio.create_task(tk.run())
        await asyncio.sleep(0.2)
        tk.stop()
        await task_coro

        assert len(executed) >= 1

    def test_schedule_in_the_past(self):
        """Test scheduling a task at a time already in the past"""
        tk = TimeKeeper(is_backtest=True)
        tk.set_time(datetime(2024, 6, 1, 12, 0, 0))

        past_time = datetime(2024, 1, 1, 0, 0, 0)
        s = tk.schedule("past_task", lambda: None, past_time)

        assert "past_task" in tk._schedules
        assert s.next_run.year == 2024
        assert s.next_run.month == 1

    def test_double_schedule_same_id_overwrites(self):
        """Test scheduling with the same ID overwrites the previous schedule"""
        tk = TimeKeeper(is_backtest=True)

        first_calls = []
        second_calls = []

        tk.schedule("dup", lambda: first_calls.append(1), datetime(2023, 1, 1, 10, 0, 0))
        tk.schedule("dup", lambda: second_calls.append(1), datetime(2023, 6, 1, 10, 0, 0))

        assert len(tk._schedules) == 1
        # The second schedule replaces the first
        s = tk._schedules["dup"]
        assert s.next_run.month == 6
