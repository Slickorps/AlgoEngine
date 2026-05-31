"""Yahoo Finance data adapter for AlgoEngine"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
import pandas as pd
import yfinance as yf

from ..data.feed import DataFeed
from ..data.models import Symbol, Bar, Resolution
from ..data.storage import DataStorage
from ..utils.logger import get_logger

logger = get_logger("adapters.yahoo")


class YahooFinanceAdapter(DataFeed):
    """Yahoo Finance data adapter"""
    
    def __init__(self, cache_data: bool = True) -> None:
        super().__init__("YahooFinance")
        self._connected: bool = False
        self._storage: Optional[DataStorage] = DataStorage() if cache_data else None
        self._rate_limit_delay: float = 0.2  # 200ms between requests
        
    def _symbol_to_ticker(self, symbol: Symbol) -> str:
        """Convert Symbol to Yahoo ticker format"""
        # Handle different security types
        if symbol.security_type == "CRYPTO":
            return f"{symbol.ticker}-USD"
        elif symbol.security_type == "FOREX":
            return f"{symbol.ticker}=X"
        return symbol.ticker
    
    def _ticker_to_symbol(self, ticker: str) -> Symbol:
        """Convert Yahoo ticker to Symbol"""
        # Remove suffixes
        if "-USD" in ticker:
            return Symbol(ticker=ticker.replace("-USD", ""), security_type="CRYPTO")
        elif "=X" in ticker:
            return Symbol(ticker=ticker.replace("=X", ""), security_type="FOREX")
        return Symbol(ticker=ticker, security_type="EQUITY")
    
    async def connect(self) -> bool:
        """Connect to Yahoo Finance (no auth required)"""
        self._connected = True
        logger.info("Connected to Yahoo Finance")
        return True
    
    async def disconnect(self) -> None:
        """Disconnect"""
        self._connected = False
        logger.info("Disconnected from Yahoo Finance")
    
    def is_connected(self) -> bool:
        """Check connection status"""
        return self._connected
    
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """Subscribe to symbols (limited real-time support)"""
        # Yahoo Finance doesn't provide true real-time streaming
        # We can only poll for updates
        for symbol in symbols:
            self._subscribed_symbols.add(symbol)
        logger.info(f"Subscribed to {len(symbols)} symbols on Yahoo Finance")
    
    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        """Unsubscribe from symbols"""
        for symbol in symbols:
            self._subscribed_symbols.discard(symbol)
        logger.info(f"Unsubscribed from {len(symbols)} symbols")
    
    def get_history(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> pd.DataFrame:
        """Get historical data from Yahoo Finance"""
        ticker = self._symbol_to_ticker(symbol)
        
        # Map resolution to yfinance interval
        interval_map = {
            Resolution.MINUTE: "1m",
            Resolution.HOUR: "1h",
            Resolution.DAILY: "1d",
            Resolution.WEEKLY: "1wk",
            Resolution.MONTHLY: "1mo"
        }
        
        interval = interval_map.get(resolution, "1d")
        
        # Limitations: 1m data only available for last 7 days
        # 1h data only available for last 730 days
        if resolution == Resolution.MINUTE and (end - start).days > 7:
            logger.warning("1-minute data limited to 7 days on Yahoo Finance, adjusting...")
            start = end - timedelta(days=7)
        
        try:
            yf_ticker = yf.Ticker(ticker)
            df = yf_ticker.history(start=start, end=end, interval=interval)
            
            if df.empty:
                logger.warning(f"No data returned for {ticker}")
                return pd.DataFrame()
            
            # Standardize column names
            df = df.rename(columns={
                'Open': 'open',
                'High': 'high',
                'Low': 'low',
                'Close': 'close',
                'Volume': 'volume'
            })
            
            logger.info(f"Retrieved {len(df)} bars for {ticker}")
            return df
            
        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return pd.DataFrame()
    
    def get_bars(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> List[Bar]:
        """Get historical bars"""
        # Check cache first
        if self._storage:
            cached = self._storage.load_bars(symbol, resolution, start, end)
            if cached:
                logger.debug(f"Using cached data for {symbol.ticker}")
                return cached
        
        # Fetch from Yahoo
        df = self.get_history(symbol, start, end, resolution)
        
        if df.empty:
            return []
        
        # Convert to Bar objects
        bars = []
        for timestamp, row in df.iterrows():
            bars.append(Bar(
                symbol=symbol,
                timestamp=timestamp.to_pydatetime(),
                open=Decimal(str(row['open'])),
                high=Decimal(str(row['high'])),
                low=Decimal(str(row['low'])),
                close=Decimal(str(row['close'])),
                volume=Decimal(str(row['volume'])),
                resolution=resolution
            ))
        
        # Cache results
        if self._storage:
            self._storage.save_bars(symbol, bars, resolution)
        
        return bars
    
    async def download_and_cache(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> List[Bar]:
        """Download and cache data asynchronously"""
        # Run synchronous yfinance call in thread pool
        loop = asyncio.get_event_loop()
        bars = await loop.run_in_executor(
            None,
            self.get_bars,
            symbol, start, end, resolution
        )
        return bars
    
    async def batch_download(
        self,
        symbols: List[Symbol],
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> dict:
        """Download multiple symbols with rate limiting"""
        results = {}
        
        for symbol in symbols:
            try:
                bars = await self.download_and_cache(symbol, start, end, resolution)
                results[symbol.ticker] = bars
                logger.info(f"Downloaded {len(bars)} bars for {symbol.ticker}")
                
                # Rate limiting
                if self._rate_limit_delay > 0:
                    await asyncio.sleep(self._rate_limit_delay)
                    
            except Exception as e:
                logger.error(f"Failed to download {symbol.ticker}: {e}")
                results[symbol.ticker] = []
        
        return results
    
    def get_company_info(self, symbol: Symbol) -> Optional[dict]:
        """Get company/fund information"""
        ticker = self._symbol_to_ticker(symbol)
        
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            
            return {
                'name': info.get('longName', ''),
                'sector': info.get('sector', ''),
                'industry': info.get('industry', ''),
                'market_cap': info.get('marketCap'),
                'pe_ratio': info.get('trailingPE'),
                'dividend_yield': info.get('dividendYield'),
                'country': info.get('country', ''),
                'website': info.get('website', '')
            }
            
        except Exception as e:
            logger.error(f"Error fetching info for {ticker}: {e}")
            return None
