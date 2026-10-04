"""Opt-in hardware QA. Only run while the user's powered Goggles 3 is connected."""
import json
import sys
import time
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest


def observe_live(app, window):
    """Opt-in cable-replug counts; no camera recording, output dismissal or unlock."""
    args = sys.argv
    out = Path(args[args.index('--qa-dir')+1]) if '--qa-dir' in args else Path('qa/field')
    out.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    events = []
    previous = None
    timer = QTimer(window)
    timer.setInterval(1000)

    def record():
        nonlocal previous
        age = time.monotonic()-window.last_frame if window.last_frame else None
        live = age is not None and age < 2
        if live != previous:
            events.append({'seconds':round(time.monotonic()-started,2),
                           'video_live':live, 'frames':window.frames,
                           'fullscreen_visible':window.output.isVisible(),
                           'locked':window.output.locked})
            previous = live
        report = {'elapsed_seconds':round(time.monotonic()-started,2),
                  'events':events, 'stats':dict(window.usb_stats),
                  'frame_age_seconds':round(age,2) if age is not None else None,
                  'video_live':live, 'fullscreen_visible':window.output.isVisible(),
                  'locked':window.output.locked,
                  'processing':dict(getattr(window, 'processing_stats', {}))}
        if hasattr(window,'stutter'):
            report['automatic_stutter'] = window.stutter.status()
            report['raw_output'] = window.raw_output
            frame = window.output.frame
            report['dimensions'] = [frame.width(),frame.height()] if not frame.isNull() else None
        (out/'observation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    timer.timeout.connect(record)
    timer.start()


def run_liveqa(app, window):
    args = sys.argv
    out = Path(args[args.index('--qa-dir')+1]) if '--qa-dir' in args else Path('qa/live')
    out.mkdir(parents=True, exist_ok=True)
    report = {'direct_usb': 'PENDING', 'physical_hdmi': 'NOT_TESTED', 'checks': [], 'sessions': []}
    start = time.monotonic()
    phase = 0
    mark = 0
    previous_image = None
    timer = QTimer(window)
    timer.setInterval(100)

    def finish(error=None):
        timer.stop()
        if error:
            report['error'] = str(error)
        report['passed'] = error is None
        report['elapsed_seconds'] = round(time.monotonic()-start,2)
        report['last_stats'] = dict(window.usb_stats)
        (out/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        window.stop()
        window.release_output()
        window.close()
        app.exit(0 if error is None else 1)

    def check():
        nonlocal phase, mark, previous_image
        try:
            if time.monotonic()-start > 45:
                raise AssertionError('Hardware QA timed out before two live sessions')
            if phase == 0 and window.frames >= 45 and not window.preview.frame.isNull():
                report['direct_usb'] = 'RECEIVED_AND_RENDERED'
                report['dimensions'] = [window.preview.frame.width(),window.preview.frame.height()]
                assert window.output.isVisible() and window.output.locked
                assert window.output.isFullScreen()
                previous_image = window.preview.frame.copy()
                if '--save-live-images' in args:
                    window.output.grab().save(str(out/'live-output.png'))
                    window.grab().save(str(out/'live-window.png'))
                report['checks'].append('usb_decoded_and_automatic_fullscreen')
                QTest.keyClick(window.output,Qt.Key_Escape)
                QTest.keyClick(window.output,Qt.Key_F4,Qt.AltModifier)
                window.output.close()
                assert window.output.isVisible() and window.output.locked
                QTest.keyClick(window.output,Qt.Key_D,Qt.ControlModifier)
                assert not window.output.isVisible() and not window.output.locked
                mark = window.frames
                phase = 1
            elif phase == 1 and window.frames >= mark+30:
                assert not window.output.isVisible(), 'Ctrl+D must suppress automatic reopening'
                assert window.preview.frame != previous_image, 'New live frames must change'
                report['checks'].append('ctrl_d_releases_output_without_stopping_live_decode')
                report['sessions'].append(dict(window.usb_stats))
                window.open_output()
                window.stop()
                assert window.output.isVisible() and window.output.locked
                phase = 2
            elif phase == 2 and not window.worker.is_alive():
                window.start_usb()
                mark = time.monotonic()
                phase = 3
            elif phase == 3 and window.frames >= 90 and not window.preview.frame.isNull():
                assert window.output.isVisible() and window.output.locked
                report['sessions'].append(dict(window.usb_stats))
                report['restart_to_90_frames_seconds'] = round(time.monotonic()-mark,2)
                report['checks'].append('fresh_usb_session_recovers_video_without_toggling_sharing')
                report['checks'].append('fullscreen_survives_receiver_stop_and_restart')
                finish()
        except Exception as exc:
            finish(exc)

    window.auto_fullscreen.setChecked(True)
    window.start_usb()
    timer.timeout.connect(check)
    timer.start()
