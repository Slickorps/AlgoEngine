"""Data synchronization management with sequence tracking and integrity checking"""

import asyncio
import hashlib
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Dict, List, Optional, Set, Tuple, Callable, Any
from threading import Lock

from .models import Symbol, MarketData
from ..utils.logger import get_logger

logger = get_logger("data.sync")


class SyncState(Enum):
    """Data synchronization states"""
    SYNCED = auto()
    SYNCING = auto()
    OUT_OF_SYNC = auto()
    DISCONNECTED = auto()
    ERROR = auto()


@dataclass
class SequenceRange:
    """Represents a range of sequence numbers"""
    start: int
    end: int
    
    def __contains__(self, seq: int) -> bool:
        return self.start <= seq <= self.end
    
    def __len__(self) -> int:
        return self.end - self.start + 1
    
    def overlaps(self, other: 'SequenceRange') -> bool:
        return not (self.end < other.start or self.start > other.end)


@dataclass
class DataPacket:
    """Wrapper for market data with sync metadata"""
    data: MarketData
    sequence_number: int
    timestamp: datetime
    checksum: str
    source: str
    retry_count: int = 0
    
    def verify_checksum(self) -> bool:
        """Verify data integrity using checksum"""
        calculated = self._calculate_checksum()
        return calculated == self.checksum
    
    def _calculate_checksum(self) -> str:
        """Calculate checksum for data"""
        data_str = f"{self.data.symbol.ticker}:{self.data.timestamp.isoformat()}:{self.sequence_number}"
        return hashlib.md5(data_str.encode()).hexdigest()[:8]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'sequence_number': self.sequence_number,
            'timestamp': self.timestamp.isoformat(),
            'checksum': self.checksum,
            'source': self.source,
            'retry_count': self.retry_count,
            'data_type': type(self.data).__name__,
            'symbol': self.data.symbol.ticker
        }


@dataclass
class SyncGap:
    """Represents a gap in sequence numbers"""
    start_seq: int
    end_seq: int
    detected_at: datetime
    retry_count: int = 0
    
    def __len__(self) -> int:
        return self.end_seq - self.start_seq + 1
    
    @property
    def age(self) -> float:
        """Get age of gap in seconds"""
        return (datetime.now() - self.detected_at).total_seconds()


@dataclass
class SyncStatus:
    """Current synchronization status"""
    symbol: Symbol
    state: SyncState
    last_sequence: int
    last_received: Optional[datetime]
    gaps: List[SyncGap]
    total_received: int
    total_missing: int
    checksum_failures: int
    avg_latency_ms: float
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol.ticker,
            'state': self.state.name,
            'last_sequence': self.last_sequence,
            'last_received': self.last_received.isoformat() if self.last_received else None,
            'gap_count': len(self.gaps),
            'total_gaps': sum(len(g) for g in self.gaps),
            'total_received': self.total_received,
            'total_missing': self.total_missing,
            'checksum_failures': self.checksum_failures,
            'avg_latency_ms': self.avg_latency_ms
        }


class SequenceManager:
    """Manages sequence numbers for data synchronization"""
    
    def __init__(self, symbol: Symbol, window_size: int = 10000):
        self._symbol = symbol
        self._window_size = window_size
        self._current_seq = 0
        self._received_sequences: Set[int] = set()
        self._sequence_window: deque = deque(maxlen=window_size)
        self._gaps: List[SyncGap] = []
        self._lock = Lock()
        
        # Track expected next sequence
        self._expected_next: int = 1
    
    @property
    def symbol(self) -> Symbol:
        return self._symbol
    
    @property
    def current_sequence(self) -> int:
        with self._lock:
            return self._current_seq
    
    @property
    def expected_sequence(self) -> int:
        with self._lock:
            return self._expected_next
    
    def get_next_sequence(self) -> int:
        """Get next sequence number to assign"""
        with self._lock:
            self._current_seq += 1
            return self._current_seq
    
    def record_received(self, sequence: int) -> Tuple[bool, Optional[SyncGap]]:
        """Record a received sequence number and detect gaps"""
        with self._lock:
            # Check for duplicates
            if sequence in self._received_sequences:
                logger.warning(f"Duplicate sequence {sequence} for {self._symbol.ticker}")
                return False, None
            
            # Add to received set
            self._received_sequences.add(sequence)
            self._sequence_window.append(sequence)
            
            # Track current sequence as max received
            if sequence > self._current_seq:
                self._current_seq = sequence
            
            # Detect gap
            gap = None
            if sequence > self._expected_next:
                # We have a gap
                gap = SyncGap(
                    start_seq=self._expected_next,
                    end_seq=sequence - 1,
                    detected_at=datetime.now()
                )
                self._gaps.append(gap)
                logger.warning(f"Gap detected for {self._symbol.ticker}: "
                             f"expected {self._expected_next}, got {sequence}")
            
            # Update expected next
            if sequence >= self._expected_next:
                self._expected_next = sequence + 1
            
            # Check if any gaps can be resolved (even for out-of-order delivery)
            self._resolve_gaps()
            
            return True, gap
    
    def _resolve_gaps(self) -> None:
        """Resolve gaps that have been filled"""
        unresolved = []
        for gap in self._gaps:
            # Check if all sequences in gap are now received
            all_received = all(
                seq in self._received_sequences
                for seq in range(gap.start_seq, gap.end_seq + 1)
            )
            
            if not all_received:
                unresolved.append(gap)
        
        if len(unresolved) != len(self._gaps):
            logger.info(f"Resolved {len(self._gaps) - len(unresolved)} gaps for "
                       f"{self._symbol.ticker}")
        
        self._gaps = unresolved
    
    def get_gaps(self) -> List[SyncGap]:
        """Get current gaps"""
        with self._lock:
            return list(self._gaps)
    
    def get_missing_sequences(self) -> List[int]:
        """Get list of missing sequence numbers"""
        with self._lock:
            missing = []
            for gap in self._gaps:
                for seq in range(gap.start_seq, gap.end_seq + 1):
                    if seq not in self._received_sequences:
                        missing.append(seq)
            return missing
    
    def is_sequence_valid(self, sequence: int) -> bool:
        """Check if sequence number is within valid window"""
        with self._lock:
            # Allow some tolerance for out-of-order delivery
            min_valid = max(1, self._current_seq - self._window_size)
            return min_valid <= sequence <= self._current_seq + 1000
    
    def get_completion_rate(self) -> float:
        """Get data completion rate"""
        with self._lock:
            if self._expected_next <= 1:
                return 1.0
            
            received = len(self._received_sequences)
            expected = self._expected_next - 1
            return received / expected if expected > 0 else 1.0
    
    def reset(self) -> None:
        """Reset sequence tracking"""
        with self._lock:
            self._current_seq = 0
            self._expected_next = 1
            self._received_sequences.clear()
            self._sequence_window.clear()
            self._gaps.clear()


class DataSyncManager:
    """Main data synchronization manager"""
    
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        gap_timeout: float = 30.0
    ):
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._gap_timeout = gap_timeout
        
        # Sequence managers per symbol
        self._sequence_managers: Dict[Symbol, SequenceManager] = {}
        
        # Packet buffer for retransmission
        self._packet_buffer: Dict[int, DataPacket] = {}
        self._buffer_size = 10000
        
        # Sync status per symbol
        self._sync_status: Dict[Symbol, SyncStatus] = {}
        
        # Callbacks
        self._gap_handlers: List[Callable[[Symbol, SyncGap], None]] = []
        self._integrity_handlers: List[Callable[[DataPacket, bool], None]] = []
        
        # Statistics
        self._total_packets = 0
        self._checksum_failures = 0
        self._gaps_detected = 0
        self._gaps_resolved = 0
        
        # Thread safety
        self._lock = Lock()
        
        # Async tasks
        self._running = False
        self._maintenance_task: Optional[asyncio.Task] = None
        
        # Latency tracking
        self._latency_samples: Dict[Symbol, deque] = defaultdict(lambda: deque(maxlen=100))
    
    def register_symbol(self, symbol: Symbol, window_size: int = 10000) -> SequenceManager:
        """Register a symbol for synchronization"""
        with self._lock:
            if symbol not in self._sequence_managers:
                self._sequence_managers[symbol] = SequenceManager(symbol, window_size)
                self._sync_status[symbol] = SyncStatus(
                    symbol=symbol,
                    state=SyncState.DISCONNECTED,
                    last_sequence=0,
                    last_received=None,
                    gaps=[],
                    total_received=0,
                    total_missing=0,
                    checksum_failures=0,
                    avg_latency_ms=0.0
                )
                logger.info(f"Registered symbol for sync: {symbol.ticker}")
            
            return self._sequence_managers[symbol]
    
    def unregister_symbol(self, symbol: Symbol) -> None:
        """Unregister a symbol from synchronization"""
        with self._lock:
            if symbol in self._sequence_managers:
                del self._sequence_managers[symbol]
                del self._sync_status[symbol]
                if symbol in self._latency_samples:
                    del self._latency_samples[symbol]
                logger.info(f"Unregistered symbol from sync: {symbol.ticker}")
    
    def create_packet(
        self,
        data: MarketData,
        source: str,
        sequence: Optional[int] = None
    ) -> DataPacket:
        """Create a data packet with sync metadata"""
        symbol = data.symbol
        
        # Get or create sequence manager
        if symbol not in self._sequence_managers:
            self.register_symbol(symbol)
        
        seq_manager = self._sequence_managers[symbol]
        
        if sequence is None:
            sequence = seq_manager.get_next_sequence()
        
        packet = DataPacket(
            data=data,
            sequence_number=sequence,
            timestamp=datetime.now(),
            checksum="",  # Will be calculated
            source=source
        )
        
        # Calculate checksum
        packet.checksum = packet._calculate_checksum()
        
        # Store in buffer
        with self._lock:
            self._packet_buffer[sequence] = packet
            
            # Trim buffer if too large
            if len(self._packet_buffer) > self._buffer_size:
                oldest = min(self._packet_buffer.keys())
                del self._packet_buffer[oldest]
        
        return packet
    
    def receive_packet(self, packet: DataPacket) -> Tuple[bool, Optional[SyncGap]]:
        """Process a received packet"""
        symbol = packet.data.symbol
        
        # Get or create sequence manager
        if symbol not in self._sequence_managers:
            self.register_symbol(symbol)
        
        seq_manager = self._sequence_managers[symbol]
        status = self._sync_status[symbol]
        
        # Verify checksum
        if not packet.verify_checksum():
            logger.error(f"Checksum verification failed for {symbol.ticker} "
                        f"seq={packet.sequence_number}")
            status.checksum_failures += 1
            self._checksum_failures += 1
            
            # Notify integrity handlers
            for handler in self._integrity_handlers:
                try:
                    handler(packet, False)
                except Exception as e:
                    logger.error(f"Error in integrity handler: {e}")
            
            return False, None
        
        # Record sequence
        is_new, gap = seq_manager.record_received(packet.sequence_number)
        
        if is_new:
            # Update status
            status.total_received += 1
            status.last_sequence = packet.sequence_number
            status.last_received = datetime.now()
            
            # Calculate latency
            latency_ms = (status.last_received - packet.timestamp).total_seconds() * 1000
            self._latency_samples[symbol].append(latency_ms)
            
            # Update average latency
            if self._latency_samples[symbol]:
                status.avg_latency_ms = sum(self._latency_samples[symbol]) / len(self._latency_samples[symbol])
            
            # Update sync state
            if seq_manager.get_gaps():
                status.state = SyncState.OUT_OF_SYNC
            else:
                status.state = SyncState.SYNCED
            
            status.gaps = seq_manager.get_gaps()
            status.total_missing = sum(len(g) for g in status.gaps)
            
            self._total_packets += 1
            
            # Notify integrity handlers
            for handler in self._integrity_handlers:
                try:
                    handler(packet, True)
                except Exception as e:
                    logger.error(f"Error in integrity handler: {e}")
            
            # Handle gap detection
            if gap:
                self._gaps_detected += 1
                logger.warning(f"Gap detected: {gap.start_seq}-{gap.end_seq} "
                             f"for {symbol.ticker}")
                
                # Notify gap handlers
                for handler in self._gap_handlers:
                    try:
                        handler(symbol, gap)
                    except Exception as e:
                        logger.error(f"Error in gap handler: {e}")
        
        return is_new, gap
    
    def request_retransmission(self, symbol: Symbol, start_seq: int, end_seq: int) -> bool:
        """Request retransmission of missing sequences"""
        logger.info(f"Requesting retransmission for {symbol.ticker}: "
                   f"{start_seq}-{end_seq}")
        
        # This would typically send a request to the data source
        # For now, just log the request
        # In a real implementation, this would send a message via WebSocket
        
        return True
    
    def get_missing_packets(self, symbol: Symbol) -> List[DataPacket]:
        """Get buffered packets for missing sequences"""
        with self._lock:
            if symbol not in self._sequence_managers:
                return []
            
            missing_seqs = self._sequence_managers[symbol].get_missing_sequences()
            packets = []
            
            for seq in missing_seqs:
                if seq in self._packet_buffer:
                    packet = self._packet_buffer[seq]
                    packet.retry_count += 1
                    packets.append(packet)
            
            return packets
    
    def add_gap_handler(self, handler: Callable[[Symbol, SyncGap], None]) -> None:
        """Add handler for gap detection"""
        self._gap_handlers.append(handler)
    
    def add_integrity_handler(self, handler: Callable[[DataPacket, bool], None]) -> None:
        """Add handler for integrity verification"""
        self._integrity_handlers.append(handler)
    
    def get_sync_status(self, symbol: Optional[Symbol] = None) -> Dict[str, Any]:
        """Get synchronization status"""
        with self._lock:
            if symbol:
                if symbol in self._sync_status:
                    return self._sync_status[symbol].to_dict()
                return {}
            
            return {
                sym.ticker: status.to_dict()
                for sym, status in self._sync_status.items()
            }
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get overall synchronization statistics"""
        with self._lock:
            return {
                'total_packets': self._total_packets,
                'checksum_failures': self._checksum_failures,
                'gaps_detected': self._gaps_detected,
                'gaps_resolved': self._gaps_resolved,
                'symbols_tracked': len(self._sequence_managers),
                'buffer_size': len(self._packet_buffer),
                'completion_rates': {
                    sym.ticker: seq_mgr.get_completion_rate()
                    for sym, seq_mgr in self._sequence_managers.items()
                }
            }
    
    def reset_symbol(self, symbol: Symbol) -> None:
        """Reset synchronization state for a symbol"""
        with self._lock:
            if symbol in self._sequence_managers:
                self._sequence_managers[symbol].reset()
            
            if symbol in self._sync_status:
                self._sync_status[symbol] = SyncStatus(
                    symbol=symbol,
                    state=SyncState.DISCONNECTED,
                    last_sequence=0,
                    last_received=None,
                    gaps=[],
                    total_received=0,
                    total_missing=0,
                    checksum_failures=0,
                    avg_latency_ms=0.0
                )
        
        logger.info(f"Reset sync state for {symbol.ticker}")
    
    async def start(self) -> None:
        """Start synchronization manager"""
        self._running = True
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        logger.info("Data sync manager started")
    
    async def stop(self) -> None:
        """Stop synchronization manager"""
        self._running = False
        
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Data sync manager stopped")
    
    async def _maintenance_loop(self) -> None:
        """Periodic maintenance tasks"""
        while self._running:
            try:
                await self._check_gaps()
                await asyncio.sleep(10)  # Check every 10 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}")
    
    async def _check_gaps(self) -> None:
        """Check and handle stale gaps"""
        with self._lock:
            for symbol, status in self._sync_status.items():
                for gap in list(status.gaps):
                    if gap.age > self._gap_timeout:
                        if gap.retry_count < self._max_retries:
                            # Request retransmission
                            self.request_retransmission(symbol, gap.start_seq, gap.end_seq)
                            gap.retry_count += 1
                        else:
                            # Mark as unresolvable
                            logger.error(f"Gap unresolvable after {self._max_retries} "
                                       f"retries: {symbol.ticker} {gap.start_seq}-{gap.end_seq}")


class SyncReporter:
    """Generate synchronization reports"""
    
    def __init__(self, sync_manager: DataSyncManager):
        self._sync_manager = sync_manager
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate comprehensive sync report"""
        stats = self._sync_manager.get_statistics()
        status = self._sync_manager.get_sync_status()
        
        # Calculate health score
        total_symbols = len(status)
        synced_symbols = sum(
            1 for s in status.values()
            if s.get('state') == 'SYNCED'
        )
        
        health_score = (synced_symbols / total_symbols * 100) if total_symbols > 0 else 100
        
        return {
            'timestamp': datetime.now().isoformat(),
            'health_score': health_score,
            'statistics': stats,
            'symbol_status': status,
            'alerts': self._generate_alerts(status)
        }
    
    def _generate_alerts(self, status: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate alerts from status"""
        alerts = []
        
        for symbol, sym_status in status.items():
            # Check for gaps
            if sym_status.get('gap_count', 0) > 0:
                alerts.append({
                    'level': 'WARNING',
                    'symbol': symbol,
                    'message': f"{sym_status['gap_count']} gaps detected",
                    'gaps': sym_status.get('total_gaps', 0)
                })
            
            # Check for checksum failures
            if sym_status.get('checksum_failures', 0) > 0:
                alerts.append({
                    'level': 'ERROR',
                    'symbol': symbol,
                    'message': f"{sym_status['checksum_failures']} checksum failures",
                    'failures': sym_status['checksum_failures']
                })
            
            # Check for high latency
            if sym_status.get('avg_latency_ms', 0) > 1000:
                alerts.append({
                    'level': 'WARNING',
                    'symbol': symbol,
                    'message': f"High latency: {sym_status['avg_latency_ms']:.1f}ms",
                    'latency_ms': sym_status['avg_latency_ms']
                })
        
        return alerts


# Factory function
def create_sync_manager(
    max_retries: int = 3,
    retry_delay: float = 1.0,
    gap_timeout: float = 30.0
) -> DataSyncManager:
    """Create a configured data sync manager"""
    return DataSyncManager(
        max_retries=max_retries,
        retry_delay=retry_delay,
        gap_timeout=gap_timeout
    )
