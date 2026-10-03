from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from app import MainWindow
from test_watermark import credentials
from test_updates import release_fixture
import sys
import pytest

qapp = QApplication.instance() or QApplication([])


def wait_for(predicate):
    for _ in range(100):
        QTest.qWait(20)
        if predicate(): return
    raise AssertionError('Program action did not finish')


def test_password_ui_does_not_block_features_and_updates_both_surfaces():
    w = MainWindow(False)
    try:
        p = w.program_panel
        w.license.credentials = credentials()
        assert p.password.echoMode() == QLineEdit.Password
        assert not w.license.unlocked and w.usb_button.isEnabled() and w.pattern_button.isEnabled()
        p.password.setText('wrong')
        p.authenticate.click()
        wait_for(lambda: not p.busy)
        assert not w.license.unlocked and p.password.text() == ''
        p.password.setText('test-only-password')
        p.authenticate.click()
        wait_for(lambda: w.license.unlocked)
        assert p.password.text() == '' and not w.watermark.timer.isActive()
        p.reset.click()
        assert not w.license.unlocked and w.watermark.timer.isActive()
        assert not p.install.isEnabled()
    finally:
        w.close()


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows EXE installer handoff')
def test_update_ui_stages_verifies_then_releases_output_only_when_installer_launches(tmp_path, monkeypatch):
    import sys
    import program_ui
    import updates
    key, _, bundle = release_fixture(tmp_path,version='9.0.0')
    monkeypatch.setitem(updates.CONFIG,'update_public_key',key.hex())
    monkeypatch.setattr(program_ui,'update_directory',lambda:tmp_path/'staging')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    installed = tmp_path/'existing install'
    installed.mkdir()
    (installed/'unins000.exe').touch()
    monkeypatch.setattr(sys,'executable',str(installed/'Goggles HDMI.exe'))
    launches = []
    monkeypatch.setattr(program_ui.subprocess,'Popen',lambda args,**kw: launches.append(args))
    monkeypatch.setattr(qapp,'quit',lambda:None)
    w = MainWindow(False)
    w.show()
    try:
        p = w.program_panel
        w.output.locked = True
        p.load_bundle(str(bundle))
        wait_for(lambda:not p.busy)
        assert p.pending and p.install.isEnabled()
        assert w.output.locked and w.isVisible() and not launches
        staged_path = p.pending.path
        p.install.click()
        wait_for(lambda:bool(launches))
        assert launches[0] == [str(staged_path),'/SP-','/SILENT','/NORESTART','/UPDATEFROMAPP=1',f'/DIR={installed}']
        assert not w.output.locked and w.stop_event.is_set() and not w.isVisible()
        assert staged_path.exists(), 'Running installer must survive application shutdown'
    finally:
        w.output.release()
        w.close()


def test_invalid_update_keeps_live_output_running(tmp_path, monkeypatch):
    import program_ui
    monkeypatch.setattr(program_ui,'update_directory',lambda:tmp_path/'staging')
    bundle = tmp_path/'bad.ghupdate'
    bundle.write_bytes(b'not an update')
    w = MainWindow(False)
    try:
        w.output.locked = True
        w.program_panel.load_bundle(str(bundle))
        wait_for(lambda:not w.program_panel.busy)
        assert w.program_panel.pending is None
        assert not w.program_panel.install.isEnabled()
        assert w.output.locked and not w.stop_event.is_set()
    finally:
        w.output.release()
        w.close()
