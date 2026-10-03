# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import PySide6
import json
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

release = json.loads(Path('release_config.json').read_text(encoding='utf-8'))
version = release['version']
numbers = tuple(map(int,version.split('.'))) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=numbers,prodvers=numbers,mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),
    kids=[StringFileInfo([StringTable('040904B0',[
        StringStruct('CompanyName','Contentrium'),StringStruct('FileDescription','Goggles HDMI'),
        StringStruct('FileVersion',version),StringStruct('ProductVersion',version),
        StringStruct('ProductName','Goggles HDMI'),StringStruct('OriginalFilename','Goggles HDMI.exe')])]),
        VarFileInfo([VarStruct('Translation',[1033,1200])])])


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/fpv-drone.ico', 'assets'), ('assets/contentrium-white.png','assets'),
           ('release_config.json','.'), ('setup_goggles_network.ps1','.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# Qt uses Windows' unversioned ICU API. Do not bundle a different ICU
# accidentally discovered in another tool's PATH (e.g. Poppler's ICU 78).
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
qt_dir = Path(PySide6.__file__).parent
for name in ['VCRUNTIME140.dll', 'VCRUNTIME140_1.dll',
             'MSVCP140.dll', 'MSVCP140_1.dll', 'MSVCP140_2.dll']:
    a.binaries = [entry for entry in a.binaries if entry[0].lower() != name.lower()]
    a.binaries.append((name, str(qt_dir / name), 'BINARY'))
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Goggles HDMI',
    version=version_info,
    icon='assets/fpv-line.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Goggles HDMI',
)

