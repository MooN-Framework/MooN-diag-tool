"""
Decoder for the binary operational frames emitted by the Rust framework.

Frame layout (little-endian, matches src/framework/wire/frame.rs):
    [0..1]    node_id            u8
    [1..9]    session_id         u64
    [9..13]   seq_num            u32
    [13..14]  node_state_wire    u8
    [14..22]  timestamp_ns       u64
    [22..23]  payload_disc       u8
    [23..N]   payload_body       (discriminator-dependent)
    [N..N+4]  crc32              u32 (crc32fast over bytes[0..N])

Discriminator values (see DISC_* constants in frame.rs):
    STATE                     0x00  -> 2 bytes  (seen_mask + active_count)
    RESULT                    0x01  -> P::WIRE_SIZE (braking curve: 10)
    ACK                       0x02  -> 3 bytes  (received_from + publisher_candidate + rejoin_vote)
    TIMESYNC_REQ              0x03  -> 8 bytes  (t1 u64)
    TIMESYNC_RESP             0x04  -> 24 bytes (t1 + t2 + t3, u64 each)
    EXCLUSION_PROPOSAL        0x05  -> 1 byte   (propose_exclude mask)
    SYSTEM_STATE_CRC          0x06  -> 4 bytes  (crc u32)
    SYSTEM_STATE_SNAPSHOT     0x07  -> variable
    SYSTEM_STATE_SNAPSHOT_ACK 0x08  -> 4 bytes  (adopted_crc u32)
    INPUT                     0x09  -> I::WIRE_SIZE
    GO_FAILSAFE               0x0A  -> 1 byte   (reason)
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional


HEADER_LEN = 23
CRC_LEN = 4


class Discriminator(IntEnum):
    # Values match src/framework/wire/frame.rs (DISC_* constants).
    STATE = 0x00
    RESULT = 0x01
    ACK = 0x02
    TIMESYNC_REQ = 0x03
    TIMESYNC_RESP = 0x04
    EXCLUSION_PROPOSAL = 0x05
    SYSTEM_STATE_CRC = 0x06
    SYSTEM_STATE_SNAPSHOT = 0x07
    SYSTEM_STATE_SNAPSHOT_ACK = 0x08
    INPUT = 0x09
    GO_FAILSAFE = 0x0A


# NodeState wire byte -> string. Taken from diag.py (test harness) and
# aligned with the values in diagnostic.rs.
NODE_STATE_NAMES: dict[int, str] = {
    0x01: "Startup",
    0x02: "InitSync",
    0x03: "CycleSync",
    0x04: "ReadInputs",
    0x05: "ShareResult",
    0x06: "SendAck",
    0x07: "PublishResult",
    0x08: "ErrorManagement",
    0x09: "Isolation",
    0x0A: "ClockSync",
    0x0B: "ResyncLostPeer",
    0x0C: "SystemStateCrcExchange",
    0x0D: "SystemStateSync",
    0x0E: "ShareInputs",
    0xFF: "Failsafe",
}

DISCRIMINATOR_NAMES: dict[int, str] = {
    Discriminator.STATE: "STATE",
    Discriminator.RESULT: "RESULT",
    Discriminator.ACK: "ACK",
    Discriminator.TIMESYNC_REQ: "TIMESYNC_REQ",
    Discriminator.TIMESYNC_RESP: "TIMESYNC_RESP",
    Discriminator.EXCLUSION_PROPOSAL: "EXCLUSION_PROPOSAL",
    Discriminator.SYSTEM_STATE_CRC: "SYSTEM_STATE_CRC",
    Discriminator.SYSTEM_STATE_SNAPSHOT: "SYSTEM_STATE_SNAPSHOT",
    Discriminator.SYSTEM_STATE_SNAPSHOT_ACK: "SYSTEM_STATE_SNAPSHOT_ACK",
    Discriminator.INPUT: "INPUT",
    Discriminator.GO_FAILSAFE: "GO_FAILSAFE",
}


@dataclass(slots=True)
class DecodedFrame:
    node_id: int
    session_id: int
    seq_num: int
    node_state_wire: int
    node_state_name: str
    timestamp_ns: int
    discriminator: int
    discriminator_name: str
    body: dict[str, Any] = field(default_factory=dict)
    body_raw: bytes = b""
    crc_ok: bool = True
    total_size: int = 0
    error: Optional[str] = None

    def summary(self) -> str:
        crc = "" if self.crc_ok else " [CRC BAD]"
        parts = [
            f"node={self.node_id}",
            f"seq={self.seq_num}",
            f"state={self.node_state_name}",
            f"kind={self.discriminator_name}",
        ]
        if self.body:
            parts.append(str(self.body))
        return " ".join(parts) + crc


def decode_frame(data: bytes) -> DecodedFrame:
    """
    Decode a single UDP payload. Never raises: on error, returns a
    DecodedFrame with `error` set so the UI can surface the issue
    rather than silently dropping the frame.
    """
    if len(data) < HEADER_LEN + CRC_LEN:
        return DecodedFrame(
            node_id=0, session_id=0, seq_num=0, node_state_wire=0,
            node_state_name="?", timestamp_ns=0, discriminator=0,
            discriminator_name="?", total_size=len(data),
            error=f"frame too short ({len(data)} bytes)",
            # crc_ok defaults to True on DecodedFrame, which is wrong
            # here: we never got far enough to check the CRC at all.
            # Downstream code (main_window._on_op_frame,
            # timing_measure._on_frame) treats crc_ok as "safe to feed
            # into the registry / timing stats" and does NOT also
            # check `error` in every place -- leaving this at the
            # default let a too-short/garbage UDP packet on the
            # operational port masquerade as a verified frame from
            # node_id=0 with all-zero fields.
            crc_ok=False,
        )

    try:
        (node_id, session_id, seq_num, state_wire, ts_ns, disc) = struct.unpack_from(
            "<BQIBQB", data, 0
        )
    except struct.error as e:
        return DecodedFrame(
            node_id=0, session_id=0, seq_num=0, node_state_wire=0,
            node_state_name="?", timestamp_ns=0, discriminator=0,
            discriminator_name="?", total_size=len(data),
            error=f"header unpack: {e}",
            crc_ok=False,  # see the "frame too short" branch above -- same reasoning
        )

    body_end = len(data) - CRC_LEN
    body_raw = data[HEADER_LEN:body_end]
    (crc_recv,) = struct.unpack_from("<I", data, body_end)
    crc_calc = zlib.crc32(data[:body_end]) & 0xFFFFFFFF
    crc_ok = crc_recv == crc_calc

    frame = DecodedFrame(
        node_id=node_id,
        session_id=session_id,
        seq_num=seq_num,
        node_state_wire=state_wire,
        node_state_name=NODE_STATE_NAMES.get(state_wire, f"0x{state_wire:02X}?"),
        timestamp_ns=ts_ns,
        discriminator=disc,
        discriminator_name=DISCRIMINATOR_NAMES.get(disc, f"0x{disc:02X}?"),
        body_raw=body_raw,
        crc_ok=crc_ok,
        total_size=len(data),
    )

    if not crc_ok:
        frame.error = f"crc mismatch: got=0x{crc_recv:08x} calc=0x{crc_calc:08x}"
        return frame

    # Decode body per discriminator.
    try:
        if disc == Discriminator.STATE and len(body_raw) >= 2:
            frame.body = {
                "seen_mask": body_raw[0],
                "active_count": body_raw[1],
            }
        elif disc == Discriminator.ACK and len(body_raw) >= 3:
            frame.body = {
                "received_from": body_raw[0],
                "publisher_candidate": body_raw[1],
                "rejoin_vote": body_raw[2],
            }
        elif disc == Discriminator.EXCLUSION_PROPOSAL and len(body_raw) >= 1:
            frame.body = {"propose_exclude": body_raw[0]}
        elif disc == Discriminator.TIMESYNC_REQ and len(body_raw) >= 8:
            (t1,) = struct.unpack_from("<Q", body_raw, 0)
            frame.body = {"t1_ns": t1}
        elif disc == Discriminator.TIMESYNC_RESP and len(body_raw) >= 24:
            t1, t2, t3 = struct.unpack_from("<QQQ", body_raw, 0)
            frame.body = {"t1_ns": t1, "t2_ns": t2, "t3_ns": t3}
        elif disc == Discriminator.SYSTEM_STATE_CRC and len(body_raw) >= 4:
            (crc,) = struct.unpack_from("<I", body_raw, 0)
            frame.body = {"crc": f"0x{crc:08x}"}
        elif disc == Discriminator.SYSTEM_STATE_SNAPSHOT_ACK and len(body_raw) >= 4:
            (adopted_crc,) = struct.unpack_from("<I", body_raw, 0)
            frame.body = {"adopted_crc": f"0x{adopted_crc:08x}"}
        elif disc == Discriminator.GO_FAILSAFE and len(body_raw) >= 1:
            frame.body = {"reason": body_raw[0]}
        elif disc == Discriminator.SYSTEM_STATE_SNAPSHOT:
            # Variable-size snapshot: nominal(1) + min(1) + prob_cycles(4)
            # + current_seq(4) + N * slot(7). We show scalars and slot count.
            if len(body_raw) >= 10:
                nom, mn = body_raw[0], body_raw[1]
                prob_cycles = struct.unpack_from("<I", body_raw, 2)[0]
                cur_seq = struct.unpack_from("<I", body_raw, 6)[0]
                slots = (len(body_raw) - 10) // 7
                frame.body = {
                    "nominal": nom, "min": mn,
                    "probation_cycles": prob_cycles,
                    "current_seq": cur_seq,
                    "slots": slots,
                }
            else:
                frame.body = {"payload_len": len(body_raw), "hex": body_raw.hex()}
        elif disc == Discriminator.RESULT:
            # Framework-generic (P::WIRE_SIZE). For the braking curve
            # typically 10 bytes; we only show length and hex dump here.
            frame.body = {"payload_len": len(body_raw), "hex": body_raw.hex()}
        elif disc == Discriminator.INPUT:
            frame.body = {"payload_len": len(body_raw), "hex": body_raw.hex()}
        else:
            frame.body = {"payload_len": len(body_raw), "hex": body_raw.hex()}
    except struct.error as e:  # pragma: no cover
        frame.error = f"body unpack ({frame.discriminator_name}): {e}"

    return frame
