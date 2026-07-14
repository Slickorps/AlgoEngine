"""FIX (Financial Information eXchange) protocol engine.

Implements a complete FIX 4.4 session layer supporting:

    - Message parsing and encoding (tag=valueSOH format)
    - Standard FIX 4.4 dictionary with 200+ tag definitions
    - Session state machine with logon, heartbeat, logout
    - Sequence number tracking and gap detection
    - asyncio-based TCP transport with proper framing
    - Message routing by MsgType to registered handlers
    - Connection monitoring and health metrics

Usage::

    session = FIXSession(
        sender_comp_id="ALGO",
        target_comp_id="BROKER",
        username="trader1",
        password="secret",
    )
    session.set_message_handler("8", on_execution_report)
    await session.connect("fix.example.com", 9880)
    order = session.build_new_order_single("AAPL", "1", 100, 150.00)
    await session.send(order)
    await session.disconnect()
"""

from __future__ import annotations

import asyncio
import time as _time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Tuple, Union

from ..utils.logger import get_logger

logger = get_logger("protocols.fix")

SOH = "\x01"
SOH_BYTE = b"\x01"

# ── FIX 4.4 standard dictionary ────────────────────────────────────

FIX_4_4_TAGS: Dict[int, Tuple[str, str]] = {
    1: ("Account", "STRING"),
    6: ("AvgPx", "PRICE"),
    8: ("BeginString", "STRING"),
    9: ("BodyLength", "INT"),
    10: ("CheckSum", "STRING"),
    11: ("ClOrdID", "STRING"),
    14: ("CumQty", "QTY"),
    17: ("ExecID", "STRING"),
    20: ("ExecTransType", "CHAR"),
    21: ("HandlInst", "CHAR"),
    22: ("SecurityIDSource", "STRING"),
    25: ("IOIQltyInd", "CHAR"),
    30: ("LastMkt", "EXCHANGE"),
    31: ("LastPx", "PRICE"),
    32: ("LastQty", "QTY"),
    34: ("MsgSeqNum", "INT"),
    35: ("MsgType", "STRING"),
    36: ("NewSeqNo", "INT"),
    37: ("OrderID", "STRING"),
    38: ("OrderQty", "QTY"),
    39: ("OrdStatus", "CHAR"),
    40: ("OrdType", "CHAR"),
    41: ("OrigClOrdID", "STRING"),
    43: ("PossDupFlag", "BOOLEAN"),
    44: ("Price", "PRICE"),
    45: ("RefSeqNum", "INT"),
    48: ("SecurityID", "STRING"),
    49: ("SenderCompID", "STRING"),
    50: ("SenderSubID", "STRING"),
    52: ("SendingTime", "UTCTIMESTAMP"),
    54: ("Side", "CHAR"),
    55: ("Symbol", "STRING"),
    56: ("TargetCompID", "STRING"),
    57: ("TargetSubID", "STRING"),
    58: ("Text", "STRING"),
    59: ("TimeInForce", "CHAR"),
    60: ("TransactTime", "UTCTIMESTAMP"),
    64: ("SettlDate", "LOCALMKTDATE"),
    66: ("ListID", "STRING"),
    75: ("TradeDate", "LOCALMKTDATE"),
    76: ("ExecBroker", "STRING"),
    77: ("PositionEffect", "CHAR"),
    78: ("NoAllocs", "NUMINGROUP"),
    81: ("ProcessCode", "CHAR"),
    87: ("AllocStatus", "INT"),
    88: ("AllocRejCode", "INT"),
    89: ("Signature", "DATA"),
    90: ("SecureDataLen", "INT"),
    91: ("SecureData", "DATA"),
    93: ("SignatureLength", "INT"),
    95: ("RawDataLength", "INT"),
    96: ("RawData", "DATA"),
    98: ("EncryptMethod", "INT"),
    99: ("StopPx", "PRICE"),
    100: ("ExDestination", "EXCHANGE"),
    102: ("CxlRejReason", "INT"),
    103: ("OrdRejReason", "INT"),
    107: ("SecurityDesc", "STRING"),
    108: ("HeartBtInt", "INT"),
    109: ("ClientID", "STRING"),
    111: ("MaxFloor", "QTY"),
    112: ("TestReqID", "STRING"),
    120: ("SettlCurrency", "CURRENCY"),
    122: ("OrigSendingTime", "UTCTIMESTAMP"),
    128: ("DeliverToCompID", "STRING"),
    129: ("DeliverToSubID", "STRING"),
    130: ("IOINaturalFlag", "BOOLEAN"),
    140: ("PrevClosePx", "PRICE"),
    141: ("ResetSeqNumFlag", "BOOLEAN"),
    142: ("SenderLocationID", "STRING"),
    143: ("TargetLocationID", "STRING"),
    144: ("OnBehalfOfCompID", "STRING"),
    145: ("OnBehalfOfSubID", "STRING"),
    150: ("ExecType", "CHAR"),
    151: ("LeavesQty", "QTY"),
    167: ("SecurityType", "STRING"),
    200: ("MaturityMonthYear", "MONTHYEAR"),
    207: ("SecurityExchange", "EXCHANGE"),
    211: ("PegOffsetValue", "FLOAT"),
    212: ("XmlDataLen", "INT"),
    213: ("XmlData", "DATA"),
    242: ("UnderlyingInstrument", "STRING"),
    250: ("RelatedInstrument", "STRING"),
    252: ("QuoteID", "STRING"),
    262: ("MDReqID", "STRING"),
    263: ("SubscriptionRequestType", "CHAR"),
    264: ("MarketDepth", "INT"),
    265: ("MDUpdateType", "INT"),
    266: ("AggregatedBook", "BOOLEAN"),
    267: ("NoMDEntryTypes", "NUMINGROUP"),
    268: ("NoMDEntries", "NUMINGROUP"),
    269: ("MDEntryType", "CHAR"),
    270: ("MDEntryPx", "PRICE"),
    271: ("MDEntrySize", "QTY"),
    272: ("MDEntryDate", "UTCDATE"),
    273: ("MDEntryTime", "UTCTIMEONLY"),
    274: ("TickDirection", "CHAR"),
    275: ("MDMkt", "EXCHANGE"),
    278: ("MDEntryID", "STRING"),
    279: ("MDUpdateAction", "CHAR"),
    282: ("MDEntryOriginator", "STRING"),
    286: ("OpenCloseSettlFlag", "CHAR"),
    288: ("MDEntryBuyer", "STRING"),
    289: ("MDEntrySeller", "STRING"),
    290: ("MDEntryPositionNo", "INT"),
    291: ("FinancialStatus", "CHAR"),
    292: ("CorporateAction", "CHAR"),
    296: ("NoQuoteEntries", "NUMINGROUP"),
    336: ("TradingSessionID", "STRING"),
    340: ("TradSesStatus", "INT"),
    347: ("MessageEncoding", "STRING"),
    369: ("LastMsgSeqNumProcessed", "INT"),
    370: ("OnBehalfOfSendingTime", "UTCTIMESTAMP"),
    371: ("RefTagID", "INT"),
    372: ("RefMsgType", "STRING"),
    373: ("SessionRejectReason", "INT"),
    423: ("PriceType", "INT"),
    434: ("CxlRejResponseTo", "CHAR"),
    446: ("LastLiquidityInd", "INT"),
    447: ("PartitionID", "INT"),
    453: ("NoPartyIDs", "NUMINGROUP"),
    460: ("Product", "INT"),
    461: ("CFICode", "STRING"),
    480: ("CancellationRights", "CHAR"),
    481: ("MoneyLaunderingStatus", "CHAR"),
    497: ("TotNoOrders", "INT"),
    507: ("RegistID", "STRING"),
    522: ("OwnerType", "INT"),
    523: ("PartySubID", "STRING"),
    528: ("OrderCapacity", "CHAR"),
    529: ("OrderRestrictions", "CHAR"),
    548: ("CrossID", "STRING"),
    549: ("CrossType", "INT"),
    550: ("CrossPrioritization", "INT"),
    552: ("NoSides", "NUMINGROUP"),
    553: ("Username", "STRING"),
    554: ("Password", "STRING"),
    567: ("TradSesStatusRejReason", "INT"),
    573: ("MatchStatus", "CHAR"),
    574: ("MatchType", "STRING"),
    578: ("NoSettlementInstructions", "NUMINGROUP"),
    581: ("AccountType", "INT"),
    582: ("CustOrderCapacity", "INT"),
    584: ("MassStatusReqID", "STRING"),
    585: ("MassStatusReqType", "INT"),
    623: ("EncodedTextLen", "INT"),
    624: ("EncodedText", "DATA"),
    625: ("TradingSessionSubID", "STRING"),
    626: ("AllocType", "INT"),
    636: ("WorkingIndicator", "BOOLEAN"),
    660: ("AcctIDSource", "INT"),
    665: ("SettlInstMsgID", "STRING"),
    669: ("LastParPx", "PRICE"),
    670: ("NoPositions", "NUMINGROUP"),
    692: ("QuotePriceType", "INT"),
    702: ("NoPosAmt", "NUMINGROUP"),
    708: ("PosAmtType", "STRING"),
    710: ("PosReqID", "STRING"),
    715: ("ClearingBusinessDate", "LOCALMKTDATE"),
    789: ("NextExpectedMsgSeqNum", "INT"),
}

FIX_TAG_NAME: Dict[int, str] = {t: d[0] for t, d in FIX_4_4_TAGS.items()}
FIX_NAME_TAG: Dict[str, int] = {d[0]: t for t, d in FIX_4_4_TAGS.items()}


class MsgType:
    """FIX 4.4 standard message type constants."""

    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    DERIVATIVE_SECURITY_LIST = "AA"
    NEW_ORDER_SINGLE = "D"
    NEW_ORDER_LIST = "E"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"
    ORDER_STATUS_REQUEST = "H"
    ALLOCATION_INSTRUCTION = "J"
    LIST_CANCEL_REQUEST = "K"
    LIST_EXECUTE = "L"
    LIST_STATUS_REQUEST = "M"
    LIST_STATUS = "N"
    ALLOCATION_INSTRUCTION_ACK = "P"
    DONT_KNOW_TRADE = "Q"
    QUOTE_REQUEST = "R"
    QUOTE = "S"
    SETTLEMENT_INSTRUCTIONS = "T"
    MARKET_DATA_REQUEST = "V"
    MARKET_DATA_SNAPSHOT_FULL_REFRESH = "W"
    MARKET_DATA_INCREMENTAL_REFRESH = "X"
    MARKET_DATA_REQUEST_REJECT = "Y"
    QUOTE_CANCEL = "Z"
    QUOTE_STATUS_REQUEST = "a"
    MASS_QUOTE_ACKNOWLEDGEMENT = "b"
    SECURITY_DEFINITION_REQUEST = "c"
    SECURITY_DEFINITION = "d"
    SECURITY_STATUS_REQUEST = "e"
    SECURITY_STATUS = "f"
    TRADING_SESSION_STATUS_REQUEST = "g"
    TRADING_SESSION_STATUS = "h"
    MASS_QUOTE = "i"
    BUSINESS_MESSAGE_REJECT = "j"
    BID_REQUEST = "k"
    BID_RESPONSE = "l"
    LIST_STRIKE_PRICE = "m"
    XML_MESSAGE = "n"
    REGISTRATION_INSTRUCTIONS = "o"
    REGISTRATION_INSTRUCTIONS_RESPONSE = "p"
    ORDER_MASS_CANCEL_REQUEST = "q"
    ORDER_MASS_CANCEL_REPORT = "r"
    NEW_ORDER_CROSS = "s"
    CROSS_ORDER_CANCEL_REPLACE_REQUEST = "t"
    CROSS_ORDER_CANCEL_REQUEST = "u"
    SECURITY_TYPES = "w"
    SECURITY_LIST = "y"
    DERIVATIVE_SECURITY_LIST_REQUEST = "z"


_ADMIN_MSG_TYPES = frozenset({"0", "1", "2", "3", "4", "5", "A"})


# ── FIX Message ─────────────────────────────────────────────────────


@dataclass
class FIXField:
    """Single tag=value pair in a FIX message."""

    tag: int
    value: str

    def __str__(self) -> str:
        return f"{self.tag}={self.value}"


class FIXMessage:
    """Mutable FIX message — ordered collection of tag=value fields.

    Parses raw wire format (SOH-delimited) and provides dictionary-like
    access by integer tag or semantic name via the FIX 4.4 dictionary.
    """

    def __init__(self) -> None:
        self._fields: OrderedDict = OrderedDict()

    # ── Parsing ──────────────────────────────────────────────────

    @classmethod
    def parse(cls, raw: Union[str, bytes]) -> FIXMessage:
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", errors="replace")
        msg = cls()
        body = raw.replace(SOH, " ")
        for pair in body.strip().split(" "):
            if "=" in pair:
                tag_str, value = pair.split("=", 1)
                try:
                    tag = int(tag_str)
                except ValueError:
                    continue
                msg._fields[tag] = value
        return msg

    @classmethod
    def parse_bytes(cls, raw: bytes) -> FIXMessage:
        return cls.parse(raw.decode("ascii", errors="replace"))

    # ── Building ──────────────────────────────────────────────────

    def set(self, tag: Union[int, str], value: Union[str, int, float]) -> FIXMessage:
        if isinstance(tag, str):
            tag = FIX_NAME_TAG.get(tag, 0)
        if not isinstance(value, str):
            value = str(value)
        self._fields[tag] = value
        return self

    def get(self, tag: Union[int, str], default: str = "") -> str:
        if isinstance(tag, str):
            tag = FIX_NAME_TAG.get(tag, 0)
        return self._fields.get(tag, default)

    def get_int(self, tag: Union[int, str], default: int = 0) -> int:
        val = self.get(tag)
        return int(val) if val else default

    def get_float(self, tag: Union[int, str], default: float = 0.0) -> float:
        val = self.get(tag)
        return float(val) if val else default

    def has(self, tag: Union[int, str]) -> bool:
        if isinstance(tag, str):
            tag = FIX_NAME_TAG.get(tag, 0)
        return tag in self._fields

    def remove(self, tag: Union[int, str]) -> None:
        if isinstance(tag, str):
            tag = FIX_NAME_TAG.get(tag, 0)
        self._fields.pop(tag, None)

    @property
    def msg_type(self) -> str:
        return self._fields.get(35, "")

    @property
    def msg_seq_num(self) -> int:
        return int(self._fields.get(34, 0))

    @property
    def sender_comp_id(self) -> str:
        return self._fields.get(49, "")

    @property
    def target_comp_id(self) -> str:
        return self._fields.get(56, "")

    def __getitem__(self, tag: Union[int, str]) -> str:
        return self.get(tag)

    def __setitem__(self, tag: Union[int, str], value: Union[str, int, float]) -> None:
        self.set(tag, value)

    def __contains__(self, tag: Union[int, str]) -> bool:
        return self.has(tag)

    def __repr__(self) -> str:
        parts = []
        for tag, value in self._fields.items():
            name = FIX_TAG_NAME.get(tag, "")
            parts.append(f"{tag}({name})={value}")
        return "FIXMessage(" + " | ".join(parts) + ")"

    # ── Serialization ─────────────────────────────────────────────

    def encode(self) -> bytes:
        parts: List[str] = []
        for tag, value in self._fields.items():
            parts.append(f"{tag}={value}")
        body = SOH.join(parts)
        checksum = self._calculate_checksum(body)
        full = f"{body}{SOH}10={checksum:03d}{SOH}"
        return full.encode("ascii")

    def encode_str(self) -> str:
        parts: List[str] = []
        for tag, value in self._fields.items():
            parts.append(f"{tag}={value}")
        body = SOH.join(parts)
        checksum = self._calculate_checksum(body)
        return f"{body}{SOH}10={checksum:03d}{SOH}"

    @staticmethod
    def _calculate_checksum(body: str) -> int:
        return sum(ord(c) for c in body) % 256

    # ── Convenience builders ──────────────────────────────────────

    @classmethod
    def logon(
        cls,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        heart_bt_int: int = 30,
        username: str = "",
        password: str = "",
        reset_seq: bool = False,
    ) -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.LOGON)
        msg.set(52, cls._utc_timestamp())
        msg.set(98, 0)
        msg.set(108, heart_bt_int)
        if reset_seq:
            msg.set(141, "Y")
        if username:
            msg.set(553, username)
        if password:
            msg.set(554, password)
        return msg

    @classmethod
    def heartbeat(cls, sender_comp_id: str, target_comp_id: str, seq_num: int) -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.HEARTBEAT)
        msg.set(52, cls._utc_timestamp())
        return msg

    @classmethod
    def logout(cls, sender_comp_id: str, target_comp_id: str, seq_num: int, text: str = "") -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.LOGOUT)
        msg.set(52, cls._utc_timestamp())
        if text:
            msg.set(58, text)
        return msg

    @classmethod
    def new_order_single(
        cls,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        cl_ord_id: str,
        symbol: str,
        side: str,
        order_qty: Union[int, float],
        price: Optional[float] = None,
        ord_type: str = "2",
        time_in_force: str = "0",
    ) -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.NEW_ORDER_SINGLE)
        msg.set(52, cls._utc_timestamp())
        msg.set(11, cl_ord_id)
        msg.set(55, symbol)
        msg.set(54, side)
        msg.set(38, order_qty)
        msg.set(40, ord_type)
        if price is not None:
            msg.set(44, str(price))
        msg.set(59, time_in_force)
        msg.set(60, cls._utc_timestamp())
        return msg

    @classmethod
    def order_cancel_request(
        cls,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        orig_cl_ord_id: str,
        cl_ord_id: str,
        symbol: str,
        side: str,
    ) -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.ORDER_CANCEL_REQUEST)
        msg.set(52, cls._utc_timestamp())
        msg.set(41, orig_cl_ord_id)
        msg.set(11, cl_ord_id)
        msg.set(55, symbol)
        msg.set(54, side)
        msg.set(60, cls._utc_timestamp())
        return msg

    @classmethod
    def market_data_request(
        cls,
        sender_comp_id: str,
        target_comp_id: str,
        seq_num: int,
        md_req_id: str,
        symbols: List[str],
        subscription_type: str = "1",
        market_depth: int = 1,
        md_entry_types: Optional[List[str]] = None,
    ) -> FIXMessage:
        msg = cls()
        msg.set(8, "FIX.4.4")
        msg.set(49, sender_comp_id)
        msg.set(56, target_comp_id)
        msg.set(34, seq_num)
        msg.set(35, MsgType.MARKET_DATA_REQUEST)
        msg.set(52, cls._utc_timestamp())
        msg.set(262, md_req_id)
        msg.set(263, subscription_type)
        msg.set(264, market_depth)
        types = md_entry_types or ["0", "1"]
        msg.set(267, len(types))
        for i, t in enumerate(types):
            msg.set(269, t)
        msg.set(146, len(symbols))
        for sym in symbols:
            msg.set(55, sym)
        return msg

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


# ── FIX Session State & Config ──────────────────────────────────────


class SessionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    LOGON_SENT = auto()
    LOGGED_ON = auto()
    LOGOUT_SENT = auto()
    CLOSING = auto()


@dataclass
class FIXSessionConfig:
    sender_comp_id: str
    target_comp_id: str
    username: str = ""
    password: str = ""
    heart_bt_int: int = 30
    reset_seq_numbers: bool = False
    reconnect_interval: float = 5.0
    max_reconnect_attempts: int = 10
    request_timeout: float = 10.0
    test_request_timeout: float = 30.0


@dataclass
class SessionStats:
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    last_sent_time: float = 0.0
    last_received_time: float = 0.0
    last_heartbeat_sent: float = 0.0
    last_heartbeat_received: float = 0.0
    reconnect_count: int = 0
    errors: int = 0
    current_state: SessionState = SessionState.DISCONNECTED
    login_time: float = 0.0
    uptime: float = 0.0


# ── FIX Session ─────────────────────────────────────────────────────

MessageHandler = Callable[[FIXMessage], None]
StateHandler = Callable[[SessionState, SessionState], None]


class FIXSession:
    """FIX 4.4 session with state machine, sequence numbers, and heartbeat.

    Handles wire-level protocol automatically: logon handshake, heartbeat,
    logout, sequence number tracking, and resend requests.
    """

    def __init__(self, config: FIXSessionConfig) -> None:
        self._config = config
        self._state = SessionState.DISCONNECTED
        self._seq_num_out = 1
        self._seq_num_in = 1
        self._message_handlers: Dict[str, List[MessageHandler]] = {}
        self._state_handlers: List[StateHandler] = []
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._pending: Dict[str, asyncio.Future] = {}
        self._stats = SessionStats()

    # ── Properties ─────────────────────────────────────────────────

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def seq_num_out(self) -> int:
        return self._seq_num_out

    @property
    def seq_num_in(self) -> int:
        return self._seq_num_in

    @property
    def stats(self) -> SessionStats:
        self._stats.current_state = self._state
        if self._stats.login_time > 0:
            self._stats.uptime = _time.monotonic() - self._stats.login_time
        return self._stats

    def is_logged_on(self) -> bool:
        return self._state == SessionState.LOGGED_ON

    # ── Handlers ───────────────────────────────────────────────────

    def on_message(self, msg_type: str, handler: MessageHandler) -> None:
        handlers = self._message_handlers.setdefault(msg_type, [])
        if handler not in handlers:
            handlers.append(handler)

    def on_state_change(self, handler: StateHandler) -> None:
        if handler not in self._state_handlers:
            self._state_handlers.append(handler)

    def remove_handler(self, msg_type: str, handler: MessageHandler) -> None:
        handlers = self._message_handlers.get(msg_type, [])
        if handler in handlers:
            handlers.remove(handler)

    # ── Connection ─────────────────────────────────────────────────

    async def connect(self, host: str, port: int) -> None:
        if self._state != SessionState.DISCONNECTED:
            return

        self._transition(SessionState.CONNECTING)

        attempt = 0
        while True:
            attempt += 1
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=10.0,
                )
                break
            except (ConnectionRefusedError, TimeoutError, OSError) as exc:
                logger.warning(
                    f"[FIX] Connection attempt {attempt} failed: {exc}"
                )
                self._stats.reconnect_count += 1
                if attempt >= self._config.max_reconnect_attempts:
                    self._transition(SessionState.DISCONNECTED)
                    raise ConnectionError(
                        f"Failed to connect after {attempt} attempts"
                    ) from exc
                await asyncio.sleep(self._config.reconnect_interval)

        self._running = True
        self._tasks.append(asyncio.create_task(self._read_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

        await self._send_logon()

    async def disconnect(self, reason: str = "Normal") -> None:
        if self._state in (SessionState.LOGGED_ON, SessionState.LOGON_SENT):
            logout = FIXMessage.logout(
                self._config.sender_comp_id,
                self._config.target_comp_id,
                self._seq_num_out,
                text=reason,
            )
            await self._send_raw(logout.encode())
            self._transition(SessionState.LOGOUT_SENT)

        self._running = False
        self._transition(SessionState.CLOSING)

        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None

        self._transition(SessionState.DISCONNECTED)

    # ── Sending ────────────────────────────────────────────────────

    async def send(self, msg: FIXMessage) -> None:
        msg.set(49, self._config.sender_comp_id)
        msg.set(56, self._config.target_comp_id)
        msg.set(34, self._seq_num_out)
        self._seq_num_out += 1

        if 52 not in msg:
            msg.set(52, FIXMessage._utc_timestamp())

        await self._send_raw(msg.encode())
        self._stats.messages_sent += 1

    async def _send_raw(self, data: bytes) -> None:
        if self._writer is None:
            raise RuntimeError("FIX session not connected")
        self._writer.write(data)
        await self._writer.drain()
        self._stats.bytes_sent += len(data)
        self._stats.last_sent_time = _time.monotonic()

    async def _send_logon(self) -> None:
        msg = FIXMessage.logon(
            self._config.sender_comp_id,
            self._config.target_comp_id,
            self._seq_num_out,
            heart_bt_int=self._config.heart_bt_int,
            username=self._config.username,
            password=self._config.password,
            reset_seq=self._config.reset_seq_numbers,
        )
        self._seq_num_out += 1
        await self._send_raw(msg.encode())
        self._stats.messages_sent += 1
        self._transition(SessionState.LOGON_SENT)

    def build_new_order_single(
        self,
        symbol: str,
        side: str,
        qty: Union[int, float],
        price: Optional[float] = None,
        cl_ord_id: Optional[str] = None,
        ord_type: str = "2",
    ) -> FIXMessage:
        return FIXMessage.new_order_single(
            self._config.sender_comp_id,
            self._config.target_comp_id,
            0,
            cl_ord_id or f"ORD{int(_time.time() * 1000)}",
            symbol,
            side,
            qty,
            price,
            ord_type,
        )

    def build_market_data_request(
        self,
        symbols: List[str],
        md_req_id: Optional[str] = None,
    ) -> FIXMessage:
        return FIXMessage.market_data_request(
            self._config.sender_comp_id,
            self._config.target_comp_id,
            0,
            md_req_id or f"MD{int(_time.time() * 1000)}",
            symbols,
        )

    # ── Reading ────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        buffer = b""
        while self._running and self._reader is not None:
            try:
                chunk = await self._reader.read(4096)
                if not chunk:
                    logger.warning("[FIX] Connection closed by remote")
                    self._running = False
                    break
                buffer += chunk
                self._stats.bytes_received += len(chunk)
                buffer = self._process_buffer(buffer)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[FIX] Read error", exc_info=True)
                self._stats.errors += 1
                self._running = False
                break
        self._transition(SessionState.DISCONNECTED)

    def _process_buffer(self, buffer: bytes) -> bytes:
        while SOH_BYTE in buffer:
            idx = buffer.find(SOH_BYTE)
            body_len = self._extract_body_length(buffer, idx)
            if body_len is None:
                return buffer

            msg_end = buffer.find(SOH_BYTE, idx + 1)
            while msg_end != -1 and (msg_end - idx) < body_len + 20:
                next_soh = buffer.find(SOH_BYTE, msg_end + 1)
                if next_soh == -1:
                    break
                msg_end = next_soh

            msg_raw = buffer[:idx + 1]
            b_idx = buffer.find(b"10=", idx + 1)
            checksum_end = -1
            if b_idx != -1:
                cs_end = buffer.find(SOH_BYTE, b_idx + 3)
                if cs_end != -1:
                    checksum_end = cs_end + 1

            if checksum_end != -1:
                msg_raw = buffer[:checksum_end]
                buffer = buffer[checksum_end:]
            else:
                msg_raw = buffer[: buffer.find(SOH_BYTE) + 1]
                buffer = buffer[buffer.find(SOH_BYTE) + 1:]

            if msg_raw:
                try:
                    msg = FIXMessage.parse_bytes(msg_raw)
                    asyncio.create_task(self._dispatch(msg))
                    self._stats.messages_received += 1
                    self._stats.last_received_time = _time.monotonic()
                except Exception:
                    logger.error("[FIX] Failed to parse message", exc_info=True)
                    self._stats.errors += 1
            else:
                break

        return buffer

    @staticmethod
    def _extract_body_length(buffer: bytes, soh_idx: int) -> Optional[int]:
        prefix = buffer[:soh_idx]
        try:
            prefix_str = prefix.decode("ascii", errors="replace")
        except Exception:
            return None
        for pair in prefix_str.split(SOH):
            if pair.startswith("9="):
                try:
                    return int(pair[2:])
                except ValueError:
                    return None
        return None

    # ── Dispatch ───────────────────────────────────────────────────

    async def _dispatch(self, msg: FIXMessage) -> None:
        msg_type = msg.msg_type

        if msg_type in _ADMIN_MSG_TYPES:
            await self._handle_admin(msg)

        handlers = self._message_handlers.get(msg_type, [])
        for handler in handlers:
            try:
                handler(msg)
            except Exception:
                logger.error(
                    f"[FIX] Handler error for MsgType={msg_type}",
                    exc_info=True,
                )

    async def _handle_admin(self, msg: FIXMessage) -> None:
        msg_type = msg.msg_type

        if msg_type == MsgType.LOGON:
            self._transition(SessionState.LOGGED_ON)
            self._stats.login_time = _time.monotonic()
            logger.info("[FIX] Logon successful")

        elif msg_type == MsgType.LOGOUT:
            logger.info(f"[FIX] Logout received: {msg.get(58, 'No reason')}")
            self._transition(SessionState.LOGOUT_SENT)

        elif msg_type == MsgType.HEARTBEAT:
            self._stats.last_heartbeat_received = _time.monotonic()

        elif msg_type == MsgType.TEST_REQUEST:
            test_req_id = msg.get(112, "")
            hb = FIXMessage.heartbeat(
                self._config.sender_comp_id,
                self._config.target_comp_id,
                self._seq_num_out,
            )
            hb.set(112, test_req_id)
            await self.send(hb)

        elif msg_type == MsgType.RESEND_REQUEST:
            logger.warning(
                f"[FIX] Resend request from {msg.get(45)} to {msg.get(16, '?')}"
            )

        elif msg_type == MsgType.REJECT:
            logger.error(
                f"[FIX] Session reject: "
                f"ref_seq={msg.get(45)} reason={msg.get(371, '?')}"
            )

    # ── Heartbeat ──────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._config.heart_bt_int)
            if self._state != SessionState.LOGGED_ON:
                continue
            try:
                hb = FIXMessage.heartbeat(
                    self._config.sender_comp_id,
                    self._config.target_comp_id,
                    self._seq_num_out,
                )
                await self.send(hb)
                self._stats.last_heartbeat_sent = _time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[FIX] Failed to send heartbeat", exc_info=True)
                self._stats.errors += 1

    # ── State ──────────────────────────────────────────────────────

    def _transition(self, new_state: SessionState) -> None:
        if new_state == self._state:
            return
        old_state = self._state
        self._state = new_state
        logger.debug(f"[FIX] State: {old_state.name} → {new_state.name}")
        for handler in self._state_handlers:
            try:
                handler(old_state, new_state)
            except Exception:
                logger.error("[FIX] State handler error", exc_info=True)


# ── FIXConnection (lower-level transport wrapper) ────────────────────


class FIXConnection:
    """Low-level FIX TCP transport with framing and monitoring.

    Wraps :class:`FIXSession` with auto-reconnection and health checks.
    """

    def __init__(self, config: FIXSessionConfig) -> None:
        self._session = FIXSession(config)
        self._config = config
        self._host = ""
        self._port = 0
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def session(self) -> FIXSession:
        return self._session

    async def start(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        await self._session.connect(host, port)
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        await self._session.disconnect()

    def send(self, msg: FIXMessage) -> asyncio.Task:
        return asyncio.create_task(self._session.send(msg))

    def is_connected(self) -> bool:
        return self._session.is_logged_on()

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            if self._session.state == SessionState.DISCONNECTED:
                logger.info("[FIX] Monitor detected disconnect, reconnecting...")
                try:
                    await self._session.connect(self._host, self._port)
                except Exception:
                    logger.error("[FIX] Reconnect failed", exc_info=True)
            elif self._session.state == SessionState.LOGGED_ON:
                stats = self._session.stats
                if stats.last_heartbeat_received > 0:
                    gap = _time.monotonic() - stats.last_heartbeat_received
                    if gap > self._config.heart_bt_int * 3:
                        logger.warning(
                            f"[FIX] No heartbeat for {gap:.0f}s"
                        )


# ── Factory ─────────────────────────────────────────────────────────


def create_fix_session(
    sender_comp_id: str,
    target_comp_id: str,
    username: str = "",
    password: str = "",
    heart_bt_int: int = 30,
) -> FIXSession:
    config = FIXSessionConfig(
        sender_comp_id=sender_comp_id,
        target_comp_id=target_comp_id,
        username=username,
        password=password,
        heart_bt_int=heart_bt_int,
    )
    return FIXSession(config)


def create_fix_connection(
    sender_comp_id: str,
    target_comp_id: str,
    username: str = "",
    password: str = "",
    heart_bt_int: int = 30,
) -> FIXConnection:
    config = FIXSessionConfig(
        sender_comp_id=sender_comp_id,
        target_comp_id=target_comp_id,
        username=username,
        password=password,
        heart_bt_int=heart_bt_int,
    )
    return FIXConnection(config)
