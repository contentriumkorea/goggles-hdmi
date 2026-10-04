import pytest
import audit_macos_bundle


def test_dylib_identity_is_not_a_missing_dependency(tmp_path,monkeypatch):
    (tmp_path/'libusb-1.0.dylib').write_bytes(b'native')
    def native(*args):
        if args[0].endswith('file'):return 'Mach-O 64-bit dynamically linked shared library arm64'
        if args[0].endswith('lipo'):return 'arm64'
        if args[0].endswith('codesign'):return ''
        if args[1] == '-l':return 'Load command 0\n cmd LC_ID_DYLIB\n name @rpath/libusb-1.0.0.dylib (offset 24)\nLoad command 1\n cmd LC_BUILD_VERSION\n minos 11.0\n'
        return 'libusb:\n @rpath/libusb-1.0.0.dylib (compatibility version 1.0.0)\n /usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n'
    monkeypatch.setattr(audit_macos_bundle,'run',native)
    report = audit_macos_bundle.audit(tmp_path)
    assert report['native_files'][0]['dependencies'] == ['/usr/lib/libSystem.B.dylib']
    def missing(*args):
        value = native(*args)
        if args[0].endswith('otool') and args[1] == '-L':
            value += ' @rpath/libmissing.dylib (compatibility version 1.0.0)\n'
        return value
    monkeypatch.setattr(audit_macos_bundle,'run',missing)
    with pytest.raises(ValueError,match='Missing bundled dylib'):
        audit_macos_bundle.audit(tmp_path)
