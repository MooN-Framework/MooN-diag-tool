"""
Passive listener on the operational multicast.

Send-only observer: never transmits, only receives. Decodes frames via
wire_decoder and signals them upstream. Runs in its own thread.
"""
from __future__ import annotations

import select
import socket
import struct
import threading
import time
from typing import Callable, Optional

from .wire_decoder import DecodedFrame, decode_frame


class OperationalListener:
    def __init__(
        self,
        multicast_group: str = "239.10.0.1",
        port: int = 5555,
        interface_ip: str = "127.0.0.1",
    ) -> None:
        self.group = multicast_group
        self.port = port
        self.interface_ip = interface_ip
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._listeners: list[Callable[[DecodedFrame, float], None]] = []

    def add_listener(self, cb: Callable[[DecodedFrame, float], None]) -> None:
        self._listeners.append(cb)

    def start(self) -> None:
        self._sock = self._open_socket()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._rx_loop, name="op-rx", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _open_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.bind(("", self.port))
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(self.group),
            socket.inet_aton(self.interface_ip),
        )
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setblocking(False)
        return s

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
                if self._stop.is_set():
                    break
                continue
            frame = decode_frame(data)
            ts = time.monotonic()
            for cb in self._listeners:
                try:
                    cb(frame, ts)
                except Exception:  # pragma: no cover
                    pass
