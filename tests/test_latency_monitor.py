"""Tests for latency monitoring functionality"""

import asyncio
import pytest
import time
from datetime import datetime

from src.monitoring.latency_monitor import (
    LatencyCalculator, PerformanceMetrics, LatencyMonitor,
    AlertThreshold, AlertSeverity, Alert,
    create_latency_threshold, create_throughput_threshold,
    get_monitor
)


class TestAlertThreshold:
    """Test alert threshold functionality"""
    
    def test_threshold_creation(self):
        """Test threshold initialization"""
        threshold = AlertThreshold(
            metric_name="test_latency",
            warning=100.0,
            critical=500.0,
            emergency=1000.0,
            direction="above"
        )
        
        assert threshold.metric_name == "test_latency"
        assert threshold.warning == 100.0
        assert threshold.critical == 500.0
        assert threshold.emergency == 1000.0
        assert threshold.direction == "above"
    
    def test_threshold_check_above(self):
        """Test threshold check with 'above' direction"""
        threshold = AlertThreshold(
            metric_name="test",
            warning=100.0,
            critical=500.0,
            emergency=1000.0,
            direction="above",
            cooldown_seconds=0.0
        )
        
        # Below warning - no alert
        assert threshold.check(50.0) is None
        
        # Above warning - WARNING
        assert threshold.check(150.0) == AlertSeverity.WARNING
        
        # Above critical - CRITICAL
        assert threshold.check(600.0) == AlertSeverity.CRITICAL
        
        # Above emergency - EMERGENCY
        assert threshold.check(1500.0) == AlertSeverity.EMERGENCY
    
    def test_threshold_check_below(self):
        """Test threshold check with 'below' direction"""
        threshold = AlertThreshold(
            metric_name="test",
            warning=1000.0,
            critical=500.0,
            emergency=100.0,
            direction="below",
            cooldown_seconds=0.0
        )
        
        # Above warning - no alert
        assert threshold.check(1500.0) is None
        
        # Below warning - WARNING
        assert threshold.check(800.0) == AlertSeverity.WARNING
        
        # Below critical - CRITICAL
        assert threshold.check(400.0) == AlertSeverity.CRITICAL
        
        # Below emergency - EMERGENCY
        assert threshold.check(50.0) == AlertSeverity.EMERGENCY
    
    def test_threshold_cooldown(self):
        """Test threshold cooldown mechanism"""
        threshold = AlertThreshold(
            metric_name="test",
            warning=100.0,
            cooldown_seconds=1.0,
            critical=None,
            emergency=None
        )
        
        # First trigger
        assert threshold.check(150.0) == AlertSeverity.WARNING
        
        # Second trigger during cooldown - no alert
        assert threshold.check(150.0) is None
        
        # Wait for cooldown
        time.sleep(1.1)
        
        # Third trigger after cooldown - should alert again
        assert threshold.check(150.0) == AlertSeverity.WARNING


class TestLatencyCalculator:
    """Test latency calculator functionality"""
    
    @pytest.fixture
    def calculator(self):
        """Create test calculator"""
        return LatencyCalculator(window_size=100)
    
    def test_add_sample(self, calculator):
        """Test adding latency samples"""
        calculator.add_sample(50.0)
        calculator.add_sample(100.0)
        calculator.add_sample(150.0)
        
        stats = calculator.get_statistics()
        assert stats['count'] == 3
        assert stats['mean'] == 100.0
    
    def test_statistics_calculation(self, calculator):
        """Test statistics calculation"""
        # Add samples
        for i in range(10):
            calculator.add_sample(float(i * 10))  # 0, 10, 20, ..., 90
        
        stats = calculator.get_statistics()
        
        assert stats['count'] == 10
        assert stats['mean'] == 45.0
        assert stats['min'] == 0.0
        assert stats['max'] == 90.0
        assert stats['median'] == 45.0
        assert stats['p95'] > 0  # Should have a value
    
    def test_percentile_calculation(self, calculator):
        """Test percentile calculation"""
        for i in range(100):
            calculator.add_sample(float(i))
        
        stats = calculator.get_statistics()
        
        # P95 should be around 95
        assert 90 <= stats['p95'] <= 99
        # P99 should be around 99
        assert 95 <= stats['p99'] <= 99
    
    def test_empty_calculator(self, calculator):
        """Test statistics with no samples"""
        stats = calculator.get_statistics()
        
        assert stats['count'] == 0
        assert stats['mean'] == 0.0
        assert stats['min'] == 0.0
        assert stats['max'] == 0.0
    
    def test_window_size_limit(self):
        """Test window size limit"""
        calculator = LatencyCalculator(window_size=10)
        
        # Add more samples than window size
        for i in range(20):
            calculator.add_sample(float(i))
        
        stats = calculator.get_statistics()
        assert stats['count'] == 10  # Only 10 most recent
    
    def test_clear(self, calculator):
        """Test clearing calculator"""
        calculator.add_sample(50.0)
        calculator.add_sample(100.0)
        
        assert calculator.get_statistics()['count'] == 2
        
        calculator.clear()
        
        assert calculator.get_statistics()['count'] == 0
    
    def test_record_decorator(self, calculator):
        """Test record decorator"""
        @calculator.record
        def test_function():
            time.sleep(0.01)
            return "result"
        
        result = test_function()
        
        assert result == "result"
        stats = calculator.get_statistics()
        assert stats['count'] == 1
        assert stats['mean'] >= 10.0  # At least 10ms


class TestPerformanceMetrics:
    """Test performance metrics collection"""
    
    @pytest.fixture
    def metrics(self):
        """Create test metrics"""
        return PerformanceMetrics()
    
    def test_record_latency(self, metrics):
        """Test recording latency"""
        metrics.record_latency("api_call", 50.0)
        metrics.record_latency("api_call", 100.0)
        metrics.record_latency("api_call", 150.0)
        
        stats = metrics.get_latency_stats("api_call")
        assert stats['count'] == 3
        assert stats['mean'] == 100.0
    
    def test_record_counter(self, metrics):
        """Test recording counter"""
        metrics.record_counter("requests", 1)
        metrics.record_counter("requests", 1)
        metrics.record_counter("requests", 1)
        
        assert metrics.get_counter("requests") == 3
    
    def test_record_gauge(self, metrics):
        """Test recording gauge"""
        metrics.record_gauge("queue_size", 50.0)
        assert metrics.get_gauge("queue_size") == 50.0
        
        metrics.record_gauge("queue_size", 75.0)
        assert metrics.get_gauge("queue_size") == 75.0
    
    def test_record_throughput(self, metrics):
        """Test recording throughput"""
        metrics.record_throughput("messages", 1000, 10.0)
        
        gauge_value = metrics.get_gauge("messages_throughput")
        assert gauge_value == 100.0  # 1000/10
    
    def test_get_all_metrics(self, metrics):
        """Test getting all metrics"""
        metrics.record_latency("latency1", 50.0)
        metrics.record_counter("counter1", 5)
        metrics.record_gauge("gauge1", 100.0)
        
        all_metrics = metrics.get_all_metrics()
        
        assert "latency" in all_metrics
        assert "counters" in all_metrics
        assert "gauges" in all_metrics
        assert "latency1" in all_metrics['latency']
        assert all_metrics['counters']['counter1'] == 5
        assert all_metrics['gauges']['gauge1'] == 100.0
    
    def test_reset_counter(self, metrics):
        """Test resetting counter"""
        metrics.record_counter("requests", 10)
        assert metrics.get_counter("requests") == 10
        
        metrics.reset_counter("requests")
        assert metrics.get_counter("requests") == 0
    
    def test_clear(self, metrics):
        """Test clearing all metrics"""
        metrics.record_latency("latency1", 50.0)
        metrics.record_counter("counter1", 5)
        metrics.record_gauge("gauge1", 100.0)
        
        metrics.clear()
        
        all_metrics = metrics.get_all_metrics()
        assert len(all_metrics['latency']) == 0
        assert len(all_metrics['counters']) == 0
        assert len(all_metrics['gauges']) == 0


class TestLatencyMonitor:
    """Test latency monitor functionality"""
    
    @pytest.fixture
    def monitor(self):
        """Create test monitor"""
        return LatencyMonitor(alert_check_interval=0.1)
    
    def test_monitor_initialization(self, monitor):
        """Test monitor initialization"""
        assert not monitor._running
        assert monitor._check_interval == 0.1
    
    @pytest.mark.asyncio
    async def test_start_stop(self, monitor):
        """Test starting and stopping monitor"""
        await monitor.start()
        assert monitor._running
        
        await monitor.stop()
        assert not monitor._running
    
    def test_timer_operations(self, monitor):
        """Test timer operations"""
        monitor.start_timer("operation1")
        time.sleep(0.01)
        latency = monitor.stop_timer("operation1")
        
        assert latency >= 10.0  # At least 10ms
        
        stats = monitor.get_statistics("operation1")
        assert stats['count'] == 1
    
    def test_timer_without_start(self, monitor):
        """Test stopping timer that wasn't started"""
        latency = monitor.stop_timer("nonexistent")
        assert latency == 0.0
    
    def test_time_operation_context(self, monitor):
        """Test time operation context manager"""
        with monitor.time_operation("test_op"):
            time.sleep(0.01)
        
        stats = monitor.get_statistics("test_op")
        assert stats['count'] == 1
        assert stats['mean'] >= 10.0
    
    def test_add_threshold(self, monitor):
        """Test adding alert threshold"""
        threshold = AlertThreshold("test_metric", warning=100.0)
        monitor.add_threshold(threshold)
        
        assert "test_metric" in monitor._thresholds
    
    def test_remove_threshold(self, monitor):
        """Test removing alert threshold"""
        threshold = AlertThreshold("test_metric", warning=100.0)
        monitor.add_threshold(threshold)
        assert "test_metric" in monitor._thresholds
        
        monitor.remove_threshold("test_metric")
        assert "test_metric" not in monitor._thresholds
    
    def test_alert_handler(self, monitor):
        """Test alert handler registration"""
        handler_called = False
        
        def test_handler(alert):
            nonlocal handler_called
            handler_called = True
        
        monitor.add_alert_handler(test_handler)
        assert test_handler in monitor._alert_handlers
        
        monitor.remove_alert_handler(test_handler)
        assert test_handler not in monitor._alert_handlers
    
    def test_record_operations(self, monitor):
        """Test various recording operations"""
        monitor.record_latency("api", 50.0)
        monitor.record_counter("requests", 1)
        monitor.record_gauge("memory", 1024.0)
        
        stats = monitor.get_statistics()
        assert "api" in stats['latency']
        assert stats['counters']['requests'] == 1
        assert stats['gauges']['memory'] == 1024.0
    
    @pytest.mark.asyncio
    async def test_alert_generation(self, monitor):
        """Test alert generation"""
        # Add threshold
        threshold = AlertThreshold(
            "high_latency",
            warning=50.0,
            critical=100.0,
            cooldown_seconds=0.1
        )
        monitor.add_threshold(threshold)
        
        # Record high latency
        monitor.record_latency("high_latency", 150.0)
        
        # Manually check thresholds
        alerts = monitor._check_thresholds()
        
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL
        assert alerts[0].metric_name == "high_latency"
    
    def test_get_alerts(self, monitor):
        """Test getting alerts"""
        # Create test alert
        alert = Alert(
            severity=AlertSeverity.WARNING,
            metric_name="test",
            metric_value=100.0,
            threshold=50.0,
            message="Test alert",
            timestamp=datetime.now()
        )
        monitor._alerts.append(alert)
        
        alerts = monitor.get_alerts()
        assert len(alerts) == 1
        
        # Test filtering by severity
        alerts = monitor.get_alerts(severity=AlertSeverity.CRITICAL)
        assert len(alerts) == 0
    
    def test_acknowledge_alert(self, monitor):
        """Test acknowledging alert"""
        timestamp = datetime.now()
        alert = Alert(
            severity=AlertSeverity.WARNING,
            metric_name="test",
            metric_value=100.0,
            threshold=50.0,
            message="Test alert",
            timestamp=timestamp
        )
        monitor._alerts.append(alert)
        
        success = monitor.acknowledge_alert(timestamp)
        assert success
        assert alert.acknowledged
    
    def test_resolve_alert(self, monitor):
        """Test resolving alert"""
        timestamp = datetime.now()
        alert = Alert(
            severity=AlertSeverity.WARNING,
            metric_name="test",
            metric_value=100.0,
            threshold=50.0,
            message="Test alert",
            timestamp=timestamp
        )
        monitor._alerts.append(alert)
        
        success = monitor.resolve_alert(timestamp)
        assert success
        assert alert.resolved
    
    def test_dashboard_data(self, monitor):
        """Test dashboard data generation"""
        monitor.record_latency("api", 50.0)
        monitor.record_counter("requests", 100)
        monitor.record_gauge("memory", 512.0)
        
        dashboard = monitor.get_dashboard_data()
        
        assert "timestamp" in dashboard
        assert "system_status" in dashboard
        assert "latency_metrics" in dashboard
        assert "counters" in dashboard
        assert "gauges" in dashboard
        assert "active_alerts" in dashboard
        assert "alert_summary" in dashboard
        
        assert dashboard['counters']['requests'] == 100
        assert dashboard['gauges']['memory'] == 512.0


class TestThresholdFactories:
    """Test threshold factory functions"""
    
    def test_create_latency_threshold(self):
        """Test latency threshold factory"""
        threshold = create_latency_threshold(
            "api_latency",
            warning_ms=100,
            critical_ms=500,
            emergency_ms=1000
        )
        
        assert threshold.metric_name == "api_latency"
        assert threshold.warning == 100.0
        assert threshold.critical == 500.0
        assert threshold.emergency == 1000.0
        assert threshold.direction == "above"
    
    def test_create_throughput_threshold(self):
        """Test throughput threshold factory"""
        threshold = create_throughput_threshold(
            "messages_throughput",
            min_throughput=1000,
            critical_min=500
        )
        
        assert threshold.metric_name == "messages_throughput"
        assert threshold.warning == 1000.0
        assert threshold.critical == 500.0
        assert threshold.direction == "below"


class TestGlobalMonitor:
    """Test global monitor instance"""
    
    def test_get_monitor(self):
        """Test getting global monitor"""
        monitor1 = get_monitor()
        monitor2 = get_monitor()
        
        # Should return same instance
        assert monitor1 is monitor2
        assert isinstance(monitor1, LatencyMonitor)


class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_full_monitoring_workflow(self):
        """Test complete monitoring workflow"""
        monitor = LatencyMonitor(alert_check_interval=0.1)
        
        # Set up threshold
        threshold = AlertThreshold(
            "database_query",
            warning=50.0,
            critical=100.0,
            cooldown_seconds=0.1
        )
        monitor.add_threshold(threshold)
        
        # Set up alert handler
        alerts_received = []
        
        def alert_handler(alert):
            alerts_received.append(alert)
        
        monitor.add_alert_handler(alert_handler)
        
        # Start monitor
        await monitor.start()
        
        try:
            # Record various metrics
            for i in range(10):
                monitor.record_latency("database_query", 150.0)  # Critical
                monitor.record_counter("queries", 1)
                monitor.record_gauge("connection_pool", 10.0)
                await asyncio.sleep(0.05)
            
            # Wait for alert check
            await asyncio.sleep(0.2)
            
            # Verify alerts were generated
            assert len(alerts_received) > 0
            
            # Check dashboard
            dashboard = monitor.get_dashboard_data()
            assert dashboard['counters']['queries'] == 10
            assert 'database_query' in dashboard['latency_metrics']
            
        finally:
            await monitor.stop()


if __name__ == "__main__":
    pytest.main([__file__])
