"""Operator actions after moving the viewer controls and settings."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app import MainWindow

qapp = QApplication.instance() or QApplication([])


def test_primary_connection_button_also_stops_current_source(monkeypatch):
    w = MainWindow(False)
    calls = []
    monkeypatch.setattr(w, 'start_usb', lambda: calls.append('connect'))
    try:
        w.usb_button.click()
        assert calls == ['connect']
        w.mode = 'pattern'
        w.refresh_actions()
        w.usb_button.click()
        assert w.mode == 'idle' and w.stop_event.is_set()
    finally:
        w.close()


def test_output_button_cancels_reconnect_wait(monkeypatch):
    w = MainWindow(False)
    calls = []
    monkeypatch.setattr(w, 'open_output', lambda: calls.append('open'))
    try:
        w.output_button.click()
        assert calls == ['open']
        w.output_pending = 'disconnected-monitor'
        w.refresh_actions()
        assert w.output_button.isEnabled()
        w.output_button.click()
        assert w.output_pending is None
        assert calls == ['open']
    finally:
        w.close()


def test_review_controls_and_settings_remain_accessible():
    w = MainWindow(False)
    w.resize(1000, 680)
    w.show()
    QTest.qWait(40)
    try:
        assert w.grading.tabs.count() == 3
        assert not w.apply_button.isVisible()
        assert not w.settings_dialog.isVisible()
        w.staged.setCurrentIndex(1)
        w.grading.exposure.setValue(100)
        assert w.look_settings.exposure == 0
        assert w.apply_button.isVisible() and w.apply_button.isEnabled()
        w.apply_button.click()
        assert w.look_settings.exposure == 1
        w.grading.exposure.setValue(50)
        w.staged.setCurrentIndex(0)
        assert w.grading.settings().exposure == 1
        assert not w.apply_button.isVisible()
        w.grading.tabs.setCurrentIndex(2)
        w.grading.stabilize.setChecked(True)
        assert w.grading.strength.number.isEnabled()
        w.grading.stabilize.setChecked(False)
        assert not w.grading.strength.number.isEnabled()
        w.settings_button.click()
        assert w.settings_dialog.isVisible()
        QTest.keyClick(w.settings_dialog, Qt.Key_Escape)
        assert not w.settings_dialog.isVisible()
    finally:
        w.close()


def test_raw_output_shortcut_works_in_review_mode():
    w=MainWindow(False)
    w.show()
    w.activateWindow()
    QTest.qWait(40)
    try:
        w.staged.setChecked(True)
        w.grading.exposure.setValue(100)
        QTest.keyClick(w,Qt.Key_B,Qt.ControlModifier)
        assert w.raw_output and w.raw_button.isChecked()
        assert w.draft_settings.exposure==1 and w.look_settings.exposure==0
        QTest.keyClick(w,Qt.Key_B,Qt.ControlModifier)
        assert not w.raw_output
    finally: w.close()


def test_show_recovers_offscreen_control_window_without_opening_hdmi():
    from PySide6.QtCore import QPoint
    w=MainWindow(False)
    try:
        w.move(-5000,-5000)
        w.show()
        QTest.qWait(80)
        title_point=w.frameGeometry().topLeft()+QPoint(20,20)
        assert any(screen.availableGeometry().contains(title_point) for screen in qapp.screens())
        assert not w.output.isVisible() and not w.output.locked
        assert w.mode=='idle'
    finally: w.close()
