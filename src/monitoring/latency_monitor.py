"""Latency monitoring and performance metrics collection"""

import asyncio
import time
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Dict, List, Optional, Callable, Any
from threading import Lock

from ..utils.logger import get_logger

logger = get_logger("monitoring.latency")


class MetricType(Enum):
    """Types of metrics that can be monitored"""
    LATENCY = auto()
    THROUGHPUT = auto()
    ERROR_RATE = auto()
    MEMORY = auto()
    CPU = auto()
    CUSTOM = auto()


class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = auto()
    WARNING = auto()
    CRITICAL = auto()
    EMERGENCY = auto()


@dataclass
class MetricValue:
    """Single metric measurement"""
    value: float
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertThreshold:
    """Alert threshold configuration"""
    metric_name: str
    warning: Optional[float] = None
    critical: Optional[float] = None
    emergency: Optional[float] = None
    direction: str = "above"  # above, below, equals
    cooldown_seconds: float = 60.0
    last_triggered: Optional[datetime] = None
    
    def check(self, value: float) -> Optional[AlertSeverity]:
        """Check if value triggers alert"""
        now = datetime.now()
        
        # Check cooldown
        if self.last_triggered and (now - self.last_triggered).total_seconds() < self.cooldown_seconds:
            return None
        
        triggered = None
        
        if self.direction == "above":
            if self.emergency is not None and value >= self.emergency:
                triggered = AlertSeverity.EMERGENCY
            elif self.critical is not None and value >= self.critical:
                triggered = AlertSeverity.CRITICAL
            elif self.warning is not None and value >= self.warning:
                triggered = AlertSeverity.WARNING
        elif self.direction == "below":
            if self.emergency is not None and value <= self.emergency:
                triggered = AlertSeverity.EMERGENCY
            elif self.critical is not None and value <= self.critical:
                triggered = AlertSeverity.CRITICAL
            elif self.warning is not None and value <= self.warning:
                triggered = AlertSeverity.WARNING
        elif self.direction == "equals":
            if self.emergency is not None and value == self.emergency:
                triggered = AlertSeverity.EMERGENCY
        
        if triggered:
            self.last_triggered = now
        
        return triggered


@dataclass
class Alert:
    """Alert notification"""
    severity: AlertSeverity
    metric_name: str
    metric_value: float
    threshold: float
    message: str
    timestamp: datetime
    acknowledged: bool = False
    resolved: bool = False


class LatencyCalculator:
    """Calculate and track latency statistics"""
    
    def __init__(self, window_size: int = 1000, max_history: int = 10000):
        self._window_size = window_size
        self._max_history = max_history
        self._samples: deque = deque(maxlen=window_size)
        self._history: deque = deque(maxlen=max_history)
        self._lock = Lock()
        
        # Statistics cache
        self._cached_stats: Optional[Dict[str, float]] = None
        self._last_calculation = datetime.min
        self._cache_ttl = timedelta(seconds=1)
    
    def add_sample(self, latency_ms: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Add a latency sample"""
        with self._lock:
            sample = {
                'latency': latency_ms,
                'timestamp': datetime.now(),
                'metadata': metadata or {}
            }
            self._samples.append(sample)
            self._history.append(sample)
            self._cached_stats = None  # Invalidate cache
    
    def record(self, func):
        """Decorator to record function execution latency"""
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                end = time.perf_counter()
                latency_ms = (end - start) * 1000
                self.add_sample(latency_ms, {'function': func.__name__})
        return wrapper
    
    def get_statistics(self) -> Dict[str, float]:
        """Get latency statistics"""
        with self._lock:
            now = datetime.now()
            
            # Return cached stats if still valid
            if self._cached_stats and (now - self._last_calculation) < self._cache_ttl:
                return self._cached_stats
            
            if not self._samples:
                return {
                    'count': 0,
                    'mean': 0.0,
                    'median': 0.0,
                    'std_dev': 0.0,
                    'min': 0.0,
                    'max': 0.0,
                    'p95': 0.0,
                    'p99': 0.0
                }
            
            latencies = [s['latency'] for s in self._samples]
            
            stats = {
                'count': len(latencies),
                'mean': statistics.mean(latencies),
                'median': statistics.median(latencies),
                'std_dev': statistics.stdev(latencies) if len(latencies) > 1 else 0.0,
                'min': min(latencies),
                'max': max(latencies),
                'p95': self._percentile(latencies, 95),
                'p99': self._percentile(latencies, 99)
            }
            
            self._cached_stats = stats
            self._last_calculation = now
            
            return stats
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile value"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def get_recent_samples(self, count: int = 100) -> List[Dict[str, Any]]:
        """Get recent latency samples"""
        with self._lock:
            return list(self._samples)[-count:]
    
    def clear(self) -> None:
        """Clear all samples"""
        with self._lock:
            self._samples.clear()
            self._history.clear()
            self._cached_stats = None


class PerformanceMetrics:
    """Collect and aggregate performance metrics"""
    
    def __init__(self, retention_seconds: float = 3600):
        self._retention = timedelta(seconds=retention_seconds)
        self._metrics: Dict[str, deque] = {}
        self._counters: Dict[str, int] = {}
        self._gauges: Dict[str, float] = {}
        self._lock = Lock()
        
        # Metric calculators
        self._latency_calculators: Dict[str, LatencyCalculator] = {}
    
    def record_latency(self, metric_name: str, latency_ms: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record a latency measurement"""
        with self._lock:
            if metric_name not in self._latency_calculators:
                self._latency_calculators[metric_name] = LatencyCalculator()
            
            self._latency_calculators[metric_name].add_sample(latency_ms, metadata)
    
    def record_counter(self, metric_name: str, increment: int = 1) -> None:
        """Increment a counter metric"""
        with self._lock:
            self._counters[metric_name] = self._counters.get(metric_name, 0) + increment
    
    def record_gauge(self, metric_name: str, value: float) -> None:
        """Record a gauge metric (current value)"""
        with self._lock:
            self._gauges[metric_name] = value
    
    def record_throughput(self, metric_name: str, count: int, time_window_seconds: float) -> None:
        """Record throughput metric"""
        throughput = count / time_window_seconds if time_window_seconds > 0 else 0
        self.record_gauge(f"{metric_name}_throughput", throughput)
    
    def get_latency_stats(self, metric_name: str) -> Dict[str, float]:
        """Get latency statistics for a metric"""
        with self._lock:
            calculator = self._latency_calculators.get(metric_name)
            if calculator:
                return calculator.get_statistics()
            return {}
    
    def get_counter(self, metric_name: str) -> int:
        """Get counter value"""
        with self._lock:
            return self._counters.get(metric_name, 0)
    
    def get_gauge(self, metric_name: str) -> Optional[float]:
        """Get gauge value"""
        with self._lock:
            return self._gauges.get(metric_name)
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """Get all current metrics"""
        with self._lock:
            return {
                'latency': {
                    name: calc.get_statistics()
                    for name, calc in self._latency_calculators.items()
                },
                'counters': dict(self._counters),
                'gauges': dict(self._gauges)
            }
    
    def reset_counter(self, metric_name: str) -> None:
        """Reset a counter to zero"""
        with self._lock:
            self._counters[metric_name] = 0
    
    def clear(self) -> None:
        """Clear all metrics"""
        with self._lock:
            self._latency_calculators.clear()
            self._counters.clear()
            self._gauges.clear()


class LatencyMonitor:
    """Main latency monitoring service"""
    
    def __init__(self, alert_check_interval: float = 5.0):
        self._metrics = PerformanceMetrics()
        self._thresholds: Dict[str, AlertThreshold] = {}
        self._alerts: deque = deque(maxlen=1000)
        self._alert_handlers: List[Callable[[Alert], None]] = []
        self._lock = Lock()
        
        # Monitoring state
        self._running = False
        self._check_interval = alert_check_interval
        self._check_task: Optional[asyncio.Task] = None
        
        # Metric tracking for specific operations
        self._operation_timers: Dict[str, float] = {}
    
    def start_timer(self, operation_name: str) -> None:
        """Start timing an operation"""
        self._operation_timers[operation_name] = time.perf_counter()
    
    def stop_timer(self, operation_name: str, metadata: Optional[Dict[str, Any]] = None) -> float:
        """Stop timing an operation and record latency"""
        if operation_name not in self._operation_timers:
            logger.warning(f"Timer {operation_name} was not started")
            return 0.0
        
        start_time = self._operation_timers.pop(operation_name)
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000
        
        self._metrics.record_latency(operation_name, latency_ms, metadata)
        return latency_ms
    
    def time_operation(self, operation_name: str):
        """Context manager/decorator for timing operations"""
        class TimerContext:
            def __init__(ctx_self, monitor, name):
                ctx_self.monitor = monitor
                ctx_self.name = name
                ctx_self.start_time = None
            
            def __enter__(ctx_self):
                ctx_self.start_time = time.perf_counter()
                return ctx_self
            
            def __exit__(ctx_self, exc_type, exc_val, exc_tb):
                end_time = time.perf_counter()
                latency_ms = (end_time - ctx_self.start_time) * 1000
                ctx_self.monitor._metrics.record_latency(
                    ctx_self.name, 
                    latency_ms,
                    {'error': str(exc_val) if exc_val else None}
                )
        
        return TimerContext(self, operation_name)
    
    def add_threshold(self, threshold: AlertThreshold) -> None:
        """Add an alert threshold"""
        with self._lock:
            self._thresholds[threshold.metric_name] = threshold
        
        logger.info(f"Added threshold for {threshold.metric_name}")
    
    def remove_threshold(self, metric_name: str) -> None:
        """Remove an alert threshold"""
        with self._lock:
            if metric_name in self._thresholds:
                del self._thresholds[metric_name]
    
    def add_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """Add an alert handler callback"""
        self._alert_handlers.append(handler)
    
    def remove_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """Remove an alert handler"""
        if handler in self._alert_handlers:
            self._alert_handlers.remove(handler)
    
    def _check_thresholds(self) -> List[Alert]:
        """Check all thresholds and generate alerts"""
        alerts = []
        
        with self._lock:
            all_metrics = self._metrics.get_all_metrics()
            
            for metric_name, threshold in self._thresholds.items():
                # Check latency metrics
                if metric_name in all_metrics['latency']:
                    stats = all_metrics['latency'][metric_name]
                    value = stats.get('mean', 0)
                    
                    severity = threshold.check(value)
                    if severity:
                        alert = Alert(
                            severity=severity,
                            metric_name=metric_name,
                            metric_value=value,
                            threshold=getattr(threshold, severity.name.lower(), threshold.warning),
                            message=f"{metric_name} latency is {value:.2f}ms (threshold: {getattr(threshold, severity.name.lower(), threshold.warning)})",
                            timestamp=datetime.now()
                        )
                        alerts.append(alert)
                        self._alerts.append(alert)
                
                # Check gauge metrics
                gauge_value = all_metrics['gauges'].get(metric_name)
                if gauge_value is not None:
                    severity = threshold.check(gauge_value)
                    if severity:
                        alert = Alert(
                            severity=severity,
                            metric_name=metric_name,
                            metric_value=gauge_value,
                            threshold=getattr(threshold, severity.name.lower(), threshold.warning),
                            message=f"{metric_name} is {gauge_value:.2f} (threshold: {getattr(threshold, severity.name.lower(), threshold.warning)})",
                            timestamp=datetime.now()
                        )
                        alerts.append(alert)
                        self._alerts.append(alert)
        
        return alerts
    
    def _notify_handlers(self, alerts: List[Alert]) -> None:
        """Notify all alert handlers"""
        for alert in alerts:
            logger.warning(f"Alert: {alert.severity.name} - {alert.message}")
            
            for handler in self._alert_handlers:
                try:
                    handler(alert)
                except Exception as e:
                    logger.error(f"Error in alert handler: {e}")
    
    async def _check_loop(self) -> None:
        """Main monitoring loop"""
        while self._running:
            try:
                # Check thresholds
                alerts = self._check_thresholds()
                
                if alerts:
                    self._notify_handlers(alerts)
                
                await asyncio.sleep(self._check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
    
    async def start(self) -> None:
        """Start the latency monitor"""
        if self._running:
            return
        
        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        
        logger.info("Latency monitor started")
    
    async def stop(self) -> None:
        """Stop the latency monitor"""
        if not self._running:
            return
        
        self._running = False
        
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Latency monitor stopped")
    
    def record_latency(self, metric_name: str, latency_ms: float, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record a latency measurement"""
        self._metrics.record_latency(metric_name, latency_ms, metadata)
    
    def record_counter(self, metric_name: str, increment: int = 1) -> None:
        """Record a counter increment"""
        self._metrics.record_counter(metric_name, increment)
    
    def record_gauge(self, metric_name: str, value: float) -> None:
        """Record a gauge value"""
        self._metrics.record_gauge(metric_name, value)
    
    def get_statistics(self, metric_name: Optional[str] = None) -> Dict[str, Any]:
        """Get latency statistics"""
        if metric_name:
            return self._metrics.get_latency_stats(metric_name)
        
        return self._metrics.get_all_metrics()
    
    def get_alerts(
        self,
        severity: Optional[AlertSeverity] = None,
        unresolved_only: bool = False,
        limit: int = 100
    ) -> List[Alert]:
        """Get alerts with optional filtering"""
        with self._lock:
            alerts = list(self._alerts)
        
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        if unresolved_only:
            alerts = [a for a in alerts if not a.resolved]
        
        return alerts[-limit:]
    
    def acknowledge_alert(self, alert_timestamp: datetime) -> bool:
        """Acknowledge an alert"""
        with self._lock:
            for alert in self._alerts:
                if alert.timestamp == alert_timestamp:
                    alert.acknowledged = True
                    return True
        return False
    
    def resolve_alert(self, alert_timestamp: datetime) -> bool:
        """Mark an alert as resolved"""
        with self._lock:
            for alert in self._alerts:
                if alert.timestamp == alert_timestamp:
                    alert.resolved = True
                    return True
        return False
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get data formatted for monitoring dashboard"""
        all_metrics = self._metrics.get_all_metrics()
        
        # Get active (unresolved) alerts
        active_alerts = self.get_alerts(unresolved_only=True, limit=10)
        
        # Format latency data for charts
        latency_charts = {}
        for name, stats in all_metrics['latency'].items():
            if stats.get('count', 0) > 0:
                latency_charts[name] = {
                    'current': stats.get('mean', 0),
                    'p95': stats.get('p95', 0),
                    'p99': stats.get('p99', 0),
                    'trend': 'stable'  # Could calculate trend
                }
        
        return {
            'timestamp': datetime.now().isoformat(),
            'system_status': 'healthy' if not active_alerts else 'degraded',
            'latency_metrics': latency_charts,
            'counters': all_metrics['counters'],
            'gauges': all_metrics['gauges'],
            'active_alerts': [
                {
                    'severity': a.severity.name,
                    'metric': a.metric_name,
                    'value': a.metric_value,
                    'message': a.message,
                    'timestamp': a.timestamp.isoformat()
                }
                for a in active_alerts
            ],
            'alert_summary': {
                'total': len(self._alerts),
                'active': len(active_alerts),
                'acknowledged': sum(1 for a in self._alerts if a.acknowledged),
                'by_severity': {
                    'EMERGENCY': sum(1 for a in self._alerts if a.severity == AlertSeverity.EMERGENCY),
                    'CRITICAL': sum(1 for a in self._alerts if a.severity == AlertSeverity.CRITICAL),
                    'WARNING': sum(1 for a in self._alerts if a.severity == AlertSeverity.WARNING)
                }
            }
        }


# Convenience functions for common monitoring scenarios

def create_latency_threshold(
    metric_name: str,
    warning_ms: float = 100,
    critical_ms: float = 500,
    emergency_ms: float = 1000
) -> AlertThreshold:
    """Create a standard latency alert threshold"""
    return AlertThreshold(
        metric_name=metric_name,
        warning=warning_ms,
        critical=critical_ms,
        emergency=emergency_ms,
        direction="above",
        cooldown_seconds=60.0
    )


def create_throughput_threshold(
    metric_name: str,
    min_throughput: float = 1000,
    critical_min: float = 500
) -> AlertThreshold:
    """Create a throughput alert threshold (alerts when below threshold)"""
    return AlertThreshold(
        metric_name=metric_name,
        warning=min_throughput,
        critical=critical_min,
        direction="below",
        cooldown_seconds=30.0
    )


# Global monitor instance
_global_monitor: Optional[LatencyMonitor] = None


def get_monitor() -> LatencyMonitor:
    """Get the global latency monitor instance"""
    global _global_monitor
    if _global_monitor is None:
        _global_monitor = LatencyMonitor()
    return _global_monitor


def set_monitor(monitor: LatencyMonitor) -> None:
    """Set the global latency monitor instance"""
    global _global_monitor
    _global_monitor = monitor
