"""Single-owner, userspace RNDIS transport. Never detach/reset a USB device.

Hardware compatibility is conditional on macOS permitting both interface claims.
Imports of PyUSB/libusb are lazy so Windows keeps its native RNDIS socket path.
"""
import socket
import struct
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from usb_network import NetworkPeer, rndis_packet, RNDISStream, RNDISFramingError
from support_report import SupportError,issue_from_exception,record_success

VID, PID = 0x2ca3, 0x0020
MAX_TRANSFER = 1024*1024


def descriptors(raw):
    raw = bytes(raw)
    while raw:
        size = raw[0]
        if size < 2 or size > len(raw):
            raise SupportError('GH-USB-DESCRIPTOR','discovery')
        yield raw[:size]
        raw = raw[size:]


@dataclass
class Layout:
    control: object
    data: object
    bulk_in: int
    bulk_out: int
    interrupt_in: int


def select_interfaces(config):
    interfaces = list(config)
    candidates = []
    for control in interfaces:
        identity = (control.bInterfaceClass,control.bInterfaceSubClass,control.bInterfaceProtocol)
        if identity not in ((0xe0,1,3),(2,2,255)):
            continue
        associated = set()
        for extra in descriptors(getattr(control,'extra_descriptors',b'')):
            if len(extra) >= 5 and extra[1:3] == b'\x24\x06' and extra[3] == control.bInterfaceNumber:
                associated.update(extra[4:])
        for extra in descriptors(getattr(config,'extra_descriptors',b'')):
            if len(extra) >= 8 and extra[1] == 11 and extra[2] == control.bInterfaceNumber and extra[3] == 2:
                associated.add(control.bInterfaceNumber+1)
        interrupt = [e.bEndpointAddress for e in control if e.bmAttributes & 3 == 3 and e.bEndpointAddress & 128]
        for data in interfaces:
            if data.bInterfaceNumber not in associated or data.bInterfaceClass != 10:
                continue
            incoming = [e.bEndpointAddress for e in data if e.bmAttributes & 3 == 2 and e.bEndpointAddress & 128]
            outgoing = [e.bEndpointAddress for e in data if e.bmAttributes & 3 == 2 and not e.bEndpointAddress & 128]
            if len(incoming) == len(outgoing) == len(interrupt) == 1:
                candidates.append(Layout(control,data,incoming[0],outgoing[0],interrupt[0]))
    if len(candidates) != 1:
        raise SupportError('GH-USB-DESCRIPTOR','discovery')
    return candidates[0]


def usb_backend():
    try:
        import usb.backend.libusb1
    except ImportError as exc:
        raise SupportError('GH-USB-DEPENDENCY','discovery') from exc
    # Frozen build explicitly bundles libusb, avoiding a Homebrew requirement.
    root = Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
    library = root/'libusb-1.0.dylib'
    backend = usb.backend.libusb1.get_backend(find_library=lambda name:str(library)) if library.is_file() else usb.backend.libusb1.get_backend()
    if backend is None:
        raise SupportError('GH-USB-DEPENDENCY','discovery')
    return backend


def find_device():
    try:
        import usb.core
    except ImportError as exc:
        raise SupportError('GH-USB-DEPENDENCY','discovery') from exc
    devices = list(usb.core.find(find_all=True,idVendor=VID,idProduct=PID,backend=usb_backend()))
    if len(devices) != 1:
        raise SupportError('GH-USB-MISSING' if not devices else 'GH-USB-DESCRIPTOR','discovery')
    return devices[0]


def is_timeout(exc):
    return isinstance(exc,(TimeoutError,socket.timeout)) or getattr(exc,'errno',None) in (60,110) or getattr(exc,'backend_error_code',None) == -7


def usb_error(exc, stage='discovery',operation=None):
    return issue_from_exception(exc,stage,operation=operation)


class RNDISControl:
    def __init__(self, device, interface, stop=None,stats=None):
        self.device,self.interface,self.stop = device,interface,stop
        self.request_id = 0
        self.initialized = False
        self.stats = stats if stats is not None else {}

    def command(self, kind, body=b'', *, reply=True):
        started = time.perf_counter()
        try:
            return self._command(kind,body,reply=reply)
        finally:
            elapsed = (time.perf_counter()-started)*1000
            self.stats['rndis_control_ms'] = round(elapsed,3)
            self.stats['rndis_max_control_ms'] = max(self.stats.get('rndis_max_control_ms',0),round(elapsed,3))

    def _command(self, kind, body=b'', *, reply=True):
        self.request_id = (self.request_id+1) & 0xffffffff
        request_id = self.request_id
        message = struct.pack('<3I',kind,12+len(body),request_id)+body
        written = self.device.ctrl_transfer(0x21,0,0,self.interface,message,timeout=500)
        if written != len(message):
            raise OSError('Short RNDIS control write')
        if not reply:
            return b''
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            if self.stop is not None and self.stop.is_set():
                raise OSError('USB connection cancelled')
            try:
                result = bytes(self.device.ctrl_transfer(0xa1,1,0,self.interface,4096,timeout=100))
            except OSError as exc:
                if is_timeout(exc):
                    continue
                raise
            if not result or not any(result):
                time.sleep(.005)
                continue
            if len(result) < 12:
                raise OSError('Truncated RNDIS response')
            response_type,length,identity = struct.unpack_from('<3I',result)
            if length != len(result):
                raise OSError('Invalid RNDIS response length')
            if response_type == 7:  # Unsolicited RNDIS_INDICATE_STATUS_MSG has no RequestId.
                if length < 20:
                    raise OSError('Truncated RNDIS status indication')
                continue
            if response_type != kind | 0x80000000 or identity != request_id or length < 16:
                raise OSError('RNDIS completion type/RequestId mismatch')
            if struct.unpack_from('<I',result,12)[0] != 0:
                raise OSError('RNDIS command failed')
            return result
        raise OSError('고글 RNDIS 응답 시간 초과 · USB를 다시 연결하세요.')

    def query(self, oid):
        response = self.command(4,struct.pack('<4I',oid,0,0,0))
        if len(response) < 24:
            raise OSError('Truncated RNDIS query completion')
        size,offset = struct.unpack_from('<2I',response,16)
        start = 8+offset
        if start < 24 or start+size > len(response):
            raise OSError('Invalid RNDIS query bounds')
        return response[start:start+size]

    def initialize(self):
        self.stats['operation'] = 'rndis_initialize'
        result = self.command(2,struct.pack('<3I',1,0,MAX_TRANSFER))
        if len(result) != 52:
            raise OSError('Invalid RNDIS initialize completion')
        major,minor,flags,medium,packets,transfer,alignment,af_offset,af_size = struct.unpack_from('<9I',result,16)
        if major != 1 or minor != 0 or medium != 0 or flags != 1 or not 1 <= packets <= 65535 or not 44+14 <= transfer <= MAX_TRANSFER or alignment > 7 or af_offset or af_size:
            raise OSError('Unsupported RNDIS transfer configuration')
        self.initialized = True
        self.stats['operation'] = 'rndis_query_mac'
        mac = self.query(0x01010102)  # OID_802_3_CURRENT_ADDRESS
        self.stats['operation'] = 'rndis_query_mtu'
        mtu_data = self.query(0x00010106)  # OID_GEN_MAXIMUM_FRAME_SIZE
        if len(mac) != 6 or mac[0] & 1 or not any(mac) or len(mtu_data) != 4:
            raise OSError('Invalid RNDIS adapter identity')
        mtu = struct.unpack('<I',mtu_data)[0]
        if not 576 <= mtu <= 65515 or transfer < 44+14+mtu:
            raise OSError('Invalid RNDIS MTU')
        self.stats['operation'] = 'rndis_set_filter'
        self.command(5,struct.pack('<4I',0x0001010e,4,20,0)+struct.pack('<I',9))
        return transfer,mac,mtu

    def keepalive(self):
        self.stats['operation'] = 'rndis_keepalive'
        self.command(8)

    def halt(self):
        if self.initialized:
            self.command(3,reply=False)
            self.initialized = False


class USBConnection:
    def __init__(self, *, device=None, util=None, stop=None, state=lambda message:None,stats=None):
        self.device,self.util,self.stop,self.state = device,util,stop,state
        self.claimed = []
        self.control = None
        self.pending = deque()
        self.last_keepalive = 0
        self.stats = stats if stats is not None else {}
        self.framing = RNDISStream(max_message=MAX_TRANSFER)

    def __enter__(self):
        try:
            self.stats['stage'] = 'discovery'
            if self.device is None:
                self.device = find_device()
            if self.util is None:
                import usb.util
                self.util = usb.util
            self.state('USB 장치 확인 · RNDIS 인터페이스 연결 중')
            config = self.device.get_active_configuration()
            self.layout = select_interfaces(config)
            self.stats['stage'] = 'claim'
            for interface in (self.layout.control,self.layout.data):
                number = interface.bInterfaceNumber
                self.util.claim_interface(self.device,number)
                self.claimed.append(number)
                if interface.bAlternateSetting or sum(i.bInterfaceNumber == number for i in config) > 1:
                    self.device.set_interface_altsetting(interface=number,alternate_setting=interface.bAlternateSetting)
            self.control = RNDISControl(self.device,self.layout.control.bInterfaceNumber,self.stop,self.stats)
            self.stats['stage'] = 'rndis_init'
            self.transfer,self.mac,self.mtu = self.control.initialize()
            record_success(self.stats,'rndis_init',recovered=False)
            self.last_keepalive = time.monotonic()
            self.peer = NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,self.mac)
            self.state('RNDIS 초기화 완료 · 고글 ARP 응답 확인 중')
            self.stats['stage'] = 'arp'
            deadline,next_request = time.monotonic()+3,0
            while self.peer.remote_mac is None and time.monotonic() < deadline:
                self._cancelled()
                if time.monotonic() >= next_request:
                    self._write(self.peer.arp_request())
                    next_request = time.monotonic()+.3
                self._read()
            if self.peer.remote_mac is None:
                raise SupportError('GH-ARP-TIMEOUT','arp')
            record_success(self.stats,'arp',recovered=False)
            self.state('USB RNDIS / ARP 확인 · 고글 영상 패킷 대기')
            return self
        except Exception as exc:
            self.close()
            if isinstance(exc,OSError):
                raise usb_error(exc,self.stats.get('stage','discovery'),self.stats.get('operation')) from exc
            raise

    def _cancelled(self):
        if self.stop is not None and self.stop.is_set():
            raise OSError('USB connection cancelled')

    def _write(self, frame):
        self.stats['operation'] = 'usb_write'
        payload = rndis_packet(frame)
        if len(payload) > self.transfer:
            raise OSError('RNDIS outgoing transfer too large')
        try:
            written = self.device.write(self.layout.bulk_out,payload,timeout=100)
        except OSError as exc:
            raise usb_error(exc,self.stats.get('stage','video'),self.stats.get('operation')) from exc
        if written != len(payload):
            raise OSError('Short RNDIS bulk write')

    def _read(self):
        previous_read_bytes = self.stats.get('last_usb_read_bytes',0)
        previous_padding_bytes = self.framing.padding_bytes
        self.stats['operation'] = 'usb_read'
        self.stats['usb_read_calls'] = self.stats.get('usb_read_calls',0)+1
        try:
            data = self.device.read(self.layout.bulk_in,MAX_TRANSFER,timeout=10)
        except OSError as exc:
            if is_timeout(exc):
                data = b''
                self.stats['usb_timeout_reads'] = self.stats.get('usb_timeout_reads',0)+1
            else:
                raise usb_error(exc,self.stats.get('stage','video'),self.stats.get('operation')) from exc
        self.stats['last_usb_read_bytes'] = len(data)
        if not data:self.stats['usb_empty_reads'] = self.stats.get('usb_empty_reads',0)+1
        self.stats['usb_read_bytes'] = self.stats.get('usb_read_bytes',0)+len(data)
        try:
            self.stats['operation'] = 'rndis_parse'
            frames = self.framing.feed(data)
        except RNDISFramingError as exc:
            exc.context.update(read_bytes=len(data),previous_read_bytes=previous_read_bytes,
                usb_read_call=self.stats['usb_read_calls'])
            self.stats['rndis_framing_errors'] = self.stats.get('rndis_framing_errors',0)+1
            raise SupportError('GH-RNDIS-FRAMING',self.stats.get('stage','video'),exc,operation='rndis_parse') from exc
        finally:
            self.stats['rndis_zero_padding_bytes'] = self.stats.get('rndis_zero_padding_bytes',0)+self.framing.padding_bytes-previous_padding_bytes
        self.stats['rndis_buffered_bytes'] = self.framing.buffered_bytes
        self.stats['rndis_expected_bytes'] = self.framing.expected_bytes
        self.stats['rndis_max_buffered_bytes'] = max(self.stats.get('rndis_max_buffered_bytes',0),self.framing.buffered_bytes)
        if data and self.framing.buffered_bytes:
            self.stats['rndis_partial_reads'] = self.stats.get('rndis_partial_reads',0)+1
        self.stats['rndis_messages'] = self.stats.get('rndis_messages',0)+len(frames)
        for frame in frames:
            self.stats['operation'] = 'network_receive'
            payload,reply = self.peer.receive(frame)
            if reply:
                self._write(reply)
            if payload is not None:
                if len(self.pending) >= 256:
                    raise SupportError('GH-VIDEO-GAP','video')
                self.pending.append(payload)

    def send(self, data):
        self._cancelled()
        self._write(self.peer.datagram(data))
        return len(data)

    def recv(self, size):
        self._cancelled()
        self.stats['stage'] = 'video'
        if time.monotonic()-self.last_keepalive > 2:
            self.control.keepalive()
            self.last_keepalive = time.monotonic()
        if not self.pending:
            self._read()
        if not self.pending:
            raise socket.timeout()
        return self.pending.popleft()[:size]

    def close(self):
        self.framing.reset()
        self.pending.clear()
        self.stats['rndis_buffered_bytes'] = self.stats['rndis_expected_bytes'] = 0
        if self.control:
            try:
                self.control.halt()
            except Exception:
                pass
        if self.device is not None and self.util is not None:
            for number in reversed(self.claimed):
                try:
                    self.util.release_interface(self.device,number)
                except Exception:
                    pass
            self.claimed.clear()
            try:
                self.util.dispose_resources(self.device)
            except Exception:
                pass

    def __exit__(self,*args):
        self.close()


def diagnostics():
    """Enumeration only: does not claim interfaces or reveal device serials."""
    try:
        device = find_device()
        config = device.get_active_configuration()
        return {'platform':'darwin','VID':'2CA3','PID':'0020',
                'interfaces':[{'number':i.bInterfaceNumber,'alternate':i.bAlternateSetting,
                               'class':i.bInterfaceClass,'subclass':i.bInterfaceSubClass,
                               'protocol':i.bInterfaceProtocol,
                               'endpoints':[{'address':e.bEndpointAddress,'type':e.bmAttributes & 3} for e in i]} for i in config],
                'Note':'USB 열거만 확인됨 · RNDIS / ARP / 영상 수신 검증은 아직 필요합니다.'}
    except OSError as exc:
        return {'platform':'darwin','error':str(usb_error(exc))}
