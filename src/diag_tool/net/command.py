"""CommandWorker — bidirektionale Diagnose-Schnittstelle.

Empfängt Command-Responses vom Multicast und sendet Commands / Fault-Injection
Frames. send_command / inject_frame sind Slots, die aus dem GUI-Thread aufgerufen
werden können.
"""

from __future__ import annotations

import socket
import struct

from PySide6.QtCore import QObject, Signal, Slot

from diag_tool.config import MulticastEndpoint
from diag_tool.net.frame import UdpFrame

_RECV_BUF = 65535


class CommandWorker(QObject):
    frame_received = Signal(object)  # UdpFrame (Response o.ä.)
    frame_sent = Signal(object)  # UdpFrame (für Logging)
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
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_TTL,
                struct.pack("b", self._endpoint.ttl),
            )
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_IF,
                socket.inet_aton(self._endpoint.interface),
            )
            sock.bind(("", self._endpoint.port))
            mreq = struct.pack(
                "4s4s",
                socket.inet_aton(str(self._endpoint.group)),
                socket.inet_aton(self._endpoint.interface),
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(0.5)
            self._sock = sock
            self._running = True
            self.started_listening.emit()
        except OSError as e:
            self.error.emit(f"Command-Socket konnte nicht geöffnet werden: {e}")
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

    @Slot(object)
    def send_frame(self, frame: UdpFrame) -> None:
        """Sendet einen (auch manipulierten) Frame auf den Diagnose-Multicast."""
        if self._sock is None:
            self.error.emit("send_frame: Socket nicht bereit")
            return
        try:
            self._sock.sendto(
                frame.encode(),
                (str(self._endpoint.group), self._endpoint.port),
            )
            self.frame_sent.emit(frame)
        except OSError as e:
            self.error.emit(f"sendto Fehler: {e}")

    @Slot()
    def stop(self) -> None:
        self._running = False

    def _close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
