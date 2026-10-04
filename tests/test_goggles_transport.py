import threading
import pytest


def test_existing_dji_receiver_decodes_through_datagram_adapter(monkeypatch):
    import av
    import numpy as np
    from fractions import Fraction
    import goggles
    encoder=av.CodecContext.create('libx264','w');encoder.width=128;encoder.height=72
    encoder.pix_fmt='yuv420p';encoder.time_base=Fraction(1,30)
    encoder.options={'preset':'ultrafast','tune':'zerolatency'}
    picture=av.VideoFrame.from_ndarray(np.full((72,128,3),100,np.uint8),format='rgb24');picture.pts=0
    packets=list(encoder.encode(picture));picture.pts=1;packets.extend(encoder.encode(picture))
    data=b''.join(bytes(p) for p in packets)
    sent=[]
    class Adapter:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send(self,data):sent.append(data)
        def recv(self,size):return goggles.make_packet(2,b'\0'*12+data,0,8)
    monkeypatch.setattr(goggles.secrets,'randbelow',lambda maximum:0)
    monkeypatch.setattr(goggles,'datagram_connection',lambda *args:Adapter())
    stop=threading.Event();stats={'sessions':0,'video_bytes':0,'frames':0,'invalid_packets':0}
    receiver=goggles.receive_session(stop,stats,lambda message:None)
    frame=next(receiver);stop.set();receiver.close()
    assert (frame.width,frame.height)==(128,72) and stats['stage']=='decode' and stats['frames']==1
    assert goggles.parse_packet(sent[0],0)[0]==0 and goggles.parse_packet(sent[1],0)[0]==4


def test_mac_transport_factory_keeps_socket_out_of_usb(monkeypatch):
    import goggles
    import macos_usb
    monkeypatch.setattr(goggles.sys,'platform','darwin')
    sentinel=object();monkeypatch.setattr(macos_usb,'USBConnection',lambda **kw:sentinel)
    assert goggles.datagram_connection(threading.Event(),lambda message:None) is sentinel
