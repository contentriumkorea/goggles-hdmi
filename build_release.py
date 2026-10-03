"""Build an installer and signed offline update, reusing the private publisher key."""
import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from licensing import CONFIG
from updates import canonical_release, validate_https

ROOT = Path(__file__).resolve().parent


def write_notices(dist):
    notices = dist/'ThirdPartyNotices'
    notices.mkdir(exist_ok=True)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.is_file():
        (notices/'Python-LICENSE.txt').write_bytes(python_license.read_bytes())
    lines = ['Goggles HDMI includes the following third-party software.\n',
             'Qt/PySide6: https://www.qt.io/licensing/ and https://code.qt.io/\n',
             'FFmpeg: https://ffmpeg.org/legal.html and https://ffmpeg.org/download.html\n',
             'The Qt and FFmpeg shared libraries remain separate in _internal.\n']
    for package in ('PySide6','shiboken6','av','numpy','opencv-python-headless','cryptography','cffi','pycparser','pyinstaller'):
        distribution = importlib.metadata.distribution(package)
        lines.append(f'{package} {distribution.version}\n')
        for entry in distribution.files or []:
            if any(word in entry.name.lower() for word in ('license','copying','notice','copyright')):
                source = distribution.locate_file(entry)
                if source.is_file() and source.stat().st_size < 2_000_000:
                    filename = package+'-'+str(entry).replace('/','_').replace('\\','_')
                    (notices/filename).write_bytes(source.read_bytes())
    (dist/'ThirdPartyNotices.txt').write_text(''.join(lines),encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compiler',type=Path,default=Path(os.environ['LOCALAPPDATA'])/'GogglesHDMI-BuildTools/InnoSetup6/ISCC.exe')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--download-url',default='')
    parser.add_argument('--notes',default='Windows 설치, 프로그램 업데이트, 콘텐츠리움 워터마크 및 패스워드 인증.')
    parser.add_argument('--qa',action='store_true',help='Separate install identity; not for distribution')
    args = parser.parse_args()
    if not args.download_url and CONFIG.get('github_repository') and not args.qa:
        args.download_url = ('https://github.com/'+CONFIG['github_repository']+
            '/releases/download/v'+CONFIG['version']+'/Goggles-HDMI-Setup-'+CONFIG['version']+'.exe')
    args.output = args.output or ((ROOT/'qa/installers' if args.qa else ROOT.parent/'releases')/CONFIG['version'])
    if args.download_url: validate_https(args.download_url)
    dist = ROOT/'dist/Goggles HDMI'
    exe = dist/'Goggles HDMI.exe'
    if not exe.is_file(): raise SystemExit('Build GogglesHDMI.spec with PyInstaller first.')
    bundled = json.loads((dist/'_internal/release_config.json').read_text(encoding='utf-8'))
    if bundled != CONFIG: raise SystemExit('Build configuration is stale. Rebuild first.')
    args.output.mkdir(parents=True,exist_ok=True)
    write_notices(dist)
    name = ('Goggles-HDMI-QA-Setup-' if args.qa else 'Goggles-HDMI-Setup-')+CONFIG['version']
    command = [str(args.compiler),'/Qp',f'/DAppVersion={CONFIG["version"]}',
               f'/DReleaseDir={args.output.resolve()}',f'/DDistDir={dist}',f'/DSetupName={name}']
    if args.qa:
        command += ['/DAppIdValue=7C43C395-ECC1-44CA-92D3-D7F64CBBEC9B','/DAppNameValue=Goggles HDMI QA']
    subprocess.run(command+[str(ROOT/'installer/GogglesHDMI.iss')],check=True,cwd=ROOT)
    setup = args.output/(name+'.exe')
    if args.qa:
        # A different installation identity must never be signed as a production update.
        print(json.dumps({'installer':str(setup),'version':CONFIG['version'],'qa':True},ensure_ascii=False))
        return
    key_path = Path(os.environ['LOCALAPPDATA'])/'GogglesHDMI-Publisher/update-signing-key.pem'
    key = serialization.load_pem_private_key(key_path.read_bytes(),password=None)
    if key.public_key().public_bytes_raw().hex() != CONFIG['update_public_key']:
        raise SystemExit('Publisher key does not match the application update verifier.')
    with setup.open('rb') as stream: digest = hashlib.file_digest(stream,'sha256').hexdigest()
    release = {'product':CONFIG['product'],'version':CONFIG['version'],'size':setup.stat().st_size,
               'sha256':digest,'notes':args.notes}
    if args.download_url: release['url'] = args.download_url
    envelope = {'release':release,'signature':base64.b64encode(key.sign(canonical_release(release))).decode('ascii')}
    manifest = json.dumps(envelope,indent=2,ensure_ascii=False).encode('utf-8')
    (args.output/'release.json').write_bytes(manifest)
    bundle = args.output/(('Goggles-HDMI-QA-' if args.qa else 'Goggles-HDMI-')+CONFIG['version']+'.ghupdate')
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr('release.json',manifest)
        archive.write(setup,'setup.exe')
    with bundle.open('rb') as stream: bundle_digest = hashlib.file_digest(stream,'sha256').hexdigest()
    (args.output/'SHA256SUMS.txt').write_text(f'{digest}  {setup.name}\n'+
        bundle_digest+f'  {bundle.name}\n',encoding='utf-8')
    print(json.dumps({'installer':str(setup),'bundle':str(bundle),'version':CONFIG['version'],'qa':args.qa},ensure_ascii=False))


if __name__ == '__main__': main()
