import hashlib
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_publisher_signs_platform_bound_manifest_and_offline_pkg(tmp_path):
    from sign_macos_release import sign_release
    from updates import parse_manifest,stage_bundle
    from licensing import CONFIG
    key=Ed25519PrivateKey.generate()
    configuration=dict(CONFIG,version='1.2.0',update_public_key=key.public_key().public_bytes_raw().hex())
    package=tmp_path/'test.pkg';package.write_bytes(b'xar!-candidate')
    output=tmp_path/'signed'
    sign_release(package,key,configuration,output)
    release=parse_manifest((output/'release-macos-arm64.json').read_bytes(),public_key=key.public_key().public_bytes_raw(),
        current_version='1.1.0',platform='darwin')
    assert release['platform']=='darwin' and release['architecture']=='arm64'
    assert '/macos-arm64-v1.2.0/' in release['url'] and release['sha256']==hashlib.sha256(package.read_bytes()).hexdigest()
    staged=stage_bundle(output/'Goggles-HDMI-macOS-arm64-1.2.0.ghupdate',tmp_path/'staging',
        public_key=key.public_key().public_bytes_raw(),current_version='1.1.0',platform='darwin')
    assert staged.path.read_bytes()==package.read_bytes()
