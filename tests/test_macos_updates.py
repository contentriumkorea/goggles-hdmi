import base64
import hashlib
import io
import json
import zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def signed(release):
    from updates import canonical_release
    key=Ed25519PrivateKey.generate()
    return json.dumps({'release':release,'signature':base64.b64encode(key.sign(canonical_release(release))).decode()}).encode(),key.public_key().public_bytes_raw()


def mac_release(payload=b'xar!-test-package',**kwargs):
    return dict(product='GogglesHDMI-Next',version='1.2.0',platform='darwin',architecture='arm64',
        channel='macos-preview',installer_kind='pkg',installer_name='setup.pkg',size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),url='https://example.com/mac.pkg',**kwargs)


@pytest.mark.parametrize('field,value',[('platform','win32'),('architecture','x86_64'),('channel','stable'),('installer_kind','exe'),('installer_name','../escape.pkg'),('platform',None)])
def test_mac_rejects_signed_wrong_platform_metadata(field,value):
    from updates import parse_manifest
    release=mac_release();release[field]=value;data,key=signed(release)
    with pytest.raises(ValueError):parse_manifest(data,public_key=key,current_version='1.1.0',platform='darwin')


def test_windows_rejects_signed_mac_and_mac_rejects_legacy():
    from updates import parse_manifest
    release=mac_release();data,key=signed(release)
    with pytest.raises(ValueError):parse_manifest(data,public_key=key,current_version='1.1.0',platform='win32')
    for name in ('platform','architecture','channel','installer_kind','installer_name'):release.pop(name)
    data,key=signed(release)
    with pytest.raises(ValueError):parse_manifest(data,public_key=key,current_version='1.1.0',platform='darwin')


def test_mac_offline_pkg_stages_and_rechecks_hash(tmp_path):
    from updates import stage_bundle,verify_installer
    payload=b'xar!-test-package';release=mac_release(payload);data,key=signed(release)
    bundle=tmp_path/'mac.ghupdate'
    with zipfile.ZipFile(bundle,'w') as archive:
        archive.writestr('release.json',data);archive.writestr('setup.pkg',payload)
    staged=stage_bundle(bundle,tmp_path/'stage',public_key=key,current_version='1.1.0',platform='darwin')
    assert staged.path.name=='setup.pkg' and staged.path.read_bytes()==payload
    verify_installer(staged,platform='darwin')
    with pytest.raises(ValueError):verify_installer(staged,platform='win32')
    staged.path.write_bytes(b'xar!-evil-package')
    with pytest.raises(ValueError):verify_installer(staged,platform='darwin')


def test_mac_handoff_uses_installer_without_sudo(tmp_path,monkeypatch):
    import updates
    payload=b'xar!-test-package';release=mac_release(payload)
    data,key=signed(release)
    release=updates.parse_manifest(data,public_key=key,current_version='1.1.0',platform='darwin')
    path=tmp_path/'setup.pkg';path.write_bytes(payload)
    calls=[];monkeypatch.setattr(updates.subprocess,'Popen',lambda args,**kwargs:calls.append(args))
    updates.launch_installer(updates.StagedUpdate(path,release),platform='darwin')
    assert calls==[['/usr/bin/open','-a','Installer',str(path)]]


def test_handoff_reverifies_signature_if_staged_metadata_changes(tmp_path,monkeypatch):
    import updates
    payload=b'xar!-test-package';release=mac_release(payload);data,key=signed(release)
    release=updates.parse_manifest(data,public_key=key,current_version='1.1.0',platform='darwin')
    path=tmp_path/'setup.pkg';path.write_bytes(b'xar!-evil-package')
    release['size']=path.stat().st_size;release['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):updates.launch_installer(updates.StagedUpdate(path,release),platform='darwin')
