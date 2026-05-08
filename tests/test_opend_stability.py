"""Tests for FutuConnector OpenD stability: connection state machine,
heartbeat transitions, exponential backoff reconnect, and startup guard.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from v3_pipeline.core.futu_connector import ConnectionState, FutuConfig, FutuConnector
from v3_launcher import _check_opend_reachable, _wait_for_opend, _safe_connector_connect


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_connector(config: FutuConfig | None = None) -> FutuConnector:
    fc = FutuConnector.__new__(FutuConnector)
    fc.config = config or FutuConfig()
    fc.logger = MagicMock()
    fc.quote_ctx = None
    fc.trade_ctx = None
    fc.trade_ctxs = {}
    fc.account_ids = {}
    fc.ft = None
    fc.login_account = None
    fc.discovered_accounts = MagicMock()
    fc._quote_cache = MagicMock()
    fc._cached_hit_count = {}
    fc._futu_fail_count = 0
    fc._FUTU_FAIL_THRESHOLD = 3
    fc._futu_degraded = False
    fc._conn_state = ConnectionState.DISCONNECTED
    fc._heartbeat_fail_count = 0
    fc._HEARTBEAT_DEGRADE_THRESHOLD = 2
    fc._heartbeat_thread = None
    fc._heartbeat_stop_event = threading.Event()
    fc._heartbeat_interval_s = 30.0
    return fc


# ── ConnectionState initial value ─────────────────────────────────────────────

class TestConnectionStateEnum:
    def test_all_states_defined(self):
        assert {s.value for s in ConnectionState} == {
            "CONNECTED", "DEGRADED", "DISCONNECTED", "RECONNECTING"
        }

    def test_new_connector_starts_disconnected(self):
        fc = _make_connector()
        assert fc.conn_state is ConnectionState.DISCONNECTED

    def test_conn_state_property_returns_enum(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        assert fc.conn_state is ConnectionState.CONNECTED


# ── heartbeat state transitions ───────────────────────────────────────────────

class TestHeartbeatTransitions:
    def test_successful_heartbeat_stays_connected(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "_safe_trade_call", return_value=(0, [])):
            with patch.object(fc, "_account_kwargs", return_value={}):
                result = fc.heartbeat()
        assert result is True
        assert fc._conn_state is ConnectionState.CONNECTED
        assert fc._heartbeat_fail_count == 0

    def test_successful_heartbeat_resets_fail_count(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        fc._heartbeat_fail_count = 1
        with patch.object(fc, "_safe_trade_call", return_value=(0, [])):
            with patch.object(fc, "_account_kwargs", return_value={}):
                fc.heartbeat()
        assert fc._heartbeat_fail_count == 0

    def test_first_heartbeat_failure_increments_count_not_degraded(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "_safe_trade_call", side_effect=RuntimeError("timeout")):
            with patch.object(fc, "_account_kwargs", return_value={}):
                result = fc.heartbeat()
        assert result is False
        assert fc._heartbeat_fail_count == 1
        assert fc._conn_state is ConnectionState.CONNECTED  # not yet degraded

    def test_threshold_heartbeat_failures_transitions_to_degraded(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        fc._HEARTBEAT_DEGRADE_THRESHOLD = 2
        with patch.object(fc, "_safe_trade_call", side_effect=RuntimeError("timeout")):
            with patch.object(fc, "_account_kwargs", return_value={}):
                fc.heartbeat()  # fail 1
                fc.heartbeat()  # fail 2 → DEGRADED
        assert fc._conn_state is ConnectionState.DEGRADED
        assert fc._heartbeat_fail_count == 2

    def test_heartbeat_does_not_overwrite_reconnecting_state(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.RECONNECTING
        with patch.object(fc, "_safe_trade_call", return_value=(0, [])):
            with patch.object(fc, "_account_kwargs", return_value={}):
                fc.heartbeat()
        assert fc._conn_state is ConnectionState.RECONNECTING

    def test_heartbeat_returns_false_on_exception(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "_safe_trade_call", side_effect=Exception("gone")):
            with patch.object(fc, "_account_kwargs", return_value={}):
                result = fc.heartbeat()
        assert result is False


# ── reconnect exponential backoff ─────────────────────────────────────────────

class TestReconnectExponentialBackoff:
    def test_reconnect_succeeds_on_first_attempt(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.DEGRADED
        with patch.object(fc, "_safe_close_contexts"):
            with patch.object(fc, "connect") as mock_connect:
                fc.reconnect(max_retries=3, base_delay_seconds=0.01)
        mock_connect.assert_called_once()
        assert fc._conn_state is ConnectionState.CONNECTED

    def test_reconnect_sets_reconnecting_during_attempt(self):
        states_seen: list[ConnectionState] = []
        fc = _make_connector()
        fc._conn_state = ConnectionState.DEGRADED

        original_close = FutuConnector._safe_close_contexts

        def _capture_state(self_inner):
            states_seen.append(self_inner._conn_state)

        with patch.object(fc, "_safe_close_contexts", side_effect=lambda: _capture_state(fc)):
            with patch.object(fc, "connect"):
                fc.reconnect(max_retries=1, base_delay_seconds=0.01)

        assert ConnectionState.RECONNECTING in states_seen

    def test_reconnect_retries_with_exponential_backoff(self):
        fc = _make_connector()
        delays: list[float] = []

        def _fake_sleep(d: float) -> None:
            delays.append(d)

        def _fail_then_succeed(*args, **kwargs):
            if len(delays) < 3:
                raise RuntimeError("not ready")

        with patch("time.sleep", side_effect=_fake_sleep):
            with patch.object(fc, "_safe_close_contexts"):
                with patch.object(fc, "connect", side_effect=_fail_then_succeed):
                    fc.reconnect(max_retries=5, base_delay_seconds=1.0, max_delay_seconds=60.0)

        # delays should be exponential: 1, 2, 4 ...
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)

    def test_reconnect_caps_delay_at_max(self):
        fc = _make_connector()
        delays: list[float] = []

        def _fake_sleep(d: float) -> None:
            delays.append(d)

        call_count = {"n": 0}

        def _always_fail(*args, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("no opend")

        with patch("time.sleep", side_effect=_fake_sleep):
            with patch.object(fc, "_safe_close_contexts"):
                with patch.object(fc, "connect", side_effect=_always_fail):
                    with pytest.raises(RuntimeError, match="RECONNECT_FAILED"):
                        fc.reconnect(max_retries=6, base_delay_seconds=10.0, max_delay_seconds=25.0)

        for d in delays:
            assert d <= 25.0

    def test_reconnect_exhausted_sets_disconnected(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.DEGRADED

        with patch.object(fc, "_safe_close_contexts"):
            with patch.object(fc, "connect", side_effect=RuntimeError("fail")):
                with patch("time.sleep"):
                    with pytest.raises(RuntimeError, match="RECONNECT_FAILED"):
                        fc.reconnect(max_retries=2, base_delay_seconds=0.01)

        assert fc._conn_state is ConnectionState.DISCONNECTED

    def test_reconnect_raises_runtime_error_on_exhaustion(self):
        fc = _make_connector()
        with patch.object(fc, "_safe_close_contexts"):
            with patch.object(fc, "connect", side_effect=RuntimeError("opend down")):
                with patch("time.sleep"):
                    with pytest.raises(RuntimeError):
                        fc.reconnect(max_retries=2, base_delay_seconds=0.01)


# ── heartbeat monitor thread ──────────────────────────────────────────────────

class TestHeartbeatMonitor:
    def test_start_heartbeat_monitor_spawns_daemon_thread(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "heartbeat", return_value=True):
            fc.start_heartbeat_monitor(interval_s=0.05)
            assert fc._heartbeat_thread is not None
            assert fc._heartbeat_thread.is_alive()
            assert fc._heartbeat_thread.daemon is True
            fc.stop_heartbeat_monitor()

    def test_stop_heartbeat_monitor_joins_thread(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "heartbeat", return_value=True):
            fc.start_heartbeat_monitor(interval_s=0.05)
            fc.stop_heartbeat_monitor()
        assert fc._heartbeat_thread is None

    def test_start_heartbeat_monitor_idempotent(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "heartbeat", return_value=True):
            fc.start_heartbeat_monitor(interval_s=0.05)
            t1 = fc._heartbeat_thread
            fc.start_heartbeat_monitor(interval_s=0.05)
            t2 = fc._heartbeat_thread
            assert t1 is t2  # not duplicated
            fc.stop_heartbeat_monitor()

    def test_monitor_skips_heartbeat_when_disconnected(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.DISCONNECTED
        heartbeat_calls = []

        def _fake_hb() -> bool:
            heartbeat_calls.append(True)
            return True

        with patch.object(fc, "heartbeat", side_effect=_fake_hb):
            fc.start_heartbeat_monitor(interval_s=0.02)
            time.sleep(0.1)
            fc.stop_heartbeat_monitor()

        assert len(heartbeat_calls) == 0

    def test_close_stops_heartbeat_monitor(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        with patch.object(fc, "heartbeat", return_value=True):
            fc.start_heartbeat_monitor(interval_s=0.05)
            assert fc._heartbeat_thread is not None
            with patch.object(fc, "_safe_close_contexts"):
                fc.close()
        assert fc._heartbeat_thread is None


# ── safe_close_contexts updates state ─────────────────────────────────────────

class TestSafeCloseContextsState:
    def test_safe_close_sets_disconnected(self):
        fc = _make_connector()
        fc._conn_state = ConnectionState.CONNECTED
        fc.quote_ctx = MagicMock()
        fc.trade_ctx = MagicMock()
        fc.trade_ctxs = {"US": MagicMock()}
        fc._safe_close_contexts()
        assert fc._conn_state is ConnectionState.DISCONNECTED

    def test_connect_failure_sets_disconnected(self):
        fc = _make_connector()
        with patch("builtins.__import__", side_effect=ImportError("no futu")):
            with pytest.raises(RuntimeError):
                fc.connect()
        assert fc._conn_state is ConnectionState.DISCONNECTED


# ── OpenD startup guard ────────────────────────────────────────────────────────

class TestCheckOpendReachable:
    def test_returns_true_when_port_open(self):
        import socket, threading
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def _accept():
            try:
                conn, _ = server.accept()
                conn.close()
            except OSError:
                pass

        t = threading.Thread(target=_accept, daemon=True)
        t.start()
        try:
            assert _check_opend_reachable("127.0.0.1", port, timeout=2.0) is True
        finally:
            server.close()

    def test_returns_false_when_port_closed(self):
        # Port 1 is almost certainly not open on localhost
        assert _check_opend_reachable("127.0.0.1", 1, timeout=0.2) is False

    def test_returns_false_on_unreachable_host(self):
        # 192.0.2.x is documentation range — never routable
        assert _check_opend_reachable("192.0.2.1", 11112, timeout=0.2) is False


class TestWaitForOpend:
    def test_returns_true_immediately_when_reachable(self):
        with patch("v3_launcher._check_opend_reachable", return_value=True) as mock_check:
            result = _wait_for_opend(host="127.0.0.1", port=11112, max_wait_seconds=5.0)
        assert result is True
        assert mock_check.call_count == 1

    def test_returns_false_after_timeout(self):
        with patch("v3_launcher._check_opend_reachable", return_value=False):
            with patch("v3_launcher.time.sleep"):
                result = _wait_for_opend(
                    host="127.0.0.1", port=11112, max_wait_seconds=0.01, poll_interval_seconds=0.001
                )
        assert result is False

    def test_retries_until_reachable(self):
        call_count = {"n": 0}

        def _flaky(*_args, **_kwargs):
            call_count["n"] += 1
            return call_count["n"] >= 3

        with patch("v3_launcher._check_opend_reachable", side_effect=_flaky):
            with patch("v3_launcher.time.sleep"):
                result = _wait_for_opend(
                    host="127.0.0.1", port=11112, max_wait_seconds=60.0, poll_interval_seconds=0.001
                )
        assert result is True
        assert call_count["n"] == 3

    def test_logs_timeout_at_error_level(self, caplog):
        import logging
        with patch("v3_launcher._check_opend_reachable", return_value=False):
            with patch("v3_launcher.time.sleep"):
                with caplog.at_level(logging.ERROR, logger="opend_guard"):
                    result = _wait_for_opend(
                        host="127.0.0.1", port=9, max_wait_seconds=0.01, poll_interval_seconds=0.001
                    )
        assert result is False
        assert any("timeout" in r.message.lower() for r in caplog.records)


# ── _safe_connector_connect timeout guard ──────────────────────────────────────

class TestSafeConnectorConnectTimeout:
    """Tests for the wall-clock timeout guard in _safe_connector_connect.

    Root cause being guarded: when port 11112 is TCP-reachable but the Futu
    protocol handshake keeps failing, ``OpenQuoteContext._connect_sync`` retries
    every 26 s for 21+ minutes, blocking the launcher completely.  The guard
    runs the SDK connect in a daemon thread and abandons it after
    ``timeout_seconds``, returning False so the caller proceeds in degraded mode.
    """

    def test_returns_true_when_connect_succeeds(self):
        connector = MagicMock()
        connector.connect = MagicMock()
        result = _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=5.0)
        assert result is True
        connector.connect.assert_called_once_with(symbols=["AAPL"])

    def test_returns_true_with_no_symbols(self):
        connector = MagicMock()
        connector.connect = MagicMock()
        result = _safe_connector_connect(connector, symbols=None, timeout_seconds=5.0)
        assert result is True
        connector.connect.assert_called_once_with()

    def test_returns_false_when_connect_hangs_past_timeout(self):
        """Simulates the observed Futu SDK _connect_sync loop that hangs 21 min."""
        import threading as _threading

        connect_started = _threading.Event()

        def _hanging_connect(**_kwargs):
            connect_started.set()
            # Block until the test finishes (simulates unbounded SDK retry)
            time.sleep(60)

        connector = MagicMock()
        connector.connect = _hanging_connect

        result = _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=0.2)
        assert result is False
        # Guard started the thread
        assert connect_started.is_set()

    def test_emits_structured_log_on_timeout(self, caplog):
        import logging as _logging

        def _hanging_connect(**_kwargs):
            time.sleep(60)

        connector = MagicMock()
        connector.connect = _hanging_connect

        with caplog.at_level(_logging.ERROR, logger="futu_connect_guard"):
            result = _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=0.2)

        assert result is False
        assert any("futu_connect_timeout" in r.message for r in caplog.records)

    def test_raises_runtime_error_when_connect_raises(self):
        connector = MagicMock()
        connector.connect = MagicMock(side_effect=OSError("connection refused"))

        with pytest.raises(RuntimeError, match="Failed to connect"):
            _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=5.0)

    def test_handles_type_error_fallback(self):
        """Old connector signature raises TypeError on symbols kwarg; shim calls bare connect()."""
        call_log: list[str] = []

        def _connect_compat(**kwargs):
            if "symbols" in kwargs:
                raise TypeError("unexpected keyword argument 'symbols'")
            call_log.append("bare_connect")

        connector = MagicMock()
        connector.connect = _connect_compat

        result = _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=5.0)
        assert result is True
        assert call_log == ["bare_connect"]

    def test_degraded_mode_does_not_block_for_full_sdk_retry_duration(self):
        """Wall-clock: even if SDK would block for minutes, we return in < 1 s."""
        def _very_slow_connect(**_kwargs):
            time.sleep(300)  # 5-minute hang

        connector = MagicMock()
        connector.connect = _very_slow_connect

        t_start = time.monotonic()
        result = _safe_connector_connect(connector, symbols=["AAPL"], timeout_seconds=0.3)
        elapsed = time.monotonic() - t_start

        assert result is False
        assert elapsed < 1.0, f"Expected < 1 s, got {elapsed:.2f} s"
