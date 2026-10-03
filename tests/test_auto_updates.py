import json
import threading
import sys
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_updates import release_fixture

qapp = QApplication.instance() or QApplication([])


def wait_for(predicate):
    for _ in range(150):
        QTest.qWait(20)
        if predicate(): return
    raise AssertionError('Background update check did not finish')


def test_signed_current_release_means_up_to_date_online_but_no_downgrade(tmp_path,monkeypatch):
    import io
    import updates
    key,envelope,_ = release_fixture(tmp_path,version='1.0.0')
    monkeypatch.setattr(updates,'open_https',lambda url:io.BytesIO(json.dumps(envelope).encode()))
    assert updates.check_online('https://example.com/release.json',public_key=key,current_version='1.0.0',platform='win32') is None


def test_auto_monitor_announces_each_version_once_and_ignores_disabled_result():
    from update_monitor import UpdateMonitor
    gate = threading.Event()
    calls = []
    def fetch(url):
        calls.append(url)
        gate.wait(1)
        return {'version':'9.0.0'}
    monitor=UpdateMonitor(fetch=fetch)
    notices=[]
    monitor.available.connect(notices.append)
    try:
        monitor.configure('https://example.com/release.json',True)
        monitor.check_now()
        wait_for(lambda:bool(calls))
        monitor.configure('https://example.com/release.json',False)
        gate.set()
        wait_for(lambda:not monitor.checking)
        assert notices==[]
        monitor.configure('https://example.com/release.json',True)
        monitor.check_now()
        wait_for(lambda:len(notices)==1)
        monitor.check_now()
        wait_for(lambda:not monitor.checking)
        assert notices==[{'version':'9.0.0'}]
        assert monitor.timer.isActive()
    finally:monitor.configure('',False)


def test_new_release_banner_offers_update_without_interrupting_output():
    from app import MainWindow
    w=MainWindow(False)
    w.show()
    try:
        w.output.locked=True
        w.program_panel.offer_release({'version':'9.0.0','notes':'Improved playback'})
        assert w.update_notice.isVisible()
        assert '9.0.0' in w.update_label.text()
        assert w.update_now.isEnabled()
        assert w.output.locked and not w.stop_event.is_set()
    finally:
        w.output.release();w.close()


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows unattended EXE installer')
def test_one_click_downloads_verifies_and_launches_unattended_upgrade(tmp_path,monkeypatch):
    import io,sys,hashlib
    import program_ui
    from updates import stage_stream
    from app import MainWindow
    payload=b'MZtest-installer'
    release={'version':'9.0.0','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),'url':'https://example.com/setup.exe'}
    def download(release,destination,progress):
        return stage_stream(io.BytesIO(payload),release,destination,progress)
    monkeypatch.setattr(program_ui,'stage_online',download)
    monkeypatch.setattr(program_ui,'update_directory',lambda:tmp_path/'staging')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    launches=[]
    monkeypatch.setattr(program_ui.subprocess,'Popen',lambda args,**kwargs: launches.append(args))
    monkeypatch.setattr(qapp,'quit',lambda:None)
    w=MainWindow(False);w.show()
    try:
        w.output.locked=True
        w.program_panel.offer_release(release)
        w.update_now.click()
        wait_for(lambda:bool(launches))
        assert '/SILENT' in launches[0] and '/UPDATEFROMAPP=1' in launches[0]
        assert not w.output.locked and w.stop_event.is_set() and not w.isVisible()
    finally:
        w.output.release();w.close()


def test_manual_check_network_failure_preserves_known_update(monkeypatch):
    import program_ui
    from app import MainWindow
    def offline(url): raise OSError('offline')
    monkeypatch.setattr(program_ui,'check_online',offline)
    w=MainWindow(False);w.show()
    try:
        release={'version':'9.0.0'}
        w.program_panel.offer_release(release)
        w.program_panel.check_update()
        wait_for(lambda:not w.program_panel.busy)
        assert w.update_notice.isVisible() and w.update_now.isEnabled()
        assert w.program_panel.online_release==release
    finally:w.close()
