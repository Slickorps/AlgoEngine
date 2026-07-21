"""Tests for EngineMetrics Prometheus collector"""

import pytest
import time
from prometheus_client import CollectorRegistry

from src.monitoring.prometheus_metrics import EngineMetrics, get_engine_metrics, _engine_metrics


class TestEngineMetrics:
    """Test EngineMetrics class"""

    @pytest.fixture
    def registry(self):
        return CollectorRegistry()

    @pytest.fixture
    def metrics(self, registry):
        return EngineMetrics(registry=registry)

    def test_record_order_submitted(self, metrics):
        metrics.record_order_submitted(side="BUY", order_type="MARKET")
        metrics.record_order_submitted(side="SELL", order_type="LIMIT")

        assert metrics.orders_submitted.labels(side="BUY", order_type="MARKET")._value.get() == 1.0
        assert metrics.orders_submitted.labels(side="SELL", order_type="LIMIT")._value.get() == 1.0
        assert metrics.active_orders._value.get() == 2.0

    def test_record_order_filled(self, metrics):
        metrics.active_orders.inc()
        metrics.record_order_filled(side="BUY")

        assert metrics.orders_filled.labels(side="BUY")._value.get() == 1.0
        assert metrics.active_orders._value.get() == 0.0

    def test_record_order_cancelled(self, metrics):
        metrics.active_orders.inc()
        metrics.record_order_cancelled(reason="user")

        assert metrics.orders_cancelled.labels(reason="user")._value.get() == 1.0
        assert metrics.active_orders._value.get() == 0.0

    def test_record_order_rejected(self, metrics):
        metrics.active_orders.inc()
        metrics.record_order_rejected(reason="risk_limit")

        assert metrics.orders_rejected.labels(reason="risk_limit")._value.get() == 1.0
        assert metrics.active_orders._value.get() == 0.0

    def test_record_fill(self, metrics):
        metrics.record_fill(side="BUY", value=1500.0)

        assert metrics.fill_count.labels(side="BUY")._value.get() == 1.0
        assert metrics.fill_value.labels(side="BUY")._value.get() == 1500.0

    def test_record_fill_multiple_sides(self, metrics):
        metrics.record_fill(side="BUY", value=1000.0)
        metrics.record_fill(side="BUY", value=500.0)
        metrics.record_fill(side="SELL", value=800.0)

        assert metrics.fill_count.labels(side="BUY")._value.get() == 2.0
        assert metrics.fill_value.labels(side="BUY")._value.get() == 1500.0
        assert metrics.fill_count.labels(side="SELL")._value.get() == 1.0
        assert metrics.fill_value.labels(side="SELL")._value.get() == 800.0

    def test_record_positions(self, metrics):
        metrics.record_positions(count=3, net_exposure=50000.0, pnl=2500.0)

        assert metrics.position_count._value.get() == 3.0
        assert metrics.net_exposure._value.get() == 50000.0
        assert metrics.unrealized_pnl._value.get() == 2500.0

    def test_record_signal(self, metrics):
        metrics.record_signal(strategy="SMA_Crossover", direction="LONG")

        assert metrics.strategy_signals.labels(
            strategy="SMA_Crossover", direction="LONG"
        )._value.get() == 1.0

    def test_set_strategy_state(self, metrics):
        metrics.set_strategy_state(strategy="MACD_Trend", state=1)
        metrics.set_strategy_state(strategy="RSI_MeanReversion", state=-1)

        assert metrics.strategy_state.labels(strategy="MACD_Trend")._value.get() == 1.0
        assert metrics.strategy_state.labels(strategy="RSI_MeanReversion")._value.get() == -1.0

    def test_record_tick(self, metrics):
        metrics.record_tick(symbol="AAPL")
        metrics.record_tick(symbol="GOOGL")

        assert metrics.ticks_received.labels(symbol="AAPL")._value.get() == 1.0
        assert metrics.ticks_received.labels(symbol="GOOGL")._value.get() == 1.0

    def test_record_bar(self, metrics):
        metrics.record_bar(symbol="AAPL", resolution="1m")

        assert metrics.bars_received.labels(symbol="AAPL", resolution="1m")._value.get() == 1.0

    def test_record_data_gap(self, metrics):
        metrics.record_data_gap(symbol="AAPL")
        metrics.record_data_gap(symbol="AAPL")

        assert metrics.data_gaps.labels(symbol="AAPL")._value.get() == 2.0

    def test_record_data_latency(self, metrics):
        metrics.record_data_latency(symbol="AAPL", data_type="tick", latency_seconds=0.025)

        sample_sum = metrics.data_latency.labels(
            symbol="AAPL", data_type="tick"
        )._sum.get()
        assert sample_sum >= 0.02

    def test_record_error(self, metrics):
        metrics.record_error(component="order_router", error_type="ConnectionError")
        metrics.record_error(component="order_router", error_type="TimeOut")

        assert metrics.engine_errors.labels(
            component="order_router", error_type="ConnectionError"
        )._value.get() == 1.0
        assert metrics.engine_errors.labels(
            component="order_router", error_type="TimeOut"
        )._value.get() == 1.0

    def test_record_event(self, metrics):
        metrics.record_event(event_type="ORDER")
        metrics.record_event(event_type="FILL")
        metrics.record_event(event_type="ORDER")

        assert metrics.event_processed.labels(event_type="ORDER")._value.get() == 2.0
        assert metrics.event_processed.labels(event_type="FILL")._value.get() == 1.0

    def test_record_event_latency(self, metrics):
        metrics.record_event_latency(event_type="ORDER", latency_seconds=0.003)

        sample_sum = metrics.event_latency.labels(event_type="ORDER")._sum.get()
        assert sample_sum >= 0.003

    def test_set_event_queue_size(self, metrics):
        metrics.set_event_queue_size(10)
        assert metrics.event_queue_size._value.get() == 10.0

        metrics.set_event_queue_size(0)
        assert metrics.event_queue_size._value.get() == 0.0

    def test_get_metrics_text(self, metrics):
        result = metrics.get_metrics_text()

        assert isinstance(result, bytes)
        assert b"algoengine_core_running" in result
        assert b"algoengine_core_uptime_seconds" in result

    def test_get_metrics_text_with_submitted_order(self, metrics):
        metrics.record_order_submitted(side="BUY", order_type="MARKET")
        result = metrics.get_metrics_text()

        assert b'algoengine_core_orders_submitted_total{order_type="MARKET",side="BUY"} 1.0' in result

    def test_get_metrics_json(self, metrics):
        metrics.start(version="2.0.0", mode="test_mode")
        time.sleep(0.01)

        result = metrics.get_metrics_json()

        assert "engine_running" in result
        assert result["engine_running"] is True
        assert "uptime_seconds" in result
        assert result["uptime_seconds"] >= 0
        assert "active_orders" in result
        assert "position_count" in result
        assert "net_exposure" in result
        assert "unrealized_pnl" in result
        assert "event_queue_size" in result
        assert "timestamp" in result

    def test_start_lifecycle(self, metrics):
        metrics.start(version="1.2.3", mode="live")

        assert metrics.engine_running._value.get() == 1.0
        assert metrics.engine_uptime._value.get() == 0.0

    def test_stop_lifecycle(self, metrics):
        metrics.start()
        metrics.stop()

        assert metrics.engine_running._value.get() == 0.0

    def test_start_then_stop_full_cycle(self, metrics):
        metrics.start(version="3.0.0", mode="live")
        assert metrics.engine_running._value.get() == 1.0

        metrics.record_order_submitted(side="BUY", order_type="MARKET")
        metrics.record_fill(side="BUY", value=1000.0)

        metrics.stop()
        assert metrics.engine_running._value.get() == 0.0

        result = metrics.get_metrics_text()
        assert isinstance(result, bytes)

    def test_tick_updates_uptime(self, metrics):
        metrics.start()
        time.sleep(0.02)
        metrics.tick()

        uptime = metrics.engine_uptime._value.get()
        assert uptime > 0.0

    def test_external_metrics_collector_callback_text(self, metrics):
        def extra_metrics():
            return {"custom_counter": 42, "custom_gauge": 7.5}

        metrics.set_metrics_collector(extra_metrics)
        result = metrics.get_metrics_text()

        assert b"algoengine_core_custom_counter 42.0" in result
        assert b"algoengine_core_custom_gauge 7.5" in result

    def test_external_metrics_collector_json(self, metrics):
        def extra_metrics():
            return {"custom_metric": 99}

        metrics.set_metrics_collector(extra_metrics)
        result = metrics.get_metrics_json()

        assert result["custom_metric"] == 99

    def test_external_metrics_collector_error_handling(self, metrics):
        def failing_callback():
            raise RuntimeError("collector failed")

        metrics.set_metrics_collector(failing_callback)

        result = metrics.get_metrics_text()
        assert isinstance(result, bytes)

        result_json = metrics.get_metrics_json()
        assert isinstance(result_json, dict)

    def test_external_metrics_non_numeric_ignored(self, metrics):
        def extra_metrics():
            return {"string_val": "hello", "numeric_val": 123}

        metrics.set_metrics_collector(extra_metrics)
        result = metrics.get_metrics_text()

        assert b"algoengine_core_numeric_val 123.0" in result
        assert b"algoengine_core_string_val" not in result

    def test_get_engine_metrics_singleton(self):
        import src.monitoring.prometheus_metrics as pm
        pm._engine_metrics = None

        m1 = pm.get_engine_metrics()
        m2 = pm.get_engine_metrics()

        assert m1 is m2
        assert isinstance(m1, EngineMetrics)

        pm._engine_metrics = None
