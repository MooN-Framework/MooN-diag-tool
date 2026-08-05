"""Roundtrip-Test für UdpFrame."""

from __future__ import annotations

import pytest

from diag_tool.net.frame import FrameKind, UdpFrame


def test_encode_decode_roundtrip() -> None:
    original = UdpFrame(
        kind=FrameKind.VOTE,
        node_id=2,
        seq=12345,
        payload=b"\xde\xad\xbe\xef",
    )
    data = original.encode()
    restored = UdpFrame.decode(data)
    assert restored == original


def test_decode_rejects_short_data() -> None:
    with pytest.raises(ValueError):
        UdpFrame.decode(b"\x00")


def test_decode_rejects_truncated_payload() -> None:
    good = UdpFrame(
        kind=FrameKind.HEARTBEAT, node_id=1, seq=1, payload=b"\x01\x02\x03\x04"
    ).encode()
    with pytest.raises(ValueError):
        UdpFrame.decode(good[:-2])
