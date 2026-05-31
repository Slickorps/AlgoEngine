"""Tests for data synchronization management"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from src.data.sync_manager import (
    SequenceManager, DataSyncManager, DataPacket, SyncGap,
    SyncState, SyncStatus, SyncReporter, create_sync_manager,
    SequenceRange
)
from src.data.models import Symbol, Tick


class TestSequenceRange:
    """Test sequence range functionality"""
    
    def test_range_creation(self):
        """Test range initialization"""
        range_obj = SequenceRange(start=10, end=20)
        assert range_obj.start == 10
        assert range_obj.end == 20
        assert len(range_obj) == 11
    
    def test_range_contains(self):
        """Test range membership"""
        range_obj = SequenceRange(start=10, end=20)
        
        assert 15 in range_obj
        assert 10 in range_obj
        assert 20 in range_obj
        assert 5 not in range_obj
        assert 25 not in range_obj
    
    def test_range_overlaps(self):
        """Test range overlap detection"""
        range1 = SequenceRange(start=10, end=20)
        range2 = SequenceRange(start=15, end=25)
        range3 = SequenceRange(start=25, end=30)
        
        assert range1.overlaps(range2)
        assert range2.overlaps(range1)
        assert not range1.overlaps(range3)


class TestDataPacket:
    """Test data packet functionality"""
    
    @pytest.fixture
    def sample_tick(self):
        """Create sample tick"""
        return Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
    
    def test_packet_creation(self, sample_tick):
        """Test packet initialization"""
        packet = DataPacket(
            data=sample_tick,
            sequence_number=100,
            timestamp=datetime.now(),
            checksum="abc123",
            source="test_feed"
        )
        
        assert packet.sequence_number == 100
        assert packet.source == "test_feed"
        assert packet.retry_count == 0
    
    def test_checksum_calculation(self, sample_tick):
        """Test checksum calculation"""
        packet = DataPacket(
            data=sample_tick,
            sequence_number=100,
            timestamp=datetime.now(),
            checksum="",
            source="test"
        )
        
        # Calculate checksum
        checksum = packet._calculate_checksum()
        packet.checksum = checksum
        
        # Verify checksum
        assert packet.verify_checksum()
    
    def test_checksum_verification_failure(self, sample_tick):
        """Test checksum verification failure"""
        packet = DataPacket(
            data=sample_tick,
            sequence_number=100,
            timestamp=datetime.now(),
            checksum="wrong_checksum",
            source="test"
        )
        
        assert not packet.verify_checksum()
    
    def test_to_dict(self, sample_tick):
        """Test packet serialization"""
        packet = DataPacket(
            data=sample_tick,
            sequence_number=100,
            timestamp=datetime.now(),
            checksum="abc123",
            source="test"
        )
        
        data_dict = packet.to_dict()
        
        assert 'sequence_number' in data_dict
        assert 'checksum' in data_dict
        assert 'source' in data_dict
        assert data_dict['data_type'] == 'Tick'


class TestSyncGap:
    """Test sync gap functionality"""
    
    def test_gap_creation(self):
        """Test gap initialization"""
        gap = SyncGap(
            start_seq=10,
            end_seq=20,
            detected_at=datetime.now()
        )
        
        assert gap.start_seq == 10
        assert gap.end_seq == 20
        assert gap.retry_count == 0
        assert len(gap) == 11
    
    def test_gap_age(self):
        """Test gap age calculation"""
        gap = SyncGap(
            start_seq=10,
            end_seq=20,
            detected_at=datetime.now() - timedelta(seconds=5)
        )
        
        assert gap.age >= 5.0


class TestSyncStatus:
    """Test sync status functionality"""
    
    def test_status_creation(self):
        """Test status initialization"""
        status = SyncStatus(
            symbol=Symbol("AAPL"),
            state=SyncState.SYNCED,
            last_sequence=100,
            last_received=datetime.now(),
            gaps=[],
            total_received=100,
            total_missing=0,
            checksum_failures=0,
            avg_latency_ms=10.0
        )
        
        assert status.symbol.ticker == "AAPL"
        assert status.state == SyncState.SYNCED
    
    def test_status_to_dict(self):
        """Test status serialization"""
        status = SyncStatus(
            symbol=Symbol("AAPL"),
            state=SyncState.SYNCED,
            last_sequence=100,
            last_received=datetime.now(),
            gaps=[],
            total_received=100,
            total_missing=0,
            checksum_failures=0,
            avg_latency_ms=10.0
        )
        
        status_dict = status.to_dict()
        
        assert status_dict['symbol'] == "AAPL"
        assert status_dict['state'] == "SYNCED"
        assert status_dict['last_sequence'] == 100


class TestSequenceManager:
    """Test sequence manager functionality"""
    
    @pytest.fixture
    def seq_manager(self):
        """Create test sequence manager"""
        return SequenceManager(Symbol("AAPL"), window_size=100)
    
    def test_initialization(self, seq_manager):
        """Test manager initialization"""
        assert seq_manager.symbol.ticker == "AAPL"
        assert seq_manager.current_sequence == 0
        assert seq_manager.expected_sequence == 1
    
    def test_get_next_sequence(self, seq_manager):
        """Test getting next sequence"""
        seq1 = seq_manager.get_next_sequence()
        seq2 = seq_manager.get_next_sequence()
        seq3 = seq_manager.get_next_sequence()
        
        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3
        assert seq_manager.current_sequence == 3
    
    def test_record_received_in_order(self, seq_manager):
        """Test recording in-order sequences"""
        is_new, gap = seq_manager.record_received(1)
        assert is_new
        assert gap is None
        
        is_new, gap = seq_manager.record_received(2)
        assert is_new
        assert gap is None
        
        assert seq_manager.expected_sequence == 3
    
    def test_record_received_with_gap(self, seq_manager):
        """Test gap detection"""
        is_new, gap = seq_manager.record_received(1)
        assert gap is None
        
        # Skip sequence 2, receive 3
        is_new, gap = seq_manager.record_received(3)
        assert is_new
        assert gap is not None
        assert gap.start_seq == 2
        assert gap.end_seq == 2
    
    def test_record_duplicate(self, seq_manager):
        """Test duplicate detection"""
        seq_manager.record_received(1)
        is_new, gap = seq_manager.record_received(1)
        
        assert not is_new
        assert gap is None
    
    def test_get_gaps(self, seq_manager):
        """Test getting gaps"""
        seq_manager.record_received(1)
        seq_manager.record_received(3)
        seq_manager.record_received(5)
        
        gaps = seq_manager.get_gaps()
        assert len(gaps) == 2
        assert gaps[0].start_seq == 2
        assert gaps[1].start_seq == 4
    
    def test_get_missing_sequences(self, seq_manager):
        """Test getting missing sequences"""
        seq_manager.record_received(1)
        seq_manager.record_received(3)
        seq_manager.record_received(5)
        
        missing = seq_manager.get_missing_sequences()
        assert missing == [2, 4]
    
    def test_gap_resolution(self, seq_manager):
        """Test automatic gap resolution"""
        seq_manager.record_received(1)
        seq_manager.record_received(3)
        
        # Should have a gap
        assert len(seq_manager.get_gaps()) == 1
        
        # Fill the gap
        seq_manager.record_received(2)
        
        # Gap should be resolved
        assert len(seq_manager.get_gaps()) == 0
    
    def test_is_sequence_valid(self, seq_manager):
        """Test sequence validity checking"""
        # Initially any low sequence is valid
        assert seq_manager.is_sequence_valid(1)
        
        # Add many sequences
        for i in range(1, 150):
            seq_manager.record_received(i)
        
        # Old sequences should be invalid
        assert not seq_manager.is_sequence_valid(1)
        
        # Recent and future sequences should be valid
        assert seq_manager.is_sequence_valid(149)
        assert seq_manager.is_sequence_valid(200)
    
    def test_get_completion_rate(self, seq_manager):
        """Test completion rate calculation"""
        assert seq_manager.get_completion_rate() == 1.0
        
        seq_manager.record_received(1)
        seq_manager.record_received(3)
        
        # Received 2, expected 3 (missing #2)
        assert seq_manager.get_completion_rate() == 2/3
    
    def test_reset(self, seq_manager):
        """Test reset functionality"""
        seq_manager.record_received(1)
        seq_manager.record_received(2)
        seq_manager.record_received(3)
        
        assert seq_manager.current_sequence == 3
        assert len(seq_manager.get_gaps()) == 0
        
        seq_manager.reset()
        
        assert seq_manager.current_sequence == 0
        assert seq_manager.expected_sequence == 1
        assert len(seq_manager.get_gaps()) == 0


class TestDataSyncManager:
    """Test data sync manager functionality"""
    
    @pytest.fixture
    def sync_manager(self):
        """Create test sync manager"""
        return DataSyncManager(max_retries=3, retry_delay=0.1)
    
    @pytest.fixture
    def sample_tick(self):
        """Create sample tick"""
        return Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
    
    def test_initialization(self, sync_manager):
        """Test manager initialization"""
        assert sync_manager._max_retries == 3
        assert sync_manager._retry_delay == 0.1
    
    def test_register_symbol(self, sync_manager):
        """Test symbol registration"""
        symbol = Symbol("AAPL")
        seq_manager = sync_manager.register_symbol(symbol)
        
        assert seq_manager.symbol == symbol
        assert symbol in sync_manager._sequence_managers
        assert symbol in sync_manager._sync_status
    
    def test_unregister_symbol(self, sync_manager):
        """Test symbol unregistration"""
        symbol = Symbol("AAPL")
        sync_manager.register_symbol(symbol)
        
        sync_manager.unregister_symbol(symbol)
        
        assert symbol not in sync_manager._sequence_managers
        assert symbol not in sync_manager._sync_status
    
    def test_create_packet(self, sync_manager, sample_tick):
        """Test packet creation"""
        packet = sync_manager.create_packet(sample_tick, "test_feed")
        
        assert packet.data == sample_tick
        assert packet.source == "test_feed"
        assert packet.sequence_number == 1
        assert len(packet.checksum) > 0
    
    def test_receive_packet_success(self, sync_manager, sample_tick):
        """Test successful packet reception"""
        packet = sync_manager.create_packet(sample_tick, "test_feed")
        
        is_new, gap = sync_manager.receive_packet(packet)
        
        assert is_new
        assert gap is None
        
        status = sync_manager.get_sync_status(sample_tick.symbol)
        assert status['total_received'] == 1
    
    def test_receive_packet_checksum_failure(self, sync_manager, sample_tick):
        """Test packet reception with checksum failure"""
        packet = DataPacket(
            data=sample_tick,
            sequence_number=1,
            timestamp=datetime.now(),
            checksum="wrong_checksum",
            source="test"
        )
        
        is_new, gap = sync_manager.receive_packet(packet)
        
        assert not is_new
        
        status = sync_manager.get_sync_status(sample_tick.symbol)
        assert status['checksum_failures'] == 1
    
    def test_receive_packet_gap_detection(self, sync_manager, sample_tick):
        """Test gap detection on packet reception"""
        packet1 = sync_manager.create_packet(sample_tick, "test")
        sync_manager.receive_packet(packet1)
        
        # Create second packet with gap
        tick2 = Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("151.00"),
            ask_price=Decimal("151.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        packet3 = sync_manager.create_packet(tick2, "test")
        packet3.sequence_number = 3  # Skip sequence 2
        packet3.checksum = packet3._calculate_checksum()
        
        is_new, gap = sync_manager.receive_packet(packet3)
        
        assert is_new
        assert gap is not None
    
    def test_gap_handler(self, sync_manager, sample_tick):
        """Test gap handler callback"""
        handler_called = False
        received_symbol = None
        received_gap = None
        
        def gap_handler(symbol, gap):
            nonlocal handler_called, received_symbol, received_gap
            handler_called = True
            received_symbol = symbol
            received_gap = gap
        
        sync_manager.add_gap_handler(gap_handler)
        
        packet1 = sync_manager.create_packet(sample_tick, "test")
        sync_manager.receive_packet(packet1)
        
        # Create gap
        tick2 = Tick(
            symbol=Symbol("AAPL"),
            timestamp=datetime.now(),
            bid_price=Decimal("151.00"),
            ask_price=Decimal("151.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        packet3 = sync_manager.create_packet(tick2, "test")
        packet3.sequence_number = 3
        packet3.checksum = packet3._calculate_checksum()
        sync_manager.receive_packet(packet3)
        
        assert handler_called
        assert received_symbol == sample_tick.symbol
        assert received_gap.start_seq == 2
    
    def test_integrity_handler(self, sync_manager, sample_tick):
        """Test integrity handler callback"""
        successes = []
        
        def integrity_handler(packet, success):
            successes.append(success)
        
        sync_manager.add_integrity_handler(integrity_handler)
        
        # Successful packet
        packet1 = sync_manager.create_packet(sample_tick, "test")
        sync_manager.receive_packet(packet1)
        
        # Failed packet
        packet2 = DataPacket(
            data=sample_tick,
            sequence_number=2,
            timestamp=datetime.now(),
            checksum="wrong",
            source="test"
        )
        sync_manager.receive_packet(packet2)
        
        assert successes == [True, False]
    
    def test_get_statistics(self, sync_manager, sample_tick):
        """Test getting statistics"""
        packet = sync_manager.create_packet(sample_tick, "test")
        sync_manager.receive_packet(packet)
        
        stats = sync_manager.get_statistics()
        
        assert 'total_packets' in stats
        assert stats['total_packets'] == 1
        assert stats['symbols_tracked'] == 1
    
    def test_reset_symbol(self, sync_manager, sample_tick):
        """Test symbol reset"""
        packet = sync_manager.create_packet(sample_tick, "test")
        sync_manager.receive_packet(packet)
        
        status_before = sync_manager.get_sync_status(sample_tick.symbol)
        assert status_before['total_received'] == 1
        
        sync_manager.reset_symbol(sample_tick.symbol)
        
        status_after = sync_manager.get_sync_status(sample_tick.symbol)
        assert status_after['total_received'] == 0
        assert status_after['last_sequence'] == 0
    
    @pytest.mark.asyncio
    async def test_start_stop(self, sync_manager):
        """Test start and stop"""
        await sync_manager.start()
        assert sync_manager._running
        
        await sync_manager.stop()
        assert not sync_manager._running


class TestSyncReporter:
    """Test sync reporter functionality"""
    
    @pytest.fixture
    def reporter(self):
        """Create test reporter"""
        manager = DataSyncManager()
        return SyncReporter(manager)
    
    def test_generate_report(self, reporter):
        """Test report generation"""
        report = reporter.generate_report()
        
        assert 'timestamp' in report
        assert 'health_score' in report
        assert 'statistics' in report
        assert 'symbol_status' in report
        assert 'alerts' in report
    
    def test_generate_alerts(self, reporter):
        """Test alert generation"""
        # Register symbol with gaps
        symbol = Symbol("AAPL")
        reporter._sync_manager.register_symbol(symbol)
        
        # Create some gaps
        tick1 = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("150.00"),
            ask_price=Decimal("150.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        packet1 = reporter._sync_manager.create_packet(tick1, "test")
        reporter._sync_manager.receive_packet(packet1)
        
        # Create gap
        tick2 = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("151.00"),
            ask_price=Decimal("151.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        packet3 = reporter._sync_manager.create_packet(tick2, "test")
        packet3.sequence_number = 3
        packet3.checksum = packet3._calculate_checksum()
        reporter._sync_manager.receive_packet(packet3)
        
        # Force checksum failure
        bad_packet = DataPacket(
            data=tick1,
            sequence_number=4,
            timestamp=datetime.now(),
            checksum="bad",
            source="test"
        )
        reporter._sync_manager.receive_packet(bad_packet)
        
        report = reporter.generate_report()
        
        # Should have alerts for gaps and checksum failures
        assert len(report['alerts']) > 0
        
        gap_alert = next((a for a in report['alerts'] if 'gaps' in a.get('message', '')), None)
        assert gap_alert is not None


class TestFactory:
    """Test factory functions"""
    
    def test_create_sync_manager(self):
        """Test sync manager factory"""
        manager = create_sync_manager(
            max_retries=5,
            retry_delay=2.0,
            gap_timeout=60.0
        )
        
        assert manager._max_retries == 5
        assert manager._retry_delay == 2.0
        assert manager._gap_timeout == 60.0


class TestIntegration:
    """Integration tests"""
    
    @pytest.mark.asyncio
    async def test_full_sync_workflow(self):
        """Test complete synchronization workflow"""
        manager = DataSyncManager()
        
        # Set up gap handler
        detected_gaps = []
        
        def gap_handler(symbol, gap):
            detected_gaps.append((symbol, gap))
        
        manager.add_gap_handler(gap_handler)
        
        # Register symbol
        symbol = Symbol("AAPL")
        manager.register_symbol(symbol)
        
        # Simulate receiving packets with gaps
        for i in [1, 2, 4, 5, 7, 8, 9]:  # Missing 3 and 6
            tick = Tick(
                symbol=symbol,
                timestamp=datetime.now(),
                bid_price=Decimal(f"{150 + i}.00"),
                ask_price=Decimal(f"{150 + i}.05"),
                bid_size=Decimal("100"),
                ask_size=Decimal("100")
            )
            packet = manager.create_packet(tick, "test_feed")
            packet.sequence_number = i
            packet.checksum = packet._calculate_checksum()
            manager.receive_packet(packet)
        
        # Should have detected gaps
        assert len(detected_gaps) == 2
        
        # Check sync status
        status = manager.get_sync_status(symbol)
        assert status['gap_count'] == 2
        assert status['total_gaps'] == 2  # Missing sequences 3, 6 (2 gaps total = 1 + 1)
        
        # Fill one gap
        tick3 = Tick(
            symbol=symbol,
            timestamp=datetime.now(),
            bid_price=Decimal("153.00"),
            ask_price=Decimal("153.05"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100")
        )
        packet3 = manager.create_packet(tick3, "test_feed")
        packet3.sequence_number = 3
        packet3.checksum = packet3._calculate_checksum()
        manager.receive_packet(packet3)
        
        # Should have one gap remaining
        status = manager.get_sync_status(symbol)
        assert status['gap_count'] == 1
        
        # Get statistics
        stats = manager.get_statistics()
        assert stats['total_packets'] == 8
        assert stats['gaps_detected'] == 2


if __name__ == "__main__":
    pytest.main([__file__])
