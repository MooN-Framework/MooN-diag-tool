"""
Bidirectional diagnostic client (JSON) for the GUI.

Similar to `harness/diag.py`, but:
- Separates the receive loop (thread) from the sender.
- Signals every received telegram upstream (log tab, registry), not
  only replies to our own requests.
- Never blocks the UI: request+wait is solved via a short ack queue
  fed by the receive thread.

Unlike the test harness, we want to see *every* telegram here -- also
broadcasts we didn't originate ourselves.
"""
from __future__ import annotations

import json
import queue
import select
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


KNOWN_INJECTION_CMDS: frozenset[str] = frozenset({
    "inject_drop_inputs",
    "inject_drop_results",
    "inject_drop_acks",
    "inject_drop_cyclesync",
    "inject_drop_crc",
    "inject_drop_votes",
    "inject_fake_crc",
    "inject_divergent_publisher",
    "inject_shutdown",
    "inject_mute",
    "inject_cycle_delay",
    "inject_targeted_input",
    "inject_corrupt_result",
    "inject_drop_from_peer",
    "inject_fake_phase_header",
    "clear_injection",
})

# Command -> parameter schema. Used by the Inject tab to build matching
# input widgets dynamically. Types: "u32", "u8_mask", "u8", "json".
COMMAND_SCHEMAS: dict[str, list[tuple[str, str, Any]]] = {
    # (field, type, default)
    "inject_drop_inputs":         [("count", "u32", 1)],
    "inject_drop_results":        [("count", "u32", 1)],
    "inject_drop_acks":           [("count", "u32", 1)],
    "inject_drop_cyclesync":      [("count", "u32", 1)],
    "inject_drop_crc":            [("count", "u32", 1)],
    "inject_drop_votes":          [("count", "u32", 1)],
    "inject_fake_crc":            [("count", "u32", 1)],
    "inject_divergent_publisher": [("count", "u32", 1)],
    "inject_corrupt_result":      [("count", "u32", 1)],
    "inject_shutdown":            [],
    "inject_mute":                [("cycles", "u32", 5)],
    "inject_cycle_delay":         [("ms", "u32", 100), ("count", "u32", 1)],
    "inject_targeted_input":      [("value", "json", {})],
    "inject_drop_from_peer":      [("peers_mask", "u8_mask", 0)],
    "inject_fake_phase_header":   [("count", "u32", 1), ("wire_value", "u8", 0x08)],
    "clear_injection":            [],
}


@dataclass(slots=True)
class DiagTelegram:
    raw: dict
    ts_mono: float

    @property
    def kind(self) -> str:
        return self.raw.get("type", "?")

    @property
    def source_node_id(self) -> Optional[int]:
        v = self.raw.get("source_node_id")
        return v if isinstance(v, int) else None


class DiagClient:
    """
    Not Qt-dependent. The UI connects via `add_listener(cb)` -- the cb
    is invoked on the receive thread and should only do `Signal.emit()`.
    """

    def __init__(
        self,
        multicast_group: str = "239.10.0.2",
        port: int = 6666,
        interface_ip: str = "127.0.0.1",
    ) -> None:
        self.group = multicast_group
        self.port = port
        self.interface_ip = interface_ip
        self._sock: Optional[socket.socket] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._listeners: list[Callable[[DiagTelegram], None]] = []
        # For get_status/inject waits: one queue per pending request.
        self._response_lock = threading.Lock()
        self._responses: list[tuple[Callable[[DiagTelegram], bool], queue.Queue]] = []

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._sock = self._open_socket()
        self._stop.clear()
        self._rx_thread = threading.Thread(
            target=self._rx_loop, name="diag-rx", daemon=True
        )
        self._rx_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=1.0)
            self._rx_thread = None

    def _open_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass  # not available on Windows
        s.bind(("", self.port))
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(self.group),
            socket.inet_aton(self.interface_ip),
        )
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
            socket.inet_aton(self.interface_ip),
        )
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setblocking(False)
        return s

    # ---- listener plumbing ------------------------------------------------

    def add_listener(self, cb: Callable[[DiagTelegram], None]) -> None:
        self._listeners.append(cb)

    def _dispatch(self, t: DiagTelegram) -> None:
        # Response matchers first (for wait_response), then generic listeners.
        with self._response_lock:
            matchers = list(self._responses)
        for match_fn, q in matchers:
            try:
                if match_fn(t):
                    q.put(t)
            except Exception:  # pragma: no cover
                pass
        for cb in self._listeners:
            try:
                cb(t)
            except Exception:  # pragma: no cover
                pass

    def _rx_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                rlist, _, _ = select.select([sock], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not rlist:
                continue
            try:
                data, _ = sock.recvfrom(8192)
            except BlockingIOError:
                # Spurious EWOULDBLOCK after a positive select() -- this
                # can legitimately happen on a non-blocking socket, it's
                # not a reason to give up. The old code treated it as
                # fatal and silently killed the whole receive thread on
                # the very first occurrence: packets kept arriving on
                # the wire (confirmed via tcpdump) but nothing ever
                # reached the UI again, with no error shown anywhere.
                continue
            except OSError:
                # A genuine socket error. If we're shutting down, that's
                # expected (stop() closes the socket from under us) --
                # exit quietly. Otherwise this is unexpected but still
                # not necessarily fatal for the socket itself, so keep
                # trying rather than permanently killing the thread.
                if self._stop.is_set():
                    break
                continue
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError:
                continue
            self._dispatch(DiagTelegram(raw=parsed, ts_mono=time.monotonic()))

    # ---- send + wait ------------------------------------------------------

    def send_raw(self, telegram: dict) -> None:
        if self._sock is None:
            raise RuntimeError("DiagClient not started")
        self._sock.sendto(json.dumps(telegram).encode(), (self.group, self.port))

    def _register_response(
        self, match_fn: Callable[[DiagTelegram], bool]
    ) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._response_lock:
            self._responses.append((match_fn, q))
        return q

    def _unregister_response(self, q: queue.Queue) -> None:
        with self._response_lock:
            self._responses = [(m, qq) for (m, qq) in self._responses if qq is not q]

    def request(
        self,
        telegram: dict,
        match_fn: Callable[[DiagTelegram], bool],
        timeout: float = 3.0,
        resend_interval: float = 0.3,
    ) -> Optional[DiagTelegram]:
        """
        Repeatedly send `telegram` until `match_fn` accepts a received
        telegram, or the timeout expires. Multicast on loopback can drop
        packets occasionally -- hence the periodic resend.
        """
        q = self._register_response(match_fn)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                self.send_raw(telegram)
                remaining = deadline - time.monotonic()
                wait = min(resend_interval, max(remaining, 0.01))
                try:
                    return q.get(timeout=wait)
                except queue.Empty:
                    continue
            return None
        finally:
            self._unregister_response(q)

    # ---- high level -------------------------------------------------------

    def get_status(self, node_id: int, timeout: float = 2.0) -> Optional[dict]:
        telegram = {"type": "command", "targets": [node_id], "cmd": "get_status"}
        resp = self.request(
            telegram,
            lambda t: t.raw.get("type") == "status"
            and t.raw.get("source_node_id") == node_id,
            timeout=timeout,
        )
        return resp.raw.get("data") if resp else None

    def inject(
        self,
        node_ids: list[int],
        cmd: str,
        params: Optional[dict] = None,
        wait_ack: bool = True,
        timeout: float = 3.0,
    ) -> dict[int, Optional[DiagTelegram]]:
        """
        Broadcast a command to several targets. If wait_ack is set,
        wait for a Staged ack (or Status, for get_status) from each
        target.
        """
        if cmd not in KNOWN_INJECTION_CMDS and cmd != "get_status":
            raise KeyError(f"unknown command '{cmd}'")

        telegram = {
            "type": "command",
            "targets": list(node_ids),
            "cmd": cmd,
            **(params or {}),
        }

        if not wait_ack:
            self.send_raw(telegram)
            return {nid: None for nid in node_ids}

        # Blocking wait per target -- acceptable since triggered by a user click.
        results: dict[int, Optional[DiagTelegram]] = {}
        for nid in node_ids:
            resp = self.request(
                telegram,
                lambda t, nid=nid: (
                    t.raw.get("type") == "staged"
                    and t.raw.get("source_node_id") == nid
                ),
                timeout=timeout,
            )
            results[nid] = resp
        return results

    def broadcast_input(self, value: dict) -> None:
        self.send_raw({"type": "input", "value": value})
