"""Tests for data storage"""

import pytest

# Skip all tests if pyarrow not available
pytest.importorskip("pyarrow")

from datetime import datetime, timedelta
from decimal import Decimal

from src.data.models import Symbol, Bar, Tick, Resolution
from src.data.storage import DataStorage


class TestDataStorage:
    """Test DataStorage class"""
    
    @pytest.fixture
    def temp_storage(self, tmp_path):
        """Create temporary storage"""
        return DataStorage(data_dir=str(tmp_path / "data"))
    
    @pytest.fixture
    def sample_bars(self):
        """Create sample bar data"""
        symbol = Symbol(ticker="AAPL")
        bars = []
        base_date = datetime(2023, 1, 1)
        
        for i in range(10):
            bars.append(Bar(
                symbol=symbol,
                timestamp=base_date + timedelta(days=i),
                open=Decimal(str(150 + i)),
                high=Decimal(str(155 + i)),
                low=Decimal(str(148 + i)),
                close=Decimal(str(152 + i)),
                volume=Decimal(str(1000000 + i * 1000)),
                resolution=Resolution.DAILY
            ))
        
        return symbol, bars
    
    @pytest.fixture
    def sample_ticks(self):
        """Create sample tick data"""
        symbol = Symbol(ticker="AAPL")
        ticks = []
        base_time = datetime(2023, 1, 1, 9, 30, 0)
        
        for i in range(100):
            ticks.append(Tick(
                symbol=symbol,
                timestamp=base_time + timedelta(seconds=i),
                bid_price=Decimal("150.00") + Decimal(str(i * 0.01)),
                ask_price=Decimal("150.05") + Decimal(str(i * 0.01)),
                bid_size=Decimal("100"),
                ask_size=Decimal("200")
            ))
        
        return symbol, ticks
    
    def test_storage_initialization(self, temp_storage):
        """Test storage directory creation"""
        assert temp_storage._data_dir.exists()
        assert temp_storage._bar_dir.exists()
        assert temp_storage._tick_dir.exists()
    
    def test_save_and_load_bars(self, temp_storage, sample_bars):
        """Test saving and loading bars"""
        symbol, bars = sample_bars
        
        # Save
        temp_storage.save_bars(symbol, bars, Resolution.DAILY)
        
        # Load
        loaded = temp_storage.load_bars(symbol, Resolution.DAILY)
        
        assert len(loaded) == len(bars)
        assert loaded[0].open == bars[0].open
        assert loaded[0].close == bars[0].close
    
    def test_load_bars_with_date_range(self, temp_storage, sample_bars):
        """Test loading bars with date filter"""
        symbol, bars = sample_bars
        temp_storage.save_bars(symbol, bars, Resolution.DAILY)
        
        # Load subset
        start = datetime(2023, 1, 3)
        end = datetime(2023, 1, 7)
        loaded = temp_storage.load_bars(symbol, Resolution.DAILY, start, end)
        
        assert len(loaded) == 5  # Jan 3, 4, 5, 6, 7
    
    def test_load_nonexistent_data(self, temp_storage):
        """Test loading data that doesn't exist"""
        symbol = Symbol(ticker="NONEXISTENT")
        loaded = temp_storage.load_bars(symbol, Resolution.DAILY)
        
        assert loaded == []
    
    def test_metadata_tracking(self, temp_storage, sample_bars):
        """Test metadata tracking"""
        symbol, bars = sample_bars
        temp_storage.save_bars(symbol, bars, Resolution.DAILY)
        
        metadata = temp_storage.get_data_availability(symbol, Resolution.DAILY)
        
        assert metadata is not None
        assert metadata['record_count'] == len(bars)
        assert metadata['start_date'] == bars[0].timestamp
        assert metadata['end_date'] == bars[-1].timestamp
    
    def test_list_available_symbols(self, temp_storage, sample_bars):
        """Test listing available symbols"""
        symbol, bars = sample_bars
        temp_storage.save_bars(symbol, bars, Resolution.DAILY)
        
        symbols = temp_storage.list_available_symbols()
        
        assert len(symbols) == 1
        assert "AAPL" in symbols[0]
    
    def test_load_bars_cache_miss_file_not_exists(self, temp_storage):
        """Test load_bars returns empty when file doesn't exist"""
        symbol = Symbol(ticker="XYZ")
        bars = temp_storage.load_bars(symbol, Resolution.DAILY)
        
        assert bars == []
    
    def test_load_bars_cache_hit_after_save(self, temp_storage, sample_bars):
        """Test load_bars returns data after save"""
        symbol, bars = sample_bars
        temp_storage.save_bars(symbol, bars, Resolution.DAILY)
        
        loaded = temp_storage.load_bars(symbol, Resolution.DAILY)
        
        assert len(loaded) == len(bars)
        for orig, loaded_bar in zip(bars, loaded):
            assert loaded_bar.open == orig.open
            assert loaded_bar.close == orig.close
    
    def test_save_bars_creates_directory(self, tmp_path):
        """Test save_bars creates the bar directory if it doesn't exist"""
        data_dir = tmp_path / "nested" / "data"
        storage = DataStorage(data_dir=str(data_dir))
        symbol = Symbol(ticker="TEST")
        bars = [
            Bar(
                symbol=symbol,
                timestamp=datetime(2023, 1, 1),
                open=Decimal("10"),
                high=Decimal("12"),
                low=Decimal("9"),
                close=Decimal("11"),
                volume=Decimal("1000"),
                resolution=Resolution.DAILY
            )
        ]
        
        storage.save_bars(symbol, bars, Resolution.DAILY)
        
        assert storage._bar_dir.exists()
        bar_file = storage._get_bar_path(symbol, Resolution.DAILY)
        assert bar_file.exists()
    
    def test_bar_path_building(self, temp_storage):
        """Test _get_bar_path returns correct path"""
        symbol = Symbol(ticker="AAPL")
        path = temp_storage._get_bar_path(symbol, Resolution.DAILY)
        
        assert path.parent == temp_storage._bar_dir
        assert "AAPL" in str(path)
        assert "daily" in str(path)
    
    def test_tick_path_building(self, temp_storage):
        """Test _get_tick_path returns correct path"""
        symbol = Symbol(ticker="AAPL")
        date = datetime(2023, 6, 15)
        path = temp_storage._get_tick_path(symbol, date)
        
        assert path.parent == temp_storage._tick_dir
        assert "AAPL" in str(path)
        assert "20230615" in str(path)
