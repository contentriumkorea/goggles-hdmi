import threading
import pytest


def test_mac_transport_factory_keeps_socket_out_of_usb(monkeypatch):
    import goggles
    import macos_usb
    monkeypatch.setattr(goggles.sys,'platform','darwin')
    sentinel=object();monkeypatch.setattr(macos_usb,'USBConnection',lambda **kw:sentinel)
    assert goggles.datagram_connection(threading.Event(),lambda message:None) is sentinel
