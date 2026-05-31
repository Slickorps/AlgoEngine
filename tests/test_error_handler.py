"""Tests for unified error handling, retry, and circuit breaker module."""

import asyncio

import pytest

from src.utils.error_handler import (
    # Enums
    ErrorCategory,
    CircuitBreakerState,
    # Configuration
    RetryConfig,
    CircuitBreakerConfig,
    ErrorHandlerConfig,
    # Results
    ErrorResult,
    CircuitBreaker,
    CircuitBreakerOpenError,
    # Retry
    RetryExhaustedError,
    RetryableError,
    categorize_exception,
    retry_async,
    retry_decorator,
    # Facade
    ErrorHandler,
)


# ---------------------------------------------------------------------------
# ErrorCategory & categorize_exception
# ---------------------------------------------------------------------------


class TestErrorCategory:
    """Tests for ErrorCategory enum and categorize_exception()."""

    def test_transient_exception(self) -> None:
        assert categorize_exception(TimeoutError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(asyncio.TimeoutError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(ConnectionError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(ConnectionRefusedError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(ConnectionResetError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(BrokenPipeError()) == ErrorCategory.TRANSIENT
        assert categorize_exception(OSError()) == ErrorCategory.TRANSIENT

    def test_permanent_exception(self) -> None:
        assert categorize_exception(ValueError()) == ErrorCategory.PERMANENT
        assert categorize_exception(TypeError()) == ErrorCategory.PERMANENT
        assert categorize_exception(KeyError()) == ErrorCategory.PERMANENT
        assert categorize_exception(AttributeError()) == ErrorCategory.PERMANENT
        assert categorize_exception(NotImplementedError()) == ErrorCategory.PERMANENT

    def test_http_status_transient(self) -> None:
        class FakeHTTPError(Exception):
            def __init__(self, status_code: int) -> None:
                self.status_code = status_code

        assert categorize_exception(FakeHTTPError(429)) == ErrorCategory.TRANSIENT
        assert categorize_exception(FakeHTTPError(503)) == ErrorCategory.TRANSIENT

    def test_http_status_permanent(self) -> None:
        class FakeHTTPError(Exception):
            def __init__(self, status_code: int) -> None:
                self.status = status_code

        assert categorize_exception(FakeHTTPError(400)) == ErrorCategory.PERMANENT
        assert categorize_exception(FakeHTTPError(404)) == ErrorCategory.PERMANENT

    def test_unknown_defaults_to_transient(self) -> None:
        class WeirdError(Exception):
            pass

        # Fail-safe: unknown errors are treated as transient
        assert categorize_exception(WeirdError()) == ErrorCategory.TRANSIENT

    def test_extra_transient(self) -> None:
        class CustomNetErr(Exception):
            pass

        assert (
            categorize_exception(CustomNetErr(), extra_transient=(CustomNetErr,))
            == ErrorCategory.TRANSIENT
        )


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_defaults(self) -> None:
        cfg = RetryConfig()
        assert cfg.max_attempts == 3
        assert cfg.base_delay == 1.0
        assert cfg.backoff_factor == 2.0
        assert cfg.max_delay == 60.0
        assert cfg.jitter is True

    def test_delay_for_attempt_no_jitter(self) -> None:
        cfg = RetryConfig(jitter=False, base_delay=1.0, backoff_factor=2.0)
        assert cfg.delay_for_attempt(1) == 1.0
        assert cfg.delay_for_attempt(2) == 2.0
        assert cfg.delay_for_attempt(3) == 4.0
        assert cfg.delay_for_attempt(4) == 8.0

    def test_delay_capped_at_max(self) -> None:
        cfg = RetryConfig(
            jitter=False, base_delay=10.0, max_delay=15.0, backoff_factor=2.0
        )
        assert cfg.delay_for_attempt(1) == 10.0
        assert cfg.delay_for_attempt(2) == 15.0  # 20 capped to 15
        assert cfg.delay_for_attempt(5) == 15.0

    def test_delay_with_jitter_in_range(self) -> None:
        cfg = RetryConfig(jitter=True, base_delay=2.0, backoff_factor=2.0, max_delay=100)
        for _ in range(20):
            d = cfg.delay_for_attempt(2)  # raw = 4.0
            # jitter: 50% – 150% → 2.0 – 6.0
            assert 2.0 <= d <= 6.0


# ---------------------------------------------------------------------------
# ErrorResult
# ---------------------------------------------------------------------------


class TestErrorResult:
    """Tests for ErrorResult dataclass."""

    def test_should_retry_transient(self) -> None:
        result = ErrorResult(
            category=ErrorCategory.TRANSIENT,
            exception=TimeoutError(),
            retries_exhausted=False,
            circuit_open=False,
        )
        assert result.should_retry is True

    def test_should_not_retry_permanent(self) -> None:
        result = ErrorResult(
            category=ErrorCategory.PERMANENT,
            exception=ValueError(),
        )
        assert result.should_retry is False

    def test_should_not_retry_exhausted(self) -> None:
        result = ErrorResult(
            category=ErrorCategory.TRANSIENT,
            exception=TimeoutError(),
            retries_exhausted=True,
        )
        assert result.should_retry is False

    def test_should_not_retry_circuit_open(self) -> None:
        result = ErrorResult(
            category=ErrorCategory.TRANSIENT,
            exception=TimeoutError(),
            circuit_open=True,
        )
        assert result.should_retry is False


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryAsync:
    """Tests for retry_async() and retry_decorator()."""

    @pytest.mark.asyncio
    async def test_success_first_try(self) -> None:
        call_count = 0

        async def work() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_async(work, config=RetryConfig(max_attempts=3))
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_and_succeed(self) -> None:
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("boom")
            return "finally"

        result = await retry_async(
            flaky,
            config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False),
        )
        assert result == "finally"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self) -> None:
        async def always_fail() -> None:
            raise ConnectionError("always")

        with pytest.raises(RetryExhaustedError) as exc_info:
            await retry_async(
                always_fail,
                config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False),
            )
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_exception, ConnectionError)

    @pytest.mark.asyncio
    async def test_permanent_error_not_retried(self) -> None:
        call_count = 0

        async def bad_arg() -> None:
            nonlocal call_count
            call_count += 1
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await retry_async(
                bad_arg,
                config=RetryConfig(max_attempts=3, base_delay=0.0),
            )
        assert call_count == 1  # no retries for permanent errors

    @pytest.mark.asyncio
    async def test_retryable_error_overrides_permanent(self) -> None:
        call_count = 0

        async def force_retry() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RetryableError("marker")
            return "ok"

        result = await retry_async(
            force_retry,
            config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False),
        )
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_extra_transient_exception_retried(self) -> None:
        call_count = 0

        class MyTransient(Exception):
            pass

        async def work() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise MyTransient("custom")
            return "ok"

        # Without extra_transient, this is unknown → transient by default anyway
        # but we explicitly test the path
        result = await retry_async(
            work,
            config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False),
            extra_transient=(MyTransient,),
        )
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_success(self) -> None:
        call_count = 0

        @retry_decorator(
            config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False)
        )
        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("fail")
            return "done"

        result = await flaky()
        assert result == "done"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_preserves_metadata(self) -> None:
        @retry_decorator(config=RetryConfig(max_attempts=2, base_delay=0.0))
        async def my_func(x: int) -> int:
            """Docstring preserved."""
            return x * 2

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Docstring preserved."
        assert await my_func(5) == 10


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for the CircuitBreaker state machine."""

    def test_initial_state(self) -> None:
        cb = CircuitBreaker()
        assert cb.stats.state == CircuitBreakerState.CLOSED
        assert cb.stats.failure_count == 0
        assert cb.stats.total_trips == 0
        assert cb.is_active is False

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        cb = CircuitBreaker()

        async def work() -> str:
            return "good"

        result = await cb.execute(work)
        assert result == "good"
        assert cb.stats.state == CircuitBreakerState.CLOSED
        assert cb.stats.failure_count == 0

    @pytest.mark.asyncio
    async def test_execute_records_failure(self) -> None:
        cb = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=10)  # high enough not to trip
        )

        async def fail() -> None:
            raise ConnectionError("err")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.execute(fail)

        assert cb.stats.failure_count == 3
        assert cb.stats.state == CircuitBreakerState.CLOSED  # threshold not hit

    @pytest.mark.asyncio
    async def test_trips_to_open(self) -> None:
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))

        async def fail() -> None:
            raise ConnectionError("err")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await cb.execute(fail)

        assert cb.stats.state == CircuitBreakerState.OPEN
        assert cb.stats.total_trips == 1
        assert cb.is_active is True

    @pytest.mark.asyncio
    async def test_open_rejects_calls(self) -> None:
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        async def fail() -> None:
            raise ConnectionError("err")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.execute(fail)

        assert cb.is_active is True

        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            await cb.execute(asyncio.sleep, 0)
        assert "OPEN" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_half_open_probe_succeeds(self) -> None:
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=2,
                cooldown_seconds=0.0,  # immediate cooldown
                reset_success_threshold=2,
            )
        )

        # Trip breaker
        async def fail() -> None:
            raise ConnectionError("e")
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.execute(fail)
        assert cb.stats.state == CircuitBreakerState.OPEN

        # Cooldown elapsed — is_active triggers half_open transition
        # Let's access is_active which auto-transitions
        assert cb.is_active is False  # cooldown 0 → transitions to HALF_OPEN immediately

        # Now HALF_OPEN probe calls
        async def ok() -> str:
            return "probe"
        assert await cb.execute(ok) == "probe"
        assert cb.stats.state == CircuitBreakerState.HALF_OPEN
        assert cb.stats.success_count == 1

        # Second probe closes the breaker
        assert await cb.execute(ok) == "probe"
        assert cb.stats.state == CircuitBreakerState.CLOSED
        assert cb.stats.success_count == 0  # reset on close

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self) -> None:
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=2,
                cooldown_seconds=0.0,
                reset_success_threshold=2,
            )
        )

        async def fail() -> None:
            raise ConnectionError("e")
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.execute(fail)
        assert cb.stats.state == CircuitBreakerState.OPEN

        # Transition to HALF_OPEN
        assert cb.is_active is False  # cooldown 0

        # Probe fails → back to OPEN
        with pytest.raises(ConnectionError):
            await cb.execute(fail)
        assert cb.stats.state == CircuitBreakerState.OPEN

    def test_record_success_in_closed(self) -> None:
        cb = CircuitBreaker()
        cb.record_success()
        assert cb.stats.failure_count == 0
        assert cb.stats.last_success_time is not None

    def test_record_failure_in_closed(self) -> None:
        cb = CircuitBreaker()
        cb.record_failure()
        assert cb.stats.failure_count == 1
        assert cb.stats.last_failure_time is not None

    def test_reset(self) -> None:
        cb = CircuitBreaker()

        # Manually set to OPEN
        cb._transition_to_open()
        assert cb.stats.state == CircuitBreakerState.OPEN
        assert cb.stats.total_trips == 1
        assert cb.is_active is True

        cb.reset()
        assert cb.stats.state == CircuitBreakerState.CLOSED
        assert cb.stats.failure_count == 0
        assert cb.stats.total_trips == 0
        assert cb.is_active is False

    def test_callbacks_on_open(self) -> None:
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))
        called: list = []

        def handler(name: str) -> None:
            called.append(name)

        cb.on_open.append(handler)
        cb.record_failure()  # threshold 1 → OPEN
        assert len(called) == 1
        assert called[0] == cb.name

    def test_callbacks_on_close(self) -> None:
        cb = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                cooldown_seconds=0.0,
                reset_success_threshold=1,
            )
        )
        called: list = []

        def handler(name: str) -> None:
            called.append(name)

        cb.on_close.append(handler)

        # Trip to OPEN
        cb.record_failure()
        assert cb.stats.state == CircuitBreakerState.OPEN

        # Cooldown → HALF_OPEN
        assert cb.is_active is False

        async def ok() -> str:
            return "ok"
        import asyncio as _asyncio
        _asyncio.get_event_loop().run_until_complete(cb.execute(ok))

        assert cb.stats.state == CircuitBreakerState.CLOSED
        assert len(called) == 1

    def test_callbacks_on_half_open(self) -> None:
        cb = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, cooldown_seconds=0.0)
        )
        called: list = []

        def handler(name: str) -> None:
            called.append(name)

        cb.on_half_open.append(handler)

        # Trip to OPEN
        cb.record_failure()
        assert cb.stats.state == CircuitBreakerState.OPEN

        # Cooldown → HALF_OPEN (via is_active)
        assert cb.is_active is False
        assert cb.stats.state == CircuitBreakerState.HALF_OPEN
        assert len(called) == 1

    def test_callback_error_does_not_crash(self) -> None:
        cb = CircuitBreaker()
        cb.on_open.append(lambda _: 1 / 0)  # deliberate error
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()  # threshold 5 — trips to OPEN
        assert cb.stats.state == CircuitBreakerState.OPEN  # breaker still tripped


# ---------------------------------------------------------------------------
# ErrorHandler facade
# ---------------------------------------------------------------------------


class TestErrorHandler:
    """Tests for the ErrorHandler façade class."""

    def test_initialization(self) -> None:
        handler = ErrorHandler()
        assert handler.config.retry.max_attempts == 3
        assert handler.config.circuit_breaker.failure_threshold == 5

    def test_categorize_delegates(self) -> None:
        handler = ErrorHandler()
        assert handler.categorize(TimeoutError()) == ErrorCategory.TRANSIENT
        assert handler.categorize(ValueError()) == ErrorCategory.PERMANENT

    def test_handle_transient(self) -> None:
        handler = ErrorHandler()
        result = handler.handle(ConnectionError("net"), context="test")
        assert result.category == ErrorCategory.TRANSIENT
        assert result.circuit_open is False
        assert result.traceback_str != ""

    def test_handle_permanent(self) -> None:
        handler = ErrorHandler()
        result = handler.handle(ValueError("bad"), context="test")
        assert result.category == ErrorCategory.PERMANENT
        assert result.should_retry is False

    def test_handle_circuit_breaker_trigger(self) -> None:
        config = ErrorHandlerConfig(
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3)
        )
        handler = ErrorHandler(config)

        for _ in range(3):
            result = handler.handle(ConnectionError("fail"), context="api.call")
        assert result.category == ErrorCategory.CIRCUIT_BREAKER
        assert result.circuit_open is True

    def test_get_circuit_breaker_creates_once(self) -> None:
        handler = ErrorHandler()
        cb1 = handler.get_circuit_breaker("api")
        cb2 = handler.get_circuit_breaker("api")
        assert cb1 is cb2

    def test_get_circuit_breaker_different_names(self) -> None:
        handler = ErrorHandler()
        cb_a = handler.get_circuit_breaker("a")
        cb_b = handler.get_circuit_breaker("b")
        assert cb_a is not cb_b

    def test_error_counts(self) -> None:
        handler = ErrorHandler()
        handler.handle(TimeoutError(), context="broker")
        handler.handle(ConnectionError(), context="broker")
        handler.handle(ValueError(), context="data")
        assert handler.error_counts == {"broker": 2, "data": 1}

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        handler = ErrorHandler()

        async def work() -> str:
            return "ok"

        result = await handler.execute(work, context="test")
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_execute_with_retry(self) -> None:
        config = ErrorHandlerConfig(
            retry=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False)
        )
        handler = ErrorHandler(config)
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("e")
            return "done"

        result = await handler.execute(flaky, context="flaky")
        assert result == "done"
        assert call_count == 2

    def test_circuit_breaker_stats(self) -> None:
        handler = ErrorHandler()
        cb = handler.get_circuit_breaker("svc")
        assert cb.stats.state == CircuitBreakerState.CLOSED
        stats = handler.circuit_breaker_stats()
        assert "svc" in stats
        assert stats["svc"].state == CircuitBreakerState.CLOSED