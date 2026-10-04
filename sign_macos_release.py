"""Sign a downloaded, independently verified native Mac pkg on the publisher PC.

The private key remains outside the source, CI and public release assets.
"""
import argparse
import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from updates import canonical_release


def sign_release(package, key, configuration, output):
    package,output = Path(package),Path(output)
    if key.public_key().public_bytes_raw().hex() != configuration['update_public_key']:
        raise ValueError('Publisher key does not match the application verifier')
    with package.open('rb') as stream:
        if stream.read(4) != b'xar!':
            raise ValueError('Not a macOS pkg')
        stream.seek(0)
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    version = configuration['version']
    name = f'Goggles-HDMI-macOS-arm64-{version}.pkg'
    release = {'product':configuration['product'],'version':version,'platform':'darwin','architecture':'arm64',
        'channel':'macos-preview','installer_kind':'pkg','installer_name':'setup.pkg',
        'size':package.stat().st_size,'sha256':digest,
        'url':f'https://github.com/{configuration["github_repository"]}/releases/download/macos-arm64-v{version}/{name}',
        'notes':'Mac HTTPS 인증서 저장소 포함 · USB 분할 수신 수정 포함 · 실제 Mac 끊김 재확인 필요 · Apple 설치 확인 필요'}
    envelope = {'release':release,'signature':base64.b64encode(key.sign(canonical_release(release))).decode('ascii')}
    manifest = json.dumps(envelope,indent=2,ensure_ascii=False).encode('utf-8')
    output.mkdir(parents=True,exist_ok=True)
    (output/'release-macos-arm64.json').write_bytes(manifest)
    bundle = output/f'Goggles-HDMI-macOS-arm64-{version}.ghupdate'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('release.json',manifest)
        archive.write(package,'setup.pkg')
    with bundle.open('rb') as stream:
        bundle_digest = hashlib.file_digest(stream,'sha256').hexdigest()
    (output/'SHA256SUMS-macos-arm64.txt').write_text(
        f'{digest}  {name}\n{bundle_digest}  {bundle.name}\n',encoding='utf-8')
    return release


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--key',type=Path,default=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'GogglesHDMI-Publisher/update-signing-key.pem')
    args = parser.parse_args()
    configuration = json.loads((Path(__file__).parent/'release_config_macos.json').read_text(encoding='utf-8'))
    key = serialization.load_pem_private_key(args.key.read_bytes(),password=None)
    release = sign_release(args.package,key,configuration,args.output)
    print(json.dumps({'version':release['version'],'size':release['size'],'sha256':release['sha256']}))


if __name__ == '__main__':
    main()
