"""Custom indicator registry, caching, and batch processing.

Provides three complementary capabilities:

    **IndicatorRegistry** — Decorator-based registration of custom indicators
        by name and category, with discovery, instantiation, and inspection.

    **IndicatorCache** — LRU cache for computed indicator values indexed by
        (indicator_name, parameters_hash, data_fingerprint), avoiding
        redundant recalculation when multiple strategies share indicators.

    **IndicatorBatchProcessor** — Vectorized batch calculation using numpy
        arrays, supporting warmup periods and multi-output indicators.

Usage::

    from src.algorithms.indicators_registry import (
        IndicatorRegistry, register_indicator, IndicatorBatchProcessor
    )

    @register_indicator("smoothed_rsi", category="oscillator")
    class SmoothedRSI(SMA):
        ...

    registry = IndicatorRegistry.instance()
    registry.discover_builtins()
    indicators = registry.create_all(config)
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from threading import Lock
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

import numpy as np

from .indicators import Indicator, SMA, EMA, RSI, MACD, BollingerBands, ATR
from ..utils.logger import get_logger

logger = get_logger("algorithms.indicators.registry")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

IndicatorFactory = Callable[..., Indicator]
"""Callable that creates an indicator instance from keyword parameters."""


class IndicatorCategory(Enum):
    """Standard indicator categories for organization and filtering."""

    MOVING_AVERAGE = "moving_average"
    OSCILLATOR = "oscillator"
    VOLATILITY = "volatility"
    MOMENTUM = "momentum"
    VOLUME = "volume"
    PATTERN = "pattern"
    STATISTICAL = "statistical"
    CUSTOM = "custom"


@dataclass(frozen=True)
class IndicatorSpec:
    """Immutable descriptor for a registered indicator."""

    name: str
    category: IndicatorCategory
    description: str
    factory: IndicatorFactory
    parameters: Dict[str, Any] = field(default_factory=dict)
    outputs: List[str] = field(default_factory=lambda: ["value"])
    requires_ohlc: bool = False
    min_period: int = 1
    tags: List[str] = field(default_factory=list)
    author: str = ""
    version: str = "1.0.0"

    def create(self, **kwargs) -> Indicator:
        merged = {**self.parameters, **kwargs}
        return self.factory(**merged)


# ---------------------------------------------------------------------------
# Indicator Registry
# ---------------------------------------------------------------------------


class IndicatorRegistry:
    """Thread-safe registry mapping indicator names to :class:`IndicatorSpec`.

    Supports two population strategies:
        - Manual registration via :meth:`register`
        - Decorator-based registration via :func:`register_indicator`
        - Bulk discovery via :meth:`discover_builtins`

    Retrieval::

        spec = registry.get("rsi")
        indicator = spec.create(period=21)
    """

    _instance: Optional[IndicatorRegistry] = None
    _lock = Lock()

    def __init__(self) -> None:
        self._specs: Dict[str, IndicatorSpec] = {}
        self._by_category: Dict[IndicatorCategory, List[str]] = {}

    @classmethod
    def instance(cls) -> IndicatorRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ── Registration ────────────────────────────────────────────────

    def register(
        self,
        name: str,
        factory: IndicatorFactory,
        category: Union[IndicatorCategory, str] = IndicatorCategory.CUSTOM,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        outputs: Optional[List[str]] = None,
        requires_ohlc: bool = False,
        min_period: int = 1,
        tags: Optional[List[str]] = None,
        author: str = "",
        version: str = "1.0.0",
    ) -> IndicatorSpec:
        cat = (
            category if isinstance(category, IndicatorCategory)
            else IndicatorCategory(category)
        )
        spec = IndicatorSpec(
            name=name,
            category=cat,
            description=description or f"{name} indicator",
            factory=factory,
            parameters=parameters or {},
            outputs=outputs or ["value"],
            requires_ohlc=requires_ohlc,
            min_period=min_period,
            tags=tags or [],
            author=author,
            version=version,
        )
        self._specs[name] = spec
        self._by_category.setdefault(cat, []).append(name)
        logger.debug(f"Registered indicator: {name} ({cat.value})")
        return spec

    def register_class(
        self,
        indicator_cls: Type[Indicator],
        name: Optional[str] = None,
        category: Union[IndicatorCategory, str] = IndicatorCategory.CUSTOM,
        **kwargs: Any,
    ) -> IndicatorSpec:
        indicator_name = name or getattr(
            indicator_cls, "__indicator_name__", indicator_cls.__name__.lower()
        )

        def factory(**params: Any) -> Indicator:
            merged = {**kwargs, **params}
            return indicator_cls(**merged)

        return self.register(
            name=indicator_name,
            factory=factory,
            category=category,
            description=indicator_cls.__doc__ or "",
            parameters=kwargs,
        )

    # ── Built-in discovery ───────────────────────────────────────────

    def discover_builtins(self) -> List[IndicatorSpec]:
        specs: List[IndicatorSpec] = []

        builtins = [
            (SMA, "sma", IndicatorCategory.MOVING_AVERAGE, "Simple Moving Average",
             {"period": 20}, 1),
            (EMA, "ema", IndicatorCategory.MOVING_AVERAGE, "Exponential Moving Average",
             {"period": 20}, 1),
            (RSI, "rsi", IndicatorCategory.OSCILLATOR, "Relative Strength Index",
             {"period": 14}, 14),
            (ATR, "atr", IndicatorCategory.VOLATILITY, "Average True Range",
             {"period": 14}, 14),
        ]

        for cls, name, cat, desc, params, min_period in builtins:
            if name not in self._specs:
                specs.append(self.register_class(cls, name=name, category=cat,
                                                  **params))
                self._specs[name].__dict__["_min_period"] = min_period

        logger.info(f"Discovered {len(specs)} built-in indicators")
        return specs

    # ── Unregistration ───────────────────────────────────────────────

    def unregister(self, name: str) -> bool:
        spec = self._specs.pop(name, None)
        if spec is None:
            return False
        cat_list = self._by_category.get(spec.category, [])
        if name in cat_list:
            cat_list.remove(name)
        logger.debug(f"Unregistered indicator: {name}")
        return True

    # ── Queries ──────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[IndicatorSpec]:
        return self._specs.get(name)

    def list_all(self) -> List[IndicatorSpec]:
        return list(self._specs.values())

    def list_names(self) -> List[str]:
        return list(self._specs.keys())

    def list_by_category(
        self, category: Union[IndicatorCategory, str]
    ) -> List[IndicatorSpec]:
        cat = (
            category if isinstance(category, IndicatorCategory)
            else IndicatorCategory(category)
        )
        return [self._specs[n] for n in self._by_category.get(cat, [])]

    def list_by_tag(self, tag: str) -> List[IndicatorSpec]:
        return [s for s in self._specs.values() if tag in s.tags]

    def summarize(self) -> Dict[str, Any]:
        return {
            "total_indicators": len(self._specs),
            "categories": {
                cat.value: len(names)
                for cat, names in self._by_category.items()
            },
            "names": sorted(self._specs.keys()),
        }

    # ── Bulk creation ────────────────────────────────────────────────

    def create(
        self, name: str, overrides: Optional[Dict[str, Any]] = None
    ) -> Optional[Indicator]:
        spec = self.get(name)
        if spec is None:
            logger.warning(f"Unknown indicator: {name}")
            return None
        overrides = overrides or {}
        return spec.create(**overrides)

    def create_all(
        self, configs: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Indicator]:
        results: Dict[str, Indicator] = {}
        for name, params in configs.items():
            indicator = self.create(name, params)
            if indicator is not None:
                results[name] = indicator
        return results


# ── Decorator-based registration ────────────────────────────────────


_REGISTRY_PENDING: List[Tuple[Type[Indicator], str, Dict[str, Any]]] = []


def register_indicator(
    name: str,
    category: Union[IndicatorCategory, str] = IndicatorCategory.CUSTOM,
    description: str = "",
    **kwargs: Any,
) -> Callable[[Type[Indicator]], Type[Indicator]]:
    """Class decorator that registers an indicator in the global registry."""

    def decorator(cls: Type[Indicator]) -> Type[Indicator]:
        if hasattr(cls, "__indicator_registered__"):
            return cls
        for _, pending_name, _ in _REGISTRY_PENDING:
            if pending_name == name:
                return cls
        cls.__indicator_registered__ = True
        cls.__indicator_name__ = name
        _REGISTRY_PENDING.append((cls, name, {**kwargs, "category": category,
                                               "description": description}))
        return cls

    return decorator


def flush_pending_registrations(
    registry: Optional[IndicatorRegistry] = None,
) -> int:
    registry = registry or IndicatorRegistry.instance()
    count = 0
    while _REGISTRY_PENDING:
        cls, name, kwargs = _REGISTRY_PENDING.pop(0)
        registry.register_class(cls, name=name, **kwargs)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Indicator Cache
# ---------------------------------------------------------------------------


@dataclass
class CacheKey:
    indicator_name: str
    params_hash: str
    data_hash: str
    
    def __hash__(self) -> int:
        return hash((self.indicator_name, self.params_hash, self.data_hash))


class IndicatorCache:
    """LRU cache for computed indicator outputs.

    Avoids recalculating the same indicator with identical parameters
    and input data when multiple consumers request the same computation.

    The cache key is a hash of (indicator_name, serialized_params, data_fingerprint).
    """

    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._cache: OrderedDict = OrderedDict()

    @staticmethod
    def _hash_params(params: Dict[str, Any]) -> str:
        raw = json.dumps(params, sort_keys=True, default=str)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _hash_data(data: Sequence[float]) -> str:
        if len(data) <= 10:
            raw = ",".join(f"{v:.6f}" for v in data)
        else:
            raw = f"{data[0]:.6f},{data[-1]:.6f},{len(data)},{np.mean(data):.6f}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def get(
        self, name: str, params: Dict[str, Any], data: Sequence[float]
    ) -> Optional[Any]:
        key = CacheKey(
            indicator_name=name,
            params_hash=self._hash_params(params),
            data_hash=self._hash_data(data),
        )
        return self._cache.get(key)

    def put(
        self, name: str, params: Dict[str, Any], data: Sequence[float],
        value: Any,
    ) -> None:
        key = CacheKey(
            indicator_name=name,
            params_hash=self._hash_params(params),
            data_hash=self._hash_data(data),
        )
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = value

    def invalidate(self, name: Optional[str] = None) -> int:
        if name is None:
            count = len(self._cache)
            self._cache.clear()
            return count
        count = 0
        for key in list(self._cache.keys()):
            if key.indicator_name == name:
                del self._cache[key]
                count += 1
        return count

    @property
    def size(self) -> int:
        return len(self._cache)

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> Dict[str, Any]:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hit_rate": getattr(self, "_hits", 0) / max(
                getattr(self, "_total", 1), 1
            ),
        }


# ---------------------------------------------------------------------------
# Batch Processor
# ---------------------------------------------------------------------------


class IndicatorBatchProcessor:
    """Efficiently compute multiple indicators over numpy price arrays.

    Supports:
        - Warmup periods (first N values discarded as indicators stabilize)
        - Multi-output indicators (MACD returns line + signal + histogram)
        - Named result arrays for direct use in strategy logic
    """

    def __init__(
        self,
        registry: Optional[IndicatorRegistry] = None,
        cache: Optional[IndicatorCache] = None,
    ) -> None:
        self._registry = registry or IndicatorRegistry.instance()
        self._cache = cache

    def compute(
        self,
        indicator_name: str,
        data: np.ndarray,
        params: Optional[Dict[str, Any]] = None,
        warmup: int = 0,
    ) -> np.ndarray:
        params = params or {}

        if self._cache is not None:
            data_list = data.tolist() if isinstance(data, np.ndarray) else list(data)
            cached = self._cache.get(indicator_name, params, data_list)
            if cached is not None:
                return cached[warmup:] if isinstance(cached, np.ndarray) else cached

        spec = self._registry.get(indicator_name)
        if spec is None:
            raise KeyError(f"Unknown indicator: {indicator_name}")

        indicator = spec.create(**params)
        results: List[float] = []
        for value in data:
            result = indicator.update(Decimal(str(float(value))))
            if indicator.is_ready:
                results.append(float(result) if result else float("nan"))

        arr = np.array(results, dtype=np.float64)
        result_arr = arr[warmup:] if warmup > 0 else arr

        if self._cache is not None:
            data_list = data.tolist() if isinstance(data, np.ndarray) else list(data)
            self._cache.put(indicator_name, params, data_list, result_arr)

        return result_arr

    def compute_multiple(
        self,
        configs: Dict[str, Dict[str, Any]],
        data: np.ndarray,
        warmup: int = 0,
    ) -> Dict[str, np.ndarray]:
        results: Dict[str, np.ndarray] = {}
        for name, params in configs.items():
            indicator_params = {k: v for k, v in params.items() if k != "warmup"}
            ind_warmup = params.get("warmup", warmup)
            try:
                results[name] = self.compute(
                    name, data, indicator_params, ind_warmup
                )
            except KeyError:
                logger.warning(f"Skipping unknown indicator: {name}")
        return results

    def compute_macd(
        self,
        data: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Dict[str, np.ndarray]:
        macd = MACD(fast_period=fast, slow_period=slow, signal_period=signal)
        macd_line: List[float] = []
        signal_line: List[float] = []
        histogram: List[float] = []

        for value in data:
            macd.update(Decimal(str(float(value))))
            if macd.is_ready:
                macd_line.append(float(macd.macd_line) if macd.macd_line else float("nan"))
                signal_line.append(float(macd.signal_line) if macd.signal_line else float("nan"))
                histogram.append(float(macd.histogram) if macd.histogram else float("nan"))

        return {
            "macd_line": np.array(macd_line, dtype=np.float64),
            "signal_line": np.array(signal_line, dtype=np.float64),
            "histogram": np.array(histogram, dtype=np.float64),
        }

    def compute_bb(
        self,
        data: np.ndarray,
        period: int = 20,
        num_std: float = 2.0,
    ) -> Dict[str, np.ndarray]:
        bb = BollingerBands(period=period, num_std=num_std)
        middle: List[float] = []
        upper: List[float] = []
        lower: List[float] = []

        for value in data:
            bb.update(Decimal(str(float(value))))
            if bb.is_ready:
                middle.append(float(bb.middle) if bb.middle else float("nan"))
                upper.append(float(bb.upper) if bb.upper else float("nan"))
                lower.append(float(bb.lower) if bb.lower else float("nan"))

        return {
            "middle": np.array(middle, dtype=np.float64),
            "upper": np.array(upper, dtype=np.float64),
            "lower": np.array(lower, dtype=np.float64),
        }

    def warmup_indicator(
        self,
        name: str,
        data: np.ndarray,
        params: Optional[Dict[str, Any]] = None,
    ) -> Indicator:
        spec = self._registry.get(name)
        if spec is None:
            raise KeyError(f"Unknown indicator: {name}")
        indicator = spec.create(**(params or {}))
        for value in data:
            indicator.update(Decimal(str(float(value))))
        return indicator

    @property
    def cache(self) -> Optional[IndicatorCache]:
        return self._cache
