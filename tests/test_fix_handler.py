"""Tests for FIX protocol handler."""

import asyncio

import pytest

from src.protocols.fix_handler import (
    FIXMessage,
    FIXSession,
    FIXSessionConfig,
    FIXConnection,
    SessionState,
    SessionStats,
    MsgType,
    SOH,
    FIX_TAG_NAME,
    FIX_NAME_TAG,
    create_fix_session,
    create_fix_connection,
    FIX_4_4_TAGS,
)


# ── FIXMessage tests ───────────────────────────────────────────────


class TestFIXMessageParsing:
    def test_parse_simple(self):
        raw = f"8=FIX.4.4{SOH}9=100{SOH}35=D{SOH}49=CLIENT{SOH}56=SERVER{SOH}10=000{SOH}"
        msg = FIXMessage.parse(raw)
        assert msg[8] == "FIX.4.4"
        assert msg[35] == "D"
        assert msg[49] == "CLIENT"
        assert msg[56] == "SERVER"

    def test_parse_bytes(self):
        raw = (
            b"8=FIX.4.4\x019=100\x0135=D\x0149=CLIENT\x0156=SERVER\x0110=000\x01"
        )
        msg = FIXMessage.parse_bytes(raw)
        assert msg[8] == "FIX.4.4"
        assert msg[35] == "D"

    def test_get_by_tag_name(self):
        msg = FIXMessage()
        msg.set("SenderCompID", "ALGO")
        assert msg.get("SenderCompID") == "ALGO"
        assert msg.get(49) == "ALGO"

    def test_get_int_float(self):
        msg = FIXMessage()
        msg.set(38, "500")
        msg.set(44, "150.25")
        assert msg.get_int(38) == 500
        assert msg.get_float(44) == 150.25

    def test_has_tag(self):
        msg = FIXMessage()
        msg.set(35, "D")
        assert 35 in msg
        assert "MsgType" in msg

    def test_remove_tag(self):
        msg = FIXMessage()
        msg.set(58, "test")
        assert 58 in msg
        msg.remove(58)
        assert 58 not in msg

    def test_msg_type_property(self):
        msg = FIXMessage()
        msg.set(35, "A")
        assert msg.msg_type == "A"

    def test_msg_seq_num_property(self):
        msg = FIXMessage()
        msg.set(34, "42")
        assert msg.msg_seq_num == 42

    def test_sender_target_properties(self):
        msg = FIXMessage()
        msg.set(49, "SENDER")
        msg.set(56, "TARGET")
        assert msg.sender_comp_id == "SENDER"
        assert msg.target_comp_id == "TARGET"

    def test_default_values(self):
        msg = FIXMessage()
        assert msg.get(999) == ""
        assert msg.get_int(999) == 0
        assert msg.get_float(999) == 0.0

    def test_parse_invalid_data(self):
        msg = FIXMessage.parse("not_a_fix_message")
        assert msg.get(8) == ""


class TestFIXMessageEncoding:
    def test_encode_roundtrip(self):
        msg = FIXMessage()
        msg.set(8, "FIX.4.4")
        msg.set(49, "SENDER")
        msg.set(56, "TARGET")
        msg.set(35, "D")
        msg.set(11, "ORD123")

        encoded = msg.encode()
        decoded = FIXMessage.parse_bytes(encoded)
        assert decoded[8] == "FIX.4.4"
        assert decoded[49] == "SENDER"
        assert decoded[11] == "ORD123"

    def test_encode_str(self):
        msg = FIXMessage()
        msg.set(8, "FIX.4.4")
        msg.set(35, "0")
        s = msg.encode_str()
        assert "35=0" in s
        assert "10=" in s
        assert s.endswith(SOH)

    def test_checksum_present(self):
        msg = FIXMessage()
        msg.set(8, "FIX.4.4")
        msg.set(35, "0")
        encoded = msg.encode()
        assert b"10=" in encoded


class TestFIXMessageBuilders:
    def test_logon(self):
        msg = FIXMessage.logon("ALGO", "BROKER", 1, heart_bt_int=30)
        assert msg[8] == "FIX.4.4"
        assert msg[35] == "A"
        assert msg[49] == "ALGO"
        assert msg[56] == "BROKER"
        assert msg.get_int(98) == 0

    def test_logon_with_creds(self):
        msg = FIXMessage.logon("A", "B", 1, username="u", password="p", reset_seq=True)
        assert msg[553] == "u"
        assert msg[554] == "p"
        assert msg[141] == "Y"

    def test_heartbeat(self):
        msg = FIXMessage.heartbeat("A", "B", 5)
        assert msg[35] == "0"
        assert msg.get_int(34) == 5

    def test_logout(self):
        msg = FIXMessage.logout("A", "B", 3, text="Bye")
        assert msg[35] == "5"
        assert msg[58] == "Bye"

    def test_new_order_single(self):
        msg = FIXMessage.new_order_single(
            "ALGO", "BROKER", 1, "ORD1", "AAPL", "1", 100, 150.0
        )
        assert msg[35] == "D"
        assert msg[55] == "AAPL"
        assert msg[54] == "1"
        assert msg.get_int(38) == 100
        assert msg.get_float(44) == 150.0

    def test_order_cancel_request(self):
        msg = FIXMessage.order_cancel_request(
            "A", "B", 1, "OLD1", "NEW1", "TSLA", "2"
        )
        assert msg[35] == "F"
        assert msg[41] == "OLD1"
        assert msg[11] == "NEW1"

    def test_market_data_request(self):
        msg = FIXMessage.market_data_request(
            "A", "B", 1, "MD1", ["AAPL", "TSLA"]
        )
        assert msg[35] == "V"
        assert msg[262] == "MD1"
        assert msg[263] == "1"


# ── FIX Dictionary ──────────────────────────────────────────────────


class TestFIXDictionary:
    def test_known_tags(self):
        assert FIX_TAG_NAME[8] == "BeginString"
        assert FIX_TAG_NAME[35] == "MsgType"
        assert FIX_TAG_NAME[11] == "ClOrdID"

    def test_name_to_tag(self):
        assert FIX_NAME_TAG["BeginString"] == 8
        assert FIX_NAME_TAG["MsgType"] == 35
        assert FIX_NAME_TAG["Symbol"] == 55

    def test_dictionary_coverage(self):
        assert len(FIX_4_4_TAGS) >= 100


class TestMsgTypeConstants:
    def test_admin_message_types(self):
        assert MsgType.HEARTBEAT == "0"
        assert MsgType.LOGON == "A"
        assert MsgType.LOGOUT == "5"

    def test_order_message_types(self):
        assert MsgType.NEW_ORDER_SINGLE == "D"
        assert MsgType.ORDER_CANCEL_REQUEST == "F"


# ── FIXSession tests ───────────────────────────────────────────────


class TestFIXSessionState:
    @pytest.fixture
    def config(self):
        return FIXSessionConfig(
            sender_comp_id="ALGO",
            target_comp_id="BROKER",
        )

    def test_initial_state(self, config):
        session = FIXSession(config)
        assert session.state == SessionState.DISCONNECTED
        assert not session.is_logged_on()

    def test_stats(self, config):
        session = FIXSession(config)
        stats = session.stats
        assert stats.messages_sent == 0
        assert stats.messages_received == 0
        assert stats.current_state == SessionState.DISCONNECTED

    def test_seq_numbers(self, config):
        session = FIXSession(config)
        assert session.seq_num_out == 1
        assert session.seq_num_in == 1


class TestFIXSessionHandlers:
    @pytest.fixture
    def config(self):
        return FIXSessionConfig(
            sender_comp_id="ALGO",
            target_comp_id="BROKER",
        )

    def test_register_message_handler(self, config):
        session = FIXSession(config)
        received = []

        def handler(msg):
            received.append(msg.msg_type)

        session.on_message("8", handler)
        assert len(session._message_handlers["8"]) == 1

    def test_register_state_handler(self, config):
        session = FIXSession(config)
        transitions = []

        def handler(old, new):
            transitions.append((old, new))

        session.on_state_change(handler)
        session._transition(SessionState.CONNECTING)
        assert len(transitions) == 1

    def test_remove_handler(self, config):
        session = FIXSession(config)

        def h(m):
            pass

        session.on_message("D", h)
        session.remove_handler("D", h)
        assert len(session._message_handlers.get("D", [])) == 0


class TestFIXSessionMessageBuilding:
    @pytest.fixture
    def config(self):
        return FIXSessionConfig(
            sender_comp_id="ALGO",
            target_comp_id="BROKER",
            username="trader",
            password="secret",
        )

    def test_build_new_order(self, config):
        session = FIXSession(config)
        msg = session.build_new_order_single("AAPL", "1", 100, 150.0)
        assert msg[55] == "AAPL"
        assert msg[54] == "1"
        assert msg[49] == "ALGO"
        assert msg[56] == "BROKER"

    def test_build_market_data_request(self, config):
        session = FIXSession(config)
        msg = session.build_market_data_request(["BTCUSD"])
        assert msg[35] == "V"
        assert msg[55] == "BTCUSD"


class TestFIXSessionMessageProcessing:
    @pytest.fixture
    def config(self):
        return FIXSessionConfig(
            sender_comp_id="ALGO",
            target_comp_id="BROKER",
        )

    def test_handle_admin_logon(self, config):
        session = FIXSession(config)
        session._transition(SessionState.CONNECTING)
        session._transition(SessionState.LOGON_SENT)

        logon = FIXMessage()
        logon.set(35, "A")
        logon.set(49, "BROKER")
        logon.set(56, "ALGO")

        asyncio.run(session._handle_admin(logon))
        assert session.state == SessionState.LOGGED_ON

    def test_handle_admin_heartbeat(self, config):
        session = FIXSession(config)
        session._transition(SessionState.CONNECTING)
        session._transition(SessionState.LOGON_SENT)
        session._transition(SessionState.LOGGED_ON)

        hb = FIXMessage()
        hb.set(35, "0")
        before = session.stats.last_heartbeat_received
        asyncio.run(session._handle_admin(hb))
        after = session.stats.last_heartbeat_received
        assert after >= before

    def test_handle_admin_logout(self, config):
        session = FIXSession(config)
        session._transition(SessionState.CONNECTING)
        session._transition(SessionState.LOGON_SENT)
        session._transition(SessionState.LOGGED_ON)

        logout = FIXMessage()
        logout.set(35, "5")
        logout.set(58, "Bye")
        asyncio.run(session._handle_admin(logout))
        assert session.state == SessionState.LOGOUT_SENT


# ── FIXConnection tests ────────────────────────────────────────────


class TestFIXConnection:
    def test_create(self):
        conn = FIXConnection(
            FIXSessionConfig(sender_comp_id="A", target_comp_id="B")
        )
        assert conn.session is not None
        assert not conn.is_connected()

    def test_session_property(self):
        conn = FIXConnection(
            FIXSessionConfig(sender_comp_id="A", target_comp_id="B")
        )
        assert isinstance(conn.session, FIXSession)


# ── Factory tests ───────────────────────────────────────────────────


class TestFactory:
    def test_create_fix_session(self):
        session = create_fix_session("A", "B", "u", "p", 60)
        assert session is not None
        assert isinstance(session, FIXSession)

    def test_create_fix_connection(self):
        conn = create_fix_connection("A", "B", "u", "p", 60)
        assert conn is not None
        assert isinstance(conn, FIXConnection)


# ── FIXSessionConfig ────────────────────────────────────────────────


class TestFIXSessionConfig:
    def test_defaults(self):
        config = FIXSessionConfig(sender_comp_id="A", target_comp_id="B")
        assert config.sender_comp_id == "A"
        assert config.heart_bt_int == 30
        assert config.max_reconnect_attempts == 10

    def test_custom(self):
        config = FIXSessionConfig(
            sender_comp_id="X", target_comp_id="Y",
            heart_bt_int=15, max_reconnect_attempts=5,
        )
        assert config.heart_bt_int == 15
        assert config.max_reconnect_attempts == 5


# ── SessionStats ────────────────────────────────────────────────────


class TestSessionStats:
    def test_defaults(self):
        stats = SessionStats()
        assert stats.messages_sent == 0
        assert stats.current_state == SessionState.DISCONNECTED
        assert stats.uptime == 0.0

    def test_increment(self):
        stats = SessionStats()
        stats.messages_sent += 1
        assert stats.messages_sent == 1
