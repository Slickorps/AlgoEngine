"""Position synchronization between local engine and broker for AlgoEngine"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from ..trading.models import Position, OrderSide
from ..data.models import Symbol
from ..utils.logger import get_logger

logger = get_logger("portfolio.position_sync")


class DifferenceType(Enum):
    """Types of position differences detected during sync"""
    MISSING_LOCALLY = auto()       # Position exists on broker but not locally
    MISSING_ON_BROKER = auto()     # Position exists locally but not on broker
    QUANTITY_MISMATCH = auto()     # Position quantities differ
    PRICE_MISMATCH = auto()        # Average entry prices differ
    SIDE_MISMATCH = auto()         # Position side differs (long vs short)


@dataclass
class PositionDifference:
    """A single detected difference between broker and local positions"""
    difference_type: DifferenceType
    symbol: Symbol
    broker_quantity: Optional[Decimal] = None
    local_quantity: Optional[Decimal] = None
    broker_avg_price: Optional[Decimal] = None
    local_avg_price: Optional[Decimal] = None
    broker_side: Optional[OrderSide] = None
    local_side: Optional[OrderSide] = None
    severity: str = "info"  # info, warning, critical


@dataclass
class SyncConfig:
    """Configuration for position synchronization"""
    quantity_tolerance: Decimal = Decimal("0.00001")
    price_tolerance_pct: float = 1.0          # Percentage difference allowed
    auto_reconcile: bool = False               # Auto-correct local to match broker
    sync_interval_seconds: float = 30.0
    max_log_entries: int = 1000
    reconcile_on_critical: bool = False        # Only auto-reconcile critical diffs
    max_quantity_diff_pct: float = 5.0         # Max quantity diff % before critical


@dataclass
class SyncResult:
    """Result of a single position synchronization run"""
    timestamp: datetime = field(default_factory=datetime.now)
    broker_positions_count: int = 0
    local_positions_count: int = 0
    matched: int = 0
    differences: List[PositionDifference] = field(default_factory=list)
    reconciled: int = 0
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def total_differences(self) -> int:
        """Total number of differences found"""
        return len(self.differences)

    @property
    def missing_locally(self) -> int:
        """Count of positions on broker but not locally"""
        return sum(
            1 for d in self.differences
            if d.difference_type == DifferenceType.MISSING_LOCALLY
        )

    @property
    def missing_on_broker(self) -> int:
        """Count of positions locally but not on broker"""
        return sum(
            1 for d in self.differences
            if d.difference_type == DifferenceType.MISSING_ON_BROKER
        )

    @property
    def quantity_mismatches(self) -> int:
        """Count of quantity mismatches"""
        return sum(
            1 for d in self.differences
            if d.difference_type == DifferenceType.QUANTITY_MISMATCH
        )

    @property
    def price_mismatches(self) -> int:
        """Count of price mismatches"""
        return sum(
            1 for d in self.differences
            if d.difference_type == DifferenceType.PRICE_MISMATCH
        )

    @property
    def has_critical(self) -> bool:
        """Check if any differences are critical severity"""
        return any(d.severity == "critical" for d in self.differences)

    @property
    def is_clean(self) -> bool:
        """No differences detected"""
        return len(self.differences) == 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "broker_positions_count": self.broker_positions_count,
            "local_positions_count": self.local_positions_count,
            "matched": self.matched,
            "total_differences": self.total_differences,
            "missing_locally": self.missing_locally,
            "missing_on_broker": self.missing_on_broker,
            "quantity_mismatches": self.quantity_mismatches,
            "price_mismatches": self.price_mismatches,
            "reconciled": self.reconciled,
            "is_clean": self.is_clean,
            "has_critical": self.has_critical,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


class PositionSynchronizer:
    """
    Synchronize positions between broker and local engine.

    Detects differences and optionally reconciles them by updating
    local positions to match the broker's state (source of truth).
    """

    def __init__(self, config: Optional[SyncConfig] = None) -> None:
        self._config = config or SyncConfig()
        self._sync_history: List[SyncResult] = []
        self._last_sync_time: Optional[datetime] = None
        self._total_syncs: int = 0
        self._total_reconciled: int = 0

        # Callbacks
        self._on_difference: List[Callable[[PositionDifference], None]] = []
        self._on_reconciled: List[Callable[[Symbol, Position], None]] = []
        self._on_sync_complete: List[Callable[[SyncResult], None]] = []

        logger.info("PositionSynchronizer initialized")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> SyncConfig:
        """Get sync configuration"""
        return self._config

    @property
    def last_sync_time(self) -> Optional[datetime]:
        """Get last sync timestamp"""
        return self._last_sync_time

    @property
    def total_syncs(self) -> int:
        """Total number of sync operations performed"""
        return self._total_syncs

    # ------------------------------------------------------------------
    # Main Sync Method
    # ------------------------------------------------------------------

    def sync(
        self,
        broker_positions: List[Position],
        local_positions: List[Position],
    ) -> SyncResult:
        """
        Synchronize broker positions with local positions.

        Broker positions are the source of truth.

        Args:
            broker_positions: List of positions from the broker
            local_positions: List of positions from the local engine

        Returns:
            SyncResult with detected differences and reconciliation details
        """
        start_time = datetime.now()
        result = SyncResult(
            broker_positions_count=len(broker_positions),
            local_positions_count=len(local_positions),
        )

        try:
            # Build lookup maps
            broker_map: Dict[Symbol, Position] = {
                p.symbol: p for p in broker_positions if p.symbol is not None
            }
            local_map: Dict[Symbol, Position] = {
                p.symbol: p for p in local_positions if p.symbol is not None
            }

            all_symbols = set(broker_map.keys()) | set(local_map.keys())
            matched_count = 0
            differences: List[PositionDifference] = []

            for symbol in all_symbols:
                broker_pos = broker_map.get(symbol)
                local_pos = local_map.get(symbol)

                diffs = self._compare_positions(symbol, broker_pos, local_pos)
                if diffs:
                    differences.extend(diffs)
                else:
                    matched_count += 1

            result.matched = matched_count
            result.differences = differences

            # Reconcile if configured
            if self._config.auto_reconcile or (
                self._config.reconcile_on_critical and result.has_critical
            ):
                reconciled = self._reconcile_differences(differences, local_map)
                result.reconciled = reconciled

            # Notify callbacks
            for diff in differences:
                self._notify_difference(diff)

            # Store history
            self._add_to_history(result)
            self._last_sync_time = datetime.now()
            self._total_syncs += 1

            if not result.is_clean:
                logger.warning(
                    f"Sync found {result.total_differences} differences "
                    f"(matched: {matched_count}, reconciled: {result.reconciled})"
                )
            else:
                logger.debug(
                    f"Sync clean: {matched_count} positions matched"
                )

        except Exception as e:
            result.errors.append(f"Sync error: {e}")
            logger.error(f"Position sync failed: {e}")

        result.duration_ms = (
            datetime.now() - start_time
        ).total_seconds() * 1000

        # Notify sync complete
        for handler in self._on_sync_complete:
            try:
                handler(result)
            except Exception as e:
                logger.error(f"Error in sync complete handler: {e}")

        return result

    # ------------------------------------------------------------------
    # Difference Detection
    # ------------------------------------------------------------------

    def _compare_positions(
        self,
        symbol: Symbol,
        broker_pos: Optional[Position],
        local_pos: Optional[Position],
    ) -> List[PositionDifference]:
        """Compare broker and local position for a symbol, return differences"""
        differences: List[PositionDifference] = []

        # Missing locally
        if broker_pos is not None and local_pos is None:
            differences.append(
                PositionDifference(
                    difference_type=DifferenceType.MISSING_LOCALLY,
                    symbol=symbol,
                    broker_quantity=broker_pos.quantity,
                    broker_avg_price=broker_pos.avg_entry_price,
                    broker_side=broker_pos.side,
                    severity="warning",
                )
            )
            return differences

        # Missing on broker
        if local_pos is not None and broker_pos is None:
            differences.append(
                PositionDifference(
                    difference_type=DifferenceType.MISSING_ON_BROKER,
                    symbol=symbol,
                    local_quantity=local_pos.quantity,
                    local_avg_price=local_pos.avg_entry_price,
                    local_side=local_pos.side,
                    severity="critical",
                )
            )
            return differences

        # Both exist — compare details
        if broker_pos is not None and local_pos is not None:
            # Check side
            if broker_pos.side != local_pos.side:
                differences.append(
                    PositionDifference(
                        difference_type=DifferenceType.SIDE_MISMATCH,
                        symbol=symbol,
                        broker_side=broker_pos.side,
                        local_side=local_pos.side,
                        broker_quantity=broker_pos.quantity,
                        local_quantity=local_pos.quantity,
                        severity="critical",
                    )
                )

            # Check quantity
            qty_diff = abs(broker_pos.quantity - local_pos.quantity)
            if qty_diff > self._config.quantity_tolerance:
                broker_qty = broker_pos.quantity
                local_qty = local_pos.quantity
                if broker_qty > 0:
                    qty_diff_pct = float(qty_diff / broker_qty) * 100
                else:
                    qty_diff_pct = 100.0 if local_qty > 0 else 0.0

                severity = (
                    "critical" if qty_diff_pct > self._config.max_quantity_diff_pct
                    else "warning"
                )

                differences.append(
                    PositionDifference(
                        difference_type=DifferenceType.QUANTITY_MISMATCH,
                        symbol=symbol,
                        broker_quantity=broker_pos.quantity,
                        local_quantity=local_pos.quantity,
                        broker_avg_price=broker_pos.avg_entry_price,
                        local_avg_price=local_pos.avg_entry_price,
                        severity=severity,
                    )
                )

            # Check average entry price
            if (
                broker_pos.avg_entry_price is not None
                and local_pos.avg_entry_price is not None
                and broker_pos.avg_entry_price > 0
            ):
                price_diff_pct = abs(
                    float(
                        (broker_pos.avg_entry_price - local_pos.avg_entry_price)
                        / broker_pos.avg_entry_price
                    )
                ) * 100
                if price_diff_pct > self._config.price_tolerance_pct:
                    differences.append(
                        PositionDifference(
                            difference_type=DifferenceType.PRICE_MISMATCH,
                            symbol=symbol,
                            broker_avg_price=broker_pos.avg_entry_price,
                            local_avg_price=local_pos.avg_entry_price,
                            broker_quantity=broker_pos.quantity,
                            local_quantity=local_pos.quantity,
                            severity="warning",
                        )
                    )

        return differences

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def _reconcile_differences(
        self,
        differences: List[PositionDifference],
        local_map: Dict[Symbol, Position],
    ) -> int:
        """Reconcile detected differences by updating local positions.

        Returns the number of positions successfully reconciled.
        """
        reconciled = 0
        for diff in differences:
            try:
                reconciled += self._reconcile_single(diff, local_map)
            except Exception as e:
                logger.error(f"Failed to reconcile {diff.symbol}: {e}")
        self._total_reconciled += reconciled
        return reconciled

    def _reconcile_single(
        self,
        diff: PositionDifference,
        local_map: Dict[Symbol, Position],
    ) -> int:
        """Reconcile a single difference"""
        if diff.difference_type == DifferenceType.MISSING_LOCALLY:
            # Broker has position, local doesn't — nothing to update locally
            # (would need to create a new local position from broker data)
            logger.info(
                f"Position {diff.symbol.ticker} exists on broker but not locally"
            )
            return 0

        elif diff.difference_type == DifferenceType.MISSING_ON_BROKER:
            # Local has position, broker doesn't — remove local position
            if diff.symbol in local_map:
                logger.warning(
                    f"Removing stale local position: {diff.symbol.ticker}"
                )
                del local_map[diff.symbol]
                return 1
            return 0

        elif diff.difference_type == DifferenceType.QUANTITY_MISMATCH:
            # Update local quantity to match broker
            if diff.symbol in local_map and diff.broker_quantity is not None:
                local_pos = local_map[diff.symbol]
                old_qty = local_pos.quantity
                local_pos.quantity = diff.broker_quantity
                logger.warning(
                    f"Reconciled quantity for {diff.symbol.ticker}: "
                    f"{old_qty} -> {diff.broker_quantity}"
                )
                self._notify_reconciled(diff.symbol, local_pos)
                return 1
            return 0

        elif diff.difference_type == DifferenceType.PRICE_MISMATCH:
            # Update local avg entry price to match broker
            if diff.symbol in local_map and diff.broker_avg_price is not None:
                local_pos = local_map[diff.symbol]
                old_price = local_pos.avg_entry_price
                local_pos.avg_entry_price = diff.broker_avg_price
                logger.warning(
                    f"Reconciled avg price for {diff.symbol.ticker}: "
                    f"{old_price} -> {diff.broker_avg_price}"
                )
                self._notify_reconciled(diff.symbol, local_pos)
                return 1
            return 0

        elif diff.difference_type == DifferenceType.SIDE_MISMATCH:
            # Side mismatch is critical — update to match broker
            if diff.symbol in local_map and diff.broker_side is not None:
                local_pos = local_map[diff.symbol]
                old_side = local_pos.side
                local_pos.side = diff.broker_side
                logger.critical(
                    f"Reconciled side for {diff.symbol.ticker}: "
                    f"{old_side.name} -> {diff.broker_side.name}"
                )
                self._notify_reconciled(diff.symbol, local_pos)
                return 1
            return 0

        return 0

    # ------------------------------------------------------------------
    # History Management
    # ------------------------------------------------------------------

    def _add_to_history(self, result: SyncResult) -> None:
        """Add sync result to history with size management"""
        self._sync_history.append(result)
        if len(self._sync_history) > self._config.max_log_entries:
            self._sync_history.pop(0)

    def get_sync_history(
        self, limit: Optional[int] = None
    ) -> List[SyncResult]:
        """Get recent sync history entries"""
        history = self._sync_history
        if limit is not None:
            history = history[-limit:]
        return history.copy()

    def get_latest_result(self) -> Optional[SyncResult]:
        """Get the most recent sync result"""
        return self._sync_history[-1] if self._sync_history else None

    def clear_history(self) -> None:
        """Clear all sync history"""
        self._sync_history.clear()
        self._total_syncs = 0
        self._total_reconciled = 0
        logger.info("Sync history cleared")

    # ------------------------------------------------------------------
    # Callback Management
    # ------------------------------------------------------------------

    def _notify_difference(self, diff: PositionDifference) -> None:
        """Notify all difference callbacks"""
        for handler in self._on_difference:
            try:
                handler(diff)
            except Exception as e:
                logger.error(f"Error in difference handler: {e}")

    def _notify_reconciled(self, symbol: Symbol, position: Position) -> None:
        """Notify all reconciled callbacks"""
        for handler in self._on_reconciled:
            try:
                handler(symbol, position)
            except Exception as e:
                logger.error(f"Error in reconciled handler: {e}")

    def on_difference(
        self, handler: Callable[[PositionDifference], None]
    ) -> None:
        """Register a callback for detected differences"""
        self._on_difference.append(handler)

    def on_reconciled(
        self,
        handler: Callable[[Symbol, Position], None],
    ) -> None:
        """Register a callback for reconciled positions"""
        self._on_reconciled.append(handler)

    def on_sync_complete(
        self, handler: Callable[[SyncResult], None]
    ) -> None:
        """Register a callback for sync completion"""
        self._on_sync_complete.append(handler)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_statistics(self) -> Dict[str, Any]:
        """Get sync statistics summary"""
        total_diffs = sum(r.total_differences for r in self._sync_history)
        total_matched = sum(r.matched for r in self._sync_history)
        avg_duration = (
            sum(r.duration_ms for r in self._sync_history) / len(self._sync_history)
            if self._sync_history
            else 0.0
        )

        return {
            "total_syncs": self._total_syncs,
            "total_reconciled": self._total_reconciled,
            "total_differences_found": total_diffs,
            "total_positions_matched": total_matched,
            "average_duration_ms": round(avg_duration, 2),
            "last_sync_time": (
                self._last_sync_time.isoformat()
                if self._last_sync_time
                else None
            ),
            "history_entries": len(self._sync_history),
            "auto_reconcile_enabled": self._config.auto_reconcile,
        }


# ------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------


def build_broker_position_map(
    broker_positions: List[Position],
) -> Dict[Symbol, Position]:
    """Build a lookup map from broker position list"""
    return {p.symbol: p for p in broker_positions if p.symbol is not None}


def build_local_position_map(
    local_positions: List[Position],
) -> Dict[Symbol, Position]:
    """Build a lookup map from local position list"""
    return {p.symbol: p for p in local_positions if p.symbol is not None}