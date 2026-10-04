"""Native build/package only. Publisher's Ed25519 key never enters CI."""
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from build_release import write_notices
from audit_macos_bundle import audit

ROOT = Path(__file__).resolve().parent


def run(*args):
    subprocess.run(args,check=True,cwd=ROOT)


def main():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Build on native macOS arm64')
    configuration = json.loads((ROOT/'release_config_macos.json').read_text())
    work = ROOT/'build/macos';work.mkdir(parents=True,exist_ok=True)
    prefix = subprocess.check_output(['brew','--prefix','libusb'],text=True).strip()
    shutil.copy2(Path(prefix)/'lib/libusb-1.0.dylib',work/'libusb-1.0.dylib')
    icons = work/'GogglesHDMI.iconset';icons.mkdir(exist_ok=True)
    for points in (16,32,128,256,512):
        for scale in (1,2):
            size = points*scale
            name = f'icon_{points}x{points}'+('@2x' if scale == 2 else '')+'.png'
            run('/usr/bin/sips','-z',str(size),str(size),str(ROOT/'assets/fpv-drone.png'),'--out',str(icons/name))
    run('/usr/bin/iconutil','-c','icns',str(icons),'-o',str(work/'GogglesHDMI.icns'))
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','GogglesHDMI-macos.spec')
    bundle = ROOT/'dist/Goggles HDMI.app'
    # Notices modify bundle resources, so re-sign only after collecting them.
    write_notices(bundle/'Contents/Resources')
    notices = bundle/'Contents/Resources/ThirdPartyNotices'
    for package in ('pyusb',):
        distribution = importlib.metadata.distribution(package)
        for entry in distribution.files or []:
            if 'license' in entry.name.lower():
                source = distribution.locate_file(entry)
                if source.is_file():shutil.copy2(source,notices/(package+'-'+entry.name))
    shutil.copy2(ROOT/'third_party/libusb-COPYING',notices/'libusb-COPYING')
    run('/usr/bin/codesign','--force','--deep','--sign','-',str(bundle))
    release = ROOT/'macos-release';release.mkdir(exist_ok=True)
    (release/'bundle-audit.json').write_text(json.dumps(audit(bundle),indent=2),encoding='utf-8')
    smoke = release/'smoke.json'
    smoke_environment = dict(os.environ,QT_QPA_PLATFORM='cocoa')
    subprocess.run([str(bundle/'Contents/MacOS/Goggles HDMI'),'--mac-smoke',str(smoke)],
        check=True,cwd=ROOT,env=smoke_environment,timeout=90)
    if not json.loads(smoke.read_text()).get('ok'):
        raise SystemExit('Packaged smoke failed')
    # Non-relocatable package targets /Applications only. macOS handles permission.
    component = work/'component.plist'
    payload = work/'payload';payload.mkdir(exist_ok=True)
    run('/usr/bin/ditto',str(bundle),str(payload/bundle.name))
    run('/usr/bin/pkgbuild','--analyze','--root',str(payload),str(component))
    import plistlib
    components = plistlib.loads(component.read_bytes())
    for entry in components:
        entry['BundleIsRelocatable'] = False
        entry['BundleOverwriteAction'] = 'upgrade'
    component.write_bytes(plistlib.dumps(components))
    package = release/f'Goggles-HDMI-macOS-arm64-{configuration["version"]}-preview.pkg'
    # Use isolated payload so PyInstaller's sibling COLLECT directory isn't installed.
    run('/usr/bin/pkgbuild','--root',str(payload),'--component-plist',str(component),
        '--identifier','com.contentrium.GogglesHDMI','--version',configuration['version'],
        '--install-location','/Applications',str(package))
    payload_files = subprocess.check_output(['/usr/sbin/pkgutil','--payload-files',str(package)],text=True)
    entries = [entry.removeprefix('./') for entry in payload_files.splitlines() if entry not in ('.','./')]
    if not entries or any(entry != bundle.name and not entry.startswith(bundle.name+'/') for entry in entries):
        raise SystemExit('Installer payload contains files outside the application bundle')
    info = {'version':configuration['version'],'architecture':'arm64','macos':platform.mac_ver()[0],
            'hardware_verified':False,'apple_signing':'ad-hoc app; unsigned pkg; not notarized',
            'dependencies':{name:importlib.metadata.version(name) for name in ('PySide6','av','numpy','opencv-python-headless','cryptography','pyusb','pyinstaller')}}
    (release/'build-info.json').write_text(json.dumps(info,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
