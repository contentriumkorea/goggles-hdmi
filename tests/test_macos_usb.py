import socket
import struct
from types import SimpleNamespace
import pytest
from test_usb_network import aligned_message


class Interface(list):
    def __init__(self,number,kind,endpoints,extra=b'',alternate=0):
        super().__init__([SimpleNamespace(bEndpointAddress=a,bmAttributes=t,wMaxPacketSize=512) for a,t in endpoints])
        self.bInterfaceNumber=number;self.bAlternateSetting=alternate
        self.bInterfaceClass=kind;self.bInterfaceSubClass=1 if kind==0xe0 else 0
        self.bInterfaceProtocol=3 if kind==0xe0 else 0;self.extra_descriptors=extra


def interfaces():
    return [Interface(2,0xe0,[(0x83,3)],bytes([5,0x24,6,2,3])),Interface(3,10,[(0x84,2),(5,2)])]


class Device:
    def __init__(self):
        self.config=interfaces();self.responses=[];self.writes=[];self.reads=[]
    def get_active_configuration(self):return self.config
    def ctrl_transfer(self,typ,request,value,index,data,timeout):
        assert index==2
        if typ==0x21:
            kind,length,rid=struct.unpack_from('<3I',data)
            if kind==2:self.responses.append(struct.pack('<13I',0x80000002,52,rid,0,1,0,1,0,1,65536,0,0,0))
            if kind==4:
                assert struct.unpack_from('<I',data,20)[0]==0
                oid=struct.unpack_from('<I',data,12)[0]
                payload=b'\x02aaaaa' if oid==0x01010102 else struct.pack('<I',1500)
                self.responses.append(struct.pack('<6I',0x80000004,24+len(payload),rid,0,len(payload),16)+payload)
            if kind in (5,8):self.responses.append(struct.pack('<4I',kind|0x80000000,16,rid,0))
            return len(data)
        return self.responses.pop(0) if self.responses else b'\0'
    def write(self,endpoint,data,timeout):self.writes.append(bytes(data));return len(data)
    def read(self,endpoint,size,timeout):
        if self.reads:return self.reads.pop(0)
        raise TimeoutError()


class Util:
    def __init__(self,fail=None):self.claims=[];self.releases=[];self.disposed=False;self.fail=fail
    def claim_interface(self,device,number):
        if number==self.fail:raise OSError('busy')
        self.claims.append(number)
    def release_interface(self,device,number):self.releases.append(number)
    def dispose_resources(self,device):self.disposed=True


def test_descriptor_selection_and_ambiguity():
    from macos_usb import select_interfaces
    layout=select_interfaces(interfaces())
    assert (layout.control.bInterfaceNumber,layout.data.bInterfaceNumber,layout.bulk_in,layout.bulk_out)==(2,3,0x84,5)
    with pytest.raises(OSError):select_interfaces(interfaces()+interfaces())


def test_matching_control_replies_and_success_status():
    from macos_usb import RNDISControl
    d=Device();r=RNDISControl(d,2)
    transfer,mac,mtu=r.initialize()
    assert (transfer,mac,mtu)==(65536,b'\x02aaaaa',1500)
    r.keepalive()
    d.responses.append(struct.pack('<4I',0x80000008,16,999,0))
    with pytest.raises(OSError):r.keepalive()


def test_initialization_failure_releases_partial_claims():
    from macos_usb import USBConnection
    d=Device();util=Util(fail=3)
    with pytest.raises(OSError):
        with USBConnection(device=d,util=util):pass
    assert util.claims==[2] and util.releases==[2] and util.disposed


def test_arp_datagrams_timeout_short_write_and_cleanup():
    from macos_usb import USBConnection
    from usb_network import NetworkPeer,rndis_packet,rndis_frames
    d=Device();util=Util()
    peer=NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb')
    d.reads.append(rndis_packet(peer.arp_request()))
    with USBConnection(device=d,util=util) as connection:
        assert connection.peer.remote_mac==peer.mac
        peer.remote_mac=connection.peer.mac
        d.reads.append(aligned_message(peer.datagram(b'first'))+rndis_packet(peer.datagram(b'second')))
        assert connection.recv(65535)==b'first' and connection.recv(65535)==b'second'
        with pytest.raises(socket.timeout):connection.recv(65535)
        connection.send(b'ack')
        assert peer.receive(rndis_frames(d.writes[-1])[0])==(b'ack',None)
        d.write=lambda *a,**kw:0
        with pytest.raises(OSError):connection.send(b'ack')
    assert util.releases==[3,2] and util.disposed


def test_valid_small_device_transmit_limit_does_not_reduce_host_receive_buffer():
    from macos_usb import RNDISControl
    d=Device();original=d.ctrl_transfer
    def transfer(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[0]==0xa1 and len(result)==52:
            result=bytearray(result);struct.pack_into('<I',result,36,1580)
        return result
    d.ctrl_transfer=transfer
    assert RNDISControl(d,2).initialize()[0]==1580
