import struct
import pytest


def test_rndis_coalesced_messages_and_invalid_offsets():
    from usb_network import rndis_packet, rndis_frames
    a,b=b'a'*63,b'b'*120
    assert rndis_frames(rndis_packet(a)+rndis_packet(b))==[a,b]
    bad=bytearray(rndis_packet(a));struct.pack_into('<I',bad,8,99999)
    with pytest.raises(ValueError):rndis_frames(bad)
    with pytest.raises(ValueError):rndis_frames(rndis_packet(a)[:-1])


def test_ipv4_udp_roundtrip_rejects_wrong_peer_and_corruption():
    from usb_network import NetworkPeer
    a=NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,b'\x02aaaaa')
    b=NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb')
    a.remote_mac=b.mac;b.remote_mac=a.mac
    frame=b.datagram(b'test video')
    assert a.receive(frame)==(b'test video',None)
    bad=bytearray(frame);bad[-1]^=1
    assert a.receive(bad)==(None,None)
    assert b.receive(frame)==(None,None)


def test_arp_discovery_learns_peer_and_answers_only_own_address():
    from usb_network import NetworkPeer
    a=NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,b'\x02aaaaa')
    b=NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb')
    data,reply=b.receive(a.arp_request())
    assert data is None and reply is not None
    assert a.receive(reply)==(None,None)
    assert a.remote_mac==b.mac and b.remote_mac==a.mac


def test_fragmented_ipv4_is_reassembled_out_of_order_and_bounded():
    from usb_network import NetworkPeer, checksum
    a=NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,b'\x02aaaaa')
    b=NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb');b.remote_mac=a.mac;a.remote_mac=b.mac
    original=b.datagram(b'z'*2200)
    payload=original[34:]
    def fragment(data,offset,more):
        h=bytearray(original[14:34]);struct.pack_into('!H',h,2,20+len(data))
        struct.pack_into('!H',h,6,offset//8 | (0x2000 if more else 0))
        h[10:12]=b'\0\0';struct.pack_into('!H',h,10,checksum(h))
        return original[:14]+h+data
    assert a.receive(fragment(payload[1400:],1400,False))==(None,None)
    assert a.receive(fragment(payload[:1400],0,True))==(b'z'*2200,None)


def peers(**kwargs):
    from usb_network import NetworkPeer
    a=NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,b'\x02aaaaa',**kwargs)
    b=NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb')
    a.remote_mac=b.mac;b.remote_mac=a.mac
    return a,b


def ip_fragment(original,data,offset,more,identifier=None):
    from usb_network import checksum
    h=bytearray(original[14:34]);struct.pack_into('!H',h,2,20+len(data))
    struct.pack_into('!H',h,6,offset//8 | (0x2000 if more else 0))
    if identifier is not None:struct.pack_into('!H',h,4,identifier)
    h[10:12]=b'\0\0';struct.pack_into('!H',h,10,checksum(h))
    return original[:14]+h+data


def test_rndis_alignment_terminal_padding_and_malformed_headers():
    from usb_network import rndis_packet,rndis_frames
    p=bytearray(rndis_packet(b'a'*61))
    p.extend(b'\0'*3);struct.pack_into('<I',p,4,len(p))
    assert rndis_frames(p+rndis_packet(b'b'*60)+b'\0')==[b'a'*61,b'b'*60]
    for index,value in ((0,99),(4,43),(8,0),(12,9999),(16,1),(28,1)):
        bad=bytearray(rndis_packet(b'a'*61));struct.pack_into('<I',bad,index,value)
        with pytest.raises(ValueError):rndis_frames(bad)
    with pytest.raises(ValueError):rndis_frames(rndis_packet(b'a'*61)+b'bad')


def test_udp_zero_checksum_padding_options_and_bad_ports():
    from usb_network import checksum
    a,b=peers();frame=bytearray(b.datagram(b'hello'))
    frame[40:42]=b'\0\0'
    assert a.receive(frame+b'padding')==(b'hello',None)
    header=bytearray(frame[14:34]);header[0]=0x46
    struct.pack_into('!H',header,2,len(frame)-14+4)
    header.extend(b'\x01\x01\x01\0');header[10:12]=b'\0\0'
    struct.pack_into('!H',header,10,checksum(header))
    assert a.receive(frame[:14]+header+frame[34:])==(b'hello',None)
    frame[34:36]=b'\0\x01'
    assert a.receive(frame)==(None,None)


def test_arp_rejects_inconsistent_sender_and_foreign_target():
    a,b=peers();a.remote_mac=None
    frame=bytearray(b.arp_request());frame[22]^=1
    assert a.receive(frame)==(None,None) and a.remote_mac is None
    frame=bytearray(b.arp_request());frame[-1]=77
    assert a.receive(frame)==(None,None) and a.remote_mac is None


def test_reassembly_rejects_overlap_expires_and_bounds_resources():
    clock=[0.0]
    a,b=peers(clock=lambda:clock[0],max_assemblies=2,max_fragment_bytes=100,fragment_ttl=1)
    original=b.datagram(b'z'*100);payload=original[34:]
    first=ip_fragment(original,payload[:64],0,True)
    assert a.receive(first)==(None,None)
    assert a.receive(ip_fragment(original,payload[32:],32,False))==(None,None)
    assert not a.fragments
    for identifier in range(8):a.receive(ip_fragment(original,payload[:64],0,True,identifier))
    assert len(a.fragments)<=2 and a.fragment_bytes<=100
    clock[0]=2
    a.receive(b'bad')
    assert not a.fragments and a.fragment_bytes==0
