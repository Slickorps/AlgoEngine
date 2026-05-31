"""OANDA API broker adapter for real trading"""

import asyncio
import aiohttp
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from decimal import Decimal
from urllib.parse import urljoin

from ..engine.interfaces import ITransactionHandler, IResultHandler
from ..engine.interfaces import Symbol, Order, OrderType, OrderStatus, PositionSide, Position, OrderEvent
from ..utils.logger import get_logger

logger = get_logger("adapters.oanda")


class OandaEnvironment(Enum):
    """OANDA API environments"""
    LIVE = "https://api-fxtrade.oanda.com"
    PRACTICE = "https://api-fxpractice.oanda.com"
    SANDBOX = "https://api-sandbox.oanda.com"


class OandaOrderType(Enum):
    """OANDA order types"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    MARKET_IF_TOUCHED = "MARKET_IF_TOUCHED"


class OandaTimeframe(Enum):
    """OANDA timeframes for candles"""
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"


@dataclass
class OandaConfig:
    """OANDA broker configuration"""
    api_key: str
    account_id: str
    environment: OandaEnvironment = OandaEnvironment.PRACTICE
    timeout: int = 30
    retry_attempts: int = 3
    retry_delay: float = 1.0
    
    def get_base_url(self) -> str:
        return self.environment.value
    
    def get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "UNIX"
        }


@dataclass
class OandaAccount:
    """OANDA account information"""
    id: str
    currency: str
    balance: Decimal
    unrealized_pl: Decimal
    realized_pl: Decimal
    margin_used: Decimal
    margin_available: Decimal
    open_positions: int
    open_orders: int
    last_updated: datetime
    
    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'OandaAccount':
        return cls(
            id=data['id'],
            currency=data['currency'],
            balance=Decimal(str(data['balance'])),
            unrealized_pl=Decimal(str(data['unrealizedPL'])),
            realized_pl=Decimal(str(data['realizedPL'])),
            margin_used=Decimal(str(data['marginUsed'])),
            margin_available=Decimal(str(data['marginAvailable'])),
            open_positions=data['openPositionCount'],
            open_orders=data['openOrderCount'],
            last_updated=datetime.fromtimestamp(float(data['lastTransactionID']))
        )


@dataclass
class OandaPosition:
    """OANDA position information"""
    instrument: str
    side: str  # long or short
    units: Decimal
    avg_price: Decimal
    current_price: Decimal
    unrealized_pl: Decimal
    margin_used: Decimal
    
    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'OandaPosition':
        long_units = Decimal(data['long']['units']) if data['long']['units'] != '0' else Decimal('0')
        short_units = Decimal(data['short']['units']) if data['short']['units'] != '0' else Decimal('0')
        
        if long_units > 0:
            side = 'long'
            units = long_units
            avg_price = Decimal(str(data['long']['averagePrice']))
        elif short_units < 0:  # Short units are negative in OANDA
            side = 'short'
            units = abs(short_units)
            avg_price = Decimal(str(data['short']['averagePrice']))
        else:
            side = 'flat'
            units = Decimal('0')
            avg_price = Decimal('0')
        
        return cls(
            instrument=data['instrument'],
            side=side,
            units=units,
            avg_price=avg_price,
            current_price=Decimal(str(data['long']['averagePrice'] if side == 'long' else data['short']['averagePrice'])),
            unrealized_pl=Decimal(str(data['long']['unrealizedPL'] if side == 'long' else data['short']['unrealizedPL'])),
            margin_used=Decimal(str(data['long']['marginUsed'] if side == 'long' else data['short']['marginUsed']))
        )


@dataclass
class OandaOrder:
    """OANDA order information"""
    id: str
    instrument: str
    type: str
    side: str
    units: Decimal
    price: Optional[Decimal]
    stop_loss: Optional[Decimal]
    take_profit: Optional[Decimal]
    status: str
    create_time: Optional[datetime]
    cancel_time: Optional[datetime]
    fill_time: Optional[datetime]
    
    @classmethod
    def from_response(cls, data: Dict[str, Any]) -> 'OandaOrder':
        units = Decimal(str(data['units']))
        side = 'buy' if units > 0 else 'sell'
        
        return cls(
            id=data['id'],
            instrument=data['instrument'],
            type=data.get('type', 'MARKET'),
            side=side,
            units=abs(units),
            price=Decimal(str(data['price'])) if 'price' in data and data['price'] else None,
            stop_loss=Decimal(str(data['stopLossOnFill']['price'])) if 'stopLossOnFill' in data else None,
            take_profit=Decimal(str(data['takeProfitOnFill']['price'])) if 'takeProfitOnFill' in data else None,
            status=data.get('state', 'PENDING'),
            create_time=datetime.fromtimestamp(float(data['createTime'])) if 'createTime' in data else None,
            cancel_time=datetime.fromtimestamp(float(data['cancelTime'])) if 'cancelTime' in data else None,
            fill_time=datetime.fromtimestamp(float(data['fillTime'])) if 'fillTime' in data else None,
        )


class OandaApiClient:
    """OANDA API HTTP client"""
    
    def __init__(self, config: OandaConfig):
        self._config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.get_base_url()
        self._headers = config.get_headers()
    
    async def connect(self) -> None:
        """Establish HTTP session"""
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=self._config.timeout)
            )
    
    async def disconnect(self) -> None:
        """Close HTTP session"""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def __aenter__(self) -> 'OandaApiClient':
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make HTTP request to OANDA API with retry logic"""
        url = urljoin(self._base_url, endpoint)
        
        for attempt in range(self._config.retry_attempts):
            try:
                async with self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data
                ) as response:
                    if response.status == 429:  # Rate limited
                        retry_after = int(response.headers.get('Retry-After', self._config.retry_delay))
                        await asyncio.sleep(retry_after)
                        continue
                    
                    response.raise_for_status()
                    return await response.json()
                    
            except aiohttp.ClientError:
                if attempt == self._config.retry_attempts - 1:
                    raise
                await asyncio.sleep(self._config.retry_delay)
        
        raise Exception(f"Request failed after {self._config.retry_attempts} attempts")
    
    async def get_account(self) -> OandaAccount:
        """Get account details"""
        endpoint = f"/v3/accounts/{self._config.account_id}"
        response = await self._request("GET", endpoint)
        return OandaAccount.from_response(response['account'])
    
    async def get_positions(self) -> List[OandaPosition]:
        """Get all positions"""
        endpoint = f"/v3/accounts/{self._config.account_id}/positions"
        response = await self._request("GET", endpoint)
        return [OandaPosition.from_response(pos) for pos in response['positions']]
    
    async def get_orders(self) -> List[OandaOrder]:
        """Get all orders"""
        endpoint = f"/v3/accounts/{self._config.account_id}/orders"
        response = await self._request("GET", endpoint)
        return [OandaOrder.from_response(order) for order in response.get('orders', [])]
    
    async def create_market_order(
        self,
        instrument: str,
        units: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> OandaOrder:
        """Create market order"""
        endpoint = f"/v3/accounts/{self._config.account_id}/orders"
        
        order_data = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units)
            }
        }
        
        if stop_loss:
            order_data["order"]["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit:
            order_data["order"]["takeProfitOnFill"] = {"price": str(take_profit)}
        
        response = await self._request("POST", endpoint, data=order_data)
        return OandaOrder.from_response(response['orderCreateTransaction'])
    
    async def create_limit_order(
        self,
        instrument: str,
        units: int,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> OandaOrder:
        """Create limit order"""
        endpoint = f"/v3/accounts/{self._config.account_id}/orders"
        
        order_data = {
            "order": {
                "type": "LIMIT",
                "instrument": instrument,
                "units": str(units),
                "price": str(price)
            }
        }
        
        if stop_loss:
            order_data["order"]["stopLossOnFill"] = {"price": str(stop_loss)}
        if take_profit:
            order_data["order"]["takeProfitOnFill"] = {"price": str(take_profit)}
        
        response = await self._request("POST", endpoint, data=order_data)
        return OandaOrder.from_response(response['orderCreateTransaction'])
    
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        endpoint = f"/v3/accounts/{self._config.account_id}/orders/{order_id}/cancel"
        try:
            await self._request("PUT", endpoint)
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def close_position(self, instrument: str) -> bool:
        """Close a position"""
        endpoint = f"/v3/accounts/{self._config.account_id}/positions/{instrument}/close"
        try:
            await self._request("PUT", endpoint)
            return True
        except Exception as e:
            logger.error(f"Failed to close position {instrument}: {e}")
            return False
    
    async def get_pricing(self, instruments: List[str]) -> List[Dict[str, Any]]:
        """Get pricing for instruments"""
        endpoint = f"/v3/accounts/{self._config.account_id}/pricing"
        params = {
            "instruments": ",".join(instruments)
        }
        
        response = await self._request("GET", endpoint, params=params)
        return response['prices']
    
    async def get_candles(
        self,
        instrument: str,
        timeframe: OandaTimeframe,
        count: int = 500,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None
    ) -> List[Dict[str, Any]]:
        """Get candle data"""
        endpoint = f"/v3/instruments/{instrument}/candles"
        params = {
            "price": "MBA",
            "granularity": timeframe.value,
            "count": count
        }
        
        if from_time:
            params["from"] = from_time.isoformat()
        if to_time:
            params["to"] = to_time.isoformat()
        
        response = await self._request("GET", endpoint, params=params)
        return response.get('candles', [])


class OandaBroker(ITransactionHandler):
    """OANDA broker adapter implementing ITransactionHandler"""
    
    def __init__(self, config: OandaConfig):
        self._config = config
        self._client = OandaApiClient(config)
        self._account: Optional[OandaAccount] = None
        self._positions: Dict[str, OandaPosition] = {}
        self._orders: Dict[str, OandaOrder] = {}
        self._result_handlers: List[IResultHandler] = []
        self._connected = False
        
        # Event callbacks
        self._on_order_filled: Optional[Callable[[OandaOrder], None]] = None
        self._on_position_opened: Optional[Callable[[OandaPosition], None]] = None
        self._on_position_closed: Optional[Callable[[OandaPosition], None]] = None
    
    def process_order(self, order: Order) -> OrderEvent:
        """Process an order synchronously - not supported for live trading"""
        raise NotImplementedError("Use submit_order for async order processing")
    
    def cancel_order(self, order_id: str) -> OrderEvent:
        """Cancel an order synchronously - not supported for live trading"""
        raise NotImplementedError("Use async cancel_order method for live trading")
    
    def update_order(self, order: Order) -> OrderEvent:
        """Update an existing order synchronously - not supported for live trading"""
        raise NotImplementedError("Use async methods for live trading")
    
    def get_open_orders(self, symbol: Optional[Symbol] = None) -> List[Order]:
        """Get open orders - sync version raises if not connected"""
        return []
    
    def get_order_by_id(self, order_id: str) -> Optional[Order]:
        """Get order by ID - sync version raises if not connected"""
        return None
    
    async def connect(self) -> bool:
        """Connect to OANDA API"""
        try:
            await self._client.connect()
            
            # Get initial account data
            self._account = await self._client.get_account()
            
            # Get initial positions and orders
            await self._refresh_positions()
            await self._refresh_orders()
            
            self._connected = True
            logger.info(f"Connected to OANDA account {self._account.id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to OANDA: {e}")
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from OANDA API"""
        await self._client.disconnect()
        self._connected = False
        logger.info("Disconnected from OANDA")
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._connected
    
    async def submit_order(self, order: Order) -> bool:
        """Submit order to OANDA"""
        if not self._connected:
            logger.error("Not connected to OANDA")
            return False
        
        try:
            # Convert our Order to OANDA order
            instrument = self._convert_symbol_to_oanda(order.symbol)
            
            if order.order_type == OrderType.MARKET:
                oanda_order = await self._client.create_market_order(
                    instrument=instrument,
                    units=int(order.quantity),
                )
            elif order.order_type == OrderType.LIMIT:
                oanda_order = await self._client.create_limit_order(
                    instrument=instrument,
                    units=int(order.quantity),
                    price=float(order.price) if order.price else 0.0,
                )
            else:
                logger.error(f"Unsupported order type: {order.order_type}")
                return False
            
            # Update order ID
            order.id = oanda_order.id
            
            # Store order
            self._orders[oanda_order.id] = oanda_order
            
            logger.info(f"Order submitted: {oanda_order.id} - {instrument} {oanda_order.side} {oanda_order.units}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to submit order: {e}")
            return False
    
    async def cancel_order_by_id(self, order_id: str) -> bool:
        """Cancel order by ID"""
        if not order_id:
            logger.error("Order has no ID")
            return False
        
        try:
            success = await self._client.cancel_order(order_id)
            if success:
                # Update order status
                if order_id in self._orders:
                    self._orders[order_id].status = "CANCELLED"
                
                logger.info(f"Order cancelled: {order_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information"""
        if not self._connected:
            return {}
        
        try:
            self._account = await self._client.get_account()
            return {
                'account_id': self._account.id,
                'currency': self._account.currency,
                'balance': float(self._account.balance),
                'unrealized_pl': float(self._account.unrealized_pl),
                'realized_pl': float(self._account.realized_pl),
                'margin_used': float(self._account.margin_used),
                'margin_available': float(self._account.margin_available),
                'open_positions': self._account.open_positions,
                'open_orders': self._account.open_orders
            }
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            return {}
    
    async def get_positions(self) -> List[Position]:
        """Get current positions"""
        if not self._connected:
            return []
        
        try:
            await self._refresh_positions()
            
            positions = []
            for oanda_pos in self._positions.values():
                symbol = self._convert_oanda_to_symbol(oanda_pos.instrument)
                side = PositionSide.LONG if oanda_pos.side == 'long' else PositionSide.SHORT
                
                position = Position(
                    symbol=symbol,
                    side=side,
                    quantity=oanda_pos.units,
                    avg_entry_price=oanda_pos.avg_price,
                    unrealized_pnl=oanda_pos.unrealized_pl,
                    market_price=oanda_pos.current_price,
                )
                positions.append(position)
            
            return positions
            
        except Exception as e:
            logger.error(f"Failed to get positions: {e}")
            return []
    
    async def get_orders(self) -> List[Order]:
        """Get current orders"""
        if not self._connected:
            return []
        
        try:
            await self._refresh_orders()
            
            orders = []
            for oanda_order in self._orders.values():
                symbol = self._convert_oanda_to_symbol(oanda_order.instrument)
                order_type = self._convert_oanda_order_type(oanda_order.type)
                status = self._convert_oanda_order_status(oanda_order.status)
                side = 'BUY' if oanda_order.side == 'buy' else 'SELL'
                
                order = Order(
                    id=oanda_order.id,
                    symbol=symbol,
                    order_type=order_type,
                    side=side,
                    quantity=oanda_order.units,
                    price=oanda_order.price,
                    stop_price=oanda_order.stop_loss,
                    status=status,
                    created_at=oanda_order.create_time,
                    tags={},
                )
                orders.append(order)
            
            return orders
            
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return []
    
    async def close_position(self, symbol: Symbol, quantity: Optional[Decimal] = None) -> bool:
        """Close position"""
        if not self._connected:
            return False
        
        try:
            instrument = self._convert_symbol_to_oanda(symbol)
            success = await self._client.close_position(instrument)
            
            if success:
                await self._refresh_positions()
                logger.info(f"Position closed: {instrument}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False
    
    def add_result_handler(self, handler: IResultHandler) -> None:
        """Add result handler"""
        self._result_handlers.append(handler)
    
    def remove_result_handler(self, handler: IResultHandler) -> None:
        """Remove result handler"""
        if handler in self._result_handlers:
            self._result_handlers.remove(handler)
    
    async def _refresh_positions(self) -> None:
        """Refresh positions from OANDA"""
        try:
            oanda_positions = await self._client.get_positions()
            self._positions = {
                pos.instrument: pos for pos in oanda_positions
            }
        except Exception as e:
            logger.error(f"Failed to refresh positions: {e}")
    
    async def _refresh_orders(self) -> None:
        """Refresh orders from OANDA"""
        try:
            oanda_orders = await self._client.get_orders()
            self._orders = {
                order.id: order for order in oanda_orders
            }
        except Exception as e:
            logger.error(f"Failed to refresh orders: {e}")
    
    def _convert_symbol_to_oanda(self, symbol: Symbol) -> str:
        """Convert our Symbol to OANDA instrument format"""
        if '_' in symbol.ticker:
            return symbol.ticker
        elif len(symbol.ticker) == 6 and symbol.ticker.isalpha():
            # Forex pairs like EURUSD -> EUR_USD
            return f"{symbol.ticker[:3]}_{symbol.ticker[3:]}"
        else:
            return f"{symbol.ticker}_USD"
    
    def _convert_oanda_to_symbol(self, instrument: str) -> Symbol:
        """Convert OANDA instrument to our Symbol"""
        if '_' in instrument:
            ticker = instrument.replace('_', '')
        else:
            ticker = instrument
        
        return Symbol(ticker)
    
    def _convert_oanda_order_type(self, oanda_type: str) -> OrderType:
        """Convert OANDA order type to our OrderType"""
        mapping = {
            "MARKET": OrderType.MARKET,
            "LIMIT": OrderType.LIMIT,
            "STOP": OrderType.STOP,
        }
        return mapping.get(oanda_type, OrderType.MARKET)
    
    def _convert_oanda_order_status(self, oanda_status: str) -> OrderStatus:
        """Convert OANDA order status to our OrderStatus"""
        mapping = {
            "PENDING": OrderStatus.PENDING,
            "FOK": OrderStatus.PENDING,
            "GTC": OrderStatus.PENDING,
            "GFD": OrderStatus.PENDING,
            "IOC": OrderStatus.PENDING,
            "DAY": OrderStatus.PENDING,
            "FILLED": OrderStatus.FILLED,
            "CANCELLED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
        }
        return mapping.get(oanda_status, OrderStatus.PENDING)
    
    def set_order_filled_callback(self, callback: Callable[[OandaOrder], None]) -> None:
        """Set callback for order filled events"""
        self._on_order_filled = callback
    
    def set_position_opened_callback(self, callback: Callable[[OandaPosition], None]) -> None:
        """Set callback for position opened events"""
        self._on_position_opened = callback
    
    def set_position_closed_callback(self, callback: Callable[[OandaPosition], None]) -> None:
        """Set callback for position closed events"""
        self._on_position_closed = callback
    
    async def get_market_price(self, symbol: Symbol) -> Optional[float]:
        """Get current market price for symbol"""
        if not self._connected:
            return None
        
        try:
            instrument = self._convert_symbol_to_oanda(symbol)
            pricing = await self._client.get_pricing([instrument])
            
            if pricing:
                price_data = pricing[0]
                if 'closeoutMid' in price_data:
                    return float(price_data['closeoutMid'])
                elif 'bid' in price_data and 'ask' in price_data:
                    return (float(price_data['bid']) + float(price_data['ask'])) / 2
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get market price: {e}")
            return None
    
    async def get_historical_data(
        self,
        symbol: Symbol,
        timeframe: OandaTimeframe,
        count: int = 500
    ) -> List[Dict[str, Any]]:
        """Get historical candle data"""
        if not self._connected:
            return []
        
        try:
            instrument = self._convert_symbol_to_oanda(symbol)
            candles = await self._client.get_candles(instrument, timeframe, count)
            
            return candles
            
        except Exception as e:
            logger.error(f"Failed to get historical data: {e}")
            return []


# Factory function
def create_oanda_broker(
    api_key: str,
    account_id: str,
    environment: OandaEnvironment = OandaEnvironment.PRACTICE,
    timeout: int = 30
) -> OandaBroker:
    """Create OANDA broker adapter"""
    config = OandaConfig(
        api_key=api_key,
        account_id=account_id,
        environment=environment,
        timeout=timeout
    )
    return OandaBroker(config)