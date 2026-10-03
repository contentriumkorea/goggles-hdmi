"""Verify signed release metadata and stage installers without touching the running app."""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from licensing import CONFIG
from platform_support import cache_directory

MAX_INSTALLER = 1024*1024*1024
MAX_MANIFEST = 64*1024


class VerifiedRelease(dict):
    """Carry the signed bytes' verifier provenance through download and handoff."""
    def __init__(self, release, signature, public_key):
        super().__init__(release)
        self.signature,self.public_key = signature,public_key

    def reverify(self):
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(self.signature,canonical_release(self))
        except InvalidSignature as exc:
            raise ValueError('배포자 서명을 다시 확인할 수 없습니다.') from exc


def canonical_release(release):
    return json.dumps(release, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})', value):
        raise ValueError('올바르지 않은 버전 형식입니다.')
    return tuple(map(int, value.split('.')))


def validate_https(url):
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 for c in url):
        raise ValueError('HTTPS 업데이트 주소를 입력하세요.')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError('사용자 정보가 없는 HTTPS 주소만 사용할 수 있습니다.')
    return url


def validate_target(release, platform=None):
    platform = platform or sys.platform
    if platform == 'darwin':
        expected = {'platform':'darwin','architecture':'arm64','channel':'macos-preview',
                    'installer_kind':'pkg','installer_name':'setup.pkg'}
        if any(release.get(key) != value for key,value in expected.items()):
            raise ValueError('이 Mac의 Apple Silicon 업데이트가 아닙니다.')
    elif platform == 'win32':
        if 'platform' in release and (release.get('platform') != 'win32' or
                release.get('architecture') != 'x86_64' or release.get('installer_kind') != 'exe'):
            raise ValueError('Windows 업데이트가 아닙니다.')
    else:
        raise ValueError('지원하지 않는 업데이트 플랫폼입니다.')
    return 'setup.pkg' if platform == 'darwin' else 'setup.exe'


def parse_manifest(data, *, public_key=None, current_version=None, require_newer=True, platform=None):
    if len(data) > MAX_MANIFEST:
        raise ValueError('업데이트 정보 파일이 너무 큽니다.')
    try:
        envelope = json.loads(data)
        release = envelope['release']
        signature = base64.b64decode(envelope['signature'], validate=True)
        verifier = public_key or bytes.fromhex(CONFIG['update_public_key'])
        key = Ed25519PublicKey.from_public_bytes(verifier)
        key.verify(signature, canonical_release(release))
        if release['product'] != CONFIG['product']:
            raise ValueError('다른 프로그램의 업데이트입니다.')
        validate_target(release,platform)
        newer = version_tuple(release['version']) > version_tuple(current_version or CONFIG['version'])
        if require_newer and not newer:
            raise ValueError('이미 설치된 버전이거나 이전 버전입니다.')
        if type(release['size']) is not int or not 1 <= release['size'] <= MAX_INSTALLER:
            raise ValueError('설치 파일 크기가 올바르지 않습니다.')
        if not isinstance(release['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', release['sha256']):
            raise ValueError('설치 파일 검증 정보가 올바르지 않습니다.')
        if not isinstance(release.get('notes',''), str) or len(release.get('notes','')) > 8000:
            raise ValueError('업데이트 설명이 올바르지 않습니다.')
        if release.get('url'):
            validate_https(release['url'])
        return VerifiedRelease(release,signature,verifier)
    except InvalidSignature as exc:
        raise ValueError('배포자 서명을 확인할 수 없는 업데이트입니다.') from exc
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('업데이트 정보 파일이 손상되었습니다.') from exc


@dataclass(frozen=True)
class StagedUpdate:
    path: Path
    release: dict


def verify_installer(staged, *, platform=None):
    path, release = staged.path, staged.release
    name = validate_target(release,platform)
    if isinstance(release,VerifiedRelease):
        release.reverify()
    elif name == 'setup.pkg':
        raise ValueError('서명 검증된 Mac 업데이트 정보가 필요합니다.')
    if path.stat().st_size != release['size']:
        raise ValueError('설치 파일 크기가 일치하지 않습니다.')
    with path.open('rb') as stream:
        magic = stream.read(4)
        if (name == 'setup.exe' and magic[:2] != b'MZ') or (name == 'setup.pkg' and magic != b'xar!'):
            raise ValueError('설치 파일 형식이 올바르지 않습니다.')
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != release['sha256']:
        raise ValueError('설치 파일이 변경되거나 손상되었습니다.')


def stage_stream(stream, release, destination, progress=None, *, platform=None):
    name = validate_target(release,platform)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='release-', dir=destination))
    partial = folder/'setup.part'
    final = folder/name
    size = 0
    deadline = time.monotonic()+300
    try:
        with partial.open('xb') as target:
            while True:
                if time.monotonic() > deadline:
                    raise ValueError('업데이트 다운로드 시간이 초과되었습니다. 다시 시도하세요.')
                chunk = stream.read(min(1024*1024, release['size']-size+1))
                if not chunk:
                    break
                size += len(chunk)
                if size > release['size']:
                    raise ValueError('설치 파일이 명시된 크기를 초과했습니다.')
                target.write(chunk)
                if progress:
                    progress(size, release['size'])
        verify_installer(StagedUpdate(partial, release),platform=platform)
        partial.replace(final)
        return StagedUpdate(final, release)
    except Exception:
        partial.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        folder.rmdir()
        raise


def stage_bundle(bundle, destination, *, public_key=None, current_version=None, progress=None, platform=None):
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            if names.count('release.json') != 1:
                raise ValueError('업데이트 패키지 구성이 올바르지 않습니다.')
            if archive.getinfo('release.json').file_size > MAX_MANIFEST:
                raise ValueError('업데이트 정보 파일이 너무 큽니다.')
            release = parse_manifest(archive.read('release.json'), public_key=public_key,
                                     current_version=current_version,platform=platform)
            name = validate_target(release,platform)
            if sorted(names) != ['release.json',name]:
                raise ValueError('업데이트 패키지 구성이 올바르지 않습니다.')
            if archive.getinfo(name).file_size != release['size']:
                raise ValueError('패키지의 설치 파일 크기가 일치하지 않습니다.')
            with archive.open(name) as stream:
                return stage_stream(stream, release, destination, progress,platform=platform)
    except zipfile.BadZipFile as exc:
        raise ValueError('업데이트 패키지가 손상되었습니다.') from exc


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_https(url):
    request = Request(validate_https(url), headers={'User-Agent':'GogglesHDMI-Next/'+CONFIG['version']})
    response = build_opener(HTTPSRedirects()).open(request, timeout=15)
    validate_https(response.geturl())
    return response


def check_online(url, *, public_key=None, current_version=None, platform=None):
    with open_https(url) as response:
        data = response.read(MAX_MANIFEST+1)
    release = parse_manifest(data, public_key=public_key, current_version=current_version, require_newer=False,platform=platform)
    if version_tuple(release['version']) <= version_tuple(current_version or CONFIG['version']):
        return None
    if not release.get('url'):
        raise ValueError('새 버전의 다운로드 주소가 없습니다.')
    return release


def stage_online(release, destination, progress=None):
    with open_https(release['url']) as response:
        return stage_stream(response, release, destination, progress)


def update_directory():
    return cache_directory()/'updates'


def launch_installer(staged, *, platform=None):
    """Recheck at handoff; Apple Installer handles OS/user confirmation."""
    platform = platform or sys.platform
    verify_installer(staged,platform=platform)
    if platform == 'darwin':
        subprocess.Popen(['/usr/bin/open','-a','Installer',str(staged.path)],cwd=str(staged.path.parent))
    else:
        current = Path(sys.executable).parent
        target = current if (current/'unins000.exe').is_file() else (
            Path(os.environ['LOCALAPPDATA'])/'Programs'/'Goggles HDMI')
        subprocess.Popen([str(staged.path),'/SP-','/SILENT','/NORESTART','/UPDATEFROMAPP=1',f'/DIR={target}'],cwd=str(staged.path.parent))


def discard_staged(staged):
    if staged is None: return
    folder = staged.path.parent
    if staged.path.name not in ('setup.exe','setup.pkg') or not folder.name.startswith('release-'):
        return
    try:
        staged.path.unlink(missing_ok=True)
        (folder/'setup.exe').unlink(missing_ok=True)
        (folder/'setup.pkg').unlink(missing_ok=True)
        (folder/'setup.part').unlink(missing_ok=True)
        folder.rmdir()
    except OSError:
        pass  # A running installer is retained until a later cleanup.


def prune_staged(directory, older_than=7*86400):
    now = time.time()
    for folder in Path(directory).glob('release-*'):
        try:
            if folder.is_dir() and not folder.is_symlink() and now-folder.stat().st_mtime > older_than:
                discard_staged(StagedUpdate(folder/'setup.exe',{}))
        except OSError:
            pass
