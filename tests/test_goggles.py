import struct
import pytest
from goggles import make_packet, parse_packet, ReceiveWindow, build_ack, RecoveryGate


def test_capture_header_and_corruption():
    raw = bytes.fromhex('0980f8070000007601')
    assert parse_packet(raw, 0x07f8) == (0, 0, b'\x01')
    assert parse_packet(raw, 1) is None
    assert parse_packet(raw[:-1], 0x07f8) is None
    bad = bytearray(raw)
    bad[7] ^= 1
    assert parse_packet(bad, 0x07f8) is None


def test_transport_and_ack_match_real_g3_shape():
    packet = make_packet(2, b'123456789012data', 17, 24)
    assert parse_packet(packet, 17) == (2,24,b'123456789012data')
    ack = build_ack(17, 8, 32)
    assert len(ack) == 34
    assert parse_packet(ack,17)[0] == 4
    assert struct.unpack_from('<HH',ack,8) == (32,32)


def test_sequence_reorder_duplicate_and_wrap():
    window = ReceiveWindow(65520)
    assert window.push(0,b'b') == []
    assert window.push(65528,b'a') == [b'a',b'b']
    assert window.cursor == 0
    assert window.push(65528,b'duplicate') == []
    assert window.push(1,b'retransmission') == []
    assert window.push(8,b'c') == [b'c']


def test_packet_gap_is_bounded():
    window = ReceiveWindow(0)
    with pytest.raises(ConnectionError):
        window.push(2048,b'too far ahead')


def test_midstream_refresh_waits_then_displays_despite_sticky_flag():
    gate = RecoveryGate()
    assert all(not gate.accept(corrupt=True) for _ in range(59))
    assert gate.accept(corrupt=True)
    assert gate.accept(corrupt=True)


def test_clean_reference_displays_immediately_but_new_corruption_reconnects():
    gate = RecoveryGate()
    assert gate.accept(corrupt=False)
    with pytest.raises(ConnectionError):
        gate.accept(corrupt=True)
