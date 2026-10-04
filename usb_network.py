"""Bounded RNDIS/Ethernet/ARP/IPv4/UDP codecs for the direct DJI USB link.

No routing, DHCP, aircraft controls or system network configuration.
"""
import socket
import struct
import time


def checksum(data):
    data = bytes(data)
    if len(data) & 1:
        data += b'\0'
    total = sum(struct.unpack('!%dH' % (len(data)//2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def rndis_packet(frame):
    return struct.pack('<11I', 1, 44+len(frame), 36, len(frame), 0, 0, 0, 0, 0, 0, 0)+frame


def rndis_frames(data):
    """A USB bulk transfer contains complete messages; reject truncated transfers."""
    data = bytes(data)
    frames = []
    while data:
        if not any(data):  # RNDIS terminal alignment/USB short-packet padding.
            break
        if len(data) < 44:
            raise ValueError('Truncated RNDIS packet')
        kind, length, offset, size, oob, oob_size, oob_count, info, info_size, vc, reserved = struct.unpack_from('<11I', data)
        start = 8+offset
        if (kind != 1 or not 44 <= length <= len(data) or start < 44 or offset % 4 or
                size < 14 or start+size > length or any((oob,oob_size,oob_count,info,info_size,vc,reserved))):
            raise ValueError('Invalid RNDIS packet bounds/metadata')
        frames.append(data[start:start+size])
        data = data[length:]
    return frames


class RNDISFramingError(ValueError):
    def __init__(self,reason,*,buffered_bytes=0,expected_bytes=0):
        self.reason = reason
        self.buffered_bytes,self.expected_bytes = buffered_bytes,expected_bytes
        self.context = {}
        super().__init__(reason)


class RNDISStream:
    """Reassemble partial PyUSB reads without guessing a new packet boundary."""
    def __init__(self,*,max_message=1024*1024,partial_ttl=1,clock=time.monotonic):
        self.max_message,self.partial_ttl,self.clock = max_message,partial_ttl,clock
        self.buffer = bytearray()
        self.partial_since = None
        self.expected_bytes = 0
        self.padding_bytes = 0
        self.previous_prefix = None

    @property
    def buffered_bytes(self):
        return len(self.buffer)

    def reset(self):
        self.buffer.clear();self.partial_since = None;self.expected_bytes = 0;self.previous_prefix = None

    def _fail(self,reason):
        error = RNDISFramingError(reason,buffered_bytes=len(self.buffer),expected_bytes=self.expected_bytes)
        # The buffer still starts at the expected header, never at video payload.
        # Capture only bounded numeric framing fields; no raw bytes leave here.
        if reason in ('message_type','length_limit','data_bounds','metadata'):
            if len(self.buffer)>=4:error.context['header_type']=struct.unpack_from('<I',self.buffer)[0]
            if len(self.buffer)>=8:error.context['header_length']=struct.unpack_from('<I',self.buffer,4)[0]
            if self.previous_prefix is not None:
                error.context.update(pending_prefix_bytes=self.previous_prefix[0],
                    pending_prefix_value=self.previous_prefix[1])
        self.reset()
        raise error

    def feed(self,data):
        self.previous_prefix = (len(self.buffer),int.from_bytes(self.buffer,'little')) if (
            self.expected_bytes==0 and 0<len(self.buffer)<=3) else None
        now = self.clock()
        if self.partial_since is not None and now-self.partial_since > self.partial_ttl:
            self._fail('partial_timeout')
        if len(data) > self.max_message or len(self.buffer)+len(data) > 2*self.max_message:
            self._fail('buffer_limit')
        self.buffer.extend(data)
        ready = []
        while self.buffer:
            # Only between messages: never trim zeros from a partial body.
            padding = len(self.buffer)-len(self.buffer.lstrip(b'\0'))
            if padding:
                del self.buffer[:padding]
                self.padding_bytes += padding
            if not self.buffer:
                break
            if len(self.buffer) >= 4 and struct.unpack_from('<I',self.buffer)[0] != 1:
                self._fail('message_type')
            if len(self.buffer) < 8:
                break
            length = struct.unpack_from('<I',self.buffer,4)[0]
            self.expected_bytes = length
            if not 44 <= length <= self.max_message:
                self._fail('length_limit')
            if len(self.buffer) < 44:
                break
            _,_,offset,size,oob,oob_size,oob_count,info,info_size,vc,reserved = struct.unpack_from('<11I',self.buffer)
            start = 8+offset
            if start < 44 or offset % 4 or size < 14 or start+size > length:
                self._fail('data_bounds')
            if any((oob,oob_size,oob_count,info,info_size,vc,reserved)):
                self._fail('metadata')
            if len(self.buffer) < length:
                break
            ready.append(bytes(self.buffer[start:start+size]))
            del self.buffer[:length]
            self.partial_since = None;self.expected_bytes = 0;self.previous_prefix = None
        if self.buffer and self.partial_since is None:
            self.partial_since = now
        elif not self.buffer:
            self.partial_since = None;self.expected_bytes = 0
        return ready


class NetworkPeer:
    def __init__(self, local_ip, remote_ip, local_port, remote_port, mac, *,
                 clock=time.monotonic, max_assemblies=32, max_fragment_bytes=1024*1024, fragment_ttl=1):
        self.local_ip, self.remote_ip = socket.inet_aton(local_ip), socket.inet_aton(remote_ip)
        self.local_port, self.remote_port = local_port, remote_port
        if len(mac) != 6 or mac[0] & 1 or not any(mac):
            raise ValueError('Invalid adapter MAC')
        self.mac, self.remote_mac = bytes(mac), None
        self.clock, self.fragment_ttl = clock, fragment_ttl
        self.max_assemblies, self.max_fragment_bytes = max_assemblies, max_fragment_bytes
        self.fragments = {}
        self.identification = 0

    @property
    def fragment_bytes(self):
        return sum(sum(map(len, entry['parts'].values())) for entry in self.fragments.values())

    def arp_request(self):
        return (b'\xff'*6+self.mac+b'\x08\x06'+struct.pack('!HHBBH',1,0x800,6,4,1)+
                self.mac+self.local_ip+b'\0'*6+self.remote_ip)

    def datagram(self, payload):
        if self.remote_mac is None:
            raise OSError('USB ARP peer not discovered')
        if len(payload) > 65507:
            raise ValueError('UDP payload too large')
        size = 8+len(payload)
        pseudo = self.local_ip+self.remote_ip+struct.pack('!BBH',0,17,size)
        udp = struct.pack('!4H',self.local_port,self.remote_port,size,0)+payload
        udp = udp[:6]+struct.pack('!H',checksum(pseudo+udp) or 65535)+udp[8:]
        self.identification = (self.identification+1) & 65535
        header = bytearray(struct.pack('!BBHHHBBH4s4s',0x45,0,20+size,self.identification,0,64,17,0,self.local_ip,self.remote_ip))
        struct.pack_into('!H',header,10,checksum(header))
        return self.remote_mac+self.mac+b'\x08\0'+header+udp

    def receive(self, frame):
        now = self.clock()
        for key, entry in list(self.fragments.items()):
            if now-entry['created'] > self.fragment_ttl:
                del self.fragments[key]
        frame = bytes(frame)
        if len(frame) < 14 or frame[:6] not in (self.mac,b'\xff'*6):
            return None,None
        sender = frame[6:12]
        if frame[12:14] == b'\x08\x06':
            return self._arp(frame,sender)
        if frame[12:14] != b'\x08\0' or self.remote_mac is None or sender != self.remote_mac:
            return None,None
        packet = frame[14:]
        if len(packet) < 20 or packet[0] >> 4 != 4:
            return None,None
        ihl = (packet[0] & 15)*4
        total = int.from_bytes(packet[2:4],'big')
        if (ihl < 20 or not ihl <= total <= len(packet) or checksum(packet[:ihl]) or
                packet[12:16] != self.remote_ip or packet[16:20] != self.local_ip or packet[9] != 17):
            return None,None
        flags = int.from_bytes(packet[6:8],'big')
        if flags & 0x8000 or (flags & 0x4000 and flags & 0x3fff):
            return None,None
        payload = packet[ihl:total]
        if flags & 0x3fff:
            key = (packet[12:20],packet[9],packet[4:6])
            payload = self._fragment(key,payload,(flags & 8191)*8,bool(flags & 8192),now)
            if payload is None:
                return None,None
        if len(payload) < 8:
            return None,None
        source,destination,size,check = struct.unpack_from('!4H',payload)
        if source != self.remote_port or destination != self.local_port or size != len(payload):
            return None,None
        pseudo = self.remote_ip+self.local_ip+struct.pack('!BBH',0,17,size)
        if check and checksum(pseudo+payload):
            return None,None
        return payload[8:],None

    def _arp(self, frame, sender):
        if len(frame) < 42 or frame[14:20] != struct.pack('!HHBB',1,0x800,6,4):
            return None,None
        operation = int.from_bytes(frame[20:22],'big')
        if (operation not in (1,2) or frame[22:28] != sender or sender[0] & 1 or not any(sender) or
                frame[28:32] != self.remote_ip or frame[38:42] != self.local_ip or
                (operation == 2 and frame[32:38] != self.mac)):
            return None,None
        if self.remote_mac is not None and sender != self.remote_mac:
            return None,None
        self.remote_mac = sender
        if operation == 1:
            return None,(sender+self.mac+b'\x08\x06'+struct.pack('!HHBBH',1,0x800,6,4,2)+
                         self.mac+self.local_ip+sender+self.remote_ip)
        return None,None

    def _fragment(self, key, data, offset, more, now):
        end = offset+len(data)
        if not data or end > 65515 or (more and len(data) % 8):
            self.fragments.pop(key,None)
            return None
        if key not in self.fragments:
            if len(self.fragments) >= self.max_assemblies:
                del self.fragments[next(iter(self.fragments))]
            self.fragments[key] = {'created':now,'parts':{},'end':None}
        entry = self.fragments[key]
        if (any(offset < start+len(part) and end > start for start,part in entry['parts'].items()) or
                (entry['end'] is not None and (end > entry['end'] or (not more and end != entry['end']))) or
                (not more and any(start+len(part) > end for start,part in entry['parts'].items()))):
            del self.fragments[key]
            return None
        entry['parts'][offset] = data
        if not more:
            entry['end'] = end
        if self.fragment_bytes > self.max_fragment_bytes:
            del self.fragments[key]
            return None
        cursor, parts = 0,[]
        for start,part in sorted(entry['parts'].items()):
            if start != cursor:
                return None
            parts.append(part)
            cursor += len(part)
        if cursor != entry['end']:
            return None
        del self.fragments[key]
        return b''.join(parts)
