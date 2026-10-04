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


def test_mac_display_identity_uses_qt_nsscreen_geometry_and_real_mode():
    from PySide6.QtCore import QRect
    from platform_support import mac_display_info
    class Screen:
        def geometry(self):return QRect(1440,120,1920,1080)
    # CG bounds need not equal Qt's flipped NSScreen points on HiDPI layouts.
    snapshot=[{'identifier':9,'geometry':(1440,120,1920,1080),'pixels':(3840,2160)}]
    info=mac_display_info(Screen(),snapshot=snapshot)
    assert info['pixels']==(3840,2160) and info['reason']=='ok' and info['identifier']==9
    assert mac_display_info(Screen(),snapshot=[])['reason']=='screen_unmatched'
    assert mac_display_info(Screen(),snapshot=snapshot*2)['reason']=='screen_ambiguous'
    assert mac_display_info(Screen(),snapshot=[dict(snapshot[0],pixels=None)])['reason']=='mode_unavailable'


def test_mac_unexpected_fullscreen_loss_repairs_once_without_activating(monkeypatch):
    import app
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    monkeypatch.setattr(app.sys,'platform','darwin')
    monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationActive))
    output=app.OutputWindow()
    try:
        assert output.lock_output()
        output.present(QApplication.primaryScreen())
        QTest.qWait(20)
        monkeypatch.setattr(output,'activateWindow',lambda:(_ for _ in ()).throw(AssertionError('must not steal focus')))
        output.showNormal();QTest.qWait(400)
        assert output.isFullScreen() and output.fullscreen_repairs==1
        output.showNormal();QTest.qWait(400)
        assert not output.isFullScreen() and output.fullscreen_repairs==1
    finally:output.release();output.close()


def test_mac_queued_fullscreen_repair_is_cancelled_by_release_or_inactive_app(monkeypatch):
    import app
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    monkeypatch.setattr(app.sys,'platform','darwin')
    monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationActive))
    output=app.OutputWindow()
    try:
        output.lock_output();output.present(QApplication.primaryScreen());QTest.qWait(20)
        output.showNormal();output.release();QTest.qWait(400)
        assert not output.isVisible() and not output.locked and output.fullscreen_repairs==0
        output.lock_output();output.present(QApplication.primaryScreen());QTest.qWait(20)
        monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationInactive))
        output.showNormal();QTest.qWait(400)
        assert not output.isFullScreen() and output.fullscreen_repairs==0
    finally:output.release();output.close()


def test_mac_removal_of_same_named_other_screen_does_not_release_output(monkeypatch):
    import app
    window=app.MainWindow(False)
    monkeypatch.setattr(app.sys,'platform','darwin')
    class Screen:
        def name(self):return 'same localized display name'
    target,other=Screen(),Screen()
    try:
        window.output.output_screen=target;window.output_target=target.name();window.output.locked=True
        monkeypatch.setattr(window,'refresh_screens',lambda:None)
        window.screen_removed(other)
        assert window.output.locked and window.output_pending is None
    finally:window.release_output();window.close()


def test_transient_mac_mode_failure_retries_without_closing_output(monkeypatch):
    import app,platform_support
    from PySide6.QtWidgets import QApplication
    window=app.MainWindow(False)
    monkeypatch.setattr(app.sys,'platform','darwin')
    calls=[]
    def info(screen):
        calls.append(screen)
        return {'pixels':None,'reason':'mode_unavailable'} if len(calls)==1 else {'pixels':(3840,2160),'reason':'ok'}
    monkeypatch.setattr(platform_support,'mac_display_info',info)
    try:
        window.output.output_screen=QApplication.primaryScreen();window.output.locked=True
        window.refresh_screens()
        assert window.display_refresh_timer.isActive() and window.output.locked
        window.display_refresh_timer.stop();window.refresh_screens()
        assert window.support_output['width']==3840 and window.support_output['mode_reason']=='ok'
        assert window.output.locked
        monkeypatch.setattr(platform_support,'mac_display_info',lambda *_:(_ for _ in ()).throw(AssertionError('copy must not query display')))
        monkeypatch.setattr(platform_support,'mac_window_state',lambda *_:(_ for _ in ()).throw(AssertionError('copy must not query native window')))
        assert '3840' in window.problem_report()
    finally:window.release_output();window.close()


def test_copy_refreshes_native_window_flags_without_display_or_usb_query(monkeypatch):
    import app,platform_support,json
    window=app.MainWindow(False)
    monkeypatch.setattr(app.sys,'platform','darwin')
    try:
        window.output.winId()
        monkeypatch.setattr(platform_support,'mac_window_state',lambda *_:{'native_fullscreen':False,'native_visible':False})
        window.observe_output('window_state')
        before=len(window.output_events)
        monkeypatch.setattr(platform_support,'mac_window_state',lambda *_:{'native_fullscreen':True,'native_visible':True})
        monkeypatch.setattr(platform_support,'mac_display_info',lambda *_:(_ for _ in ()).throw(AssertionError('no mode probe')))
        monkeypatch.setattr(app,'device_diagnostics',lambda *_:(_ for _ in ()).throw(AssertionError('no device probe')))
        report=json.loads(window.problem_report().split('\n',1)[1])
        assert report['output']['native_fullscreen'] is True and report['output']['native_visible'] is True
        assert len(window.output_events)==before
        monkeypatch.setattr(platform_support,'mac_window_state',lambda *_:{})
        report=json.loads(window.problem_report().split('\n',1)[1])
        assert 'native_fullscreen' not in report['output'] and 'native_visible' not in report['output']
    finally:window.release_output();window.close()


def test_mac_lost_fullscreen_summary_does_not_claim_output_is_running(monkeypatch):
    import app
    window=app.MainWindow(False)
    monkeypatch.setattr(app.sys,'platform','darwin')
    try:
        window.output.locked=True
        window.refresh_actions();window.last_status_update=0;window.tick()
        assert window.output_button.text()=='출력 복원'
        assert '출력 복원 필요' in window.pipeline_status.text()
        assert 'HDMI 출력 복원 필요' in window.summary_status.text()
    finally:window.release_output();window.close()
