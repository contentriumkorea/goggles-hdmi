import hashlib
from PySide6.QtCore import QSettings
from test_watermark import credentials


class Keychain:
    def __init__(self):self.items={}
    def get(self,reference):return self.items.get(reference)
    def set(self,reference,proof):self.items[reference]=proof
    def delete(self,reference):self.items.pop(reference,None)


def test_mac_auth_reference_rotation_reset_and_missing_item(tmp_path):
    from licensing import LicenseState,password_proof
    settings=QSettings(str(tmp_path/'mac.ini'),QSettings.IniFormat);backend=Keychain()
    state=LicenseState(settings,credentials(),platform='darwin',backend=backend)
    proof=password_proof('test-only-password',credentials());state.activate(proof)
    assert state.unlocked and LicenseState(settings,credentials(),platform='darwin',backend=backend).unlocked
    reference=settings.value('program/authentication')
    assert reference.startswith('keychain-v1:') and proof.hex() not in reference
    changed=dict(credentials(),digest=hashlib.sha256(b'other-proof').hexdigest())
    assert not LicenseState(settings,changed,platform='darwin',backend=backend).unlocked
    state.reset()
    assert not backend.items and not LicenseState(settings,credentials(),platform='darwin',backend=backend).unlocked


def test_denied_keychain_keeps_session_unlock_and_reports_persistence(tmp_path):
    from licensing import LicenseState,password_proof
    settings=QSettings(str(tmp_path/'mac.ini'),QSettings.IniFormat)
    class Denied(Keychain):
        def set(self,*args):raise OSError('locked')
    state=LicenseState(settings,credentials(),platform='darwin',backend=Denied())
    state.activate(password_proof('test-only-password',credentials()))
    assert state.unlocked and state.persistence_error and not settings.value('program/authentication')


def test_macos_output_escape_releases_without_raising_forever(monkeypatch):
    import app
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    monkeypatch.setattr(app.sys,'platform','darwin')
    output=app.OutputWindow();released=[];output.release_requested.connect(lambda:released.append(True))
    try:
        assert output.lock_output()
        assert not output.topmost_guard.isActive()
        QTest.keyClick(output,Qt.Key_Escape)
        assert released==[True]
    finally:output.release();output.close()


def test_platform_paths_do_not_write_inside_application(tmp_path):
    from platform_support import state_directory,cache_directory
    assert state_directory(platform='darwin',home=tmp_path)==tmp_path/'Library/Application Support/GogglesHDMI-Next'
    assert cache_directory(platform='darwin',home=tmp_path)==tmp_path/'Library/Caches/GogglesHDMI-Next'
