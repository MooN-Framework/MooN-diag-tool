"""ListenerWorker — hört passiv auf dem Betriebs-Multicast.

Läuft in eigenem QThread. Kommunikation ausschließlich via Signals in den
GUI-Thread; interner Zustand wird nicht direkt von außen gelesen.
"""

from __future__ import annotations

import socket
import struct

from PySide6.QtCore import QObject, Signal, Slot

from diag_tool.config import MulticastEndpoint
from diag_tool.net.frame import UdpFrame

# Buffer groß genug für maximalen UDP-Payload
_RECV_BUF = 65535


class ListenerWorker(QObject):
    frame_received = Signal(object)  # UdpFrame
    error = Signal(str)
    started_listening = Signal()
    stopped = Signal()

    def __init__(self, endpoint: MulticastEndpoint) -> None:
        super().__init__()
        self._endpoint = endpoint
        self._sock: socket.socket | None = None
        self._running = False

    @Slot()
    def start(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self._endpoint.port))
            mreq = struct.pack(
                "4s4s",
                socket.inet_aton(str(self._endpoint.group)),
                socket.inet_aton(self._endpoint.interface),
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(0.5)  # damit stop() reagieren kann
            self._sock = sock
            self._running = True
            self.started_listening.emit()
        except OSError as e:
            self.error.emit(f"Listener-Socket konnte nicht geöffnet werden: {e}")
            return

        while self._running:
            try:
                data, _addr = sock.recvfrom(_RECV_BUF)
            except TimeoutError:
                continue
            except OSError as e:
                self.error.emit(f"recvfrom Fehler: {e}")
                break

            try:
                frame = UdpFrame.decode(data)
            except ValueError as e:
                self.error.emit(f"Frame-Dekodierung: {e}")
                continue

            self.frame_received.emit(frame)

        self._close()
        self.stopped.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
