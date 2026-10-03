"""Real signed packages: reject altered metadata, payloads and downgrades before execution."""
import base64
import hashlib
import json
import zipfile
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def release_fixture(tmp_path, *, version='1.1.0', product='GogglesHDMI-Next', payload=b'MZtest-installer'):
    key = Ed25519PrivateKey.generate()
    release = {'product':product, 'version':version, 'size':len(payload),
               'sha256':hashlib.sha256(payload).hexdigest(), 'notes':'New release'}
    canonical = json.dumps(release, sort_keys=True, separators=(',',':'), ensure_ascii=True).encode()
    envelope = {'release':release, 'signature':base64.b64encode(key.sign(canonical)).decode()}
    bundle = tmp_path/'release.ghupdate'
    with zipfile.ZipFile(bundle,'w') as archive:
        archive.writestr('release.json', json.dumps(envelope))
        archive.writestr('setup.exe',payload)
    return key.public_key().public_bytes_raw(), envelope, bundle


def test_signed_bundle_is_staged_and_rechecked_before_launch(tmp_path):
    from updates import stage_bundle, verify_installer
    key, _, bundle = release_fixture(tmp_path)
    staged = stage_bundle(bundle, tmp_path/'staging', public_key=key, current_version='1.0.0',platform='win32')
    assert staged.release['version'] == '1.1.0'
    assert staged.path.read_bytes() == b'MZtest-installer'
    verify_installer(staged,platform='win32')
    staged.path.write_bytes(b'MZevil-installer')
    with pytest.raises(ValueError):
        verify_installer(staged,platform='win32')


@pytest.mark.parametrize('version,product', [('1.0.0','GogglesHDMI-Next'),('0.9.0','GogglesHDMI-Next'),
                                           ('1.1.0','another-app')])
def test_non_new_or_foreign_releases_rejected(tmp_path, version, product):
    from updates import stage_bundle
    key, _, bundle = release_fixture(tmp_path, version=version, product=product)
    with pytest.raises(ValueError):
        stage_bundle(bundle,tmp_path/'staging', public_key=key,current_version='1.0.0',platform='win32')
    assert not list((tmp_path/'staging').glob('*/setup.exe'))


def test_metadata_forgery_rejected(tmp_path):
    from updates import parse_manifest
    key, envelope, _ = release_fixture(tmp_path)
    envelope['release']['version'] = '9.9.9'
    with pytest.raises(ValueError):
        parse_manifest(json.dumps(envelope).encode(),public_key=key,current_version='1.0.0',platform='win32')


@pytest.mark.parametrize('entry', ['setup.exe', '../escape.exe'])
def test_corrupt_payload_and_extra_paths_never_leave_runnable_file(tmp_path, entry):
    from updates import stage_bundle
    key, envelope, bundle = release_fixture(tmp_path)
    with zipfile.ZipFile(bundle,'w') as archive:
        archive.writestr('release.json',json.dumps(envelope))
        archive.writestr(entry,b'MZcorrupt')
    with pytest.raises(ValueError):
        stage_bundle(bundle,tmp_path/'staging',public_key=key,current_version='1.0.0',platform='win32')
    assert not list((tmp_path/'staging').rglob('*.exe'))
    assert not (tmp_path/'escape.exe').exists()


@pytest.mark.parametrize('url', ['http://example.com/latest.json','file:///C:/setup.exe',
                              'https://user:pass@example.com/a','https://example.com/a#fragment'])
def test_update_urls_require_plain_https_without_credentials(url):
    from updates import validate_https
    with pytest.raises(ValueError):
        validate_https(url)


def test_numeric_version_ordering_and_valid_url():
    from updates import version_tuple,validate_https
    assert version_tuple('1.10.0') > version_tuple('1.9.9')
    assert validate_https('https://example.com/latest.json') == 'https://example.com/latest.json'
    for bad in ('1.0','1.0.0-beta','1.2.3.4','-1.2.3'):
        with pytest.raises(ValueError): version_tuple(bad)


def test_staging_cleanup_only_removes_its_own_payload(tmp_path):
    from updates import stage_bundle, discard_staged, prune_staged
    key, _, bundle = release_fixture(tmp_path)
    destination = tmp_path/'staging'
    staged = stage_bundle(bundle,destination,public_key=key,current_version='1.0.0',platform='win32')
    discard_staged(staged)
    assert not staged.path.exists() and not staged.path.parent.exists()
    stale = destination/'release-interrupted'
    stale.mkdir()
    (stale/'setup.part').write_bytes(b'partial')
    unrelated = destination/'user-documents'
    unrelated.mkdir()
    (unrelated/'setup.exe').write_bytes(b'keep')
    prune_staged(destination,older_than=0)
    assert not stale.exists() and (unrelated/'setup.exe').read_bytes() == b'keep'
