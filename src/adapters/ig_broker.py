"""IG Markets broker adapter for real trading"""

import asyncio
import aiohttp
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional, Any, Callable, Tuple
from urllib.parse import urljoin

from ..engine.interfaces import ITransactionHandler, IResultHandler, PositionSide
from ..trading.models import Symbol, Order, OrderType, OrderSide, OrderStatus, Position
from ..utils.logger import get_logger
from ..utils.error_handler import retry_async, RetryConfig, RetryExhaustedError

logger = get_logger("adapters.ig")

# ---------------------------------------------------------------------------
# IG Markets API Constants
# ---------------------------------------------------------------------------

IG_API_BASE = "https://api.ig.com/gateway/deal"
IG_API_DEMO = "https://demo-api.ig.com/gateway/deal"


class IGEnvironment(Enum):
    """IG Markets API environments"""
    LIVE = IG_API_BASE
    DEMO = IG_API_DEMO


class IGAccountType(Enum):
    """IG account type"""
    CFD = "CFD"
    SPREADBET = "SPREADBET"
    PHYSICAL = "PHYSICAL"


class IGDealStatus(Enum):
    """Deal confirmation status"""
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    AMENDED = "AMENDED"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    DELETED = "DELETED"
    FULLY_CLOSED = "FULLY_CLOSED"


class IGOrderType(Enum):
    """IG order types for working orders"""
    LIMIT = "LIMIT"
    STOP = "STOP"


class IGPositionDirection(Enum):
    """IG position direction"""
    BUY = "BUY"
    SELL = "SELL"


class IGDealDirection(Enum):
    """IG deal direction"""
    BUY = "BUY"
    SELL = "SELL"


class IGTimeInForce(Enum):
    """IG time in force for working orders"""
    GOOD_TILL_CANCELLED = "GOOD_TILL_CANCELLED"
    GOOD_TILL_DATE = "GOOD_TILL_DATE"


class IGPriceResolution(Enum):
    """Candle price resolution for IG"""
    SECOND = "SECOND"
    MINUTE = "MINUTE"
    MINUTE_2 = "MINUTE_2"
    MINUTE_3 = "MINUTE_3"
    MINUTE_5 = "MINUTE_5"
    MINUTE_10 = "MINUTE_10"
    MINUTE_15 = "MINUTE_15"
    MINUTE_30 = "MINUTE_30"
    HOUR = "HOUR"
    HOUR_2 = "HOUR_2"
    HOUR_3 = "HOUR_3"
    HOUR_4 = "HOUR_4"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class IGConfig:
    """IG Markets broker configuration"""
    api_key: str
    username: str
    password: str
    account_id: str = ""
    environment: IGEnvironment = IGEnvironment.DEMO
    timeout: int = 30
    retry_attempts: int = 3
    retry_delay: float = 1.0
    max_batch_requests: int = 50  # IG max batch size for price requests

    def get_base_url(self) -> str:
        return self.environment.value if isinstance(self.environment, IGEnvironment) else self.environment

    def get_versioned_url(self, endpoint: str, version: int = 1) -> str:
        """Build URL with API version"""
        base = self.get_base_url().rstrip("/")
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{base}{endpoint}"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class IGAccount:
    """IG account information"""
    account_id: str
    account_name: str
    account_alias: str
    status: str
    account_type: IGAccountType
    currency: str
    balance: Decimal
    deposit: Decimal
    profit_loss: Decimal
    available_cash: Decimal
    margin: Decimal
    equity: Decimal
    last_updated: Optional[datetime] = None

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'IGAccount':
        return cls(
            account_id=data.get('accountId', ''),
            account_name=data.get('accountName', ''),
            account_alias=data.get('accountAlias', ''),
            status=data.get('status', ''),
            account_type=IGAccountType(data['accountType']) if data.get('accountType') else IGAccountType.CFD,
            currency=data.get('currency', ''),
            balance=Decimal(str(data.get('balance', 0))),
            deposit=Decimal(str(data.get('deposit', 0))),
            profit_loss=Decimal(str(data.get('profitLoss', 0))),
            available_cash=Decimal(str(data.get('availableCash', 0))),
            margin=Decimal(str(data.get('margin', 0))),
            equity=Decimal(str(data.get('equity', 0))),
            last_updated=datetime.now()
        )


@dataclass
class IGPosition:
    """IG position information"""
    deal_id: str
    epic: str
    instrument_name: str
    direction: IGPositionDirection
    size: Decimal
    level: Decimal
    limit_level: Optional[Decimal] = None
    stop_level: Optional[Decimal] = None
    currency: str = ""
    created_date: Optional[datetime] = None
    created_date_utc: Optional[datetime] = None
    deal_reference: str = ""
    stop_range: Decimal = Decimal('0')
    limit_range: Decimal = Decimal('0')
    controlled_risk: bool = False
    trailing_stop: bool = False
    trailing_stop_distance: Optional[Decimal] = None
    trailing_stop_step: Optional[Decimal] = None

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'IGPosition':
        return cls(
            deal_id=data.get('dealId', ''),
            epic=data.get('epic', ''),
            instrument_name=data.get('instrumentName', ''),
            direction=IGPositionDirection(data['direction']) if data.get('direction') else IGPositionDirection.BUY,
            size=Decimal(str(data.get('size', 0))),
            level=Decimal(str(data.get('level', 0))),
            limit_level=Decimal(str(data['limitLevel'])) if data.get('limitLevel') else None,
            stop_level=Decimal(str(data['stopLevel'])) if data.get('stopLevel') else None,
            currency=data.get('currency', ''),
            created_date=datetime.fromisoformat(data['createdDate']) if data.get('createdDate') else None,
            created_date_utc=datetime.fromisoformat(data['createdDateUtc'].replace('Z', '+00:00')) if data.get('createdDateUtc') else None,
            deal_reference=data.get('dealReference', ''),
            stop_range=Decimal(str(data.get('stopRange', 0))),
            limit_range=Decimal(str(data.get('limitRange', 0))),
            controlled_risk=data.get('controlledRisk', False),
            trailing_stop=data.get('trailingStop', False),
            trailing_stop_distance=Decimal(str(data['trailingStopDistance'])) if data.get('trailingStopDistance') else None,
            trailing_stop_step=Decimal(str(data['trailingStopStep'])) if data.get('trailingStopStep') else None,
        )

    def is_long(self) -> bool:
        return self.direction == IGPositionDirection.BUY

    @property
    def unrealized_pnl(self) -> Decimal:
        """Estimate unrealized P&L (IG provides this via positions snapshot)"""
        return Decimal('0')


@dataclass
class IGWorkingOrder:
    """IG working order (limit/stop)"""
    deal_id: str
    direction: IGDealDirection
    epic: str
    order_type: IGOrderType
    level: Decimal
    size: Decimal
    limit_distance: Optional[Decimal] = None
    stop_distance: Optional[Decimal] = None
    currency: str = ""
    good_till: Optional[datetime] = None
    good_till_date: Optional[datetime] = None
    guaranteed_stop: bool = False
    deal_reference: str = ""
    created_date: Optional[datetime] = None
    created_date_utc: Optional[datetime] = None
    time_in_force: IGTimeInForce = IGTimeInForce.GOOD_TILL_CANCELLED

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'IGWorkingOrder':
        return cls(
            deal_id=data.get('dealId', ''),
            direction=IGDealDirection(data['direction']) if data.get('direction') else IGDealDirection.BUY,
            epic=data.get('epic', ''),
            order_type=IGOrderType(data['orderType']) if data.get('orderType') else IGOrderType.LIMIT,
            level=Decimal(str(data.get('level', 0))),
            size=Decimal(str(data.get('size', 0))),
            limit_distance=Decimal(str(data['limitDistance'])) if data.get('limitDistance') else None,
            stop_distance=Decimal(str(data['stopDistance'])) if data.get('stopDistance') else None,
            currency=data.get('currency', ''),
            good_till=datetime.fromisoformat(data['goodTill']) if data.get('goodTill') else None,
            good_till_date=datetime.fromisoformat(data['goodTillDate'].replace('Z', '+00:00')) if data.get('goodTillDate') else None,
            guaranteed_stop=data.get('guaranteedStop', False),
            deal_reference=data.get('dealReference', ''),
            created_date=datetime.fromisoformat(data['createdDate']) if data.get('createdDate') else None,
            created_date_utc=datetime.fromisoformat(data['createdDateUtc'].replace('Z', '+00:00')) if data.get('createdDateUtc') else None,
            time_in_force=IGTimeInForce(data['timeInForce']) if data.get('timeInForce') else IGTimeInForce.GOOD_TILL_CANCELLED,
        )


@dataclass
class IGDealConfirmation:
    """IG deal confirmation response"""
    deal_reference: str
    deal_status: IGDealStatus
    epic: Optional[str] = None
    direction: Optional[IGDealDirection] = None
    level: Optional[Decimal] = None
    size: Optional[Decimal] = None
    stop_level: Optional[Decimal] = None
    limit_level: Optional[Decimal] = None
    reason: Optional[str] = None
    deal_id: Optional[str] = None
    instrument_name: Optional[str] = None
    profit: Optional[Decimal] = None
    profit_currency: Optional[str] = None

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'IGDealConfirmation':
        return cls(
            deal_reference=data.get('dealReference', ''),
            deal_status=IGDealStatus(data['dealStatus']) if data.get('dealStatus') else IGDealStatus.REJECTED,
            epic=data.get('epic'),
            direction=IGDealDirection(data['direction']) if data.get('direction') else None,
            level=Decimal(str(data['level'])) if data.get('level') else None,
            size=Decimal(str(data['size'])) if data.get('size') else None,
            stop_level=Decimal(str(data['stopLevel'])) if data.get('stopLevel') else None,
            limit_level=Decimal(str(data['limitLevel'])) if data.get('limitLevel') else None,
            reason=data.get('reason'),
            deal_id=data.get('dealId'),
            instrument_name=data.get('instrumentName'),
            profit=Decimal(str(data['profit'])) if data.get('profit') else None,
            profit_currency=data.get('profitCurrency'),
        )


@dataclass
class IGMarketSnapshot:
    """IG market snapshot"""
    epic: str
    instrument_name: str
    instrument_type: str
    bid: Optional[Decimal] = None
    offer: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    change: Optional[Decimal] = None
    change_percent: Optional[Decimal] = None
    update_time: Optional[datetime] = None
    market_status: str = "CLOSED"
    scaling_factor: int = 0
    margin_deposit_buy: Optional[Decimal] = None
    margin_deposit_sell: Optional[Decimal] = None
    limited_risk_premium: Optional[Decimal] = None
    min_normal_stop_distance: Optional[int] = None
    min_controlled_stop_distance: Optional[int] = None
    min_deal_size: Optional[Decimal] = None
    max_deal_size: Optional[Decimal] = None
    lot_size: Optional[int] = None
    expiry: Optional[str] = None

    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'IGMarketSnapshot':
        snapshot = data.get('snapshot', data)
        instrument = data.get('instrument', data)
        return cls(
            epic=data.get('epic', ''),
            instrument_name=instrument.get('name', ''),
            instrument_type=instrument.get('type', ''),
            bid=Decimal(str(snapshot.get('bid', 0))),
            offer=Decimal(str(snapshot.get('offer', 0))),
            high=Decimal(str(snapshot.get('high', 0))),
            low=Decimal(str(snapshot.get('low', 0))),
            change=Decimal(str(snapshot.get('change', 0))),
            change_percent=Decimal(str(snapshot.get('changePct', 0))),
            update_time=datetime.fromisoformat(snapshot['updateTime'].replace('Z', '+00:00')) if snapshot.get('updateTime') else None,
            market_status=snapshot.get('marketStatus', 'CLOSED'),
            scaling_factor=snapshot.get('scalingFactor', 0),
            margin_deposit_buy=Decimal(str(snapshot['marginDepositBuy'])) if snapshot.get('marginDepositBuy') else None,
            margin_deposit_sell=Decimal(str(snapshot['marginDepositSell'])) if snapshot.get('marginDepositSell') else None,
            limited_risk_premium=Decimal(str(snapshot['limitedRiskPremium'])) if snapshot.get('limitedRiskPremium') else None,
            min_normal_stop_distance=int(snapshot['minNormalStopOrLimitDistance']) if snapshot.get('minNormalStopOrLimitDistance') else None,
            min_controlled_stop_distance=int(snapshot['minControlledStopDistance']) if snapshot.get('minControlledStopDistance') else None,
            min_deal_size=Decimal(str(snapshot['minDealSize'])) if snapshot.get('minDealSize') else None,
            max_deal_size=Decimal(str(snapshot['maxDealSize'])) if snapshot.get('maxDealSize') else None,
            lot_size=int(snapshot['lotSize']) if snapshot.get('lotSize') else None,
            expiry=snapshot.get('expiry'),
        )

    @property
    def mid_price(self) -> Optional[Decimal]:
        if self.bid is not None and self.offer is not None:
            return (self.bid + self.offer) / 2
        return None


# ---------------------------------------------------------------------------
# IG Markets API Client
# ---------------------------------------------------------------------------


class IGApiClient:
    """IG Markets REST API client

    Handles authentication, session management, and all IG API endpoints.
    IG uses a two-token authentication model:
      - CST (Client Security Token) from session creation
      - X-SECURITY-TOKEN from session creation
    """

    def __init__(self, config: IGConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.get_base_url()

        # Authentication tokens
        self._cst: Optional[str] = None          # Client Security Token
        self._security_token: Optional[str] = None  # X-SECURITY-TOKEN

        # Account override
        self._active_account_id: Optional[str] = None

        # Retry config for API calls
        self._retry_cfg = RetryConfig(
            max_attempts=config.retry_attempts,
            base_delay=config.retry_delay,
            backoff_factor=2.0,
            max_delay=30.0,
            jitter=True,
        )

    # -- Session management --------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return self._cst is not None and self._security_token is not None

    def _get_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers for IG API requests.

        IG requires:
          - X-IG-API-KEY: API key
          - CST: Client Security Token (after authentication)
          - X-SECURITY-TOKEN: Security token (after authentication)
          - IG-ACCOUNT-ID: Account to operate on (optional, can be varied)
        """
        headers = {
            "X-IG-API-KEY": self._config.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "VERSION": "1",
        }

        if self._cst:
            headers["CST"] = self._cst
        if self._security_token:
            headers["X-SECURITY-TOKEN"] = self._security_token
        if self._active_account_id:
            headers["IG-ACCOUNT-ID"] = self._active_account_id

        return headers

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()

    async def connect(self) -> None:
        """Create HTTP session"""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self._config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def disconnect(self) -> None:
        """Close HTTP session and clear tokens"""
        self._cst = None
        self._security_token = None
        self._active_account_id = None
        if self._session:
            await self._session.close()
            self._session = None

    # -- Authentication ------------------------------------------------------

    async def authenticate(self, account_id: Optional[str] = None) -> bool:
        """Authenticate with IG using API key + username/password

        IG authentication flow:
          1. POST /session with API key in header + Basic Auth
          2. Response returns CST in header and X-SECURITY-TOKEN in header
          3. Subsequent requests use both tokens

        Args:
            account_id: Optional account to operate on. If not provided,
                       IG will use the first available account.

        Returns:
            True if authentication succeeded
        """
        if not self._session:
            await self.connect()

        endpoint = f"{self._base_url}/session"

        # Build Basic Auth header
        auth_str = f"{self._config.username}:{self._config.password}"
        basic_auth = base64.b64encode(auth_str.encode()).decode()

        headers = {
            "X-IG-API-KEY": self._config.api_key,
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "VERSION": "2",
        }

        identifier = account_id or self._config.account_id
        payload: Dict[str, Any] = {}
        if identifier:
            payload["identifier"] = identifier

        try:
            async with self._session.post(
                endpoint, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"IG authentication failed: {response.status} - {error_text}")
                    return False

                # Extract tokens from response headers
                self._cst = response.headers.get("CST")
                self._security_token = response.headers.get("X-SECURITY-TOKEN")

                if not self._cst or not self._security_token:
                    logger.error("IG authentication: missing CST or X-SECURITY-TOKEN in response")
                    return False

                data = await response.json()

                # Set active account
                accounts = data.get("accounts", [])
                if accounts:
                    if identifier:
                        # Find matching account
                        for acc in accounts:
                            if acc.get("accountId") == identifier:
                                self._active_account_id = identifier
                                break
                    if not self._active_account_id:
                        # Use first account
                        self._active_account_id = accounts[0].get("accountId")

                logger.info(
                    f"IG authenticated (account: {self._active_account_id or 'default'})"
                )
                return True

        except asyncio.TimeoutError:
            logger.error("IG authentication timed out")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"IG authentication connection error: {e}")
            return False

    async def ensure_authenticated(self) -> bool:
        """Ensure we have valid authentication, re-authenticating if needed."""
        if self.authenticated:
            return True
        logger.info("IG session expired, re-authenticating...")
        return await self.authenticate()

    async def logout(self) -> bool:
        """Delete the current session (log out)."""
        if not self.authenticated:
            return True

        endpoint = f"{self._base_url}/session"
        headers = self._get_auth_headers()

        try:
            async with self._session.delete(endpoint, headers=headers) as response:
                self._cst = None
                self._security_token = None
                self._active_account_id = None
                logger.info("IG session deleted")
                return True
        except Exception as e:
            logger.warning(f"Ignoring logout error: {e}")
            self._cst = None
            self._security_token = None
            return True

    # -- Base request with retry ---------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        version: int = 1,
        auth_required: bool = True,
    ) -> Dict[str, Any]:
        """Make an HTTP request to the IG API with retry logic.

        Args:
            method: HTTP method
            endpoint: API endpoint path (will be joined with base URL)
            params: Query parameters
            data: JSON body
            version: API version header
            auth_required: Whether authentication is required

        Returns:
            Parsed JSON response as dict

        Raises:
            IGConnectionError: If the request fails after retries
            IGAuthenticationError: If authentication fails
        """
        if not self._session:
            await self.connect()

        if auth_required:
            if not await self.ensure_authenticated():
                raise IGAuthenticationError("Failed to authenticate with IG API")

        url = urljoin(self._base_url + "/", endpoint.lstrip("/"))
        headers = self._get_auth_headers()
        headers["VERSION"] = str(version)

        return await retry_async(
            self._do_request,
            method=method,
            url=url,
            headers=headers,
            params=params,
            data=data,
            config=self._retry_cfg,
            context=f"IG.{endpoint}",
        )

    async def _do_request(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a single HTTP request to IG."""
        async with self._session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=data,
        ) as response:
            # Handle 401 - token expired, will trigger re-authentication
            if response.status == 401:
                self._cst = None
                self._security_token = None
                raise IGAuthenticationError("IG session expired")

            # Handle 429 - rate limited
            if response.status == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                logger.warning(f"IG rate limited, retry after {retry_after}s")
                raise IGRateLimitError(f"Rate limited: retry after {retry_after}s")

            if response.status not in (200, 201):
                error_text = await response.text()
                error_msg = f"IG API error {response.status}: {error_text}"
                logger.error(error_msg)
                raise IGApiError(error_msg, response.status)

            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return await response.json()
            # Some endpoints return no content (204) or non-JSON
            return {}

    # -- Account endpoints ---------------------------------------------------

    async def get_accounts(self) -> List[IGAccount]:
        """Get all accounts for the authenticated user."""
        # Special auth flow: /session uses Basic Auth, not tokens
        if not self._session:
            await self.connect()

        endpoint = f"{self._base_url}/session"
        auth_str = f"{self._config.username}:{self._config.password}"
        basic_auth = base64.b64encode(auth_str.encode()).decode()

        headers = {
            "X-IG-API-KEY": self._config.api_key,
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "VERSION": "1",
        }

        try:
            async with self._session.get(endpoint, headers=headers) as response:
                if response.status != 200:
                    logger.error(f"Failed to get IG accounts: {response.status}")
                    return []
                data = await response.json()
                return [IGAccount.from_response(acc) for acc in data.get("accounts", [])]
        except Exception as e:
            logger.error(f"Failed to get IG accounts: {e}")
            return []

    async def get_account_details(self) -> Optional[Dict[str, Any]]:
        """Get current account details (balance, margin, etc.)."""
        endpoint = "/accounts"
        response = await self._request("GET", endpoint, version=1)

        accounts_data = response.get("accounts", [])
        if not accounts_data:
            return None

        # Find active account
        for acc in accounts_data:
            if acc.get("accountId") == self._active_account_id:
                ig_account = IGAccount.from_response(acc)
                return {
                    "account_id": ig_account.account_id,
                    "account_name": ig_account.account_name,
                    "account_type": ig_account.account_type.value,
                    "currency": ig_account.currency,
                    "balance": float(ig_account.balance),
                    "deposit": float(ig_account.deposit),
                    "profit_loss": float(ig_account.profit_loss),
                    "available_cash": float(ig_account.available_cash),
                    "margin": float(ig_account.margin),
                    "equity": float(ig_account.equity),
                    "status": ig_account.status,
                }

        # Fallback to first account
        ig_account = IGAccount.from_response(accounts_data[0])
        return {
            "account_id": ig_account.account_id,
            "account_name": ig_account.account_name,
            "account_type": ig_account.account_type.value,
            "currency": ig_account.currency,
            "balance": float(ig_account.balance),
            "deposit": float(ig_account.deposit),
            "profit_loss": float(ig_account.profit_loss),
            "available_cash": float(ig_account.available_cash),
            "margin": float(ig_account.margin),
            "equity": float(ig_account.equity),
        }

    # -- Position endpoints --------------------------------------------------

    async def get_positions(self) -> List[IGPosition]:
        """Get all open positions."""
        endpoint = "/positions"
        response = await self._request("GET", endpoint, version=2)

        positions = []
        for pos_data in response.get("positions", []):
            position = pos_data.get("position", pos_data)
            if position:
                positions.append(IGPosition.from_response(position))

        return positions

    async def get_position_by_deal_id(self, deal_id: str) -> Optional[IGPosition]:
        """Get a specific position by deal ID."""
        endpoint = f"/positions/{deal_id}"
        try:
            response = await self._request("GET", endpoint, version=2)
            position = response.get("position", response)
            return IGPosition.from_response(position)
        except (IGApiError, IGAuthenticationError) as e:
            logger.error(f"Failed to get position {deal_id}: {e}")
            return None

    async def create_position(
        self,
        epic: str,
        direction: IGDealDirection,
        size: Decimal,
        stop_level: Optional[Decimal] = None,
        limit_level: Optional[Decimal] = None,
        guaranteed_stop: bool = False,
        deal_reference: Optional[str] = None,
    ) -> IGDealConfirmation:
        """Create a new position (market order).

        Args:
            epic: IG market epic identifier (e.g. "CS.D.EURUSD.TODAY.IP")
            direction: BUY or SELL
            size: Deal size in units
            stop_level: Optional stop loss level
            limit_level: Optional take profit level
            guaranteed_stop: Whether to use a guaranteed stop
            deal_reference: Optional unique reference for idempotency

        Returns:
            Deal confirmation with status
        """
        endpoint = "/positions/otc"

        order_data: Dict[str, Any] = {
            "epic": epic,
            "direction": direction.value,
            "size": str(size),
            "currencyCode": "",  # IG will use account currency
            "forceOpen": True,
            "guaranteedStop": guaranteed_stop,
            "dealReference": deal_reference or self._generate_deal_reference(),
        }

        if stop_level is not None:
            order_data["stopLevel"] = str(stop_level)
        if limit_level is not None:
            order_data["limitLevel"] = str(limit_level)
        if guaranteed_stop:
            order_data["guaranteedStop"] = True

        response = await self._request("POST", endpoint, data=order_data, version=2)
        deal_ref = response.get("dealReference", "")
        return await self._wait_for_confirmation(deal_ref)

    async def close_position(
        self,
        deal_id: str,
        size: Optional[Decimal] = None,
        direction: Optional[IGDealDirection] = None,
    ) -> IGDealConfirmation:
        """Close an existing position.

        Args:
            deal_id: Position deal ID to close
            size: Number of units to close (None = close all)
            direction: Direction of the close (opposite of position)

        Returns:
            Deal confirmation
        """
        endpoint = f"/positions/otc/{deal_id}"

        # Get current position to determine close direction
        position = await self.get_position_by_deal_id(deal_id)
        close_direction = direction or (
            IGDealDirection.SELL if position and position.is_long() else IGDealDirection.BUY
        )

        close_data: Dict[str, Any] = {
            "direction": close_direction.value,
            "dealReference": self._generate_deal_reference(),
        }

        if size is not None:
            close_data["size"] = str(size)

        response = await self._request("DELETE", endpoint, data=close_data, version=2)
        deal_ref = response.get("dealReference", "")
        return await self._wait_for_confirmation(deal_ref)

    # -- Working orders endpoints --------------------------------------------

    async def get_working_orders(self) -> List[IGWorkingOrder]:
        """Get all working orders (limit/stop orders)."""
        endpoint = "/workingorders"
        response = await self._request("GET", endpoint, version=2)

        orders = []
        for order_data in response.get("workingOrders", []):
            order = order_data.get("workingOrderData", order_data)
            if order:
                orders.append(IGWorkingOrder.from_response(order))

        return orders

    async def create_working_order(
        self,
        epic: str,
        direction: IGDealDirection,
        order_type: IGOrderType,
        level: Decimal,
        size: Decimal,
        stop_level: Optional[Decimal] = None,
        limit_level: Optional[Decimal] = None,
        guaranteed_stop: bool = False,
        good_till_date: Optional[datetime] = None,
        deal_reference: Optional[str] = None,
    ) -> IGDealConfirmation:
        """Create a working order (limit or stop order).

        Args:
            epic: IG market epic identifier
            direction: BUY or SELL
            order_type: LIMIT or STOP
            level: Price level for the order
            size: Deal size in units
            stop_level: Optional stop loss level
            limit_level: Optional take profit level
            guaranteed_stop: Whether to use guaranteed stop
            good_till_date: Optional expiry date (None = GTC)
            deal_reference: Optional unique reference for idempotency

        Returns:
            Deal confirmation
        """
        endpoint = "/workingorders/otc"

        order_data: Dict[str, Any] = {
            "epic": epic,
            "direction": direction.value,
            "orderType": order_type.value,
            "level": str(level),
            "size": str(size),
            "currencyCode": "",
            "forceOpen": True,
            "guaranteedStop": guaranteed_stop,
            "dealReference": deal_reference or self._generate_deal_reference(),
        }

        if stop_level is not None:
            order_data["stopLevel"] = str(stop_level)
        if limit_level is not None:
            order_data["limitLevel"] = str(limit_level)
        if good_till_date:
            order_data["goodTillDate"] = good_till_date.isoformat()
            order_data["timeInForce"] = IGTimeInForce.GOOD_TILL_DATE.value
        else:
            order_data["timeInForce"] = IGTimeInForce.GOOD_TILL_CANCELLED.value

        response = await self._request("POST", endpoint, data=order_data, version=2)
        deal_ref = response.get("dealReference", "")
        return await self._wait_for_confirmation(deal_ref)

    async def update_working_order(
        self,
        deal_id: str,
        level: Decimal,
        size: Decimal,
        stop_level: Optional[Decimal] = None,
        limit_level: Optional[Decimal] = None,
        good_till_date: Optional[datetime] = None,
    ) -> IGDealConfirmation:
        """Update an existing working order."""
        endpoint = f"/workingorders/otc/{deal_id}"

        update_data: Dict[str, Any] = {
            "level": str(level),
            "size": str(size),
            "dealReference": self._generate_deal_reference(),
        }

        if stop_level is not None:
            update_data["stopLevel"] = str(stop_level)
        if limit_level is not None:
            update_data["limitLevel"] = str(limit_level)
        if good_till_date:
            update_data["goodTillDate"] = good_till_date.isoformat()
            update_data["timeInForce"] = IGTimeInForce.GOOD_TILL_DATE.value

        response = await self._request("PUT", endpoint, data=update_data, version=2)
        deal_ref = response.get("dealReference", "")
        return await self._wait_for_confirmation(deal_ref)

    async def delete_working_order(self, deal_id: str) -> IGDealConfirmation:
        """Delete/cancel a working order."""
        endpoint = f"/workingorders/otc/{deal_id}"
        delete_data = {
            "dealReference": self._generate_deal_reference(),
        }
        response = await self._request("DELETE", endpoint, data=delete_data, version=2)
        deal_ref = response.get("dealReference", "")
        return await self._wait_for_confirmation(deal_ref)

    # -- Market data endpoints -----------------------------------------------

    async def get_market_snapshot(self, epic: str) -> Optional[IGMarketSnapshot]:
        """Get market snapshot for a single epic.

        Args:
            epic: IG market epic identifier

        Returns:
            Market snapshot or None if not found
        """
        endpoint = f"/markets/{epic}"
        try:
            response = await self._request("GET", endpoint, version=3)
            instrument = response.get("instrument", {})
            snapshot = response.get("snapshot", {})

            return IGMarketSnapshot(
                epic=epic,
                instrument_name=instrument.get("name", ""),
                instrument_type=instrument.get("type", ""),
                bid=Decimal(str(snapshot.get("bid", 0))),
                offer=Decimal(str(snapshot.get("offer", 0))),
                high=Decimal(str(snapshot.get("high", 0))),
                low=Decimal(str(snapshot.get("low", 0))),
                change=Decimal(str(snapshot.get("change", 0))),
                change_percent=Decimal(str(snapshot.get("changePct", 0))),
                update_time=datetime.fromisoformat(snapshot["updateTime"].replace("Z", "+00:00")) if snapshot.get("updateTime") else None,
                market_status=snapshot.get("marketStatus", "CLOSED"),
                scaling_factor=snapshot.get("scalingFactor", 0),
                min_deal_size=Decimal(str(snapshot.get("minDealSize", 0))),
                max_deal_size=Decimal(str(snapshot.get("maxDealSize", 0))),
                lot_size=int(snapshot.get("lotSize", 1)) if snapshot.get("lotSize") else None,
                expiry=snapshot.get("expiry"),
            )
        except (IGApiError, IGAuthenticationError) as e:
            logger.error(f"Failed to get market snapshot for {epic}: {e}")
            return None

    async def get_market_snapshots(self, epics: List[str]) -> List[IGMarketSnapshot]:
        """Get market snapshots for multiple epics.

        Args:
            epics: List of IG market epic identifiers

        Returns:
            List of market snapshots
        """
        results = []
        # IG allows batch queries but with limits; process in batches
        for i in range(0, len(epics), self._config.max_batch_requests):
            batch = epics[i:i + self._config.max_batch_requests]
            endpoint = "/markets"
            params = {"epics": ",".join(batch)}

            try:
                response = await self._request("GET", endpoint, params=params, version=2)
                market_list = response.get("marketDetails", [])
                for md in market_list:
                    instrument = md.get("instrument", {})
                    snapshot = md.get("snapshot", {})
                    epic = md.get("epic", "")
                    results.append(IGMarketSnapshot(
                        epic=epic,
                        instrument_name=instrument.get("name", ""),
                        instrument_type=instrument.get("type", ""),
                        bid=Decimal(str(snapshot.get("bid", 0))),
                        offer=Decimal(str(snapshot.get("offer", 0))),
                        high=Decimal(str(snapshot.get("high", 0))),
                        low=Decimal(str(snapshot.get("low", 0))),
                        change=Decimal(str(snapshot.get("change", 0))),
                        change_percent=Decimal(str(snapshot.get("changePct", 0))),
                        update_time=datetime.fromisoformat(snapshot.get("updateTime", "")) if snapshot.get("updateTime") else None,
                        market_status=snapshot.get("marketStatus", "CLOSED"),
                        scaling_factor=snapshot.get("scalingFactor", 0),
                    ))
            except Exception as e:
                logger.error(f"Failed to get market snapshots for batch: {e}")

        return results

    async def get_price_history(
        self,
        epic: str,
        resolution: IGPriceResolution = IGPriceResolution.DAY,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        max_points: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Get historical price data (candles).

        Args:
            epic: IG market epic identifier
            resolution: Candle resolution
            start: Start time
            end: End time
            max_points: Maximum number of data points

        Returns:
            List of candle data dicts
        """
        endpoint = f"/prices/{epic}"
        params: Dict[str, Any] = {
            "resolution": resolution.value,
            "max": min(max_points, 1000),  # IG API limit
        }

        if start:
            params["start"] = start.isoformat()
        if end:
            params["end"] = end.isoformat()
        # IG also supports "number" param for last N data points
        if not start and not end:
            params["max"] = min(max_points, 1000)

        response = await self._request("GET", endpoint, params=params, version=3)

        prices = response.get("prices", [])
        # If more data is available, IG returns an "allowance" object
        allowance = response.get("allowance", {})
        if allowance.get("remainingAllowance", 0) == 0:
            logger.warning(f"IG price history allowance exhausted for {epic}")

        return prices

    async def search_markets(self, search_term: str) -> List[Dict[str, Any]]:
        """Search for markets by term.

        Args:
            search_term: Search query

        Returns:
            List of matching market items
        """
        endpoint = "/markets"
        params = {"searchTerm": search_term}
        response = await self._request("GET", endpoint, params=params, version=1)
        return response.get("markets", [])

    # -- Deal confirmation ---------------------------------------------------

    async def _wait_for_confirmation(
        self,
        deal_reference: str,
        max_retries: int = 10,
        delay: float = 0.5,
    ) -> IGDealConfirmation:
        """Poll for deal confirmation until available.

        IG sends deal references that must be polled for confirmation.
        """
        endpoint = f"/confirms/{deal_reference}"

        for attempt in range(max_retries):
            try:
                response = await self._request("GET", endpoint, version=1)
                if response:
                    return IGDealConfirmation.from_response(response)
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error(f"Deal confirmation failed for {deal_reference}: {e}")
                    return IGDealConfirmation(
                        deal_reference=deal_reference,
                        deal_status=IGDealStatus.REJECTED,
                        reason=str(e),
                    )

            await asyncio.sleep(delay * (1.5 ** attempt))

        return IGDealConfirmation(
            deal_reference=deal_reference,
            deal_status=IGDealStatus.REJECTED,
            reason="Timeout waiting for confirmation",
        )

    # -- Helpers -------------------------------------------------------------

    @staticmethod
    def _generate_deal_reference() -> str:
        """Generate a unique deal reference for idempotency."""
        return f"AE{int(time.time() * 1000000)}"

    @staticmethod
    def _is_forex_epic(epic: str) -> bool:
        """Check if an epic represents a forex instrument."""
        return epic.startswith("CS.D.") and "TODAY" in epic


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class IGError(Exception):
    """Base IG Markets exception"""
    pass


class IGAuthenticationError(IGError):
    """Authentication with IG failed"""
    pass


class IGConnectionError(IGError):
    """Connection to IG failed"""
    pass


class IGRateLimitError(IGError):
    """IG rate limit exceeded"""
    pass


class IGApiError(IGError):
    """IG API returned an error"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# IG Broker Adapter
# ---------------------------------------------------------------------------


class IGBroker(ITransactionHandler):
    """IG Markets broker adapter implementing ITransactionHandler.

    Provides a high-level interface for trading on IG Markets,
    mirroring the OANDA broker adapter pattern.
    """

    def __init__(self, config: IGConfig):
        self._config = config
        self._client = IGApiClient(config)
        self._account: Optional[Dict[str, Any]] = None
        self._positions: Dict[str, IGPosition] = {}
        self._orders: Dict[str, IGWorkingOrder] = {}
        self._result_handlers: List[IResultHandler] = []
        self._connected = False

        # Event callbacks
        self._on_order_filled: Optional[Callable[[Any], None]] = None
        self._on_position_opened: Optional[Callable[[IGPosition], None]] = None
        self._on_position_closed: Optional[Callable[[IGPosition], None]] = None

        # Retry config for broker operations
        self._retry_cfg = RetryConfig(
            max_attempts=config.retry_attempts,
            base_delay=config.retry_delay,
            backoff_factor=2.0,
            max_delay=30.0,
            jitter=True,
        )

    # -- ITransactionHandler Interface Implementation ------------------------

    async def process_order(self, order: "Order") -> "OrderEvent":
        """Process an order and return the result event.

        Args:
            order: The order to process

        Returns:
            OrderEvent with the result
        """
        success = await self.submit_order(order)
        if success and order.id:
            return OrderEvent(
                order_id=order.id,
                symbol=order.symbol,
                status=OrderStatus.FILLED if order.order_type == OrderType.MARKET else OrderStatus.SUBMITTED,
                timestamp=datetime.now(),
                filled_quantity=order.quantity if order.order_type == OrderType.MARKET else Decimal('0'),
                message="Order submitted successfully",
            )
        return OrderEvent(
            order_id=order.id or "",
            symbol=order.symbol,
            status=OrderStatus.FAILED,
            timestamp=datetime.now(),
            message="Failed to process order",
        )

    async def update_order(self, order: "Order") -> "OrderEvent":
        """Update an existing order.

        Args:
            order: The order with updated fields

        Returns:
            OrderEvent with the result
        """
        if order.id:
            # Cancel existing order first
            await self.cancel_order(order.id)
            # Then resubmit
            submit_event = await self.process_order(order)
            return submit_event
        return await self.process_order(order)

    async def get_open_orders(self, symbol: Optional["Symbol"] = None) -> List["Order"]:
        """Get list of open working orders.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of Order objects
        """
        all_orders = await self.get_orders()
        if symbol:
            return [o for o in all_orders if o.symbol.ticker == symbol.ticker]
        return all_orders

    async def get_order_by_id(self, order_id: str) -> Optional["Order"]:
        """Get order by ID.

        Args:
            order_id: The order ID to look up

        Returns:
            Order if found, None otherwise
        """
        if order_id in self._positions:
            ig_pos = self._positions[order_id]
            return self._convert_ig_position_to_model(ig_pos)

        if order_id in self._orders:
            ig_wo = self._orders[order_id]
            order = self._convert_ig_order_to_model(ig_wo)
            order.id = ig_wo.deal_id
            return order

        return None

    # -- Connection ----------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to IG Markets API and initialize account data.

        Steps:
          1. Create HTTP session
          2. Authenticate with API key + username/password
          3. Fetch initial account, positions, and orders

        Returns:
            True if connected successfully
        """
        try:
            await self._client.connect()

            # Authenticate
            account_id = self._config.account_id or None
            auth_success = await self._client.authenticate(account_id)
            if not auth_success:
                logger.error("Failed to authenticate with IG Markets")
                return False

            # Get initial account data
            self._account = await self._client.get_account_details()

            # Get initial positions and working orders
            await self._refresh_positions()
            await self._refresh_orders()

            self._connected = True
            account_label = self._account.get("account_id", "unknown") if self._account else "unknown"
            logger.info(f"Connected to IG Markets account {account_label}")

            return True

        except Exception as e:
            logger.error(f"Failed to connect to IG Markets: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from IG Markets API."""
        try:
            await self._client.logout()
        except Exception as e:
            logger.warning(f"Ignoring IG logout error: {e}")

        await self._client.disconnect()
        self._connected = False
        self._account = None
        self._positions.clear()
        self._orders.clear()
        logger.info("Disconnected from IG Markets")

    def is_connected(self) -> bool:
        """Check if connected to IG."""
        return self._connected

    # -- Order Execution -----------------------------------------------------

    async def submit_order(self, order: Order) -> bool:
        """Submit an order to IG Markets.

        Supports:
          - MARKET orders → create_position (OTC)
          - LIMIT orders → create_working_order
          - STOP orders → create_working_order

        Args:
            order: Our internal Order model

        Returns:
            True if order was submitted successfully
        """
        if not self._connected:
            logger.error("Not connected to IG Markets")
            return False

        try:
            # Convert our Symbol to IG epic
            epic = self._convert_symbol_to_epic(order.symbol)
            direction = IGDealDirection.BUY if order.side == OrderSide.BUY else IGDealDirection.SELL

            if order.order_type == OrderType.MARKET:
                confirmation = await self._client.create_position(
                    epic=epic,
                    direction=direction,
                    size=order.quantity,
                    stop_level=None,
                    limit_level=None,
                )
            elif order.order_type == OrderType.LIMIT:
                if order.limit_price is None:
                    logger.error("Limit order requires a limit price")
                    return False
                ig_type = IGOrderType.LIMIT
                confirmation = await self._client.create_working_order(
                    epic=epic,
                    direction=direction,
                    order_type=ig_type,
                    level=order.limit_price,
                    size=order.quantity,
                    stop_level=None,
                    limit_level=None,
                )
            elif order.order_type == OrderType.STOP:
                if order.stop_price is None:
                    logger.error("Stop order requires a stop price")
                    return False
                confirmation = await self._client.create_working_order(
                    epic=epic,
                    direction=direction,
                    order_type=IGOrderType.STOP,
                    level=order.stop_price,
                    size=order.quantity,
                    stop_level=None,
                    limit_level=None,
                )
            else:
                logger.error(f"Unsupported order type: {order.order_type}")
                return False

            # Handle confirmation
            if confirmation.deal_status == IGDealStatus.ACCEPTED:
                if confirmation.deal_id:
                    order.id = confirmation.deal_id
                logger.info(
                    f"Order accepted: ref={confirmation.deal_reference}, "
                    f"deal_id={confirmation.deal_id}"
                )

                # Refresh positions and orders to get latest state
                await self._refresh_positions()
                await self._refresh_orders()
                return True

            else:
                logger.error(
                    f"Order rejected: {confirmation.reason or 'unknown reason'}"
                )
                return False

        except IGRateLimitError:
            logger.error("IG rate limit exceeded, will retry on next attempt")
            return False
        except Exception as e:
            logger.error(f"Failed to submit order: {e}")
            return False

    async def cancel_order(self, order: Order) -> bool:
        """Cancel a working order.

        Args:
            order: The order to cancel

        Returns:
            True if cancelled successfully
        """
        if not self._connected:
            return False

        deal_id = order.id
        if not deal_id:
            logger.error("Order has no ID")
            return False

        try:
            # Check if it's a position (market order) or working order
            if deal_id in self._positions:
                # Close position
                confirmation = await self._client.close_position(deal_id)
            else:
                # Delete working order
                confirmation = await self._client.delete_working_order(deal_id)

            if confirmation.deal_status in (IGDealStatus.CLOSED, IGDealStatus.DELETED,
                                            IGDealStatus.FULLY_CLOSED, IGDealStatus.ACCEPTED):
                logger.info(f"Order cancelled: {deal_id}")
                await self._refresh_positions()
                await self._refresh_orders()
                return True

            logger.error(f"Cancel failed for {deal_id}: {confirmation.reason}")
            return False

        except Exception as e:
            logger.error(f"Failed to cancel order {deal_id}: {e}")
            return False

    # -- Account & Position Querying -----------------------------------------

    async def get_account_info(self) -> Dict[str, Any]:
        """Get current account information.

        Returns:
            Dict with account details or empty dict on failure
        """
        if not self._connected:
            return {}

        try:
            self._account = await self._client.get_account_details()
            return self._account or {}
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return {}

    async def get_positions(self) -> List[Position]:
        """Get current positions.

        Converts IG positions to our internal Position model.

        Returns:
            List of Position objects
        """
        if not self._connected:
            return []

        try:
            await self._refresh_positions()

            positions = []
            for ig_pos in self._positions.values():
                symbol = self._convert_epic_to_symbol(ig_pos.epic)
                side = OrderSide.BUY if ig_pos.is_long() else OrderSide.SELL

                position = Position(
                    symbol=symbol,
                    side=side,
                    quantity=ig_pos.size,
                    avg_entry_price=ig_pos.level,
                    current_price=ig_pos.level,
                    opened_at=ig_pos.created_date_utc or datetime.now(),
                )
                positions.append(position)

            return positions

        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []

    async def get_orders(self) -> List[Order]:
        """Get current working orders.

        Converts IG working orders to our internal Order model.

        Returns:
            List of Order objects
        """
        if not self._connected:
            return []

        try:
            await self._refresh_orders()

            orders = []
            for ig_order in self._orders.values():
                symbol = self._convert_epic_to_symbol(ig_order.epic)
                order_type = (
                    OrderType.LIMIT if ig_order.order_type == IGOrderType.LIMIT
                    else OrderType.STOP
                )
                side = OrderSide.BUY if ig_order.direction == IGDealDirection.BUY else OrderSide.SELL

                order = Order(
                    symbol=symbol,
                    side=side,
                    quantity=ig_order.size,
                    order_type=order_type,
                    limit_price=ig_order.level if ig_order.order_type == IGOrderType.LIMIT else None,
                    stop_price=ig_order.level if ig_order.order_type == IGOrderType.STOP else None,
                    status=OrderStatus.PENDING,
                )
                order.id = ig_order.deal_id
                orders.append(order)

            return orders

        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return []

    async def close_position(self, symbol: Symbol, quantity: Optional[Decimal] = None) -> bool:
        """Close a position for the given symbol.

        Args:
            symbol: Symbol to close
            quantity: Number of units to close (None = all)

        Returns:
            True if close was successful
        """
        if not self._connected:
            return False

        try:
            # Find the position for this symbol
            epic = self._convert_symbol_to_epic(symbol)
            target_position = None

            for pos in self._positions.values():
                if pos.epic == epic:
                    target_position = pos
                    break

            if not target_position:
                logger.warning(f"No IG position found for {epic}")
                return False

            confirmation = await self._client.close_position(
                deal_id=target_position.deal_id,
                size=quantity,
            )

            if confirmation.deal_status in (IGDealStatus.CLOSED, IGDealStatus.ACCEPTED,
                                            IGDealStatus.FULLY_CLOSED):
                logger.info(f"Position closed: {epic}")
                await self._refresh_positions()
                return True

            logger.error(f"Failed to close position {epic}: {confirmation.reason}")
            return False

        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False

    # -- Result Handlers -----------------------------------------------------

    def add_result_handler(self, handler: IResultHandler) -> None:
        """Add a result handler for trade results."""
        self._result_handlers.append(handler)

    def remove_result_handler(self, handler: IResultHandler) -> None:
        """Remove a result handler."""
        if handler in self._result_handlers:
            self._result_handlers.remove(handler)

    # -- Market Data ---------------------------------------------------------

    async def get_market_price(self, symbol: Symbol) -> Optional[float]:
        """Get current market price for a symbol.

        Args:
            symbol: Symbol to get price for

        Returns:
            Mid price as float, or None on failure
        """
        if not self._connected:
            return None

        try:
            epic = self._convert_symbol_to_epic(symbol)
            snapshot = await self._client.get_market_snapshot(epic)

            if snapshot and snapshot.mid_price is not None:
                return float(snapshot.mid_price)

            return None

        except Exception as e:
            logger.error(f"Failed to get market price: {e}")
            return None

    async def get_historical_data(
        self,
        symbol: Symbol,
        resolution: IGPriceResolution = IGPriceResolution.DAY,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        count: int = 500,
    ) -> List[Dict[str, Any]]:
        """Get historical price data.

        Args:
            symbol: Symbol to get data for
            resolution: Candle resolution
            start: Start time
            end: End time
            count: Maximum number of candles

        Returns:
            List of candle data dicts
        """
        if not self._connected:
            return []

        try:
            epic = self._convert_symbol_to_epic(symbol)
            return await self._client.get_price_history(
                epic=epic,
                resolution=resolution,
                start=start,
                end=end,
                max_points=count,
            )
        except Exception as e:
            logger.error(f"Failed to get historical data: {e}")
            return []

    # -- Internal Helpers ----------------------------------------------------

    async def _refresh_positions(self) -> None:
        """Refresh positions from IG."""
        try:
            ig_positions = await self._client.get_positions()
            self._positions = {pos.deal_id: pos for pos in ig_positions}
        except Exception as e:
            logger.error(f"Failed to refresh positions: {e}")

    async def _refresh_orders(self) -> None:
        """Refresh working orders from IG."""
        try:
            ig_orders = await self._client.get_working_orders()
            self._orders = {order.deal_id: order for order in ig_orders}
        except Exception as e:
            logger.error(f"Failed to refresh orders: {e}")

    def _convert_symbol_to_epic(self, symbol: Symbol) -> str:
        """Convert our Symbol to IG epic format.

        IG uses epic format like:
          - "CS.D.EURUSD.TODAY.IP" for forex
          - "IX.D.FTSE.DAILY.IP" for indices
          - "CS.D.USCGC.TODAY.IP" for commodities

        For forex, we pattern match: "CS.D.{BASE}{QUOTE}.TODAY.IP"
        """
        ticker = symbol.ticker.upper()

        # If it's already an epic (contains dots), return as-is
        if '.' in ticker:
            return ticker

        # Assume forex pair format (e.g., "EURUSD" → "CS.D.EURUSD.TODAY.IP")
        if len(ticker) == 6 and ticker.isalpha():
            return f"CS.D.{ticker}.TODAY.IP"

        # For other instruments, try the ticker directly
        return ticker

    def _convert_epic_to_symbol(self, epic: str) -> Symbol:
        """Convert IG epic to our Symbol.

        Extracts the instrument identifier from IG epic format.
        E.g., "CS.D.EURUSD.TODAY.IP" → Symbol("EURUSD")
        """
        # Handle various epic formats
        if "CS.D." in epic:
            # Forex: "CS.D.EURUSD.TODAY.IP" → "EURUSD"
            parts = epic.split(".")
            if len(parts) >= 3:
                # CS.D.EURUSD.TODAY.IP → EURUSD
                return Symbol(parts[2])
        elif "IX.D." in epic:
            # Index: "IX.D.FTSE.DAILY.IP" → "FTSE"
            parts = epic.split(".")
            if len(parts) >= 3:
                return Symbol(parts[2])
        elif "CRUDE" in epic:
            # Commodities
            return Symbol(epic)

        # Fallback: use epic directly
        return Symbol(epic)

    def _convert_ig_position_to_model(self, ig_pos: IGPosition) -> Position:
        """Convert IG position to our internal Position model."""
        symbol = self._convert_epic_to_symbol(ig_pos.epic)
        side = OrderSide.BUY if ig_pos.is_long() else OrderSide.SELL

        return Position(
            symbol=symbol,
            side=side,
            quantity=ig_pos.size,
            avg_entry_price=ig_pos.level,
            current_price=ig_pos.level,
            opened_at=ig_pos.created_date_utc or datetime.now(),
        )

    def _convert_ig_order_to_model(self, ig_order: IGWorkingOrder) -> Order:
        """Convert IG working order to our internal Order model."""
        symbol = self._convert_epic_to_symbol(ig_order.epic)
        order_type = (
            OrderType.LIMIT if ig_order.order_type == IGOrderType.LIMIT
            else OrderType.STOP
        )
        side = OrderSide.BUY if ig_order.direction == IGDealDirection.BUY else OrderSide.SELL

        order = Order(
            symbol=symbol,
            side=side,
            quantity=ig_order.size,
            order_type=order_type,
            limit_price=ig_order.level if ig_order.order_type == IGOrderType.LIMIT else None,
            stop_price=ig_order.level if ig_order.order_type == IGOrderType.STOP else None,
            status=OrderStatus.PENDING,
        )
        order.id = ig_order.deal_id
        return order

    # -- Callbacks -----------------------------------------------------------

    def set_order_filled_callback(self, callback: Callable[[Any], None]) -> None:
        """Set callback for order filled events."""
        self._on_order_filled = callback

    def set_position_opened_callback(self, callback: Callable[[IGPosition], None]) -> None:
        """Set callback for position opened events."""
        self._on_position_opened = callback

    def set_position_closed_callback(self, callback: Callable[[IGPosition], None]) -> None:
        """Set callback for position closed events."""
        self._on_position_closed = callback


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------


def create_ig_broker(
    api_key: str,
    username: str,
    password: str,
    account_id: str = "",
    environment: IGEnvironment = IGEnvironment.DEMO,
    timeout: int = 30,
) -> IGBroker:
    """Create an IG Markets broker adapter.

    Args:
        api_key: IG API key
        username: IG username (login ID)
        password: IG password
        account_id: Optional account ID (if multiple accounts)
        environment: IGEnvironment.DEMO or IGEnvironment.LIVE
        timeout: HTTP request timeout in seconds

    Returns:
        Configured IGBroker instance

    Example:
        >>> broker = create_ig_broker(
        ...     api_key="your_api_key",
        ...     username="your_username",
        ...     password="your_password",
        ...     environment=IGEnvironment.DEMO,
        ... )
        >>> await broker.connect()
        True
    """
    config = IGConfig(
        api_key=api_key,
        username=username,
        password=password,
        account_id=account_id,
        environment=environment,
        timeout=timeout,
    )
    return IGBroker(config)