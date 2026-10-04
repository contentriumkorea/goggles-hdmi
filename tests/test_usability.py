import threading
import time
from dataclasses import asdict, replace
import json
import numpy as np
import av
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSettings
from PySide6.QtTest import QTest
from app import MainWindow
from effects import Settings, Processor, Stabilizer
from grading_ui import GradingPanel, validate_preset

_app = QApplication.instance() or QApplication([])

def wait_until(predicate):
    for _ in range(150):
        QTest.qWait(10)
        if predicate():
            return
    raise AssertionError('Timed out')

def running_window(monkeypatch, fail=False):
    w = MainWindow(False)
    w.mode = 'stream'
    w.grading.exposure.setValue(100)
    def process(self, pixels, settings):
        if fail:
            raise RuntimeError('synthetic effect failure')
        return np.full_like(pixels, 200)
    monkeypatch.setattr(Processor, 'process', process)
    def frames():
        while not w.stop_event.is_set():
            yield av.VideoFrame.from_ndarray(np.full((180, 320, 3), 100, np.uint8), format='rgb24')
            w.stop_event.wait(.025)
    w.worker = threading.Thread(target=w.receive_frames, args=(frames(), w.stop_event), daemon=True)
    w.worker.start()
    return w

def finish(w):
    w.stop()
    if w.worker:
        w.worker.join(2)
    w.close()

def test_effect_failure_keeps_receiving_original(monkeypatch):
    w = running_window(monkeypatch, fail=True)
    try:
        wait_until(lambda: w.processing_stats['processed'] >= 5)
        assert not w.stop_event.is_set()
        assert w.effect_fault
        assert w.output.frame.pixelColor(100, 100).red() == 100
        assert w.worker.is_alive()
    finally:
        finish(w)

def test_preview_comparison_and_zebra_do_not_change_output(monkeypatch):
    w = running_window(monkeypatch)
    try:
        w.compare.setChecked(True)
        w.zebra_control.setChecked(True)
        wait_until(lambda: not w.preview.frame.isNull() and w.preview.frame.pixelColor(100, 100).red() == 100)
        assert w.output.frame.pixelColor(100, 100).red() == 200
        assert not w.output.zebra
        w.compare.setChecked(False)
        wait_until(lambda: w.preview.frame.pixelColor(100, 100).red() == 200)
    finally:
        finish(w)

def test_removed_output_waits_without_choosing_laptop(monkeypatch):
    import app
    monkeypatch.setattr(app.sys,'platform','win32')  # Legacy Windows name-based reconnect contract.
    w = MainWindow(False)
    class Screen:
        def name(self): return 'external-test'
    w.output_target = 'external-test'
    w.output.locked = True
    monkeypatch.setattr(w.output, 'release', lambda: setattr(w.output, 'locked', False))
    monkeypatch.setattr(w, 'open_output', lambda: (_ for _ in ()).throw(AssertionError('must not move output')))
    w.screen_removed(Screen())
    assert not w.output.locked
    assert w.output_pending == 'external-test'
    w.release_output()
    assert w.output_pending is None
    finish(w)

def test_numbers_reset_and_curve_undo():
    p = GradingPanel()
    p.show()
    p.exposure.number.setValue(1.25)
    assert p.settings().exposure == 1.25
    QTest.mouseDClick(p.exposure.number.lineEdit(), Qt.LeftButton)
    assert p.exposure.value() == 0
    p.editor.remember()
    p.editor.curves[0] = [(0., 0.), (.5, .7), (1., 1.)]
    p.editor.undo()
    assert tuple(p.editor.curves[0]) == Settings().curves[0]
    p.close()

def test_persistence_roundtrip_and_old_presets(tmp_path):
    w = MainWindow(False)
    w.settings = QSettings(str(tmp_path/'test.ini'), QSettings.IniFormat)
    settings = Settings(exposure=.75, temperature=20, max_crop=.045, adaptive_crop=True)
    w.grading.apply(settings)
    w.save_look()
    restored = validate_preset(json.loads(w.settings.value('lastLook')))
    assert restored == settings
    old = asdict(settings)
    old.pop('max_crop'); old.pop('adaptive_crop')
    assert validate_preset({'version': 1, 'settings': old}).max_crop == .1
    finish(w)

def test_adaptive_crop_bounded_and_slowly_relaxes(monkeypatch):
    st = Stabilizer()
    ticks = iter(np.arange(10, 20, 1/30))
    monkeypatch.setattr('effects.time.monotonic', lambda: next(ticks))
    monkeypatch.setattr(st, 'estimate_motion', lambda a,b: np.array([8., 0., .01]))
    rgb = np.full((180,320,3),100,np.uint8)
    crops=[]
    for _ in range(40):
        out = st.process(rgb, .8, .04, True)
        crops.append(st.current_crop)
        assert out.shape == rgb.shape
    assert 0 < max(crops) <= .04
    assert max(abs(np.diff(crops))) < .005
    monkeypatch.setattr(st, 'estimate_motion', lambda a,b: np.zeros(3))
    for _ in range(150): st.process(rgb, .8, .04, True)
    assert st.current_crop < crops[-1]

def test_rate_display_uses_recent_frames():
    w = MainWindow(False)
    w.update_health(10.)
    w.frames = 60
    w.processing_stats['processed'] = 60
    w.update_health(12.)
    assert '수신 30.0 fps' in w.rate_status.text()
    assert '처리 30.0 fps' in w.rate_status.text()
    finish(w)
