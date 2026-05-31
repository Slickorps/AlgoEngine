"""Historical data storage for AlgoEngine"""

import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Dict, Any
import pandas as pd

# Optional pyarrow import - fallback to CSV if not available
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

from .models import Symbol, Bar, Tick, Resolution, DataType
from ..utils.logger import get_logger
from ..utils.config import get_config

logger = get_logger("data.storage")


class DataStorage:
    """Historical data storage manager"""
    
    def __init__(self, data_dir: Optional[str] = None) -> None:
        config = get_config()
        self._data_dir = Path(data_dir or config.data.data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        # Subdirectories for different data types
        self._tick_dir = self._data_dir / "ticks"
        self._bar_dir = self._data_dir / "bars"
        self._metadata_file = self._data_dir / "metadata.db"
        
        self._tick_dir.mkdir(exist_ok=True)
        self._bar_dir.mkdir(exist_ok=True)
        
        # Initialize metadata database
        self._init_metadata_db()
        
    def _init_metadata_db(self) -> None:
        """Initialize SQLite metadata database"""
        with sqlite3.connect(self._metadata_file) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS data_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    resolution TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    record_count INTEGER,
                    file_path TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(symbol, data_type, resolution)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_info (
                    symbol TEXT PRIMARY KEY,
                    security_type TEXT,
                    exchange TEXT,
                    currency TEXT,
                    name TEXT,
                    sector TEXT,
                    industry TEXT,
                    first_seen TEXT,
                    last_updated TEXT
                )
            """)
            conn.commit()
    
    def _get_bar_path(self, symbol: Symbol, resolution: Resolution) -> Path:
        """Get storage path for bar data"""
        ext = "parquet" if HAS_PYARROW else "csv"
        filename = f"{symbol.ticker}_{resolution.value}.{ext}"
        return self._bar_dir / filename
    
    def _get_tick_path(self, symbol: Symbol, date: datetime) -> Path:
        """Get storage path for tick data"""
        ext = "parquet" if HAS_PYARROW else "csv"
        filename = f"{symbol.ticker}_{date.strftime('%Y%m%d')}.{ext}"
        return self._tick_dir / filename
    
    def save_bars(
        self,
        symbol: Symbol,
        bars: List[Bar],
        resolution: Resolution = Resolution.DAILY
    ) -> None:
        """Save bar data to storage"""
        if not bars:
            return
        
        file_path = self._get_bar_path(symbol, resolution)
        
        # Convert to DataFrame
        data = []
        for bar in bars:
            data.append({
                'timestamp': bar.timestamp.isoformat(),
                'open': float(bar.open),
                'high': float(bar.high),
                'low': float(bar.low),
                'close': float(bar.close),
                'volume': float(bar.volume)
            })
        
        df = pd.DataFrame(data)
        
        # Save to Parquet with compression (if available) or CSV
        if HAS_PYARROW:
            table = pa.Table.from_pandas(df)
            pq.write_table(table, file_path, compression='zstd')
        else:
            df.to_csv(file_path, index=False)
        
        # Update metadata
        start_date = min(b.timestamp for b in bars)
        end_date = max(b.timestamp for b in bars)
        self._update_metadata(
            symbol, DataType.BAR, resolution,
            start_date, end_date, len(bars), str(file_path)
        )
        
        logger.debug(f"Saved {len(bars)} bars for {symbol.ticker} to {file_path}")
    
    def load_bars(
        self,
        symbol: Symbol,
        resolution: Resolution = Resolution.DAILY,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None
    ) -> List[Bar]:
        """Load bar data from storage"""
        file_path = self._get_bar_path(symbol, resolution)
        
        if not file_path.exists():
            # Try alternative extension
            alt_ext = "csv" if HAS_PYARROW else "parquet"
            alt_path = file_path.with_suffix(f".{alt_ext}")
            if alt_path.exists():
                file_path = alt_path
            else:
                return []
        
        try:
            # Load from Parquet or CSV
            if HAS_PYARROW and file_path.suffix == '.parquet':
                table = pq.read_table(file_path)
                df = table.to_pandas()
            else:
                df = pd.read_csv(file_path)
            
            # Filter by date range
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            if start:
                df = df[df['timestamp'] >= start]
            if end:
                df = df[df['timestamp'] <= end]
            
            # Convert back to Bar objects
            bars = []
            for _, row in df.iterrows():
                bars.append(Bar(
                    symbol=symbol,
                    timestamp=row['timestamp'].to_pydatetime(),
                    open=Decimal(str(row['open'])),
                    high=Decimal(str(row['high'])),
                    low=Decimal(str(row['low'])),
                    close=Decimal(str(row['close'])),
                    volume=Decimal(str(row['volume'])),
                    resolution=resolution
                ))
            
            logger.debug(f"Loaded {len(bars)} bars for {symbol.ticker}")
            return bars
            
        except Exception as e:
            logger.error(f"Error loading bars for {symbol.ticker}: {e}")
            return []
    
    def save_ticks(self, symbol: Symbol, ticks: List[Tick], date: datetime) -> None:
        """Save tick data for a specific date"""
        if not ticks:
            return
        
        file_path = self._get_tick_path(symbol, date)
        
        data = []
        for tick in ticks:
            data.append({
                'timestamp': tick.timestamp.isoformat(),
                'bid_price': float(tick.bid_price),
                'ask_price': float(tick.ask_price),
                'bid_size': float(tick.bid_size),
                'ask_size': float(tick.ask_size),
                'last_price': float(tick.last_price) if tick.last_price else None,
                'last_size': float(tick.last_size) if tick.last_size else None
            })
        
        df = pd.DataFrame(data)
        if HAS_PYARROW:
            table = pa.Table.from_pandas(df)
            pq.write_table(table, file_path, compression='zstd')
        else:
            df.to_csv(file_path, index=False)
        
        logger.debug(f"Saved {len(ticks)} ticks for {symbol.ticker} on {date.date()}")
    
    def load_ticks(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime
    ) -> List[Tick]:
        """Load tick data for date range"""
        ticks = []
        
        current_date = start.date()
        end_date = end.date()
        
        while current_date <= end_date:
            file_path = self._get_tick_path(symbol, datetime.combine(current_date, datetime.min.time()))
            
            # Try alternative extension if primary doesn't exist
            if not file_path.exists():
                alt_ext = "csv" if HAS_PYARROW else "parquet"
                alt_path = file_path.with_suffix(f".{alt_ext}")
                if alt_path.exists():
                    file_path = alt_path
                else:
                    current_date += timedelta(days=1)
                    continue
            
            if file_path.exists():
                try:
                    # Load from Parquet or CSV
                    if HAS_PYARROW and file_path.suffix == '.parquet':
                        table = pq.read_table(file_path)
                        df = table.to_pandas()
                    else:
                        df = pd.read_csv(file_path)
                    
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    
                    # Filter time range on first and last day
                    day_start = datetime.combine(current_date, datetime.min.time())
                    day_end = datetime.combine(current_date, datetime.max.time())
                    
                    if current_date == start.date():
                        day_start = start
                    if current_date == end.date():
                        day_end = end
                    
                    df = df[(df['timestamp'] >= day_start) & (df['timestamp'] <= day_end)]
                    
                    for _, row in df.iterrows():
                        ticks.append(Tick(
                            symbol=symbol,
                            timestamp=row['timestamp'].to_pydatetime(),
                            bid_price=Decimal(str(row['bid_price'])),
                            ask_price=Decimal(str(row['ask_price'])),
                            bid_size=Decimal(str(row['bid_size'])),
                            ask_size=Decimal(str(row['ask_size'])),
                            last_price=Decimal(str(row['last_price'])) if pd.notna(row['last_price']) else None,
                            last_size=Decimal(str(row['last_size'])) if pd.notna(row['last_size']) else None
                        ))
                        
                except Exception as e:
                    logger.error(f"Error loading ticks for {symbol.ticker} on {current_date}: {e}")
            
            current_date += timedelta(days=1)
        
        logger.debug(f"Loaded {len(ticks)} ticks for {symbol.ticker}")
        return ticks
    
    def _update_metadata(
        self,
        symbol: Symbol,
        data_type: DataType,
        resolution: Optional[Resolution],
        start_date: datetime,
        end_date: datetime,
        record_count: int,
        file_path: str
    ) -> None:
        """Update data metadata in database"""
        now = datetime.now().isoformat()
        
        with sqlite3.connect(self._metadata_file) as conn:
            conn.execute("""
                INSERT INTO data_metadata 
                (symbol, data_type, resolution, start_date, end_date, record_count, file_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, data_type, resolution) DO UPDATE SET
                    end_date = excluded.end_date,
                    record_count = excluded.record_count,
                    updated_at = excluded.updated_at
            """, (
                str(symbol), data_type.name, resolution.value if resolution else None,
                start_date.isoformat(), end_date.isoformat(),
                record_count, file_path, now, now
            ))
            conn.commit()
    
    def get_data_availability(
        self,
        symbol: Symbol,
        data_type: DataType,
        resolution: Optional[Resolution] = None
    ) -> Optional[Dict[str, Any]]:
        """Check data availability for symbol"""
        with sqlite3.connect(self._metadata_file) as conn:
            cursor = conn.execute("""
                SELECT start_date, end_date, record_count, file_path
                FROM data_metadata
                WHERE symbol = ? AND data_type = ? AND (resolution = ? OR ? IS NULL)
            """, (str(symbol), data_type.name, resolution.value if resolution else None, resolution.value if resolution else None))
            
            row = cursor.fetchone()
            if row:
                return {
                    'start_date': datetime.fromisoformat(row[0]),
                    'end_date': datetime.fromisoformat(row[1]),
                    'record_count': row[2],
                    'file_path': row[3]
                }
            return None
    
    def list_available_symbols(self, data_type: Optional[DataType] = None) -> List[str]:
        """List all symbols with available data"""
        with sqlite3.connect(self._metadata_file) as conn:
            if data_type:
                cursor = conn.execute(
                    "SELECT DISTINCT symbol FROM data_metadata WHERE data_type = ?",
                    (data_type.name,)
                )
            else:
                cursor = conn.execute("SELECT DISTINCT symbol FROM data_metadata")
            
            return [row[0] for row in cursor.fetchall()]
