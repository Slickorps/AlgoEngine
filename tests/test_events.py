"""Tests for event system"""

import pytest
import asyncio
from datetime import datetime
from src.engine.events import Event, EventType, EventBus, get_event_bus


class TestEvent:
    """Test event data structure"""
    
    def test_event_creation(self):
        """Test creating an event"""
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now(),
            data={"price": 100.0},
            symbol="AAPL"
        )
        assert event.event_type == EventType.TICK
        assert event.symbol == "AAPL"
        assert event.data["price"] == 100.0


class TestEventBus:
    """Test event bus functionality"""
    
    def test_subscribe_and_emit(self):
        """Test subscribing and emitting events"""
        bus = EventBus()
        received = []
        
        def handler(event):
            received.append(event)
        
        bus.subscribe(EventType.TICK, handler)
        
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now()
        )
        bus.emit(event)
        
        assert len(received) == 1
        assert received[0].event_type == EventType.TICK
    
    def test_unsubscribe(self):
        """Test unsubscribing from events"""
        bus = EventBus()
        received = []
        
        def handler(event):
            received.append(event)
        
        bus.subscribe(EventType.TICK, handler)
        bus.unsubscribe(EventType.TICK, handler)
        
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now()
        )
        bus.emit(event)
        
        assert len(received) == 0
    
    def test_global_handler(self):
        """Test global event handler"""
        bus = EventBus()
        received = []
        
        def handler(event):
            received.append(event)
        
        bus.subscribe_all(handler)
        
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now()
        )
        bus.emit(event)
        
        assert len(received) == 1
    
    def test_handler_count(self):
        """Test getting handler count"""
        bus = EventBus()
        
        def handler1(event):
            pass
        
        def handler2(event):
            pass
        
        bus.subscribe(EventType.TICK, handler1)
        bus.subscribe(EventType.TICK, handler2)
        
        assert bus.get_handler_count(EventType.TICK) == 2
        assert bus.get_handler_count() == 2
    
    def test_clear_handlers(self):
        """Test clearing handlers"""
        bus = EventBus()
        
        def handler(event):
            pass
        
        bus.subscribe(EventType.TICK, handler)
        bus.clear_handlers(EventType.TICK)
        
        assert bus.get_handler_count(EventType.TICK) == 0
    
    @pytest.mark.asyncio
    async def test_async_handler(self):
        """Test async event handler"""
        bus = EventBus()
        received = []
        
        async def handler(event):
            received.append(event)
        
        bus.subscribe(EventType.TICK, handler, async_handler=True)
        
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now()
        )
        bus.emit(event)
        
        # Give async handler time to execute
        await asyncio.sleep(0.1)
        
        assert len(received) == 1


class TestEventBusMultipleSubscribers:
    """Test EventBus with multiple subscribers"""

    def test_multiple_sync_subscribers(self):
        """Test multiple synchronous subscribers for same event type"""
        bus = EventBus()
        received_a = []
        received_b = []
        received_c = []

        def handler_a(event):
            received_a.append(event)

        def handler_b(event):
            received_b.append(event)

        def handler_c(event):
            received_c.append(event)

        bus.subscribe(EventType.TICK, handler_a)
        bus.subscribe(EventType.TICK, handler_b)
        bus.subscribe(EventType.TICK, handler_c)

        event = Event(event_type=EventType.TICK, timestamp=datetime.now())
        bus.emit(event)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert len(received_c) == 1
        assert bus.get_handler_count(EventType.TICK) == 3

    def test_subscribe_unsubscribe_cycle(self):
        """Test subscribe then unsubscribe with remaining subscribers intact"""
        bus = EventBus()
        received = []

        def h1(event):
            received.append("h1")

        def h2(event):
            received.append("h2")

        bus.subscribe(EventType.BAR, h1)
        bus.subscribe(EventType.BAR, h2)
        assert bus.get_handler_count(EventType.BAR) == 2

        bus.unsubscribe(EventType.BAR, h1)
        assert bus.get_handler_count(EventType.BAR) == 1

        event = Event(event_type=EventType.BAR, timestamp=datetime.now())
        bus.emit(event)
        assert received == ["h2"]

    def test_unsubscribe_nonexistent_handler(self):
        """Test unsubscribing a handler that was never subscribed"""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.unsubscribe(EventType.TICK, handler)
        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_mixed_sync_async_subscribers(self):
        """Test sync and async subscribers together"""
        bus = EventBus()
        sync_received = []
        async_received = []

        def sync_handler(event):
            sync_received.append(event)

        async def async_handler(event):
            async_received.append(event)

        bus.subscribe(EventType.TICK, sync_handler, async_handler=False)
        bus.subscribe(EventType.TICK, async_handler, async_handler=True)

        event = Event(event_type=EventType.TICK, timestamp=datetime.now())
        bus.emit(event)

        assert len(sync_received) == 1
        await asyncio.sleep(0.1)
        assert len(async_received) == 1

    def test_global_and_type_specific_subscribers(self):
        """Test both global and type-specific subscribers receive events"""
        bus = EventBus()
        type_received = []
        global_received = []

        def type_handler(event):
            type_received.append(event)

        def global_handler(event):
            global_received.append(event)

        bus.subscribe(EventType.TICK, type_handler)
        bus.subscribe_all(global_handler)

        event = Event(event_type=EventType.TICK, timestamp=datetime.now())
        bus.emit(event)

        assert len(type_received) == 1
        assert len(global_received) == 1

    def test_global_handler_unsubscribe(self):
        """Test unsubscribing a global handler"""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe_all(handler)
        bus.unsubscribe_all(handler)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        assert len(received) == 0


class TestEventBusEmitDifferentTypes:
    """Test EventBus emitting different EventTypes"""

    def test_emit_multiple_event_types(self):
        """Test emitting events of different types"""
        bus = EventBus()
        ticks = []
        bars = []
        orders = []

        def tick_handler(event):
            ticks.append(event)

        def bar_handler(event):
            bars.append(event)

        def order_handler(event):
            orders.append(event)

        bus.subscribe(EventType.TICK, tick_handler)
        bus.subscribe(EventType.BAR, bar_handler)
        bus.subscribe(EventType.ORDER_SUBMITTED, order_handler)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        bus.emit(Event(event_type=EventType.BAR, timestamp=datetime.now()))
        bus.emit(Event(event_type=EventType.ORDER_SUBMITTED, timestamp=datetime.now()))

        assert len(ticks) == 1
        assert len(bars) == 1
        assert len(orders) == 1
        assert ticks[0].event_type == EventType.TICK
        assert bars[0].event_type == EventType.BAR
        assert orders[0].event_type == EventType.ORDER_SUBMITTED

    def test_emit_system_events(self):
        """Test emitting system lifecycle events"""
        bus = EventBus()
        events = []

        def handler(event):
            events.append(event.event_type)

        bus.subscribe_all(handler)

        bus.emit(Event(event_type=EventType.START, timestamp=datetime.now()))
        bus.emit(Event(event_type=EventType.STOP, timestamp=datetime.now()))
        bus.emit(Event(event_type=EventType.PAUSE, timestamp=datetime.now()))
        bus.emit(Event(event_type=EventType.RESUME, timestamp=datetime.now()))

        assert events == [EventType.START, EventType.STOP, EventType.PAUSE, EventType.RESUME]

    def test_emit_unregistered_event_type(self):
        """Test emitting event type with no subscribers"""
        bus = EventBus()
        bus.emit(Event(event_type=EventType.WEBSOCKET_MESSAGE, timestamp=datetime.now()))
        assert bus.get_handler_count(EventType.WEBSOCKET_MESSAGE) == 0

    def test_emit_event_only_dispatches_to_correct_type(self):
        """Test event is only dispatched to matching event type handlers"""
        bus = EventBus()
        tick_received = []
        bar_received = []

        def tick_handler(event):
            tick_received.append(event)

        def bar_handler(event):
            bar_received.append(event)

        bus.subscribe(EventType.TICK, tick_handler)
        bus.subscribe(EventType.BAR, bar_handler)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))

        assert len(tick_received) == 1
        assert len(bar_received) == 0


class TestEventBusAsyncStartStop:
    """Test EventBus async start/stop cycle"""

    @pytest.mark.asyncio
    async def test_start_stop_cycle(self):
        """Test starting and stopping the event bus"""
        bus = EventBus()
        assert bus._running is False

        start_task = asyncio.create_task(bus.start())
        await asyncio.sleep(0.05)
        assert bus._running is True

        bus.stop()
        await asyncio.sleep(0.05)
        assert bus._running is False

        await start_task

    @pytest.mark.asyncio
    async def test_emit_async_during_run(self):
        """Test async emit while bus is running"""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TICK, handler)

        start_task = asyncio.create_task(bus.start())
        await asyncio.sleep(0.05)

        await bus.emit_async(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        await asyncio.sleep(0.15)

        assert len(received) == 1

        bus.stop()
        await start_task

    @pytest.mark.asyncio
    async def test_queue_processed_after_start(self):
        """Test events queued before start are processed after start"""
        bus = EventBus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.TICK, handler)

        await bus.emit_async(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        await bus.emit_async(Event(event_type=EventType.TICK, timestamp=datetime.now()))

        start_task = asyncio.create_task(bus.start())
        await asyncio.sleep(0.15)

        assert len(received) >= 1

        bus.stop()
        await start_task


class TestEventCreation:
    """Test event creation with different data payloads"""

    def test_event_with_dict_data(self):
        """Test event with dictionary data"""
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now(),
            data={"price": 150.0, "volume": 1000},
            symbol="MSFT"
        )
        assert event.data["price"] == 150.0
        assert event.data["volume"] == 1000
        assert event.symbol == "MSFT"
        assert event.event_type == EventType.TICK

    def test_event_with_string_data(self):
        """Test event with string data"""
        event = Event(
            event_type=EventType.ALERT,
            timestamp=datetime.now(),
            data="Risk limit breached"
        )
        assert event.data == "Risk limit breached"
        assert event.event_type == EventType.ALERT

    def test_event_with_numeric_data(self):
        """Test event with numeric data"""
        event = Event(
            event_type=EventType.SIGNAL,
            timestamp=datetime.now(),
            data=1.5,
            priority=10
        )
        assert event.data == 1.5
        assert event.priority == 10

    def test_event_with_metadata(self):
        """Test event with metadata"""
        event = Event(
            event_type=EventType.CUSTOM,
            timestamp=datetime.now(),
            metadata={"reason": "rebalance", "strategy": "momentum"}
        )
        assert event.metadata["reason"] == "rebalance"
        assert event.metadata["strategy"] == "momentum"

    def test_event_default_values(self):
        """Test event default field values"""
        event = Event(
            event_type=EventType.TICK,
            timestamp=datetime.now()
        )
        assert event.data is None
        assert event.symbol is None
        assert event.source == ""
        assert event.priority == 0
        assert event.metadata == {}

    def test_event_timestamp_string_conversion(self):
        """Test event with string timestamp auto-conversion"""
        ts = "2024-01-15T10:30:00"
        event = Event(event_type=EventType.BAR, timestamp=ts)
        assert isinstance(event.timestamp, datetime)
        assert event.timestamp.year == 2024
        assert event.timestamp.month == 1
        assert event.timestamp.day == 15

    def test_event_with_all_fields(self):
        """Test event with all fields populated"""
        event = Event(
            event_type=EventType.ORDER_FILLED,
            timestamp=datetime.now(),
            data={"fill_price": 152.0},
            symbol="AAPL",
            source="NYSE",
            priority=5,
            metadata={"order_id": "ord-123"}
        )
        assert event.event_type == EventType.ORDER_FILLED
        assert event.symbol == "AAPL"
        assert event.source == "NYSE"
        assert event.priority == 5
        assert event.metadata["order_id"] == "ord-123"


class TestEventBusErrorHandling:
    """Test EventBus error handling and filtering"""

    def test_handler_exception_does_not_block_others(self):
        """Test that a failing handler does not prevent other handlers from running"""
        bus = EventBus()
        received = []

        def failing_handler(event):
            raise ValueError("Handler failure")

        def working_handler(event):
            received.append(event)

        bus.subscribe(EventType.TICK, failing_handler)
        bus.subscribe(EventType.TICK, working_handler)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))

        assert len(received) == 1

    def test_global_handler_exception_isolation(self):
        """Test exception in global handler does not block type-specific handlers"""
        bus = EventBus()
        received = []

        def failing_global(event):
            raise RuntimeError("Global handler error")

        def type_handler(event):
            received.append(event)

        bus.subscribe_all(failing_global)
        bus.subscribe(EventType.TICK, type_handler)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))

        assert len(received) == 1

    def test_clear_handlers_all(self):
        """Test clearing all handlers"""
        bus = EventBus()

        def h1(event):
            pass

        def h2(event):
            pass

        bus.subscribe(EventType.TICK, h1)
        bus.subscribe(EventType.BAR, h2)
        bus.subscribe_all(h1)

        assert bus.get_handler_count() == 3

        bus.clear_handlers()
        assert bus.get_handler_count() == 0

    def test_clear_handlers_type_with_async(self):
        """Test clearing handlers for a type that has async handlers"""
        bus = EventBus()

        async def async_handler(event):
            pass

        def sync_handler(event):
            pass

        bus.subscribe(EventType.TICK, sync_handler)
        bus.subscribe(EventType.TICK, async_handler, async_handler=True)

        assert bus.get_handler_count(EventType.TICK) == 2
        bus.clear_handlers(EventType.TICK)
        assert bus.get_handler_count(EventType.TICK) == 0

    def test_handler_count_includes_global(self):
        """Test get_handler_count() includes global handlers"""
        bus = EventBus()

        def h1(event):
            pass

        def h2(event):
            pass

        bus.subscribe(EventType.TICK, h1)
        bus.subscribe_all(h2)

        assert bus.get_handler_count() == 2

    @pytest.mark.asyncio
    async def test_async_handler_exception_caught(self):
        """Test that async handler exceptions are caught"""
        bus = EventBus()
        received = []

        async def failing_async(event):
            raise ValueError("Async error")

        async def working_async(event):
            received.append(event)

        bus.subscribe(EventType.TICK, failing_async, async_handler=True)
        bus.subscribe(EventType.TICK, working_async, async_handler=True)

        bus.emit(Event(event_type=EventType.TICK, timestamp=datetime.now()))
        await asyncio.sleep(0.1)

        assert len(received) == 1


class TestGetEventBus:
    """Test get_event_bus singleton"""

    def test_get_event_bus_returns_same_instance(self):
        """Test that get_event_bus returns the same instance"""
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2

    def test_get_event_bus_is_event_bus_instance(self):
        """Test that get_event_bus returns an EventBus instance"""
        bus = get_event_bus()
        assert isinstance(bus, EventBus)

    def test_get_event_bus_module_level_variable(self):
        """Test the module-level event_bus is the same as get_event_bus"""
        from src.engine import events
        assert events.event_bus is events.get_event_bus()

    def test_singleton_survives_operations(self):
        """Test singleton identity after subscribe/emit operations"""
        bus = get_event_bus()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.CUSTOM, handler)
        bus.emit(Event(event_type=EventType.CUSTOM, timestamp=datetime.now()))

        assert bus is get_event_bus()
        assert len(received) == 1

        bus.unsubscribe(EventType.CUSTOM, handler)
