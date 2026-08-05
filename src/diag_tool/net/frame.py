"""UDP-Wireformat — Spiegel von ``udp_frame.rs``.

STUB: Die tatsächlichen Feld-Layouts, Byte-Order und CRC-Details müssen mit
der Rust-Implementierung abgeglichen werden. Aktuell als Platzhalter mit
klarer Serialisierungsschnittstelle, damit ListenerWorker/CommandWorker
schon gegen ein Interface arbeiten können.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import ClassVar


class FrameKind(IntEnum):
    HEARTBEAT = 0x01
    VOTE = 0x02
    RESULT = 0x03
    COMMAND = 0x10
    RESPONSE = 0x11


@dataclass(frozen=True, slots=True)
class UdpFrame:
    """Minimaler Frame-Header. Payload als Bytes, um sie später typisiert
    zu dekodieren (je nach ``kind``)."""

    # Kopf: kind(u8) | node_id(u8) | seq(u32) | payload_len(u16)
    HEADER_FMT: ClassVar[str] = "!BBIH"
    HEADER_LEN: ClassVar[int] = struct.calcsize(HEADER_FMT)

    kind: FrameKind
    node_id: int
    seq: int
    payload: bytes

    def encode(self) -> bytes:
        head = struct.pack(
            self.HEADER_FMT,
            int(self.kind),
            self.node_id,
            self.seq,
            len(self.payload),
        )
        return head + self.payload

    @classmethod
    def decode(cls, data: bytes) -> UdpFrame:
        if len(data) < cls.HEADER_LEN:
            raise ValueError(f"Frame zu kurz: {len(data)} < {cls.HEADER_LEN}")
        kind_raw, node_id, seq, plen = struct.unpack(
            cls.HEADER_FMT, data[: cls.HEADER_LEN]
        )
        payload = data[cls.HEADER_LEN : cls.HEADER_LEN + plen]
        if len(payload) != plen:
            raise ValueError(
                f"Payload-Länge inkonsistent: erwartet {plen}, gelesen {len(payload)}"
            )
        return cls(
            kind=FrameKind(kind_raw),
            node_id=node_id,
            seq=seq,
            payload=payload,
        )
