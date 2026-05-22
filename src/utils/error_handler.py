"""
Unified error handling, retry with exponential backoff, and circuit breaker
for AlgoEngine.

Provides reusable primitives that can be shared across brokers, data feeds,
and the live engine, replacing the scattered inline implementations.
"""

from __future__ import annotations

import asyncio
import functools
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import (
    Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Type, TypeVar, Union,
)

from .logger import get_logger

logger = get_logger("utils.error_handler")

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class ErrorCategory(Enum):
    """Broad categories that drive retry / circuit-breaker decisions."""

    TRANSIENT = auto()          # temporary – worth retrying (timeout, rate-limit …)
    PERMANENT = auto()          # will never succeed (auth failure, bad request …)
    CIRCUIT_BREAKER = auto()    # severe – should trip the circuit breaker


# Well-known exception types – kept minimal to stay framework-agnostic.
_TRANSIENT_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    asyncio.TimeoutError,
    TimeoutError,
    ConnectionError,
    ConnectionRefusedError,
    ConnectionResetError,
    BrokenPipeError,
    OSError,  # many networking errors inherit from OSError
)

_PERMANENT_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
)

_HTTP_TRANSIENT_STATUSES: Set[int] = {408, 429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RetryConfig:
    """Fine-grained control over retry behaviour."""

    max_attempts: int = 3
    base_delay: float = 1.0          # seconds
    backoff_factor: float = 2.0      # multiplicative
    max_delay: float = 60.0          # cap
    jitter: bool = True              # add random noise to avoid thundering-herd
    retryable_exceptions: Tuple[Type[BaseException], ...] = _TRANSIENT_EXCEPTIONS

    def delay_for_attempt(self, attempt: int) -> float:
        """Compute the sleep duration for *attempt* (1‑based)."""
        raw = min(self.base_delay * (self.backoff_factor ** (attempt - 1)), self.max_delay)
        if self.jitter:
            raw *= 0.5 + random.random()  # 50 % – 150 % jitter
        return raw


@dataclass
class CircuitBreakerConfig:
    """Tuning knobs for the circuit breaker state machine."""

    failure_threshold: int = 5               # consecutive failures before OPEN
    cooldown_seconds: float = 60.0           # how long to stay OPEN
    half_open_max_calls: int = 1             # probe requests allowed in HALF_OPEN
    reset_success_threshold: int = 2         # consecutive successes in HALF_OPEN to CLOSE


# ---------------------------------------------------------------------------
# Result wrappers
# ---------------------------------------------------------------------------


@dataclass
class ErrorResult:
    """Normalised outcome produced by ErrorHandler.handle()."""

    category: ErrorCategory
    exception: BaseException
    retries_exhausted: bool = False
    circuit_open: bool = False
    traceback_str: str = ""

    @property
    def should_retry(self) -> bool:
        return (
            self.category == ErrorCategory.TRANSIENT
            and not self.retries_exhausted
            and not self.circuit_open
        )


T = TypeVar("T")

# ---------------------------------------------------------------------------
# Circuit breaker state machine
# ---------------------------------------------------------------------------


class CircuitBreakerState(Enum):
    CLOSED = "closed"        # normal operation
    OPEN = "open"            # failing fast
    HALF_OPEN = "half_open"  # probing


@dataclass
class CircuitBreakerStats:
    state: CircuitBreakerState = CircuitBreakerState.CLOSED
    failure_count: int = 0
    success_count: int = 0        # consecutive successes in HALF_OPEN
    half_open_inflight: int = 0   # active probe calls
    last_failure_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    total_trips: int = 0


class CircuitBreaker:
    """
    Generic circuit breaker that protects an external dependency.

    State machine:
        CLOSED  ──(threshold reached)──▶ OPEN
        OPEN    ──(cooldown elapsed)──▶ HALF_OPEN
        HALF_OPEN ──(probes succeed)──▶ CLOSED
        HALF_OPEN ──(any failure)─────▶ OPEN   (fast-fail again)

    Usage::

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=5))
        try:
            result = await cb.execute(some_async_call, arg1, arg2)
        except CircuitBreakerOpenError:
            ...  # fast-fail path
    """

    def __init__(
        self,
        config: Optional[CircuitBreakerConfig] = None,
        name: str = "default",
    ) -> None:
        self.config = config or CircuitBreakerConfig()
        self.name = name
        self.stats = CircuitBreakerStats()

        # Callbacks
        self.on_open: List[Callable[[str], None]] = []
        self.on_close: List[Callable[[str], None]] = []
        self.on_half_open: List[Callable[[str], None]] = []

    # -- public helpers ------------------------------------------------------

    @property
    def is_active(self) -> bool:
        """Return True if the circuit is currently OPEN (fast-failing)."""
        if self.stats.state != CircuitBreakerState.OPEN:
            return False
        if self._cooldown_elapsed():
            self._transition_to_half_open()
            # Now allow the pending call through as a probe
            return False
        return True

    def record_success(self) -> None:
        """Notify the breaker that a protected call succeeded."""
        self.stats.last_success_time = datetime.now()
        self.stats.failure_count = 0

        if self.stats.state == CircuitBreakerState.HALF_OPEN:
            self.stats.success_count += 1
            if self.stats.success_count >= self.config.reset_success_threshold:
                self._transition_to_closed()
        else:
            # CLOSED – nothing special to track
            pass

    def record_failure(self) -> None:
        """Notify the breaker that a protected call failed."""
        now = datetime.now()
        self.stats.failure_count += 1
        self.stats.last_failure_time = now

        if self.stats.state == CircuitBreakerState.CLOSED:
            if self.stats.failure_count >= self.config.failure_threshold:
                self._transition_to_open()
        elif self.stats.state == CircuitBreakerState.HALF_OPEN:
            # Any failure in HALF_OPEN → back to OPEN immediately
            self._transition_to_open()

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *fn* under circuit-breaker protection.

        Raises:
            CircuitBreakerOpenError: if the breaker is OPEN.
            BaseException: any exception raised by *fn*.
        """
        if self.is_active:
            raise CircuitBreakerOpenError(self.name)

        if self.stats.state == CircuitBreakerState.HALF_OPEN:
            self.stats.half_open_inflight += 1

        try:
            result = await fn(*args, **kwargs)
        except BaseException:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result
        finally:
            if self.stats.state == CircuitBreakerState.HALF_OPEN:
                self.stats.half_open_inflight = max(0, self.stats.half_open_inflight - 1)

    # -- state transitions ---------------------------------------------------

    def _transition_to_open(self) -> None:
        if self.stats.state == CircuitBreakerState.OPEN:
            return  # already open
        logger.warning(f"CircuitBreaker [{self.name}] OPEN – failing fast")
        self.stats.state = CircuitBreakerState.OPEN
        self.stats.opened_at = datetime.now()
        self.stats.total_trips += 1
        self.stats.success_count = 0
        self._fire(self.on_open, self.name)

    def _transition_to_half_open(self) -> None:
        logger.info(f"CircuitBreaker [{self.name}] HALF_OPEN – probing")
        self.stats.state = CircuitBreakerState.HALF_OPEN
        self.stats.failure_count = 0
        self.stats.success_count = 0
        self._fire(self.on_half_open, self.name)

    def _transition_to_closed(self) -> None:
        logger.info(f"CircuitBreaker [{self.name}] CLOSED – normal operation")
        self.stats.state = CircuitBreakerState.CLOSED
        self.stats.failure_count = 0
        self.stats.success_count = 0
        self.stats.opened_at = None
        self._fire(self.on_close, self.name)

    def _cooldown_elapsed(self) -> bool:
        if self.stats.opened_at is None:
            return True
        return (
            datetime.now() - self.stats.opened_at
        ).total_seconds() >= self.config.cooldown_seconds

    # -- callbacks -----------------------------------------------------------

    @staticmethod
    def _fire(handlers: List[Callable[[str], None]], name: str) -> None:
        for h in handlers:
            try:
                h(name)
            except Exception:
                logger.debug(f"CircuitBreaker callback error for [{name}]", exc_info=True)

    def reset(self) -> None:
        """Force-reset to CLOSED (e.g. after manual intervention)."""
        logger.info(f"CircuitBreaker [{self.name}] manually reset to CLOSED")
        self.stats = CircuitBreakerStats()


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, breaker_name: str) -> None:
        super().__init__(f"Circuit breaker [{breaker_name}] is OPEN – request rejected")
        self.breaker_name = breaker_name


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def categorize_exception(
    exc: BaseException,
    extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
) -> ErrorCategory:
    """Heuristic classification of an exception."""
    # Check explicit transient list first (config-supplied + built-ins)
    transient_checks: Tuple[Type[BaseException], ...] = _TRANSIENT_EXCEPTIONS
    if extra_transient:
        transient_checks = transient_checks + extra_transient

    if isinstance(exc, transient_checks):
        return ErrorCategory.TRANSIENT

    if isinstance(exc, _PERMANENT_EXCEPTIONS):
        return ErrorCategory.PERMANENT

    # Handle HTTP-like exceptions that carry a status code attribute
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status is not None:
        try:
            code = int(status)
            if code in _HTTP_TRANSIENT_STATUSES:
                return ErrorCategory.TRANSIENT
            if 400 <= code < 500:
                return ErrorCategory.PERMANENT
        except (TypeError, ValueError):
            pass

    # Default: assume transient (fail-safe for unknown errors)
    return ErrorCategory.TRANSIENT


class RetryExhaustedError(Exception):
    """All retry attempts were consumed without success."""

    def __init__(self, attempts: int, last_exception: BaseException) -> None:
        super().__init__(f"Retry exhausted after {attempts} attempt(s): {last_exception}")
        self.attempts = attempts
        self.last_exception = last_exception


class RetryableError(Exception):
    """Marker exception that forces a retry even for non-transient errors."""

    pass


async def retry_async(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
    context: str = "",
    **kwargs: Any,
) -> T:
    """Execute *fn* with exponential-backoff retry and optional circuit breaker.

    Parameters
    ----------
    fn:
        Async callable to protect.
    config:
        Retry parameters; defaults to ``RetryConfig()``.
    circuit_breaker:
        If supplied, each attempt is wrapped in ``circuit_breaker.execute()``.
    extra_transient:
        Additional exception types to treat as transient.
    context:
        Human-readable label used in log messages.
    *args, **kwargs:
        Forwarded to *fn*.

    Returns
    -------
        The return value of *fn*.

    Raises
    ------
    CircuitBreakerOpenError:
        If the circuit breaker is OPEN before any attempt.
    RetryExhaustedError:
        If all attempts fail.
    """
    cfg = config or RetryConfig()
    last_exc: Optional[BaseException] = None

    for attempt in range(1, cfg.max_attempts + 1):
        try:
            if circuit_breaker is not None:
                return await circuit_breaker.execute(fn, *args, **kwargs)
            return await fn(*args, **kwargs)
        except CircuitBreakerOpenError:
            raise  # don't retry – propagate immediately
        except Exception as exc:
            last_exc = exc
            cat = categorize_exception(exc, extra_transient)

            if cat == ErrorCategory.PERMANENT and not isinstance(exc, RetryableError):
                logger.error(
                    f"Permanent error in {context or fn.__name__}: {exc}", exc_info=False
                )
                raise

            if attempt == cfg.max_attempts:
                logger.error(
                    f"Retry exhausted for {context or fn.__name__} "
                    f"({attempt}/{cfg.max_attempts}): {exc}",
                    exc_info=False,
                )
                raise RetryExhaustedError(attempt, exc) from exc

            delay = cfg.delay_for_attempt(attempt)
            logger.warning(
                f"Retry {attempt}/{cfg.max_attempts} for {context or fn.__name__} "
                f"in {delay:.1f}s – {exc}"
            )
            await asyncio.sleep(delay)

    # Should be unreachable
    assert last_exc is not None
    raise RetryExhaustedError(cfg.max_attempts, last_exc)


def retry_decorator(
    config: Optional[RetryConfig] = None,
    circuit_breaker: Optional[CircuitBreaker] = None,
    extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that wraps an async function with ``retry_async``."""

    def wrapper(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> T:
            return await retry_async(
                fn,
                *args,
                config=config,
                circuit_breaker=circuit_breaker,
                extra_transient=extra_transient,
                context=fn.__name__,
                **kwargs,
            )

        return wrapped

    return wrapper


# ---------------------------------------------------------------------------
# Unified error handler
# ---------------------------------------------------------------------------


@dataclass
class ErrorHandlerConfig:
    """Aggregate configuration for the ErrorHandler façade."""

    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


class ErrorHandler:
    """
    Façade that combines error categorisation, retry, and circuit breaking.

    Typical lifecycle::

        handler = ErrorHandler(ErrorHandlerConfig())
        result = handler.handle(exc, "broker.connect")
        if not result.should_retry:
            ...
    """

    def __init__(self, config: Optional[ErrorHandlerConfig] = None) -> None:
        self.config = config or ErrorHandlerConfig()
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._error_counts: Dict[str, int] = {}
        self._retry_config = self.config.retry

    # -- circuit breaker factory ---------------------------------------------

    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """Return (or create) a named circuit breaker."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(
                self.config.circuit_breaker, name=name
            )
        return self._circuit_breakers[name]

    # -- error handling ------------------------------------------------------

    def categorize(
        self,
        exc: BaseException,
        extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
    ) -> ErrorCategory:
        """Classify *exc* as TRANSIENT, PERMANENT, or CIRCUIT_BREAKER."""
        return categorize_exception(exc, extra_transient)

    def handle(
        self,
        exc: BaseException,
        context: str = "",
        extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
    ) -> ErrorResult:
        """Produce a normalised ErrorResult for the given exception.

        The *context* string (e.g. ``"broker.connect"``) is used to
        track per-component error counts for circuit-breaker decisions.
        """
        cat = categorize_exception(exc, extra_transient)

        # Track error count
        self._error_counts[context] = self._error_counts.get(context, 0) + 1

        # Check if this error should trip a circuit breaker
        cb = self.get_circuit_breaker(context) if context else None
        if cb is not None and cat == ErrorCategory.TRANSIENT:
            cb.record_failure()
            if cb.is_active:
                return ErrorResult(
                    category=ErrorCategory.CIRCUIT_BREAKER,
                    exception=exc,
                    circuit_open=True,
                    traceback_str=traceback.format_exc(),
                )

        return ErrorResult(
            category=cat,
            exception=exc,
            traceback_str=traceback.format_exc(),
        )

    async def execute(
        self,
        fn: Callable[..., Awaitable[T]],
        *args: Any,
        context: str = "",
        extra_transient: Optional[Tuple[Type[BaseException], ...]] = None,
        **kwargs: Any,
    ) -> T:
        """Execute *fn* with retry + circuit-breaker protection.

        Convenience wrapper around :func:`retry_async` that auto-selects
        the correct circuit breaker using the *context* label.
        """
        cb = self.get_circuit_breaker(context) if context else None
        return await retry_async(
            fn,
            *args,
            config=self._retry_config,
            circuit_breaker=cb,
            extra_transient=extra_transient,
            context=context,
            **kwargs,
        )

    # -- statistics ----------------------------------------------------------

    @property
    def error_counts(self) -> Dict[str, int]:
        return dict(self._error_counts)

    def circuit_breaker_stats(self) -> Dict[str, CircuitBreakerStats]:
        return {name: cb.stats for name, cb in self._circuit_breakers.items()}