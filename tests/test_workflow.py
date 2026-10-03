"""Regression checks for preview transactions, history, and timing diagnostics."""
import threading
import time
import json
import av
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from effects import Settings
from app import MainWindow

qapp = QApplication.instance() or QApplication([])

def wait_for(test):
    for _ in range(150):
        QTest.qWait(10)
        if test(): return
    raise AssertionError('Timed out')

def stream(w):
    w.mode = 'stream'
    def frames():
        while not w.stop_event.is_set():
            yield av.VideoFrame.from_ndarray(np.full((90,160,3),100,np.uint8), format='rgb24')
            w.stop_event.wait(.015)
    w.worker = threading.Thread(target=w.receive_frames,args=(frames(),w.stop_event),daemon=True)
    w.worker.start()

def close(w):
    w.stop()
    if w.worker: w.worker.join(2)
    w.close()

def test_draft_apply_cancel_and_saved_look(tmp_path):
    from PySide6.QtCore import QSettings
    w = MainWindow(False)
    try:
        w.settings = QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat)
        w.staged.setChecked(True)
        stream(w)
        w.grading.exposure.setValue(100)
        wait_for(lambda: not w.preview.frame.isNull() and w.preview.frame.pixelColor(80,45).red()>130)
        assert w.output.frame.pixelColor(80,45).red() == 100
        w.save_look()
        assert json.loads(w.settings.value('lastLook'))['settings']['exposure'] == 0
        w.apply_draft()
        wait_for(lambda: w.output.frame.pixelColor(80,45).red()>130)
        w.grading.exposure.setValue(-100)
        w.cancel_draft()
        assert w.grading.settings().exposure == 1
        assert w.look_settings.exposure == 1
    finally: close(w)

def test_output_only_skips_preview_and_keeps_output(monkeypatch):
    w = MainWindow(False)
    try:
        w.preview_mode.setCurrentIndex(2)
        w.compare.setChecked(True)
        w.zebra_control.setChecked(True)
        monkeypatch.setattr(w.preview,'set_frame',lambda *args: (_ for _ in ()).throw(AssertionError('preview disabled')))
        stream(w)
        wait_for(lambda: w.processing_stats['processed']>8)
        assert not w.output.frame.isNull()
        assert w.preview_stats['processed']==0
    finally: close(w)

def test_history_merges_gesture_and_undoes_preset():
    from grading_ui import GradingPanel
    p = GradingPanel()
    p.temperature.sliderPressed.emit()
    for v in (5,10,20): p.temperature.setValue(v)
    p.temperature.sliderReleased.emit()
    p.undo_settings()
    assert p.settings().temperature==0
    p.redo_settings()
    assert p.settings().temperature==20
    p.apply(Settings(exposure=1, temperature=-30))
    p.undo_settings()
    assert p.settings().temperature==20 and p.settings().exposure==0
    p.close()

def test_trace_bounds_and_stage_delays():
    from goggles_workflow import FrameTrace
    trace = FrameTrace(limit=5)
    trace.start(now=10)
    trace.add('receive',1,now=10)
    trace.add('process_start',1,now=10.002)
    trace.add('ready',1,now=10.006)
    trace.add('submit',1,now=10.010)
    trace.add('paint',1,now=10.014)
    report=trace.report()
    assert abs(report['summary']['queue_ms']['median']-2)<.001
    assert abs(report['summary']['processing_ms']['median']-4)<.001
    assert abs(report['summary']['ready_to_submit_ms']['median']-4)<.001
    assert abs(report['summary']['submit_to_paint_ms']['median']-4)<.001
    trace.add('receive',2,now=10.02)
    assert len(trace.report()['events'])==5
    assert trace.report()['events_truncated']
    trace.stop()
    trace.add('receive',3,now=11)
    assert len(trace.report()['events'])==5

def test_slow_draft_never_blocks_or_leaks_into_output(monkeypatch):
    from effects import Processor
    w = MainWindow(False)
    entered, release = threading.Event(), threading.Event()
    try:
        w.staged.setChecked(True)
        w.grading.exposure.setValue(100)
        def slow_preview(self, pixels, settings):
            entered.set()
            release.wait(2)
            return np.full_like(pixels,240)
        monkeypatch.setattr(Processor,'process',slow_preview)
        stream(w)
        assert entered.wait(2)
        wait_for(lambda: w.processing_stats['processed'] >= 10)
        assert w.output.frame.pixelColor(80,45).red()==100
        w.cancel_draft()
        w.staged.setChecked(False)
        release.set()
        QTest.qWait(100)
        assert w.output.frame.pixelColor(80,45).red()==100
        assert w.preview.frame.pixelColor(80,45).red()==100
    finally:
        release.set()
        close(w)

def test_preview_rate_limit_and_minimized_output(monkeypatch):
    w = MainWindow(False)
    try:
        w.preview_mode.setCurrentIndex(1)
        draws=[]
        draw=w.preview.set_frame
        def recorded(image):
            draws.append(time.monotonic())
            draw(image)
        monkeypatch.setattr(w.preview,'set_frame',recorded)
        stream(w)
        wait_for(lambda: len(draws)>=5)
        assert min(np.diff(draws))>=1/15-.004
        w.showMinimized()
        wait_for(lambda: not w.preview_active)
        count=len(draws)
        before=w.processing_stats['processed']
        QTest.qWait(120)
        assert len(draws)==count
        assert w.processing_stats['processed']>before
    finally: close(w)

def test_visible_output_trace_records_paint_and_stops():
    w = MainWindow(False)
    try:
        w.output.resize(320,180)
        w.output.show()
        w.toggle_trace()
        stream(w)
        wait_for(lambda: w.processing_stats['processed']>=10)
        w.finish_trace()
        stages={event['stage'] for event in w.trace_report['events']}
        assert {'receive','process_start','ready','submit','paint'} <= stages
        assert not w.trace.active
        assert not w.trace_report['video_recorded']
        assert w.trace_report['summary']['processing_ms']['count']>0
    finally: close(w)

def test_trace_uses_high_resolution_clock(monkeypatch):
    import goggles_workflow as workflow
    monkeypatch.setattr(workflow.time,'monotonic',lambda:100.)
    ticks=iter([100.,100.00125])
    monkeypatch.setattr(workflow.time,'perf_counter',lambda:next(ticks))
    trace=workflow.FrameTrace()
    trace.start()
    trace.add('receive',1)
    assert trace.report()['events'][0]['ms']==1.25


def test_color_edit_keeps_completed_inflight_output(monkeypatch):
    """A color edit must not starve output by rejecting every finished frame."""
    from effects import Processor
    w = MainWindow(False)
    entered, release = threading.Event(), threading.Event()
    process = Processor.process
    try:
        w.mode = 'stream'
        w.set_look(Settings(exposure=1))
        def controlled_effect(self, pixels, settings):
            entered.set()
            release.wait(2)
            return process(self, pixels, settings)
        monkeypatch.setattr(Processor, 'process', controlled_effect)
        def frames():
            yield av.VideoFrame.from_ndarray(np.full((90,160,3),100,np.uint8), format='rgb24')
            w.stop_event.wait(3)
        w.worker = threading.Thread(target=w.receive_frames, args=(frames(),w.stop_event), daemon=True)
        w.worker.start()
        assert entered.wait(2)
        w.set_look(Settings(exposure=.5))
        release.set()
        wait_for(lambda: not w.output.frame.isNull())
        assert w.output.frame.pixelColor(80,45).red() > 130
    finally:
        release.set()
        close(w)


def test_raw_output_overrides_review_and_preserves_both_looks():
    w = MainWindow(False)
    try:
        assert hasattr(w, 'raw_button'), 'An immediate output override is missing'
        w.grading.exposure.setValue(100)
        stream(w)
        wait_for(lambda: not w.output.frame.isNull() and w.output.frame.pixelColor(80,45).red()>130)
        w.staged.setChecked(True)
        w.grading.exposure.setValue(-100)
        w.raw_button.click()
        wait_for(lambda: w.output.frame.pixelColor(80,45).red()==100)
        assert w.look_settings.exposure == 1
        assert w.draft_settings.exposure == -1
        assert not w.look_settings.bypass and not w.draft_settings.bypass
        w.raw_button.click()
        wait_for(lambda: w.output.frame.pixelColor(80,45).red()>130)
        assert w.draft_settings.exposure == -1
        w.raw_button.click()
        w.apply_draft()
        QTest.qWait(80)
        assert w.output.frame.pixelColor(80,45).red()==100
        w.raw_button.click()
        wait_for(lambda: w.output.frame.pixelColor(80,45).red()<80)
    finally: close(w)


def test_raw_output_rejects_inflight_color_and_accepts_next_raw_frame(monkeypatch):
    from effects import Processor
    w = MainWindow(False)
    entered, release, next_frame = threading.Event(), threading.Event(), threading.Event()
    try:
        assert hasattr(w, 'raw_button'), 'An immediate output override is missing'
        w.mode = 'stream'
        w.set_look(Settings(exposure=1))
        def delayed(self, pixels, settings):
            entered.set()
            release.wait(2)
            return np.full_like(pixels,240)
        monkeypatch.setattr(Processor, 'process', delayed)
        def frames():
            yield av.VideoFrame.from_ndarray(np.full((90,160,3),100,np.uint8),format='rgb24')
            next_frame.wait(2)
            yield av.VideoFrame.from_ndarray(np.full((90,160,3),100,np.uint8),format='rgb24')
            w.stop_event.wait(3)
        w.worker = threading.Thread(target=w.receive_frames,args=(frames(),w.stop_event),daemon=True)
        w.worker.start()
        assert entered.wait(2)
        w.raw_button.click()
        release.set()
        QTest.qWait(80)
        assert w.output.frame.isNull(), 'The pre-override colored frame must be rejected'
        next_frame.set()
        wait_for(lambda: not w.output.frame.isNull())
        assert w.output.frame.pixelColor(80,45).red()==100
        assert w.processing_stats['tracking']=='꺼짐'
    finally:
        release.set()
        next_frame.set()
        close(w)


def test_short_receive_stall_is_captured_without_manual_trace(tmp_path):
    from stutter import IncidentStore
    w = MainWindow(False)
    try:
        assert hasattr(w, 'stutter'), 'Automatic capture must be connected to the live pipeline'
        w.incident_store = IncidentStore(tmp_path/'stutter-latest.json')
        w.mode = 'stream'
        def frames():
            for i in range(125):
                if w.stop_event.is_set(): return
                yield av.VideoFrame.from_ndarray(np.full((90,160,3),100,np.uint8),format='rgb24')
                w.stop_event.wait(.22 if i==42 else 1/30)
            w.stop_event.wait(2)
        w.worker = threading.Thread(target=w.receive_frames,args=(frames(),w.stop_event),daemon=True)
        w.worker.start()
        for _ in range(250):
            QTest.qWait(20)
            if w.incident_store.path.exists(): break
        assert w.incident_store.path.exists(), 'Completed stall evidence should be saved automatically'
        report=json.loads(w.incident_store.path.read_text(encoding='utf-8'))
        assert report['incidents'][-1]['reason']=='receive'
        assert not w.trace.active and w.trace.report()['events']==[]
        assert not w.output.frame.isNull()
    finally: close(w)


def test_slow_stutter_export_keeps_output_flowing(tmp_path, monkeypatch):
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    w=MainWindow(False)
    entered, release=threading.Event(), threading.Event()
    path=tmp_path/'export.json'
    write_text=Path.write_text
    def slow_write(destination,*args,**kwargs):
        if destination.parent==tmp_path:
            entered.set()
            release.wait(2)
        return write_text(destination,*args,**kwargs)
    try:
        w.stutter.start(now=0)
        for i in range(45):
            for stage in ('receive','ready','submit'):
                w.stutter.add(stage,i,now=i/30)
        w.stutter.poll(now=1.8)
        w.stutter.stop(now=1.9)
        stream(w)
        wait_for(lambda: not w.output.frame.isNull())
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *args,**kwargs: (str(path),'JSON'))
        monkeypatch.setattr(Path,'write_text',slow_write)
        started=time.perf_counter()
        w.save_stutter()
        assert time.perf_counter()-started < .25, 'Saving must return without blocking output delivery'
        assert entered.wait(1)
        before=w.last_frame
        QTest.qWait(80)
        assert w.last_frame>before
        release.set()
        wait_for(path.exists)
        assert json.loads(path.read_text(encoding='utf-8'))['incidents']
    finally:
        release.set()
        close(w)
