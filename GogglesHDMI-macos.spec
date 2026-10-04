# Native Apple Silicon build; retain separate Windows spec.
import json
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

release = json.loads(Path('release_config_macos.json').read_text())
libusb = str(Path('build/macos/libusb-1.0.dylib').resolve())
a = Analysis(['app.py'], pathex=[], binaries=[(libusb,'.')],
    datas=[('assets/fpv-drone.ico','assets'),('assets/contentrium-white.png','assets'),
           ('build/macos/release_config_macos.json','.'),('release_config.json','.')]+collect_data_files('certifi'),
    hiddenimports=collect_submodules('usb'), hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='Goggles HDMI',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False,argv_emulation=False,target_arch='arm64',
    codesign_identity=None,entitlements_file=None)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='Goggles HDMI')
app = BUNDLE(coll,name='Goggles HDMI.app',icon='build/macos/GogglesHDMI.icns',
    bundle_identifier='com.contentrium.GogglesHDMI',version=release['version'],
    info_plist={'CFBundleShortVersionString':release['version'],
                'LSMinimumSystemVersion':'15.6',
                'NSHighResolutionCapable':True,
                'NSHumanReadableCopyright':'Contentrium'})
