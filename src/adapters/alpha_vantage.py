"""Alpha Vantage data adapter for AlgoEngine"""

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
import aiohttp
import pandas as pd

from ..data.feed import DataFeed
from ..data.models import Symbol, Resolution
from ..utils.logger import get_logger
from ..utils.config import get_config

logger = get_logger("adapters.alpha_vantage")


class AlphaVantageAdapter(DataFeed):
    """Alpha Vantage API adapter"""
    
    BASE_URL = "https://www.alphavantage.co/query"
    
    def __init__(self, api_key: Optional[str] = None, cache_data: bool = True) -> None:
        super().__init__("AlphaVantage")
        
        config = get_config()
        self._api_key = api_key or config.data.providers.get('alpha_vantage', {}).get('api_key', '')
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_per_minute = 5  # Free tier: 5 calls per minute
        self._call_times: List[datetime] = []
        
    async def connect(self) -> bool:
        """Initialize HTTP session"""
        if not self._api_key:
            logger.error("Alpha Vantage API key not configured")
            return False
        
        self._session = aiohttp.ClientSession()
        self._connected = True
        logger.info("Connected to Alpha Vantage")
        return True
    
    async def disconnect(self) -> None:
        """Close HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("Disconnected from Alpha Vantage")
    
    def is_connected(self) -> bool:
        """Check connection"""
        return self._connected and self._session is not None
    
    async def _rate_limit(self) -> None:
        """Apply rate limiting"""
        now = datetime.now()
        
        # Remove calls older than 1 minute
        self._call_times = [t for t in self._call_times if (now - t).seconds < 60]
        
        # If at limit, wait
        if len(self._call_times) >= self._rate_limit_per_minute:
            sleep_time = 60 - (now - self._call_times[0]).seconds
            if sleep_time > 0:
                logger.debug(f"Rate limit reached, waiting {sleep_time}s")
                await asyncio.sleep(sleep_time)
        
        self._call_times.append(datetime.now())
    
    async def _make_request(self, params: Dict[str, Any]) -> Optional[Dict]:
        """Make API request"""
        if not self._session:
            return None
        
        await self._rate_limit()
        
        params['apikey'] = self._api_key
        
        try:
            async with self._session.get(self.BASE_URL, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Check for API errors
                    if 'Error Message' in data:
                        logger.error(f"Alpha Vantage API error: {data['Error Message']}")
                        return None
                    
                    if 'Note' in data:
                        logger.warning(f"Alpha Vantage API note: {data['Note']}")
                        return None
                    
                    return data
                else:
                    logger.error(f"HTTP error {response.status}")
                    return None
                    
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    
    async def get_intraday(
        self,
        symbol: Symbol,
        interval: str = "5min",
        output_size: str = "compact"
    ) -> pd.DataFrame:
        """Get intraday data"""
        params = {
            'function': 'TIME_SERIES_INTRADAY',
            'symbol': symbol.ticker,
            'interval': interval,
            'outputsize': output_size,
            'datatype': 'json'
        }
        
        data = await self._make_request(params)
        if not data:
            return pd.DataFrame()
        
        # Parse time series
        time_series_key = f"Time Series ({interval})"
        if time_series_key not in data:
            return pd.DataFrame()
        
        records = []
        for timestamp, values in data[time_series_key].items():
            records.append({
                'timestamp': timestamp,
                'open': float(values['1. open']),
                'high': float(values['2. high']),
                'low': float(values['3. low']),
                'close': float(values['4. close']),
                'volume': int(values['5. volume'])
            })
        
        df = pd.DataFrame(records)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        return df
    
    async def get_daily(
        self,
        symbol: Symbol,
        output_size: str = "compact",
        adjusted: bool = True
    ) -> pd.DataFrame:
        """Get daily data"""
        function = 'TIME_SERIES_DAILY_ADJUSTED' if adjusted else 'TIME_SERIES_DAILY'
        
        params = {
            'function': function,
            'symbol': symbol.ticker,
            'outputsize': output_size,
            'datatype': 'json'
        }
        
        data = await self._make_request(params)
        if not data:
            return pd.DataFrame()
        
        time_series_key = 'Time Series (Daily)' if not adjusted else 'Time Series (Daily Adjusted)'
        if time_series_key not in data:
            return pd.DataFrame()
        
        records = []
        for timestamp, values in data[time_series_key].items():
            record = {
                'timestamp': timestamp,
                'open': float(values['1. open']),
                'high': float(values['2. high']),
                'low': float(values['3. low']),
                'close': float(values['4. close']),
                'volume': int(values['6. volume'])
            }
            if adjusted:
                record['adjusted_close'] = float(values['5. adjusted close'])
                record['dividend'] = float(values['7. dividend amount'])
                record['split_coefficient'] = float(values['8. split coefficient'])
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        
        return df
    
    def get_history(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        resolution: Resolution = Resolution.DAILY
    ) -> pd.DataFrame:
        """Get historical data (synchronous wrapper)"""
        # Alpha Vantage is primarily async, use event loop
        loop = asyncio.get_event_loop()
        
        if resolution in [Resolution.MINUTE, Resolution.HOUR]:
            # Use intraday
            interval_map = {
                Resolution.MINUTE: "1min",
                Resolution.HOUR: "60min"
            }
            interval = interval_map.get(resolution, "5min")
            return loop.run_until_complete(self.get_intraday(symbol, interval))
        else:
            # Use daily
            return loop.run_until_complete(self.get_daily(symbol))
    
    async def get_quote(self, symbol: Symbol) -> Optional[Dict[str, Any]]:
        """Get real-time quote"""
        params = {
            'function': 'GLOBAL_QUOTE',
            'symbol': symbol.ticker,
            'datatype': 'json'
        }
        
        data = await self._make_request(params)
        if not data or 'Global Quote' not in data:
            return None
        
        quote = data['Global Quote']
        return {
            'symbol': quote['01. symbol'],
            'open': Decimal(quote['02. open']),
            'high': Decimal(quote['03. high']),
            'low': Decimal(quote['04. low']),
            'price': Decimal(quote['05. price']),
            'volume': int(quote['06. volume']),
            'latest_trading_day': quote['07. latest trading day'],
            'previous_close': Decimal(quote['08. previous close']),
            'change': Decimal(quote['09. change']),
            'change_percent': quote['10. change percent'].rstrip('%')
        }
    
    async def search_symbol(self, keywords: str) -> List[Dict[str, Any]]:
        """Search for symbols"""
        params = {
            'function': 'SYMBOL_SEARCH',
            'keywords': keywords,
            'datatype': 'json'
        }
        
        data = await self._make_request(params)
        if not data or 'bestMatches' not in data:
            return []
        
        return [
            {
                'symbol': match['1. symbol'],
                'name': match['2. name'],
                'type': match['3. type'],
                'region': match['4. region'],
                'market_open': match['5. marketOpen'],
                'market_close': match['6. marketClose'],
                'timezone': match['7. timezone'],
                'currency': match['8. currency'],
                'match_score': float(match['9. matchScore'])
            }
            for match in data['bestMatches']
        ]
    
    async def subscribe(self, symbols: List[Symbol]) -> None:
        """Subscribe to symbols (limited to quote polling)"""
        for symbol in symbols:
            self._subscribed_symbols.add(symbol)
        logger.info(f"Subscribed to {len(symbols)} symbols on Alpha Vantage")
    
    async def unsubscribe(self, symbols: List[Symbol]) -> None:
        """Unsubscribe from symbols"""
        for symbol in symbols:
            self._subscribed_symbols.discard(symbol)
        logger.info(f"Unsubscribed from {len(symbols)} symbols")
