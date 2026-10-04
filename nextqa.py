"""Synthetic end-to-end checks without touching the connected USB or HDMI output."""
import json
import threading
import time
import sys
import os
from pathlib import Path
import av
import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest


def run(app, window):
    out = Path(sys.argv[sys.argv.index('--qa-dir')+1]) if '--qa-dir' in sys.argv else Path('qa/next-ui')
    out.mkdir(parents=True, exist_ok=True)
    report = {'checks': [], 'live_usb_tested': False, 'physical_hdmi_tested': False}
    rgb = np.full((1080, 1920, 3), 100, dtype=np.uint8)
    rgb[:, :480] = [150, 70, 50]
    rgb[:, 1440:] = [50, 90, 150]
    window.mode = 'stream'
    pause_source = threading.Event()
    def frames():
        while not window.stop_event.is_set():
            if pause_source.is_set():
                pause_source.clear()
                if window.stop_event.wait(.25): return
            yield av.VideoFrame.from_ndarray(rgb, format='rgb24')
            window.stop_event.wait(1/30)
    window.worker = threading.Thread(target=window.receive_frames, args=(frames(), window.stop_event), daemon=True)
    window.worker.start()
    def wait_for(test, timeout=5000):
        deadline = time.monotonic()+timeout/1000
        while time.monotonic() < deadline:
            QTest.qWait(30)
            if test():
                return
        raise AssertionError('Condition timed out')
    def red():
        return window.preview.frame.pixelColor(960, 540).red()
    try:
        assert window.copy_problem_button.isEnabled()
        window.copy_problem_button.click()
        copied = app.clipboard().text()
        assert copied.startswith('Goggles HDMI support report\n')
        assert json.loads(copied.split('\n',1)[1])['source_mode'] == 'stream'
        report['checks'].append('packaged_support_copy_reads_native_clipboard')
        wait_for(lambda: not window.preview.frame.isNull())
        assert red() == 100
        window.grading.exposure.setValue(100)
        wait_for(lambda: red() > 130)
        assert window.output.frame == window.preview.frame
        report['checks'].append('exposure_updates_preview_and_hdmi_surface')
        window.grading.bypass.setChecked(True)
        wait_for(lambda: red() == 100)
        report['checks'].append('bypass_restores_original_pixels')
        window.grading.reset()
        window.grading.tabs.setCurrentIndex(1)
        editor = window.grading.editor
        QTest.qWait(50)
        QTest.mouseClick(editor, Qt.LeftButton, pos=editor.pos((.4, .65)).toPoint())
        wait_for(lambda: red() > 145)
        report['checks'].append('curve_click_changes_decoded_video')
        window.grading.reset()
        window.grading.stabilize.setChecked(True)
        wait_for(lambda: window.processing_stats['tracking'] != '꺼짐')
        assert window.preview.frame.size().width() == 1920
        report['checks'].append('stabilizer_keeps_output_dimensions')
        # Original preview and output both reference the same processed QImage.
        window.grading.stabilize.setChecked(False)
        window.grading.exposure.setValue(40)
        window.grading.temperature.setValue(25)
        QTest.qWait(150)
        window.grading.reset()
        wait_for(lambda: red() == 100 and window.output.frame.pixelColor(960,540).red() == 100)
        window.staged.setChecked(True)
        window.grading.exposure.setValue(100)
        wait_for(lambda: red() > 130)
        assert window.output.frame.pixelColor(960,540).red() == 100
        window.apply_draft()
        wait_for(lambda: window.output.frame.pixelColor(960,540).red() > 130)
        window.grading.temperature.setValue(40)
        window.cancel_draft()
        assert window.grading.temperature.value() == 0
        report['checks'].append('draft_preview_apply_cancel_preserves_output')
        window.grading.exposure.setValue(-100)
        saved_look, saved_draft = window.look_settings, window.draft_settings
        window.raw_button.click()
        wait_for(lambda: window.output.frame.pixelColor(960,540).red()==100)
        assert window.look_settings==saved_look and window.draft_settings==saved_draft
        window.raw_button.click()
        wait_for(lambda: window.output.frame.pixelColor(960,540).red()>130)
        assert window.draft_settings==saved_draft
        report['checks'].append('immediate_raw_output_preserves_committed_and_draft_looks')
        window.staged.setChecked(False)
        window.grading.undo_settings()
        window.grading.redo_settings()
        report['checks'].append('grading_history_undo_redo')
        window.toggle_trace()
        wait_for(lambda: len(window.trace.report()['events']) >= 20)
        window.finish_trace()
        assert window.trace_report['summary']['processing_ms']['count'] > 0
        (out/'timing.json').write_text(json.dumps(window.trace_report,indent=2),encoding='utf-8')
        report['checks'].append('timing_capture_without_video')
        window.preview_mode.setCurrentIndex(2)
        processed = window.processing_stats['processed']
        wait_for(lambda: window.processing_stats['processed'] > processed+3)
        assert window.preview.frame.isNull()
        assert not window.output.frame.isNull()
        report['checks'].append('output_only_keeps_pipeline_running')
        window.stutter.start()
        received = window.frames
        wait_for(lambda: window.frames>=received+40)
        previous_incidents = window.stutter.status()['count']
        pause_source.set()
        wait_for(lambda: window.stutter.status()['count']>previous_incidents)
        incidents = window.stutter.report()
        (out/'automatic-stutter.json').write_text(json.dumps(incidents,indent=2),encoding='utf-8')
        assert incidents['incidents'][-1]['reason']=='receive'
        assert not incidents['video_recorded']
        report['checks'].append('short_receive_stall_automatically_captures_timing_context')
        window.preview_mode.setCurrentIndex(0)
        wait_for(lambda: not window.preview.frame.isNull())
        window.grading.tabs.setCurrentIndex(0)
        window.grab().save(str(out/'controls-preview.png'))
        window.resize(1000, 680)
        QTest.qWait(50)
        report['compact_window'] = [window.width(), window.height()]
        for i, name in enumerate(('color', 'curves', 'stabilizer')):
            window.grading.tabs.setCurrentIndex(i)
            QTest.qWait(50)
            window.grab().save(str(out/f'compact-{name}.png'))
        window.staged.setChecked(True)
        window.grading.exposure.setValue(50)
        QTest.qWait(80)
        window.grab().save(str(out/'compact-review.png'))
        for control in (window.grading.max_crop.number, window.apply_button, window.grading.undo_button,
                        window.raw_button,window.output_button,window.settings_button):
            assert control.isVisible()
            bottom = control.mapTo(window, control.rect().bottomRight()).y()
            assert bottom < window.height(), 'Grading controls must fit in compact window'
            right = control.mapTo(window,control.rect().bottomRight()).x()
            assert right < window.width(), 'Primary actions must fit in compact window'
        window.show_settings(2)
        QTest.qWait(50)
        window.settings_dialog.grab().save(str(out/'settings-diagnostics.png'))
        window.settings_dialog.close()
        report['checks'].append('compact_tabs_review_footer_and_separate_diagnostics')
        # The overlay uses the same clock on both rendered surfaces, including raw output.
        window.staged.setChecked(False)
        window.watermark.origin = time.monotonic()-2
        window.watermark.refresh()
        assert window.preview.watermark is window.output.watermark
        assert window.watermark.opacity == 1
        window.grab().save(str(out/'watermark-preview.png'))
        window.output.resize(960,540)
        window.output.grab().save(str(out/'watermark-output.png'))
        window.raw_button.click()
        assert window.watermark.opacity == 1 and not window.license.unlocked
        window.raw_button.click()
        window.show_settings(3)
        QTest.qWait(50)
        window.settings_dialog.grab().save(str(out/'settings-program.png'))
        panel = window.program_panel
        assert panel.install.isVisible() and not panel.install.isEnabled()
        bottom = panel.install.mapTo(window.settings_dialog,panel.install.rect().bottomRight()).y()
        assert bottom < window.settings_dialog.height()
        assert (Path(__file__).parent/'setup_goggles_network.ps1').is_file()
        password = os.environ.get('GOGGLES_QA_PASSWORD')
        if password:
            panel.password.setText(password)
            panel.authenticate.click()
            wait_for(lambda: window.license.unlocked)
            assert window.watermark.opacity == 0 and not window.watermark.timer.isActive()
            panel.reset.click()
            assert not window.license.unlocked
            report['checks'].append('configured_password_unlocks_both_surfaces_and_reset_restores_watermark')
        window.settings_dialog.close()
        report['checks'].append('shared_watermark_raw_output_and_program_settings_packaged')
        report['processing'] = dict(window.processing_stats)
        window.stop()
        wait_for(lambda: not window.worker.is_alive())
        assert window.preview.frame.isNull()
        report['checks'].append('stop_cancels_pipeline_and_clears_video')
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = repr(exc)
        import traceback
        report['traceback'] = traceback.format_exc()
        (out/'automatic-stutter-on-failure.json').write_text(json.dumps(window.stutter.report(),indent=2),encoding='utf-8')
    finally:
        window.stop()
        window.close()
        (out/'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        app.exit(0 if report['passed'] else 1)
