"""Cryptocurrency broker and data adapters for AlgoEngine.

Provides two providers:

    **BinanceAdapter** — Full-featured exchange adapter supporting:
        - REST: kline/candles, ticker, order book, exchange info
        - WebSocket: real-time kline, ticker, trade streams
        - Auto-reconnection with exponential backoff

    **CoinGeckoAdapter** — Free market data provider supporting:
        - Current prices, market caps, volumes
        - Historical OHLCV (daily resolution)
        - Trending and top coins

Usage::

    adapter = BinanceAdapter()
    await adapter.connect()
    bars = await adapter.get_klines("BTCUSDT", "1h", limit=100)
    await adapter.start_websocket(["btcusdt@kline_1h"], on_kline)
    await adapter.disconnect()
"""

import asyncio
import hashlib
import hmac
import json
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from urllib.parse import urljoin, urlencode

import aiohttp

from ..data.models import Symbol, Bar, Tick, Resolution
from ..utils.logger import get_logger

logger = get_logger("adapters.crypto")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class KlineInterval(Enum):
    """Binance kline/candlestick intervals."""

    S1 = "1s"
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    MN1 = "1M"


@dataclass
class CryptoKline:
    """OHLCV candle from a crypto exchange."""

    symbol: str
    interval: KlineInterval
    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time: int
    quote_volume: Decimal
    trades: int
    taker_buy_volume: Decimal
    taker_buy_quote_volume: Decimal

    def to_bar(self) -> Bar:
        return Bar(
            symbol=Symbol(ticker=self.symbol, security_type="CRYPTO"),
            timestamp=datetime.fromtimestamp(self.open_time / 1000, tz=timezone.utc),
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            resolution=Resolution.MINUTE,
        )


@dataclass
class CryptoTicker:
    """24hr ticker statistics."""

    symbol: str
    price_change: Decimal
    price_change_pct: Decimal
    weighted_avg_price: Decimal
    last_price: Decimal
    last_qty: Decimal
    bid_price: Decimal
    bid_qty: Decimal
    ask_price: Decimal
    ask_qty: Decimal
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    volume: Decimal
    quote_volume: Decimal
    open_time: int
    close_time: int
    count: int

    def to_tick(self) -> Tick:
        return Tick(
            symbol=Symbol(ticker=self.symbol, security_type="CRYPTO"),
            timestamp=datetime.fromtimestamp(self.close_time / 1000, tz=timezone.utc),
            bid_price=self.bid_price,
            ask_price=self.ask_price,
            bid_size=self.bid_qty,
            ask_size=self.ask_qty,
            last_price=self.last_price,
            last_size=self.last_qty,
        )


@dataclass
class CoinGeckoMarket:
    """CoinGecko market overview for a single coin."""

    coin_id: str
    symbol: str
    name: str
    current_price: Dict[str, Decimal]
    market_cap: Dict[str, Decimal]
    market_cap_rank: int
    total_volume: Dict[str, Decimal]
    high_24h: Dict[str, Decimal]
    low_24h: Dict[str, Decimal]
    price_change_24h: Decimal
    price_change_pct_24h: Decimal
    circulating_supply: Decimal
    total_supply: Optional[Decimal]
    max_supply: Optional[Decimal]
    ath: Decimal
    ath_change_pct: Decimal
    ath_date: str
    last_updated: str


# ---------------------------------------------------------------------------
# Base crypto adapter
# ---------------------------------------------------------------------------


class CryptoExchangeAdapter:
    """Base class for cryptocurrency exchange adapters."""

    def __init__(self, name: str, base_url: str) -> None:
        self._name = name
        self._base_url = base_url
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._last_request_time = 0.0
        self._rate_limit_interval = 0.05

    async def connect(self) -> bool:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        self._connected = True
        logger.info(f"[{self._name}] Connected to {self._base_url}")
        return True

    async def disconnect(self) -> None:
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info(f"[{self._name}] Disconnected")

    def is_connected(self) -> bool:
        return self._connected and self._session is not None

    async def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        if not self._session:
            raise RuntimeError(f"{self._name} not connected")
        await self._rate_limit()
        url = urljoin(self._base_url, path)
        async with self._session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(
        self, path: str, data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Any:
        if not self._session:
            raise RuntimeError(f"{self._name} not connected")
        await self._rate_limit()
        url = urljoin(self._base_url, path)
        async with self._session.post(url, json=data, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _rate_limit(self) -> None:
        now = _time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_interval:
            await asyncio.sleep(self._rate_limit_interval - elapsed)
        self._last_request_time = _time.monotonic()

    @property
    def name(self) -> str:
        return self._name


# ---------------------------------------------------------------------------
# Binance Adapter
# ---------------------------------------------------------------------------

BINANCE_REST_URL = "https://api.binance.com/api/v3/"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_WS_COMBINED_URL = "wss://stream.binance.com:9443/stream"


class BinanceAdapter(CryptoExchangeAdapter):
    """Binance exchange adapter with REST API and WebSocket streaming.

    Provides:
        - Kline/candlestick data
        - Real-time ticker and book ticker
        - Trade streaming
        - Exchange info
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ) -> None:
        base = "https://testnet.binance.vision/api/v3/" if testnet else BINANCE_REST_URL
        super().__init__("Binance", base)
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_callbacks: Dict[str, List[Callable]] = {}
        self._ws_running = False

    # ── Signed headers ─────────────────────────────────────────────

    def _signed_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._api_secret:
            return params
        params["timestamp"] = int(_time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _auth_headers(self) -> Dict[str, str]:
        if self._api_key:
            return {"X-MBX-APIKEY": self._api_key}
        return {}

    # ── REST Endpoints ─────────────────────────────────────────────

    async def ping(self) -> bool:
        """Test connectivity."""
        try:
            result = await self._get("ping")
            return result == {}
        except Exception:
            return False

    async def get_server_time(self) -> int:
        """Get exchange server time in milliseconds."""
        data = await self._get("time")
        return data.get("serverTime", 0)

    async def get_exchange_info(self) -> Dict:
        """Get trading rules and symbol information."""
        return await self._get("exchangeInfo")

    async def get_klines(
        self,
        symbol: str,
        interval: Union[str, KlineInterval],
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[CryptoKline]:
        interval_str = interval.value if isinstance(interval, KlineInterval) else interval
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval_str,
            "limit": min(limit, 1000),
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time

        raw = await self._get("klines", params)
        return self._parse_klines(symbol, interval_str, raw)

    async def get_klines_range(
        self,
        symbol: str,
        interval: Union[str, KlineInterval],
        start_time: int,
        end_time: int,
    ) -> List[CryptoKline]:
        interval_str = interval.value if isinstance(interval, KlineInterval) else interval
        all_klines: List[CryptoKline] = []
        current_start = start_time

        while current_start < end_time:
            batch = await self.get_klines(
                symbol, interval_str, limit=1000,
                start_time=current_start, end_time=end_time,
            )
            if not batch:
                break
            all_klines.extend(batch)
            current_start = batch[-1].close_time + 1
            if len(batch) < 1000:
                break
            await asyncio.sleep(0.1)

        return all_klines

    async def get_ticker_24h(self, symbol: str) -> Optional[CryptoTicker]:
        raw = await self._get("ticker/24hr", {"symbol": symbol.upper()})
        return self._parse_ticker(raw) if raw else None

    async def get_tickers_24h(self) -> List[CryptoTicker]:
        raw = await self._get("ticker/24hr")
        return [self._parse_ticker(r) for r in raw if r]

    async def get_price(self, symbol: str) -> Optional[Decimal]:
        raw = await self._get("ticker/price", {"symbol": symbol.upper()})
        return Decimal(raw["price"]) if raw else None

    async def get_order_book(
        self, symbol: str, limit: int = 100
    ) -> Dict[str, Any]:
        return await self._get("depth", {"symbol": symbol.upper(), "limit": limit})

    # ── Parsing helpers ────────────────────────────────────────────

    @staticmethod
    def _parse_klines(
        symbol: str, interval: str, raw: List[List]
    ) -> List[CryptoKline]:
        results: List[CryptoKline] = []
        for k in raw:
            results.append(CryptoKline(
                symbol=symbol,
                interval=KlineInterval(interval),
                open_time=k[0],
                open=Decimal(str(k[1])),
                high=Decimal(str(k[2])),
                low=Decimal(str(k[3])),
                close=Decimal(str(k[4])),
                volume=Decimal(str(k[5])),
                close_time=k[6],
                quote_volume=Decimal(str(k[7])),
                trades=k[8],
                taker_buy_volume=Decimal(str(k[9])),
                taker_buy_quote_volume=Decimal(str(k[10])),
            ))
        return results

    @staticmethod
    def _parse_ticker(raw: Dict) -> CryptoTicker:
        return CryptoTicker(
            symbol=raw["symbol"],
            price_change=Decimal(raw.get("priceChange", 0)),
            price_change_pct=Decimal(raw.get("priceChangePercent", 0)),
            weighted_avg_price=Decimal(raw.get("weightedAvgPrice", 0)),
            last_price=Decimal(raw.get("lastPrice", 0)),
            last_qty=Decimal(raw.get("lastQty", 0)),
            bid_price=Decimal(raw.get("bidPrice", 0)),
            bid_qty=Decimal(raw.get("bidQty", 0)),
            ask_price=Decimal(raw.get("askPrice", 0)),
            ask_qty=Decimal(raw.get("askQty", 0)),
            open_price=Decimal(raw.get("openPrice", 0)),
            high_price=Decimal(raw.get("highPrice", 0)),
            low_price=Decimal(raw.get("lowPrice", 0)),
            volume=Decimal(raw.get("volume", 0)),
            quote_volume=Decimal(raw.get("quoteVolume", 0)),
            open_time=raw.get("openTime", 0),
            close_time=raw.get("closeTime", 0),
            count=raw.get("count", 0),
        )

    async def get_bars(
        self,
        symbol: str,
        interval: Union[str, KlineInterval],
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Bar]:
        klines = await self.get_klines(symbol, interval, limit, start_time, end_time)
        return [k.to_bar() for k in klines]

    # ── Account (authenticated) ────────────────────────────────────

    async def get_account_info(self) -> Dict:
        params = self._signed_params({})
        headers = self._auth_headers()
        url = urljoin(self._base_url, "account")
        if self._session is None:
            raise RuntimeError("Binance not connected")
        async with self._session.get(url, params=params, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── WebSocket Streaming ────────────────────────────────────────

    async def start_websocket(
        self,
        streams: List[str],
        on_message: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        if self._ws_running:
            return

        self._ws_running = True
        self._ws_task = asyncio.create_task(
            self._websocket_loop(streams, on_message)
        )
        logger.info(f"[Binance] WebSocket started: {len(streams)} streams")

    async def stop_websocket(self) -> None:
        self._ws_running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        logger.info("[Binance] WebSocket stopped")

    async def _websocket_loop(
        self,
        streams: List[str],
        on_message: Optional[Callable[[Dict], None]],
    ) -> None:
        backoff = 1.0

        while self._ws_running:
            try:
                if len(streams) == 1:
                    ws_url = f"{BINANCE_WS_URL}/{streams[0]}"
                else:
                    ws_url = (
                        f"{BINANCE_WS_COMBINED_URL}?"
                        f"streams={'/'.join(streams)}"
                    )

                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url) as ws:
                        self._ws = ws
                        logger.info(
                            f"[Binance] WebSocket connected to {ws_url}"
                        )
                        backoff = 1.0

                        async for msg in ws:
                            if not self._ws_running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if on_message:
                                    try:
                                        on_message(data)
                                    except Exception:
                                        logger.error(
                                            "[Binance] WebSocket callback error",
                                            exc_info=True,
                                        )
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.error(f"[Binance] WS error: {ws.exception()}")
                                break

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    f"[Binance] WebSocket disconnected: {exc}. "
                    f"Reconnecting in {backoff}s..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def subscribe_kline(
        self,
        symbol: str,
        interval: Union[str, KlineInterval],
        callback: Callable[[CryptoKline], None],
        use_combined: bool = False,
    ) -> None:
        interval_str = interval.value if isinstance(interval, KlineInterval) else interval
        stream = f"{symbol.lower()}@kline_{interval_str}"

        async def handler(data: Dict) -> None:
            kline_data = (
                data.get("data", data) if use_combined else data
            )
            k = kline_data.get("k", {})
            if k:
                kline = CryptoKline(
                    symbol=k.get("s", symbol),
                    interval=KlineInterval(interval_str),
                    open_time=k.get("t", 0),
                    open=Decimal(str(k.get("o", 0))),
                    high=Decimal(str(k.get("h", 0))),
                    low=Decimal(str(k.get("l", 0))),
                    close=Decimal(str(k.get("c", 0))),
                    volume=Decimal(str(k.get("v", 0))),
                    close_time=k.get("T", 0),
                    quote_volume=Decimal(str(k.get("q", 0))),
                    trades=k.get("n", 0),
                    taker_buy_volume=Decimal(str(k.get("V", 0))),
                    taker_buy_quote_volume=Decimal(str(k.get("Q", 0))),
                )
                callback(kline)

        self._ws_callbacks.setdefault(stream, []).append(handler)
        await self.start_websocket([stream], handler)


# ---------------------------------------------------------------------------
# CoinGecko Adapter
# ---------------------------------------------------------------------------

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3/"


class CoinGeckoAdapter(CryptoExchangeAdapter):
    """CoinGecko free market data adapter.

    Provides cryptocurrency market data without an API key for basic usage.
    Rate limit: 10-50 calls/minute for free tier.
    """

    def __init__(self, api_key: str = "") -> None:
        super().__init__("CoinGecko", COINGECKO_BASE_URL)
        self._api_key = api_key
        self._rate_limit_interval = 1.5
        self._coin_id_cache: Dict[str, str] = {}

    # ── Coin lookup ────────────────────────────────────────────────

    async def get_coins_list(self) -> List[Dict[str, str]]:
        return await self._get("coins/list")

    async def _resolve_coin_id(self, symbol_or_id: str) -> str:
        if symbol_or_id in self._coin_id_cache:
            return self._coin_id_cache[symbol_or_id]
        coins = await self.get_coins_list()
        lookup = symbol_or_id.lower()
        for coin in coins:
            if coin["id"].lower() == lookup or coin["symbol"].lower() == lookup:
                self._coin_id_cache[symbol_or_id] = coin["id"]
                return coin["id"]
        raise ValueError(f"Unknown coin: {symbol_or_id}")

    # ── Market data ────────────────────────────────────────────────

    async def get_price(
        self,
        coin_ids: Union[str, List[str]],
        vs_currencies: Union[str, List[str]] = "usd",
    ) -> Dict[str, Dict[str, Decimal]]:
        if isinstance(coin_ids, list):
            ids = ",".join(coin_ids)
        else:
            ids = coin_ids
        if isinstance(vs_currencies, list):
            currencies = ",".join(vs_currencies)
        else:
            currencies = vs_currencies

        params = {"ids": ids, "vs_currencies": currencies}
        raw = await self._get("simple/price", params)
        result: Dict[str, Dict[str, Decimal]] = {}
        for coin, prices in raw.items():
            result[coin] = {k: Decimal(str(v)) for k, v in prices.items()}
        return result

    async def get_markets(
        self,
        vs_currency: str = "usd",
        coin_ids: Optional[List[str]] = None,
        per_page: int = 100,
        page: int = 1,
        sparkline: bool = False,
    ) -> List[CoinGeckoMarket]:
        params: Dict[str, Any] = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": min(per_page, 250),
            "page": page,
            "sparkline": str(sparkline).lower(),
        }
        if coin_ids:
            params["ids"] = ",".join(coin_ids)

        raw = await self._get("coins/markets", params)
        return [self._parse_market(r) for r in raw]

    async def get_coin_market(
        self,
        coin_id: str,
        vs_currency: str = "usd",
    ) -> Optional[CoinGeckoMarket]:
        resolved = await self._resolve_coin_id(coin_id)
        markets = await self.get_markets(vs_currency, [resolved], per_page=1)
        return markets[0] if markets else None

    async def get_trending(self) -> List[Dict]:
        raw = await self._get("search/trending")
        return raw.get("coins", [])

    async def get_top_coins(self, limit: int = 10) -> List[CoinGeckoMarket]:
        pages = (limit + 99) // 100
        results: List[CoinGeckoMarket] = []
        for page in range(1, pages + 1):
            per_page = min(limit - len(results), 100)
            markets = await self.get_markets(per_page=per_page, page=page)
            results.extend(markets)
            if len(results) >= limit:
                break
        return results[:limit]

    # ── Historical data ────────────────────────────────────────────

    async def get_coin_history(
        self,
        coin_id: str,
        date: str,
    ) -> Dict:
        resolved = await self._resolve_coin_id(coin_id)
        return await self._get(f"coins/{resolved}/history", {"date": date})

    async def get_market_chart(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 30,
    ) -> Dict[str, List[List[float]]]:
        resolved = await self._resolve_coin_id(coin_id)
        params = {"vs_currency": vs_currency, "days": days}
        return await self._get(f"coins/{resolved}/market_chart", params)

    async def get_ohlc(
        self,
        coin_id: str,
        vs_currency: str = "usd",
        days: int = 7,
    ) -> List[List[float]]:
        resolved = await self._resolve_coin_id(coin_id)
        params = {
            "vs_currency": vs_currency,
            "days": days,
        }
        return await self._get(f"coins/{resolved}/ohlc", params)

    # ── Parsing ────────────────────────────────────────────────────

    @staticmethod
    def _parse_market(raw: Dict) -> CoinGeckoMarket:
        return CoinGeckoMarket(
            coin_id=raw.get("id", ""),
            symbol=raw.get("symbol", ""),
            name=raw.get("name", ""),
            current_price={
                k: Decimal(str(v)) for k, v in raw.get("current_price", {}).items()
            },
            market_cap={
                k: Decimal(str(v)) for k, v in raw.get("market_cap", {}).items()
            },
            market_cap_rank=raw.get("market_cap_rank", 0) or 0,
            total_volume={
                k: Decimal(str(v)) for k, v in raw.get("total_volume", {}).items()
            },
            high_24h={
                k: Decimal(str(v)) for k, v in raw.get("high_24h", {}).items()
            },
            low_24h={
                k: Decimal(str(v)) for k, v in raw.get("low_24h", {}).items()
            },
            price_change_24h=Decimal(str(raw.get("price_change_24h", 0))),
            price_change_pct_24h=Decimal(str(raw.get("price_change_percentage_24h", 0))),
            circulating_supply=Decimal(str(raw.get("circulating_supply", 0))),
            total_supply=(
                Decimal(str(raw["total_supply"]))
                if raw.get("total_supply") else None
            ),
            max_supply=(
                Decimal(str(raw["max_supply"]))
                if raw.get("max_supply") else None
            ),
            ath=Decimal(str(raw.get("ath", 0))),
            ath_change_pct=Decimal(str(raw.get("ath_change_percentage", 0))),
            ath_date=str(raw.get("ath_date", "")),
            last_updated=str(raw.get("last_updated", "")),
        )

    async def get_crypto_bars(
        self,
        symbol: str,
        days: int = 30,
        vs_currency: str = "usd",
    ) -> List[Bar]:
        data = await self.get_ohlc(symbol, vs_currency, days)
        bars: List[Bar] = []
        sym = Symbol(ticker=symbol, security_type="CRYPTO", currency=vs_currency.upper())
        for entry in data:
            bars.append(Bar(
                symbol=sym,
                timestamp=datetime.fromtimestamp(entry[0] / 1000, tz=timezone.utc),
                open=Decimal(str(entry[1])),
                high=Decimal(str(entry[2])),
                low=Decimal(str(entry[3])),
                close=Decimal(str(entry[4])),
                volume=Decimal("0"),
                resolution=Resolution.DAILY,
            ))
        return bars


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


async def get_crypto_adapter(
    provider: str = "binance",
    api_key: str = "",
    api_secret: str = "",
) -> CryptoExchangeAdapter:
    if provider == "binance":
        adapter = BinanceAdapter(api_key=api_key, api_secret=api_secret)
    elif provider == "coingecko":
        adapter = CoinGeckoAdapter(api_key=api_key)
    else:
        raise ValueError(f"Unknown crypto provider: {provider}")
    await adapter.connect()
    return adapter
