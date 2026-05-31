"""Tests for position synchronization module"""

import pytest
from decimal import Decimal

from src.portfolio.position_sync import (
    PositionSynchronizer,
    SyncConfig,
    SyncResult,
    DifferenceType,
    PositionDifference,
    build_broker_position_map,
    build_local_position_map,
)
from src.trading.models import Position, OrderSide
from src.data.models import Symbol


class TestSyncResult:
    """Tests for SyncResult dataclass"""

    def test_sync_result_defaults(self) -> None:
        """Test default SyncResult values"""
        result = SyncResult()
        assert result.broker_positions_count == 0
        assert result.local_positions_count == 0
        assert result.matched == 0
        assert result.differences == []
        assert result.reconciled == 0
        assert result.errors == []
        assert result.total_differences == 0
        assert result.is_clean is True
        assert result.has_critical is False

    def test_sync_result_with_differences(self) -> None:
        """Test SyncResult with differences present"""
        symbol = Symbol(ticker="AAPL")
        diff = PositionDifference(
            difference_type=DifferenceType.QUANTITY_MISMATCH,
            symbol=symbol,
            broker_quantity=Decimal("100"),
            local_quantity=Decimal("50"),
            severity="warning",
        )
        result = SyncResult(
            broker_positions_count=3,
            local_positions_count=3,
            matched=2,
            differences=[diff],
            reconciled=1,
        )

        assert result.total_differences == 1
        assert result.missing_locally == 0
        assert result.missing_on_broker == 0
        assert result.quantity_mismatches == 1
        assert result.price_mismatches == 0
        assert result.is_clean is False
        assert result.has_critical is False

    def test_sync_result_has_critical(self) -> None:
        """Test has_critical detection"""
        symbol = Symbol(ticker="AAPL")
        critical_diff = PositionDifference(
            difference_type=DifferenceType.MISSING_ON_BROKER,
            symbol=symbol,
            severity="critical",
        )
        result = SyncResult(differences=[critical_diff])
        assert result.has_critical is True

    def test_sync_result_to_dict(self) -> None:
        """Test to_dict conversion"""
        result = SyncResult(
            broker_positions_count=2,
            local_positions_count=2,
            matched=2,
        )
        d = result.to_dict()
        assert d["broker_positions_count"] == 2
        assert d["local_positions_count"] == 2
        assert d["matched"] == 2
        assert d["total_differences"] == 0
        assert d["is_clean"] is True
        assert d["has_critical"] is False
        assert "timestamp" in d

    def test_sync_result_missing_counts(self) -> None:
        """Test missing and mismatch counts"""
        symbol1 = Symbol(ticker="AAPL")
        symbol2 = Symbol(ticker="TSLA")
        symbol3 = Symbol(ticker="MSFT")
        symbol4 = Symbol(ticker="GOOGL")

        diffs = [
            PositionDifference(
                difference_type=DifferenceType.MISSING_LOCALLY,
                symbol=symbol1,
                severity="warning",
            ),
            PositionDifference(
                difference_type=DifferenceType.MISSING_ON_BROKER,
                symbol=symbol2,
                severity="critical",
            ),
            PositionDifference(
                difference_type=DifferenceType.QUANTITY_MISMATCH,
                symbol=symbol3,
                severity="warning",
            ),
            PositionDifference(
                difference_type=DifferenceType.PRICE_MISMATCH,
                symbol=symbol4,
                severity="warning",
            ),
        ]
        result = SyncResult(differences=diffs)

        assert result.missing_locally == 1
        assert result.missing_on_broker == 1
        assert result.quantity_mismatches == 1
        assert result.price_mismatches == 1
        assert result.total_differences == 4


class TestPositionDifference:
    """Tests for PositionDifference dataclass"""

    def test_position_difference_creation(self) -> None:
        """Test creating a PositionDifference"""
        symbol = Symbol(ticker="AAPL")
        diff = PositionDifference(
            difference_type=DifferenceType.QUANTITY_MISMATCH,
            symbol=symbol,
            broker_quantity=Decimal("100"),
            local_quantity=Decimal("50"),
            broker_avg_price=Decimal("150.00"),
            local_avg_price=Decimal("150.00"),
            broker_side=OrderSide.BUY,
            local_side=OrderSide.BUY,
            severity="warning",
        )
        assert diff.difference_type == DifferenceType.QUANTITY_MISMATCH
        assert diff.symbol == symbol
        assert diff.broker_quantity == Decimal("100")
        assert diff.local_quantity == Decimal("50")
        assert diff.severity == "warning"

    def test_position_difference_defaults(self) -> None:
        """Test default PositionDifference values"""
        diff = PositionDifference(
            difference_type=DifferenceType.MISSING_LOCALLY,
            symbol=Symbol(ticker="TSLA"),
        )
        assert diff.broker_quantity is None
        assert diff.local_quantity is None
        assert diff.broker_avg_price is None
        assert diff.local_avg_price is None
        assert diff.broker_side is None
        assert diff.local_side is None
        assert diff.severity == "info"


class TestPositionSynchronizerBasic:
    """Tests for PositionSynchronizer basic operations"""

    def test_initialization(self) -> None:
        """Test synchronizer initialization"""
        sync = PositionSynchronizer()
        assert sync.config is not None
        assert sync.config.quantity_tolerance == Decimal("0.00001")
        assert sync.total_syncs == 0
        assert sync.last_sync_time is None

    def test_initialization_with_config(self) -> None:
        """Test initialization with custom config"""
        config = SyncConfig(
            quantity_tolerance=Decimal("0.01"),
            auto_reconcile=True,
            price_tolerance_pct=2.0,
        )
        sync = PositionSynchronizer(config=config)
        assert sync.config.quantity_tolerance == Decimal("0.01")
        assert sync.config.auto_reconcile is True
        assert sync.config.price_tolerance_pct == 2.0

    def test_sync_empty_positions(self) -> None:
        """Test sync with empty position lists"""
        sync = PositionSynchronizer()
        result = sync.sync([], [])

        assert result.is_clean is True
        assert result.matched == 0
        assert result.total_differences == 0
        assert sync.total_syncs == 1

    def test_sync_identical_positions(self) -> None:
        """Test sync with identical broker and local positions"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")
        pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
            current_price=Decimal("155.00"),
        )

        result = sync.sync([pos], [pos])

        assert result.is_clean is True
        assert result.matched == 1
        assert result.total_differences == 0
        assert sync.total_syncs == 1

    def test_sync_multiple_identical(self) -> None:
        """Test sync with multiple identical positions"""
        sync = PositionSynchronizer()
        aapl = Symbol(ticker="AAPL")
        tsla = Symbol(ticker="TSLA")

        broker = [
            Position(
                symbol=aapl, side=OrderSide.BUY,
                quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            ),
            Position(
                symbol=tsla, side=OrderSide.BUY,
                quantity=Decimal("50"), avg_entry_price=Decimal("200.00"),
            ),
        ]

        result = sync.sync(broker, broker)

        assert result.is_clean is True
        assert result.matched == 2
        assert result.total_differences == 0

    def test_last_sync_time(self) -> None:
        """Test last_sync_time is updated after sync"""
        sync = PositionSynchronizer()
        assert sync.last_sync_time is None

        sync.sync([], [])
        assert sync.last_sync_time is not None


class TestDifferenceDetection:
    """Tests for difference detection logic"""

    def test_missing_locally(self) -> None:
        """Test detection of broker-only positions"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")
        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([broker_pos], [])

        assert not result.is_clean
        assert result.missing_locally == 1
        assert result.total_differences == 1
        assert result.differences[0].difference_type == DifferenceType.MISSING_LOCALLY
        assert result.differences[0].severity == "warning"

    def test_missing_on_broker(self) -> None:
        """Test detection of local-only positions"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([], [local_pos])

        assert not result.is_clean
        assert result.missing_on_broker == 1
        assert result.total_differences == 1
        assert result.differences[0].difference_type == DifferenceType.MISSING_ON_BROKER
        assert result.differences[0].severity == "critical"

    def test_quantity_mismatch(self) -> None:
        """Test detection of quantity difference"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("50"),
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([broker_pos], [local_pos])

        assert not result.is_clean
        assert result.quantity_mismatches == 1
        assert result.differences[0].difference_type == DifferenceType.QUANTITY_MISMATCH
        assert result.differences[0].broker_quantity == Decimal("100")
        assert result.differences[0].local_quantity == Decimal("50")

    def test_quantity_within_tolerance(self) -> None:
        """Test that small quantity differences within tolerance are ignored"""
        config = SyncConfig(quantity_tolerance=Decimal("0.01"))
        sync = PositionSynchronizer(config=config)
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100.00"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100.000001"),
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([broker_pos], [local_pos])

        assert result.is_clean is True
        assert result.matched == 1

    def test_price_mismatch(self) -> None:
        """Test detection of price difference"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("160.00"),  # 6.67% difference
        )

        result = sync.sync([broker_pos], [local_pos])

        assert not result.is_clean
        assert result.price_mismatches >= 1

    def test_price_within_tolerance(self) -> None:
        """Test that small price differences within tolerance are ignored"""
        config = SyncConfig(price_tolerance_pct=2.0)
        sync = PositionSynchronizer(config=config)
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("151.00"),  # ~0.67% difference
        )

        result = sync.sync([broker_pos], [local_pos])

        assert result.is_clean is True
        assert result.matched == 1

    def test_side_mismatch(self) -> None:
        """Test detection of side difference (long vs short)"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([broker_pos], [local_pos])

        assert not result.is_clean
        diffs = [d for d in result.differences if d.difference_type == DifferenceType.SIDE_MISMATCH]
        assert len(diffs) == 1
        assert diffs[0].severity == "critical"

    def test_quantity_diff_critical_severity(self) -> None:
        """Test that large quantity difference gets critical severity"""
        config = SyncConfig(max_quantity_diff_pct=5.0)
        sync = PositionSynchronizer(config=config)
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("60"),  # 40% difference
            avg_entry_price=Decimal("150.00"),
        )

        result = sync.sync([broker_pos], [local_pos])
        diff = result.differences[0]
        assert diff.severity == "critical"

    def test_multiple_differences_same_position(self) -> None:
        """Test detection of multiple differences on same position"""
        sync = PositionSynchronizer()
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.SELL,  # Side mismatch
            quantity=Decimal("50"),  # Quantity mismatch
            avg_entry_price=Decimal("200.00"),  # Price mismatch
        )

        result = sync.sync([broker_pos], [local_pos])

        # Should detect side, quantity, and price mismatches
        assert result.total_differences >= 3  # All three mismatch types


class TestSyncHistory:
    """Tests for sync history management"""

    def test_history_recording(self) -> None:
        """Test that sync results are stored in history"""
        sync = PositionSynchronizer()

        sync.sync([], [])
        sync.sync([], [])

        history = sync.get_sync_history()
        assert len(history) == 2

    def test_get_latest_result(self) -> None:
        """Test getting latest sync result"""
        sync = PositionSynchronizer()
        assert sync.get_latest_result() is None

        sync.sync([], [])
        result = sync.get_latest_result()
        assert result is not None
        assert result.is_clean is True

    def test_history_limit(self) -> None:
        """Test history size enforcement"""
        config = SyncConfig(max_log_entries=5)
        sync = PositionSynchronizer(config=config)

        for _ in range(10):
            sync.sync([], [])

        history = sync.get_sync_history()
        assert len(history) == 5

    def test_get_history_with_limit(self) -> None:
        """Test getting history with specific limit"""
        sync = PositionSynchronizer()

        for _ in range(5):
            sync.sync([], [])

        limited = sync.get_sync_history(limit=3)
        assert len(limited) == 3

    def test_clear_history(self) -> None:
        """Test clearing all history"""
        sync = PositionSynchronizer()

        sync.sync([], [])
        sync.sync([], [])

        sync.clear_history()
        assert sync.total_syncs == 0
        assert sync.get_latest_result() is None
        assert len(sync.get_sync_history()) == 0


class TestAutoReconciliation:
    """Tests for automatic position reconciliation"""

    @pytest.fixture
    def reconciling_sync(self) -> PositionSynchronizer:
        """Create synchronizer with auto_reconcile enabled"""
        config = SyncConfig(auto_reconcile=True)
        return PositionSynchronizer(config=config)

    def test_reconcile_quantity_mismatch(self, reconciling_sync) -> None:
        """Test auto-reconciliation of quantity mismatch"""
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("50"),
            avg_entry_price=Decimal("150.00"),
        )

        result = reconciling_sync.sync([broker_pos], [local_pos])

        assert result.reconciled >= 1
        assert local_pos.quantity == Decimal("100")

    def test_reconcile_price_mismatch(self, reconciling_sync) -> None:
        """Test auto-reconciliation of price mismatch"""
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("160.00"),
        )

        result = reconciling_sync.sync([broker_pos], [local_pos])

        assert result.reconciled >= 1
        assert local_pos.avg_entry_price == Decimal("150.00")

    def test_reconcile_side_mismatch(self, reconciling_sync) -> None:
        """Test auto-reconciliation of side mismatch"""
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        result = reconciling_sync.sync([broker_pos], [local_pos])

        assert result.reconciled >= 1
        assert local_pos.side == OrderSide.BUY

    def test_reconcile_missing_on_broker(self, reconciling_sync) -> None:
        """Test that local-only position is removed during reconciliation"""
        symbol = Symbol(ticker="AAPL")
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        local_map = {symbol: local_pos}
        # Simulate: reconciling_sync will remove from the local_map
        result = reconciling_sync.sync([], [local_pos])

        assert result.reconciled >= 1

    def test_reconcile_missing_locally_noop(self, reconciling_sync) -> None:
        """Test that broker-only positions do not trigger reconciliation changes"""
        symbol = Symbol(ticker="AAPL")
        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        result = reconciling_sync.sync([broker_pos], [])

        # Missing locally has nothing to update locally, so no recon
        assert result.reconciled == 0

    def test_reconcile_on_critical_only(self) -> None:
        """Test reconcile_on_critical config option"""
        config = SyncConfig(auto_reconcile=False, reconcile_on_critical=True)
        sync = PositionSynchronizer(config=config)
        symbol = Symbol(ticker="AAPL")

        # Non-critical: quantity within threshold
        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("95"),
            avg_entry_price=Decimal("150.00"),
        )
        result = sync.sync([broker_pos], [local_pos])
        # Should be warning (5% exactly = not > 5%), no critical
        assert result.reconciled == 0

    def test_no_reconcile_when_disabled(self) -> None:
        """Test that reconciliation does not happen when disabled"""
        config = SyncConfig(auto_reconcile=False, reconcile_on_critical=False)
        sync = PositionSynchronizer(config=config)
        symbol = Symbol(ticker="AAPL")

        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("50"),
            avg_entry_price=Decimal("200.00"),
        )

        result = sync.sync([broker_pos], [local_pos])

        assert result.reconciled == 0
        # Local position should be unchanged
        assert local_pos.quantity == Decimal("50")


class TestCallbacks:
    """Tests for callback registration and invocation"""

    def test_difference_callback(self) -> None:
        """Test that difference callbacks are invoked"""
        sync = PositionSynchronizer()
        called: list = []

        def handler(diff: PositionDifference) -> None:
            called.append(diff)

        sync.on_difference(handler)

        symbol = Symbol(ticker="AAPL")
        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )

        sync.sync([broker_pos], [])

        assert len(called) == 1
        assert called[0].symbol.ticker == "AAPL"
        assert called[0].difference_type == DifferenceType.MISSING_LOCALLY

    def test_reconciled_callback(self) -> None:
        """Test that reconciled callbacks are invoked"""
        config = SyncConfig(auto_reconcile=True)
        sync = PositionSynchronizer(config=config)
        called: list = []

        def handler(sym: Symbol, pos: Position) -> None:
            called.append((sym, pos))

        sync.on_reconciled(handler)

        symbol = Symbol(ticker="AAPL")
        broker_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("100"),
            avg_entry_price=Decimal("150.00"),
        )
        local_pos = Position(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=Decimal("50"),
            avg_entry_price=Decimal("150.00"),
        )

        sync.sync([broker_pos], [local_pos])

        assert len(called) == 1
        assert called[0][0].ticker == "AAPL"

    def test_sync_complete_callback(self) -> None:
        """Test that sync complete callbacks are invoked"""
        sync = PositionSynchronizer()
        called: list = []

        def handler(result: SyncResult) -> None:
            called.append(result)

        sync.on_sync_complete(handler)

        sync.sync([], [])

        assert len(called) == 1
        assert called[0].is_clean is True

    def test_multiple_callbacks(self) -> None:
        """Test multiple callbacks of same type"""
        sync = PositionSynchronizer()
        count = 0

        def handler1(result: SyncResult) -> None:
            nonlocal count
            count += 1

        def handler2(result: SyncResult) -> None:
            nonlocal count
            count += 2

        sync.on_sync_complete(handler1)
        sync.on_sync_complete(handler2)

        sync.sync([], [])

        assert count == 3

    def test_callback_error_handling(self) -> None:
        """Test that callback errors don't crash the synchronizer"""
        sync = PositionSynchronizer()

        def bad_handler(result: SyncResult) -> None:
            raise RuntimeError("test error")

        sync.on_sync_complete(bad_handler)

        # Should not raise
        result = sync.sync([], [])
        assert result.is_clean is True  # Still completed successfully


class TestUtilFunctions:
    """Tests for utility functions"""

    def test_build_broker_position_map(self) -> None:
        """Test building broker position lookup map"""
        aapl = Symbol(ticker="AAPL")
        tsla = Symbol(ticker="TSLA")

        positions = [
            Position(
                symbol=aapl, side=OrderSide.BUY,
                quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
            ),
            Position(
                symbol=tsla, side=OrderSide.BUY,
                quantity=Decimal("50"), avg_entry_price=Decimal("200.00"),
            ),
        ]

        result = build_broker_position_map(positions)
        assert len(result) == 2
        assert result[aapl].quantity == Decimal("100")
        assert result[tsla].quantity == Decimal("50")

    def test_build_local_position_map(self) -> None:
        """Test building local position lookup map"""
        aapl = Symbol(ticker="AAPL")

        positions = [
            Position(
                symbol=aapl, side=OrderSide.SELL,
                quantity=Decimal("25"), avg_entry_price=Decimal("300.00"),
            ),
        ]

        result = build_local_position_map(positions)
        assert len(result) == 1
        assert result[aapl].side == OrderSide.SELL

    def test_build_map_empty(self) -> None:
        """Test building position maps with empty lists"""
        assert build_broker_position_map([]) == {}
        assert build_local_position_map([]) == {}

    def test_build_map_skips_none_symbol(self) -> None:
        """Test that positions without symbol are excluded (via dict comprehension filter)"""
        pos = Position(
            symbol=None,  # type: ignore
            side=OrderSide.BUY,
            quantity=Decimal("10"),
            avg_entry_price=Decimal("100"),
        )
        # The filter `if p.symbol is not None` should exclude this
        result = build_broker_position_map([pos])
        assert result == {}


class TestStatistics:
    """Tests for statistics reporting"""

    def test_empty_statistics(self) -> None:
        """Test statistics with no sync history"""
        sync = PositionSynchronizer()
        stats = sync.get_statistics()

        assert stats["total_syncs"] == 0
        assert stats["total_reconciled"] == 0
        assert stats["total_differences_found"] == 0
        assert stats["total_positions_matched"] == 0
        assert stats["average_duration_ms"] == 0.0
        assert stats["last_sync_time"] is None
        assert stats["history_entries"] == 0

    def test_statistics_after_syncs(self) -> None:
        """Test statistics after multiple syncs"""
        sync = PositionSynchronizer()

        aapl = Symbol(ticker="AAPL")
        broker_pos = Position(
            symbol=aapl, side=OrderSide.BUY,
            quantity=Decimal("100"), avg_entry_price=Decimal("150.00"),
        )
        # Sync with differences
        sync.sync([broker_pos], [])

        # Clean sync
        sync.sync([broker_pos], [broker_pos])

        stats = sync.get_statistics()

        assert stats["total_syncs"] == 2
        assert stats["total_differences_found"] == 1
        assert stats["total_positions_matched"] == 1
        assert stats["history_entries"] == 2
        assert stats["last_sync_time"] is not None
        assert "average_duration_ms" in stats


class TestSyncConfig:
    """Tests for SyncConfig"""

    def test_default_config(self) -> None:
        """Test default SyncConfig values"""
        config = SyncConfig()
        assert config.quantity_tolerance == Decimal("0.00001")
        assert config.price_tolerance_pct == 1.0
        assert config.auto_reconcile is False
        assert config.sync_interval_seconds == 30.0
        assert config.max_log_entries == 1000
        assert config.reconcile_on_critical is False
        assert config.max_quantity_diff_pct == 5.0

    def test_custom_config(self) -> None:
        """Test custom SyncConfig values"""
        config = SyncConfig(
            quantity_tolerance=Decimal("0.1"),
            price_tolerance_pct=5.0,
            auto_reconcile=True,
            sync_interval_seconds=60.0,
        )
        assert config.quantity_tolerance == Decimal("0.1")
        assert config.price_tolerance_pct == 5.0
        assert config.auto_reconcile is True
        assert config.sync_interval_seconds == 60.0