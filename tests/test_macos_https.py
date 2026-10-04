import base64
import hashlib
import json
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

import pytest
certifi=pytest.importorskip('certifi')
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID


@pytest.fixture
def tls_server(tmp_path, monkeypatch):
    import updates
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost test CA')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False)
        .sign(key,hashes.SHA256()))
    ca=tmp_path/'test-ca.pem';ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private=tmp_path/'test-key.pem';private.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    responses={}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/redirect':
                self.send_response(302);self.send_header('Location',url+'/package');self.end_headers();return
            body=responses.get(self.path,b'xar!-test-package')
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
            self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ca,private)
    server.socket=context.wrap_socket(server.socket,server_side=True)
    url=f'https://localhost:{server.server_port}'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(updates.sys,'platform','darwin')
    # Reproduce the frozen interpreter's absent default trust store.
    monkeypatch.setattr(ssl,'_create_default_https_context',lambda:ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    try:yield url,ca,responses
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_mac_feed_and_redirected_installer_use_explicit_ca(tls_server,tmp_path,monkeypatch):
    import updates
    url,ca,responses=tls_server
    monkeypatch.setattr(certifi,'where',lambda:str(ca))
    payload=b'xar!-test-package';key=ed25519.Ed25519PrivateKey.generate()
    release=dict(product=updates.CONFIG['product'],version='9.9.9',platform='darwin',architecture='arm64',
        channel='macos-preview',installer_kind='pkg',installer_name='setup.pkg',size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),url=url+'/redirect')
    responses['/manifest']=json.dumps({'release':release,'signature':base64.b64encode(
        key.sign(updates.canonical_release(release))).decode()}).encode()
    checked=updates.check_online(url+'/manifest',public_key=key.public_key().public_bytes_raw(),
        current_version='1.2.1',platform='darwin')
    staged=updates.stage_online(checked,tmp_path/'stage')
    assert staged.path.read_bytes()==payload
    updates.verify_installer(staged,platform='darwin')


def test_mac_rejects_unknown_ca(tls_server):
    import updates
    with pytest.raises(URLError) as error:updates.open_https(tls_server[0]+'/manifest')
    assert isinstance(error.value.reason,ssl.SSLCertVerificationError)


def test_mac_rejects_wrong_hostname(tls_server,monkeypatch):
    import updates
    url,ca,_=tls_server;monkeypatch.setattr(certifi,'where',lambda:str(ca))
    with pytest.raises(URLError) as error:updates.open_https(url.replace('localhost','127.0.0.1')+'/manifest')
    assert isinstance(error.value.reason,ssl.SSLCertVerificationError)


def test_mac_missing_bundle_fails_closed(tls_server,tmp_path,monkeypatch):
    import updates
    monkeypatch.setattr(certifi,'where',lambda:str(tmp_path/'missing-ca.pem'))
    with pytest.raises(FileNotFoundError):updates.open_https(tls_server[0]+'/manifest')
