"""Deterministic rendered-app QA using a locally encoded test movie, never DJI footage."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import av
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest


def run_foreground_helper(app):
    """A separate, disposable ordinary application window for focus/Z-order QA."""
    from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout
    args = sys.argv
    out = Path(args[args.index('--qa-dir')+1])
    rect = [int(n) for n in args[args.index('--helper-rect')+1].split(',')]
    helper = QWidget()
    helper.setWindowTitle('Goggles HDMI QA — foreground application')
    helper.setGeometry(rect[0]+80,rect[1]+80,min(600,rect[2]-160),min(350,rect[3]-160))
    QVBoxLayout(helper).addWidget(QLabel('별도 앱 활성화 시험 · 검사 후 자동 종료'))
    helper.show()
    (out/'foreground-helper.json').write_text(json.dumps({'hwnd':int(helper.winId()),'pid':os.getpid()}))
    QTimer.singleShot(20000,app.quit)  # Bounded cleanup even if the parent test fails.
    return helper


def check_other_application(window, out):
    """Verify actual Windows stacking and hotkey input with another process focused."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32',use_last_error=True)
    user32.GetWindowLongW.argtypes = [wintypes.HWND,ctypes.c_int]
    user32.GetWindow.argtypes = [wintypes.HWND,wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
    hwnd = int(window.output.winId())
    assert user32.GetWindowLongW(hwnd,-20) & 8, 'Native WS_EX_TOPMOST was not applied'
    assert window.output.hotkey_registered
    marker = out/'foreground-helper.json'
    marker.unlink(missing_ok=True)
    command = [sys.executable]
    if not getattr(sys,'frozen',False):
        command.append(str(Path(__file__).with_name('app.py')))
    rect = window.output.geometry()
    command += ['--foreground-helper','--qa-dir',str(out.resolve()),'--helper-rect',
                f'{rect.x()},{rect.y()},{rect.width()},{rect.height()}']
    helper = subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:
            QTest.qWait(50)
        assert marker.exists(), 'Foreground helper did not start'
        other = json.loads(marker.read_text())['hwnd']
        # Give our test process genuine foreground permission by clicking its own
        # verified output surface. Windows may ignore programmatic activation from
        # a background test runner. Never click an unrelated application.
        bounds, old_cursor = wintypes.RECT(),wintypes.POINT()
        user32.GetWindowRect(hwnd,ctypes.byref(bounds))
        point = wintypes.POINT((bounds.left+bounds.right)//2,(bounds.top+bounds.bottom)//2)
        assert user32.WindowFromPoint(point) == hwnd, 'Test click must hit our output only'
        user32.GetCursorPos(ctypes.byref(old_cursor))
        user32.SetCursorPos(point.x,point.y)
        try:
            user32.mouse_event(0x0002,0,0,0,0)
            user32.mouse_event(0x0004,0,0,0,0)
            QTest.qWait(100)
        finally:
            user32.SetCursorPos(old_cursor.x,old_cursor.y)
        assert user32.GetForegroundWindow() == hwnd, 'Output test click must activate our process'
        user32.SetForegroundWindow(other)
        QTest.qWait(200)
        assert user32.GetForegroundWindow() == other, 'Test must activate the other process'

        def above_other():
            cursor = other
            for _ in range(10000):
                cursor = user32.GetWindow(cursor,3)  # GW_HWNDPREV: preceding Z-order
                if not cursor:
                    return False
                if cursor == hwnd:
                    return True
            return False

        assert above_other(), 'An activated ordinary app covered the output'
        # Also check recovery when another app requests always-on-top status.
        window.output.user32.SetWindowPos(other,-1,0,0,0,0,0x0013)
        QTest.qWait(250)
        assert above_other(), 'Output did not regain top position'
        assert user32.GetForegroundWindow() == other, 'Guard must not steal keyboard focus'

        class KeyboardInput(ctypes.Structure):
            _fields_ = [('vk',wintypes.WORD),('scan',wintypes.WORD),('flags',wintypes.DWORD),
                        ('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        class MouseInput(ctypes.Structure):
            _fields_ = [('x',wintypes.LONG),('y',wintypes.LONG),('data',wintypes.DWORD),
                        ('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        class InputUnion(ctypes.Union):
            _fields_ = [('keyboard',KeyboardInput),('mouse',MouseInput)]
        class Input(ctypes.Structure):
            _fields_ = [('type',wintypes.DWORD),('value',InputUnion)]
        inputs = (Input*4)()
        for item,(key,flags) in zip(inputs,[(0x11,0),(0x44,0),(0x44,2),(0x11,2)]):
            item.type = 1
            item.value.keyboard = KeyboardInput(key,0,flags,0,0)
        user32.SendInput.argtypes = [wintypes.UINT,ctypes.POINTER(Input),ctypes.c_int]
        assert user32.SendInput(4,inputs,ctypes.sizeof(Input)) == 4
        QTest.qWait(200)
        assert not window.output.isVisible() and not window.output.locked, 'Global Ctrl+D did not release output'
        assert not window.output.hotkey_registered and not window.output.topmost_guard.isActive()

        # A conflicting hotkey must leave output unlocked and the user in control.
        api = window.output.user32
        assert api.RegisterHotKey(hwnd,0x4749,0x4002,ord('D')), 'Ctrl+D was not released'
        try:
            window.open_output()
            assert not window.output.locked and not window.output.isVisible()
        finally:
            api.UnregisterHotKey(hwnd,0x4749)
    finally:
        helper.terminate()
        helper.wait(timeout=5)
    window.open_output()
    QTest.qWait(100)


def run_selftest(app, window):
    args = sys.argv
    out = Path(args[args.index('--qa-dir')+1]) if '--qa-dir' in args else Path('qa')
    out.mkdir(parents=True, exist_ok=True)
    results = {'direct_usb': 'NOT_TESTED_BY_SYNTHETIC_TEST', 'physical_hdmi': 'NOT_TESTED', 'checks': []}
    def fail(exc):
        results['error'] = repr(exc)
        (out/'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        window.release_output()
        window.close()
        app.exit(1)
    try:
        window.pattern()
        window.tick()
        assert not window.preview.frame.isNull()
        window.open_output()
        app.processEvents()
        assert window.output.isFullScreen()
        assert window.output.windowFlags() & Qt.WindowStaysOnTopHint, 'Output must remain above other applications'
        check_other_application(window,out)
        results['checks'].append('native_topmost_above_foreground_process_and_global_ctrl_d')
        results['checks'].append('hotkey_released_and_conflict_does_not_lock_output')
        assert window.output.frame.size() == window.preview.frame.size()
        window.output.grab().save(str(out/'output-pattern.png'))
        QTest.keyClick(window.output, Qt.Key_Escape)
        QTest.keyClick(window.output, Qt.Key_F4, Qt.AltModifier)
        window.output.close()
        window.close()
        app.processEvents()
        assert window.output.isVisible() and window.isVisible()
        assert window.output.locked
        assert window.output.keep_awake(True)
        window.output.showMinimized()
        app.processEvents()
        app.processEvents()
        assert window.output.isFullScreen() and not window.output.isMinimized()
        window.screen_removed()
        app.processEvents()
        assert window.output.isVisible() and window.output.locked
        window.stop()
        assert window.output.isVisible()
        QTest.keyClick(window.output, Qt.Key_D, Qt.ControlModifier)
        app.processEvents()
        assert not window.output.isVisible() and not window.output.locked
        results['checks'].append('fullscreen_requires_ctrl_d_and_survives_stop_and_close')
        window.pattern()
        window.tick()
        window.grab().save(str(out/'window-pattern.png'))
        results['checks'].append('test_pattern_and_fullscreen_window')
        window.stop()
        assert window.preview.frame.isNull() and window.output.frame.isNull()
        results['checks'].append('stop_clears_both_surfaces')
        movie = out/'generated-test.mp4'
        with av.open(str(movie), 'w') as c:
            s = c.add_stream('mpeg4', rate=25)
            s.width, s.height, s.pix_fmt = 320, 180, 'yuv420p'
            for i in range(100):
                f = av.VideoFrame(320, 180, 'rgb24')
                for plane in f.planes:
                    plane.update(bytes([40 + i % 180])*plane.buffer_size)
                for packet in s.encode(f):
                    c.mux(packet)
            for packet in s.encode():
                c.mux(packet)
        window.source.setText(str(movie.resolve()))
        window.play()
        start = time.monotonic()
        def check_live():
            try:
                if window.frames >= 3 and not window.preview.frame.isNull():
                    assert window.preview.frame.width() == 320
                    assert window.preview.frame.height() == 180
                    window.grab().save(str(out/'window-video.png'))
                    results['checks'].append('encoded_video_decode_and_display')
                    window.last_frame = time.monotonic()-3
                    window.mode = 'stream'
                    with window.lock:
                        window.latest = None
                    # Stop the decoder first so a fresh frame cannot mask the stale-frame test.
                    window.stop_event.set()
                    window.worker.join(timeout=1)
                    assert not window.worker.is_alive()
                    with window.lock:
                        window.latest = None
                        window.finished = None
                    window.tick()
                    assert window.preview.frame.isNull() and window.output.frame.isNull()
                    results['checks'].append('stale_video_cleared')
                    window.stop()
                    results['checks'].append('decoder_cancelled_cleanly')
                    window.show()
                    window.grab().save(str(out/'window-idle.png'))
                    results['passed'] = True
                    (out/'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
                    window.close()
                    app.exit(0)
                elif time.monotonic()-start > 10:
                    raise AssertionError('No rendered decoded frame within timeout')
                else:
                    QTimer.singleShot(40, check_live)
            except Exception as exc:
                fail(exc)
        QTimer.singleShot(40, check_live)
    except Exception as exc:
        fail(exc)
