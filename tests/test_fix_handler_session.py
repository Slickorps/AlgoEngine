"""Tests for FIX session transport, framing, and connection monitoring."""

import asyncio
import time as _time
from unittest.mock import AsyncMock, patch

import pytest

from src.protocols.fix_handler import (
    FIXConnection,
    FIXField,
    FIXMessage,
    FIXSession,
    FIXSessionConfig,
    SessionState,
    SOH,
    SOH_BYTE,
)


@pytest.fixture
def config():
    return FIXSessionConfig(
        sender_comp_id="ALGO",
        target_comp_id="BROKER",
        heart_bt_int=1,
        reconnect_interval=0.0,
        max_reconnect_attempts=2,
    )


@pytest.fixture
def session(config):
    return FIXSession(config)


class FakeWriter:
    def __init__(self, fail_wait_closed=False):
        self.data = b""
        self.closed = False
        self.fail_wait_closed = fail_wait_closed

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        if self.fail_wait_closed:
            raise RuntimeError("wait_closed failed")


class FakeReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class TestFIXMessageExtras:
    def test_field_str(self):
        assert str(FIXField(35, "D")) == "35=D"

    def test_parse_bytes(self):
        raw = f"8=FIX.4.4{SOH}9=5{SOH}35=D{SOH}10=000{SOH}".encode()
        assert FIXMessage.parse_bytes(raw)[35] == "D"

    def test_parse_accepts_bytes_directly(self):
        raw = b"8=FIX.4.4\x0135=D\x01"
        assert FIXMessage.parse(raw)[35] == "D"

    def test_parse_skips_invalid_tag(self):
        raw = f"8=FIX.4.4{SOH}abc=x{SOH}35=D{SOH}"
        assert FIXMessage.parse(raw)[35] == "D"

    def test_remove_by_name(self):
        msg = FIXMessage()
        msg.set(35, "D")
        msg.remove("MsgType")
        assert not msg.has(35)

    def test_setitem(self):
        msg = FIXMessage()
        msg[35] = "D"
        assert msg[35] == "D"

    def test_repr(self):
        msg = FIXMessage()
        msg.set(35, "D")
        assert "FIXMessage(" in repr(msg)
        assert "35" in repr(msg)

    def test_encode_includes_body_length_and_checksum(self):
        encoded = FIXMessage.heartbeat("ALGO", "BROKER", 1).encode_str()
        assert f"{SOH}9=" in encoded
        assert encoded.endswith(SOH)

        cs_idx = encoded.rindex("10=")
        prefix = encoded[:cs_idx]
        expected = sum(prefix.encode("ascii")) % 256
        assert encoded[cs_idx + 3:cs_idx + 6] == f"{expected:03d}"

    def test_body_length_matches_body(self):
        encoded = FIXMessage.heartbeat("ALGO", "BROKER", 1).encode()
        first = encoded.find(SOH_BYTE)
        second = encoded.find(SOH_BYTE, first + 1)
        declared = int(encoded[first + 3:second])
        actual = encoded.rfind(b"10=") - (second + 1)
        assert declared == actual


class TestSessionBasics:
    def test_stats_uptime(self, session):
        session._stats.login_time = _time.monotonic() - 1.0
        stats = session.stats
        assert stats.uptime > 0
        assert stats.current_state == SessionState.DISCONNECTED

    def test_default_stats(self, session):
        assert session.stats.messages_sent == 0
        assert session.is_logged_on() is False


class TestSessionConnection:
    async def test_connect_success(self, session):
        writer = FakeWriter()
        reader = FakeReader([])

        with patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))):
            await session.connect("localhost", 5000)

        assert session.state == SessionState.LOGON_SENT
        assert writer.data  # logon was sent
        await session.disconnect()

    async def test_connect_ignored_when_not_disconnected(self, session):
        session._transition(SessionState.LOGGED_ON)
        await session.connect("localhost", 5000)
        assert session.state == SessionState.LOGGED_ON

    async def test_connect_fails_after_attempts(self, session):
        with patch(
            "asyncio.open_connection",
            new=AsyncMock(side_effect=ConnectionRefusedError("no")),
        ):
            with pytest.raises(ConnectionError):
                await session.connect("localhost", 5000)

        assert session.state == SessionState.DISCONNECTED
        assert session.stats.reconnect_count == 2

    async def test_disconnect_sends_logout_and_closes(self, session):
        writer = FakeWriter(fail_wait_closed=True)
        session._writer = writer
        session._transition(SessionState.LOGGED_ON)

        await session.disconnect("bye")

        assert writer.closed
        assert session.state == SessionState.DISCONNECTED
        assert session._writer is None

    async def test_disconnect_cancels_tasks(self, session):
        async def forever():
            await asyncio.sleep(10)

        session._tasks.append(asyncio.create_task(forever()))
        await session.disconnect()
        assert session._tasks == []


class TestSessionSending:
    async def test_send_assigns_ids_and_counts(self, session):
        session._writer = FakeWriter()
        msg = FIXMessage()
        msg.set(35, "D")

        await session.send(msg)

        assert msg[49] == "ALGO"
        assert msg[56] == "BROKER"
        assert msg[34] == "1"
        assert msg.has(52)
        assert session.seq_num_out == 2
        assert session.stats.messages_sent == 1

    async def test_send_without_writer_raises(self, session):
        with pytest.raises(RuntimeError):
            await session._send_raw(b"x")


class TestFraming:
    async def test_process_single_message(self, session):
        raw = FIXMessage.heartbeat("ALGO", "BROKER", 1).encode()

        with patch.object(session, "_dispatch", new=AsyncMock()) as dispatch:
            remainder = session._process_buffer(raw)
            await asyncio.sleep(0)

        assert remainder == b""
        assert session.stats.messages_received == 1
        dispatch.assert_awaited_once()

    async def test_process_two_messages(self, session):
        raw = (
            FIXMessage.heartbeat("A", "B", 1).encode()
            + FIXMessage.heartbeat("A", "B", 2).encode()
        )

        with patch.object(session, "_dispatch", new=AsyncMock()):
            remainder = session._process_buffer(raw)
            await asyncio.sleep(0)

        assert remainder == b""
        assert session.stats.messages_received == 2

    async def test_process_partial_message_kept(self, session):
        raw = FIXMessage.heartbeat("A", "B", 1).encode()
        partial = raw[:10]
        assert session._process_buffer(partial) == partial

    async def test_process_buffer_without_soh(self, session):
        assert session._process_buffer(b"garbage") == b"garbage"

    async def test_process_missing_checksum_waits(self, session):
        raw = f"8=FIX.4.4{SOH}9=10{SOH}35=0{SOH}".encode()
        assert session._process_buffer(raw) == raw

    async def test_process_missing_body_length_waits(self, session):
        raw = f"8=FIX.4.4{SOH}35=D{SOH}10=000{SOH}".encode()
        assert session._process_buffer(raw) == raw

    async def test_process_truncated_checksum_waits(self, session):
        raw = f"8=FIX.4.4{SOH}9=5{SOH}35=D{SOH}10=000".encode()
        assert session._process_buffer(raw) == raw

    async def test_process_parse_failure_counted(self, session):
        raw = FIXMessage.heartbeat("A", "B", 1).encode()

        with patch(
            "src.protocols.fix_handler.FIXMessage.parse_bytes",
            side_effect=Exception("boom"),
        ):
            remainder = session._process_buffer(raw)

        assert remainder == b""
        assert session.stats.errors == 1

    def test_extract_body_length_invalid(self, session):
        raw = f"8=FIX.4.4{SOH}9=abc{SOH}".encode()
        idx = raw.find(SOH_BYTE, raw.find(SOH_BYTE) + 1)
        assert session._extract_body_length(raw, idx) is None


class TestDispatch:
    async def test_dispatch_admin_logon(self, session):
        await session._dispatch(FIXMessage.logon("A", "B", 1))
        assert session.is_logged_on()

    async def test_dispatch_handler_error(self, session):
        def bad(msg):
            raise ValueError("boom")

        session.on_message("D", bad)
        msg = FIXMessage()
        msg.set(35, "D")
        await session._dispatch(msg)  # must not raise

    def test_transition_same_state(self, session):
        session._transition(SessionState.DISCONNECTED)
        assert session.state == SessionState.DISCONNECTED

    def test_transition_handler_error(self, session):
        def bad(old, new):
            raise ValueError("boom")

        session.on_state_change(bad)
        session._transition(SessionState.CONNECTING)
        assert session.state == SessionState.CONNECTING


class TestHeartbeatLoop:
    async def test_heartbeat_sends_when_logged_on(self, session):
        session._running = True
        session._writer = FakeWriter()
        session._transition(SessionState.LOGGED_ON)

        async def fake_sleep(_):
            session._running = False

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            await session._heartbeat_loop()

        assert session.stats.messages_sent >= 1

    async def test_heartbeat_skips_when_not_logged_on(self, session):
        session._running = True
        session._writer = FakeWriter()
        session._transition(SessionState.CONNECTING)

        async def fake_sleep(_):
            session._running = False

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            await session._heartbeat_loop()

        assert session.stats.messages_sent == 0

    async def test_heartbeat_error_counted(self, session):
        session._running = True
        session._writer = FakeWriter()
        session._transition(SessionState.LOGGED_ON)
        session.send = AsyncMock(side_effect=RuntimeError("boom"))

        async def fake_sleep(_):
            session._running = False

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            await session._heartbeat_loop()

        assert session.stats.errors == 1

    async def test_heartbeat_cancelled(self, session):
        session._running = True
        session._transition(SessionState.LOGGED_ON)
        session.send = AsyncMock(side_effect=asyncio.CancelledError())

        async def fake_sleep(_):
            pass

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            await session._heartbeat_loop()


class TestReadLoop:
    async def test_reads_chunk_then_closes(self, session):
        session._running = True
        session._reader = FakeReader([b"junk"])

        await session._read_loop()

        assert session.state == SessionState.DISCONNECTED
        assert session.stats.bytes_received == 4

    async def test_read_error(self, session):
        session._running = True
        session._reader = FakeReader([RuntimeError("boom")])

        await session._read_loop()

        assert session.stats.errors == 1
        assert session.state == SessionState.DISCONNECTED

    async def test_read_cancelled(self, session):
        session._running = True
        session._reader = FakeReader([asyncio.CancelledError()])

        await session._read_loop()

        assert session.state == SessionState.DISCONNECTED


class TestFIXConnectionMonitor:
    async def test_start_and_stop(self, config):
        conn = FIXConnection(config)
        conn._session.connect = AsyncMock()

        await conn.start("localhost", 5000)
        assert conn._host == "localhost"
        assert conn._port == 5000
        assert conn._monitor_task is not None

        await conn.stop()
        assert conn._monitor_task is None

    async def test_send_returns_task(self, config):
        conn = FIXConnection(config)
        conn._session._writer = FakeWriter()

        task = conn.send(FIXMessage.heartbeat("A", "B", 1))
        await task

        assert conn._session.stats.messages_sent >= 1

    async def test_monitor_reconnects(self, config):
        conn = FIXConnection(config)
        conn._session.connect = AsyncMock()
        conn._session._state = SessionState.DISCONNECTED
        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await conn._monitor_loop()

        conn._session.connect.assert_awaited()

    async def test_monitor_warns_on_heartbeat_gap(self, config):
        conn = FIXConnection(config)
        conn._session._state = SessionState.LOGGED_ON
        conn._session._stats.last_heartbeat_received = _time.monotonic() - 1000
        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await conn._monitor_loop()

    async def test_monitor_handles_reconnect_failure(self, config):
        conn = FIXConnection(config)
        conn._session.connect = AsyncMock(side_effect=Exception("boom"))
        conn._session._state = SessionState.DISCONNECTED
        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise asyncio.CancelledError()

        with patch("src.protocols.fix_handler.asyncio.sleep", new=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await conn._monitor_loop()

        conn._session.connect.assert_awaited()
