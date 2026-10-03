"""Goggles 3 live H.264 over its USB RNDIS link, verified with Avata 2.

Only session-open and transport ACK messages are sent. No aircraft controls.
Transport reference: samuelsadok/dji_protocol/udp_protocol.md; G3 ACK shape
and receiving behavior verified on the connected hardware on 2026-09-14.
"""
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
import av

LOCAL_IP = '192.168.60.1'
GOGGLES_IP = '192.168.60.2'
SYN_TAIL = bytes.fromhex('64006400c0051400000a0064006400c0051400006400'
                        '14006400c00514000064000101040a02')


def make_packet(kind, body, session, sequence=0):
    header = struct.pack('<HHHB', 0x8000 | (8+len(body)), session, sequence, kind)
    check = 0
    for byte in header:
        check ^= byte
    return header + bytes([check]) + body


def parse_packet(data, session):
    if len(data) < 8 or (int.from_bytes(data[:2],'little') & 0x7fff) != len(data):
        return None
    if int.from_bytes(data[2:4],'little') != session:
        return None
    check = 0
    for byte in data[:8]:
        check ^= byte
    if check:
        return None
    return data[6], int.from_bytes(data[4:6],'little'), data[8:]


def build_ack(session, seed, cursor):
    return make_packet(4, struct.pack('<13H', cursor,cursor,0,0,
                       seed,seed,0,0,seed,seed,0,0,0), session)


class ReceiveWindow:
    """Deliver contiguous payload once; keep bounded reorder space across wrap."""
    def __init__(self, seed):
        self.cursor = seed
        self.pending = {}
        self.gap_since = None

    def push(self, sequence, payload):
        sequence &= 0xfff8  # low bits identify retransmissions
        ahead = (sequence-self.cursor) & 0xffff
        if ahead == 0 or ahead >= 0x8000:
            return []
        if ahead > 1024:
            raise ConnectionError('Video packet window exceeded')
        self.pending.setdefault(sequence,payload)
        ready = []
        while ((self.cursor+8)&0xffff) in self.pending:
            self.cursor = (self.cursor+8)&0xffff
            ready.append(self.pending.pop(self.cursor))
        self.gap_since = (self.gap_since or time.monotonic()) if self.pending else None
        return ready


class RecoveryGate:
    """Allow G3 gradual intra refresh after 60 contiguous decoded pictures.

    FFmpeg propagates the initial missing-reference flag indefinitely when this
    stream is joined without an IDR. Bench comparison against IDR-started decode
    matched every luma pixel after 24-43 pictures at six join points. Keep a
    60-picture settling interval; restart on transport loss or decoder errors.
    This interval is verified for the tested G3/Avata 2 stream, not all firmware.
    """
    def __init__(self):
        self.count = 0
        self.clean_reference = False

    def accept(self, corrupt):
        self.count += 1
        if not corrupt:
            self.clean_reference = True
            return True
        if self.clean_reference:
            raise ConnectionError('Decoded video reference damaged')
        return self.count >= 60


def usb_ready():
    """Verify DJI PNP identity before binding; never fall back to Wi-Fi or LAN."""
    if sys.platform == 'darwin':
        # Interface claim, RNDIS and ARP are verified inside the connection.
        return True, 'macOS 직접 USB 연결 확인 중'
    if os.name != 'nt':
        return False, 'Windows에서 실행하세요.'
    script = r'''
$d = @(Get-CimInstance Win32_NetworkAdapter | Where-Object {
    $_.PNPDeviceID -like 'USB\VID_2CA3&PID_0020&MI_00\*'
})
$ready = $false
foreach ($a in $d) {
    if (Get-NetIPAddress -InterfaceIndex $a.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq '192.168.60.1' -and $_.AddressState -eq 'Preferred' }) { $ready=$true }
}
@{found=($d.Count -gt 0); ready=$ready} | ConvertTo-Json -Compress
'''
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
        capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    data = json.loads(result.stdout.decode('utf-8-sig'))
    if data['ready']:
        return True, 'USB 연결 확인'
    return False, ('USB 최초 설정이 필요합니다. USB 설정 버튼을 누르세요.' if data['found']
                   else '고글 USB 연결 대기 · 데이터 케이블을 연결하세요.')


@contextmanager
def windows_connection():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,2*1024*1024)
        connection.bind((LOCAL_IP,12346))
        connection.connect((GOGGLES_IP,9003))
        connection.settimeout(.05)
        yield connection


def datagram_connection(stop, state):
    if sys.platform == 'darwin':
        from macos_usb import USBConnection
        return USBConnection(stop=stop,state=state)
    return windows_connection()


def receive_session(stop, stats, state):
    seed = secrets.randbelow(8192)*8
    session = secrets.randbelow(65536)
    window = ReceiveWindow(seed)
    decoder = av.CodecContext.create('h264','r')
    decoder.thread_type = 'SLICE'
    decoder.thread_count = 4
    decoder.flags |= av.codec.context.Flags.low_delay
    decoder.flags2 |= av.codec.context.Flags2.show_all
    recovery = RecoveryGate()
    with datagram_connection(stop,state) as connection:
        connection.send(make_packet(0,struct.pack('<H',seed)+SYN_TAIL,session))
        stats['sessions'] += 1
        state('고글 영상 연결 중 · 고글의 라이브뷰 공유가 켜져 있어야 합니다.')
        last_decoded = last_video = time.monotonic()
        last_ack = 0
        while not stop.is_set():
            now = time.monotonic()
            if now-last_ack >= .01:
                connection.send(build_ack(session,seed,window.cursor))
                last_ack = now
            if now-last_video > 3:
                raise TimeoutError('영상 신호 대기 · USB 연결 / 라이브뷰 공유 / 기체 영상을 확인하세요.')
            if now-last_decoded > 5:
                raise ConnectionError('새 영상 시작 프레임 대기 · 다시 연결 중')
            if window.gap_since and now-window.gap_since > .3:
                raise ConnectionError('영상 패킷 누락 · 다시 연결 중')
            try:
                raw = connection.recv(65535)
            except socket.timeout:
                continue
            parsed = parse_packet(raw,session)
            if parsed is None:
                stats['invalid_packets'] += 1
                continue
            stats['last_packet_time'] = time.monotonic()
            kind, sequence, payload = parsed
            if kind != 2 or len(payload)<12:
                continue
            last_video = time.monotonic()
            stats['video_bytes'] += len(payload)-12
            for data in window.push(sequence,payload[12:]):
                stats['ordered_bytes'] = stats.get('ordered_bytes',0)+len(data)
                for packet in decoder.parse(data):
                    stats['parsed_frames'] = stats.get('parsed_frames',0)+1
                    try:
                        frames = decoder.decode(packet)
                    except av.error.InvalidDataError:
                        # A reconnect can join a GOP before its SPS/IDR arrives.
                        stats['decode_errors'] = stats.get('decode_errors',0)+1
                        recovery = RecoveryGate()
                        continue
                    for frame in frames:
                        if stop.is_set():
                            return
                        last_decoded = time.monotonic()
                        if not recovery.accept(frame.is_corrupt):
                            stats['warmup_frames'] = stats.get('warmup_frames',0)+1
                            if recovery.count == 1:
                                state('USB 영상 복원 중 · 약 2초 후 출력합니다.')
                            continue
                        if frame.is_corrupt:
                            stats['intra_refresh_frames'] = stats.get('intra_refresh_frames',0)+1
                        stats['frames'] += 1
                        stats['width'],stats['height'] = frame.width,frame.height
                        yield frame


def decode_goggles(stop, state=lambda message:None, stats=None, retry_delay=1):
    """Wait for USB and reconnect with fresh transport/decoder state on failure."""
    if stats is None:
        stats = {}
    for key in ('sessions','video_bytes','frames','invalid_packets','retries'):
        stats.setdefault(key,0)
    while not stop.is_set():
        try:
            ready,message = usb_ready()
            if stop.is_set():
                return
            if not ready:
                state(message)
            else:
                yield from receive_session(stop,stats,state)
        except (OSError,ValueError,subprocess.SubprocessError,av.error.FFmpegError) as exc:
            stats['retries'] += 1
            stats['last_error'] = type(exc).__name__ + ': ' + str(exc)
            if isinstance(exc,TimeoutError):
                state(str(exc))
            elif getattr(exc,'winerror',None) == 10048:
                state('다른 고글 수신 프로그램이 실행 중입니다. 해당 프로그램을 종료하세요.')
            else:
                state(str(exc) if sys.platform == 'darwin' else 'USB 영상 다시 연결 중…')
        if stop.wait(retry_delay):
            return
