"""Windows HDMI output with Goggles 3 USB RNDIS live-view reception."""
import json
import os
import subprocess
import sys
import threading
import time
import queue
from collections import deque
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRect, QSettings, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QShortcut, QKeySequence, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox, QFileDialog, QTextEdit, QMessageBox, QCheckBox, QScrollArea)
from core import decode_frames, validate_source
from goggles import decode_goggles
from effects import Settings, Processor
from grading_ui import GradingPanel, validate_preset
from goggles_workflow import FrameTrace
from stutter import StutterMonitor, IncidentStore
from window_placement import visible_control_rect
from pipeline import receive_frames as run_pipeline
from licensing import LicenseState, CONFIG
from watermark import Watermark
from platform_support import state_directory
from support_report import build_report,issue_record


def screen_pixel_size(screen):
    """Return the display mode in pixels, not Qt's scaled desktop coordinates."""
    if sys.platform == 'darwin':
        from platform_support import mac_display_pixels
        pixels = mac_display_pixels(screen)
        if pixels:
            return pixels
        return 0,0  # Unknown pixel mode must not be labeled as logical dimensions.
    if os.name == 'nt':
        import ctypes
        import struct
        # DEVMODEW: dmSize at 68, dmPelsWidth/dmPelsHeight at 172/176.
        mode = ctypes.create_string_buffer(220)
        struct.pack_into('<H', mode, 68, len(mode))
        query = ctypes.windll.user32.EnumDisplaySettingsW
        query.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
        query.restype = ctypes.c_int
        if query(screen.name(), 0xffffffff, mode):
            width, height = struct.unpack_from('<II', mode, 172)
            if width and height:
                return width, height
    size = screen.size()
    scale = screen.devicePixelRatio()
    return round(size.width() * scale), round(size.height() * scale)


class VideoSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.frame = QImage()
        self.message = '영상 대기'
        self.zebra = False
        self.crop_guide = 0.
        self.zebra_image = QImage()
        self.trace = None
        self.trace_frame = None
        self.painted_frame = None
        self.watermark = None
        self.setMinimumSize(320, 180)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#141414'))
        if not self.frame.isNull():
            size = self.frame.size().scaled(self.size(), Qt.KeepAspectRatio)
            rect = QRect((self.width()-size.width())//2, (self.height()-size.height())//2,
                         size.width(), size.height())
            p.drawImage(rect, self.frame)
            if self.zebra and not self.zebra_image.isNull():
                p.drawImage(rect, self.zebra_image)
            if self.crop_guide > 0:
                dx, dy = int(rect.width()*self.crop_guide), int(rect.height()*self.crop_guide)
                p.setPen(QPen(QColor('#00d9ed'), 2, Qt.DashLine))
                p.drawRect(rect.adjusted(dx, dy, -dx, -dy))
            if self.watermark is not None:
                self.watermark.paint(p, rect)
        else:
            p.setPen(QColor('#a0a0a0'))
            p.setFont(QFont('Malgun Gothic', 18))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
        p.end()
        if self.trace is not None and self.trace_frame is not None and self.trace_frame != self.painted_frame:
            self.trace.add('paint', self.trace_frame)
            self.painted_frame = self.trace_frame

    def set_frame(self, frame):
        self.frame = frame
        self.zebra_image = QImage()
        if self.zebra:
            import numpy as np
            small = frame.scaledToWidth(320).convertToFormat(QImage.Format_RGBA8888)
            pixels = np.frombuffer(small.bits(), np.uint8).reshape(small.height(), small.bytesPerLine())
            pixels = pixels[:, :small.width()*4].reshape(small.height(), small.width(), 4)
            yy, xx = np.indices(pixels.shape[:2])
            mask = (pixels[:, :, :3].max(axis=2) >= 250) & ((xx+yy)%10 < 5)
            overlay = np.zeros_like(pixels)
            overlay[mask] = [255, 190, 0, 210]
            self.zebra_image = QImage(overlay.data, small.width(), small.height(), overlay.strides[0], QImage.Format_RGBA8888).copy()
        self.update()

    def clear(self, message='영상 대기'):
        self.message = message
        self.frame = QImage()
        self.update()


class OutputWindow(VideoSurface):
    release_requested = Signal()
    state_observed = Signal(str)
    HOTKEY_ID = 0x4748

    def __init__(self):
        self.locked = False
        self.hotkey_registered = False
        self.awake_process = None
        self.output_screen = None
        self.fullscreen_repairs = 0
        self.output_generation = 0
        self.repair_generation = None
        super().__init__()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle('Goggles HDMI — 영상 출력')
        self.setCursor(Qt.BlankCursor)
        self.topmost_guard = QTimer(self)
        self.topmost_guard.setInterval(100)
        self.topmost_guard.timeout.connect(self.enforce_topmost)
        self.fullscreen_repair = QTimer(self)
        self.fullscreen_repair.setSingleShot(True)
        self.fullscreen_repair.setInterval(250)
        self.fullscreen_repair.timeout.connect(self.repair_fullscreen)
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.user32 = ctypes.WinDLL('user32',use_last_error=True)
            self.user32.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,
                ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
            self.user32.SetWindowPos.restype = wintypes.BOOL
            self.user32.RegisterHotKey.argtypes = [wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
            self.user32.RegisterHotKey.restype = wintypes.BOOL
            self.user32.UnregisterHotKey.argtypes = [wintypes.HWND,ctypes.c_int]
            self.user32.UnregisterHotKey.restype = wintypes.BOOL

    def lock_output(self):
        if self.locked:
            return True
        if sys.platform == 'win32':
            # System-wide only while output is locked: works with a browser focused.
            self.hotkey_registered = bool(self.user32.RegisterHotKey(
                int(self.winId()),self.HOTKEY_ID,0x4002,ord('D')))
            if not self.hotkey_registered:
                return False
        self.locked = True
        self.output_generation += 1
        self.fullscreen_repairs = 0
        if sys.platform != 'darwin':
            self.topmost_guard.start()
        return True

    def present(self, screen):
        self.output_generation += 1
        self.fullscreen_repairs = 0
        self.fullscreen_repair.stop()
        self.output_screen = screen
        self.winId()  # Create while hidden, then select the screen before first show.
        self.windowHandle().setScreen(screen)
        self.setGeometry(screen.geometry())
        self.showFullScreen()

    def repair_fullscreen(self):
        if (not self.locked or self.repair_generation!=self.output_generation
                or self.fullscreen_repairs or self.isFullScreen() or not self.isVisible()
                or QApplication.applicationState()!=Qt.ApplicationActive
                or self.output_screen not in QApplication.screens()
                or self.windowHandle().screen()!=self.output_screen):
            self.state_observed.emit('repair_skipped')
            return
        self.fullscreen_repairs += 1
        self.showFullScreen()
        self.state_observed.emit('fullscreen_repair')

    def restore_minimized(self):
        if self.locked:self.showFullScreen()

    def enforce_topmost(self):
        if sys.platform == 'darwin':
            return
        if not self.locked:
            return
        if not self.isVisible() or self.isMinimized():
            self.showFullScreen()
        if os.name == 'nt':
            # HWND_TOPMOST; keep geometry and the user's keyboard focus unchanged.
            self.user32.SetWindowPos(int(self.winId()),-1,0,0,0,0,0x0013)
        else:
            self.raise_()

    def event(self, event):
        if self.locked and event.type() == QEvent.WindowDeactivate:
            QTimer.singleShot(0,self.enforce_topmost)
        result = super().event(event)
        if event.type() in (QEvent.Show,QEvent.Hide,QEvent.WindowActivate,QEvent.WindowDeactivate):
            self.state_observed.emit({QEvent.Show:'show',QEvent.Hide:'hide',
                QEvent.WindowActivate:'activate',QEvent.WindowDeactivate:'deactivate'}[event.type()])
        return result

    def nativeEvent(self, event_type, message):
        if os.name == 'nt' and self.locked:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.HOTKEY_ID:
                self.release_requested.emit()
                return True,0
        return super().nativeEvent(event_type,message)

    def keep_awake(self, enabled):
        if sys.platform == 'darwin':
            if not enabled:
                if self.awake_process is not None:
                    self.awake_process.terminate()
                    self.awake_process = None
                return True
            if self.awake_process is None:
                try:
                    self.awake_process = subprocess.Popen(['/usr/bin/caffeinate','-di','-w',str(os.getpid())],
                        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                except OSError:
                    return False
            return self.awake_process.poll() is None
        if os.name == 'nt':
            import ctypes
            # Thread-scoped request; released on Ctrl+D or process termination.
            flags = 0x80000003 if enabled else 0x80000000
            return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
        return True

    def release(self):
        self.locked = False
        self.output_generation += 1
        self.fullscreen_repair.stop()
        self.topmost_guard.stop()
        if os.name == 'nt' and self.hotkey_registered:
            self.user32.UnregisterHotKey(int(self.winId()),self.HOTKEY_ID)
            self.hotkey_registered = False
        self.keep_awake(False)
        self.hide()

    def closeEvent(self, event):
        if self.locked:
            event.ignore()
        else:
            event.accept()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type()==QEvent.WindowStateChange:
            self.state_observed.emit('window_state')
            if sys.platform=='darwin':
                if (self.locked and event.oldState() & Qt.WindowFullScreen and not self.isFullScreen()
                        and not self.fullscreen_repairs):
                    self.repair_generation=self.output_generation
                    self.fullscreen_repair.start()
            elif self.locked and self.isMinimized():
                QTimer.singleShot(0, self.restore_minimized)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if sys.platform == 'darwin':
                self.state_observed.emit('escape')
                self.release_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


def device_diagnostics():
    """Read only; no driver installs, network changes, secrets or serial numbers."""
    if sys.platform == 'darwin':
        from macos_usb import diagnostics
        return diagnostics()
    if os.name != 'nt':
        return {'platform': sys.platform, 'error': 'Windows에서 실행하세요.'}
    script = r'''
$ErrorActionPreference = 'Stop'
$devices = @(Get-CimInstance Win32_PnPEntity | Where-Object {
    $_.PNPDeviceID -like 'USB\VID_2CA3*' -or $_.Name -match 'DJI|Goggles'
} | ForEach-Object { [PSCustomObject]@{
    Name=$_.Name; Status=$_.Status; Class=$_.PNPClass;
    Hardware=$_.HardwareID; ErrorCode=$_.ConfigManagerErrorCode
}})
$adapters = @(Get-NetAdapter | Where-Object {
    $_.InterfaceDescription -match 'RNDIS|Remote NDIS|DJI|Goggles|USB.*Ethernet'
} | ForEach-Object { [PSCustomObject]@{
    Name=$_.Name; Description=$_.InterfaceDescription; Status=[string]$_.Status
}})
@{DJIDevices=$devices; CandidateAdapters=$adapters} | ConvertTo-Json -Depth 5 -Compress
'''
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;'+script],
        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('Windows 장치 조회 실패. 장치 관리자를 확인하세요.')
    data = json.loads(result.stdout.decode('utf-8-sig'))
    data['DirectUSBReceiver'] = 'USB_RNDIS_IMPLEMENTED'
    data['Note'] = '장치 인식은 영상 수신 성공을 의미하지 않습니다.'
    return data


class MainWindow(QMainWindow):
    def __init__(self, auto_start=True):
        super().__init__()
        self.setWindowTitle('Goggles HDMI')
        self.resize(1240, 780)
        self.setMinimumSize(960, 620)
        self.stop_event = threading.Event()
        self.worker = None
        self.lock = threading.Lock()
        self.latest = None
        self.finished = None
        self.diag_result = None
        self.diag_report = ''
        self.last_frame = 0
        self.frames = 0
        self.started = 0
        self.mode = 'idle'
        self.latest_original = None
        self.preview_original = False
        self.output_target = None
        self.output_pending = None
        self.rate_samples = deque()
        self.last_rate_update = 0.
        self.last_packet_time = 0.
        self.video_interruptions = 0
        self.was_live = False
        self.effect_fault = None
        self.receive_gaps = deque(maxlen=120)
        self.last_received_at = None
        self.usb_device_state = '미확인'
        self.settings = QSettings('GogglesHDMI', 'GogglesHDMI-Next') if auto_start else None
        self.license = LicenseState(self.settings, parent=self)
        self.watermark = Watermark(self.license, self)
        self.look_settings = Settings()
        self.look_revision = 0
        self.output_epoch = 0
        self.raw_output = False
        self.draft_settings = self.look_settings
        self.draft_revision = 0
        self.staging = False
        self.preview_active = True
        self.preview_interval = 0.
        self.preview_epoch = 0
        self.latest_preview = None
        self.latest_meta = None
        self.last_preview_draw = 0.
        self.preview_stats = {'processed':0, 'ms':0.}
        self.preview_fault = None
        self.stutter = StutterMonitor()
        self.trace = FrameTrace(monitor=self.stutter)
        self.incident_store = (IncidentStore(state_directory() / 'diagnostics' / 'stutter-latest.json') if auto_start else None)
        self.stutter_revision = 0
        self.stutter_summary = ''
        self.stutter_save_error = None
        self.stutter_export = None
        self.trace_frame_id = 0
        self.trace_report = None
        self.trace_end = 0.
        self.trace_start_stats = {}
        self.last_status_update = 0
        self.processing_stats = {'ms': 0., 'dropped': 0, 'processed': 0, 'tracking': '꺼짐'}
        self.usb_stats = {}
        self.support_history = deque(maxlen=10)
        self.component_issues = {}
        self.cached_diagnosis = None
        self.support_output = {}
        self.display_modes = []
        self.output_events = deque(maxlen=10)
        self.output_release_reason = 'not_started'
        self.usb_note = None
        self.last_usb_note = None
        self.usb_auto_output_pending = False
        self.output = OutputWindow()
        self.output.trace = self.trace
        self.output.release_requested.connect(self.release_output)
        self.output.state_observed.connect(self.observe_output)
        QApplication.instance().aboutToQuit.connect(self.output.release)
        self.exit_output_shortcut = QShortcut(QKeySequence('Ctrl+D'), self)
        self.exit_output_shortcut.setContext(Qt.ApplicationShortcut)
        self.exit_output_shortcut.setAutoRepeat(False)
        self.exit_output_shortcut.activated.connect(self.release_output)
        if sys.platform == 'darwin':
            self.mac_exit_shortcut = QShortcut(QKeySequence('Meta+D'),self)
            self.mac_exit_shortcut.setContext(Qt.ApplicationShortcut)
            self.mac_exit_shortcut.activated.connect(self.release_output)
        self.raw_shortcut = QShortcut(QKeySequence('Ctrl+B'), self)
        self.raw_shortcut.setContext(Qt.ApplicationShortcut)
        self.raw_shortcut.setAutoRepeat(False)
        self.raw_shortcut.activated.connect(lambda: self.raw_button.toggle())

        from ui_layout import build_main_ui
        build_main_ui(self, VideoSurface)
        for surface in (self.preview, self.output):
            surface.watermark = self.watermark
            self.watermark.changed.connect(surface.update)
        self.display_refresh_timer = QTimer(self)
        self.display_refresh_timer.setSingleShot(True)
        self.display_refresh_timer.setInterval(150)
        self.display_refresh_timer.timeout.connect(self.refresh_screens)
        self.display_retry_count = 0
        self.refresh_screens()
        QApplication.instance().screenAdded.connect(self.screen_added)
        QApplication.instance().screenRemoved.connect(self.screen_removed)
        self.placement_timer = QTimer(self)
        self.placement_timer.setSingleShot(True)
        self.placement_timer.timeout.connect(self.ensure_control_visible)
        for screen in QApplication.screens(): self.watch_control_screen(screen)
        QApplication.instance().screenAdded.connect(self.watch_control_screen)
        QApplication.instance().screenRemoved.connect(self.schedule_control_placement)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save_look)
        self.grading.restore_stabilizer.toggled.connect(lambda _: self.save_timer.start())
        self.grading.attach_presets(self.settings)
        if self.settings:
            try:
                restore = self.settings.value('restoreStabilizer', False, type=bool)
                self.grading.restore_stabilizer.setChecked(restore)
                saved = self.settings.value('lastLook', '')
                if saved:
                    look = validate_preset(json.loads(saved))
                    self.grading.apply(replace(look, stabilize=look.stabilize and restore, bypass=False))
            except (ValueError, TypeError, KeyError):
                self.log('저장된 보정값을 읽지 못해 기본값으로 시작합니다.')
        self.update_draft_status()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(4)
        if auto_start and '--connect' in sys.argv:
            QTimer.singleShot(200,self.start_usb)

    def save_look(self):
        if self.settings:
            self.settings.setValue('lastLook', json.dumps({'version': 1, 'settings': asdict(self.look_settings)}))
            self.settings.setValue('restoreStabilizer', self.grading.restore_stabilizer.isChecked())
            self.settings.sync()

    def set_look(self, settings):
        with self.lock:
            if settings != self.draft_settings:
                self.draft_settings = settings
                self.draft_revision += 1
                self.latest_preview = None
            if not self.staging and settings != self.look_settings:
                self._commit_look_locked(settings)
        if hasattr(self, 'save_timer') and not self.staging:
            self.save_timer.start()
        if hasattr(self, 'draft_status'):
            self.update_draft_status()

    def preview_key(self):
        return (self.draft_revision, self.look_revision, self.preview_epoch)

    def _invalidate_output_locked(self):
        self.output_epoch += 1
        self.latest = self.latest_meta = self.latest_original = None
        self.latest_preview = None
        self.preview_epoch += 1

    def _commit_look_locked(self, settings):
        # Ordinary edits take effect on the next frame without starving output.
        old = self.look_settings
        modes_before = (old.bypass, old.stabilize and old.strength > 0)
        modes_after = (settings.bypass, settings.stabilize and settings.strength > 0)
        self.look_settings = settings
        self.look_revision += 1
        if modes_before != modes_after:
            self._invalidate_output_locked()

    def set_raw_output(self, enabled):
        with self.lock:
            if self.raw_output == enabled: return
            self.raw_output = enabled
            self._invalidate_output_locked()
        self.raw_button.setChecked(enabled)
        self.raw_button.setText('보정 복귀' if enabled else '원본 출력')
        self.update_draft_status()
        self.log('HDMI 원본 출력 · 보정 편집값 유지' if enabled else 'HDMI에 적용된 보정으로 복귀')

    def set_staging(self, enabled):
        # Leaving preview mode discards unapplied changes; it never commits them.
        with self.lock:
            self.staging = enabled
            self.draft_settings = self.look_settings
            self.draft_revision += 1
            self.latest_preview = None
        self.grading.apply(self.look_settings)
        self.update_draft_status()

    def apply_draft(self):
        if not self.staging: return
        with self.lock:
            if self.look_settings != self.draft_settings:
                self._commit_look_locked(self.draft_settings)
                self.latest_preview = None
        self.save_timer.start()
        self.update_draft_status()

    def cancel_draft(self):
        if self.staging:
            self.grading.apply(self.look_settings)
            self.preview_fault = None
            self.update_draft_status()

    def update_draft_status(self):
        dirty = self.draft_settings != self.look_settings
        self.apply_button.setEnabled(self.staging and dirty)
        self.cancel_button.setEnabled(self.staging and dirty)
        self.draft_actions.setVisible(self.staging)
        self.draft_status.setText(('미적용 변경 있음 · HDMI 유지 중' if dirty else '출력과 같은 보정값')
            if self.staging else ('모든 보정 꺼짐' if self.look_settings.bypass else '변경값을 출력에 바로 반영'))
        if self.raw_output:
            self.draft_status.setText('HDMI 원본 출력 중 · '+('미적용 변경 있음' if dirty else '보정값 보관 중'))
        self.update_preview_context()

    def update_preview_context(self):
        if not hasattr(self, 'preview_mode'): return
        parts = ['미리보기']
        if self.preview_mode.currentIndex() == 2:
            parts.append('꺼짐')
        else:
            if self.compare.isChecked() or self.crop_control.isChecked():
                parts.append('원본')
            elif self.staging and self.draft_settings != self.look_settings:
                parts.append('미적용 보정')
            elif self.staging and self.raw_output:
                parts.append('검토용 보정')
            if self.zebra_control.isChecked(): parts.append('노출 표시')
            if self.crop_control.isChecked(): parts.append('크롭 기준선')
        if self.raw_output: parts.append('HDMI 원본 출력 중')
        self.preview_context.setText(' · '.join(parts))

    def update_preview_policy(self, *_):
        if not hasattr(self, 'preview_mode'): return
        with self.lock:
            self.preview_active = not self.isMinimized() and self.preview_mode.currentIndex() != 2
            self.preview_interval = 1/15 if self.preview_mode.currentIndex() == 1 else 0.
            self.preview_original = self.compare.isChecked()
            self.preview_crop = self.crop_control.isChecked()
            self.preview_epoch += 1
            self.latest_preview = None
        if not self.preview_active and hasattr(self, 'preview'):
            self.preview.clear('미리보기 꺼짐 · 출력은 계속됩니다')
        self.update_preview_context()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self.update_preview_policy()
            self.schedule_control_placement()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_control_placement()

    def watch_control_screen(self, screen):
        screen.geometryChanged.connect(self.schedule_control_placement)
        screen.availableGeometryChanged.connect(self.schedule_control_placement)
        screen.geometryChanged.connect(self.schedule_display_refresh)
        screen.refreshRateChanged.connect(self.schedule_display_refresh)
        self.schedule_control_placement()

    def schedule_display_refresh(self,*_):
        if hasattr(self,'display_refresh_timer'):
            self.display_retry_count=0
            self.display_refresh_timer.start()

    def schedule_control_placement(self, *_):
        if hasattr(self,'placement_timer'): self.placement_timer.start(0)

    def ensure_control_visible(self):
        if not self.isVisible() or self.isMinimized() or self.isMaximized(): return
        primary = QApplication.primaryScreen()
        screens = QApplication.screens()
        if primary is not None:
            screens = [primary]+[screen for screen in screens if screen!=primary]
        frame = self.frameGeometry()
        decoration = frame.size()-self.size()
        minimum = self.minimumSize().expandedTo(self.minimumSizeHint())+decoration
        target = visible_control_rect(frame,minimum,[screen.availableGeometry() for screen in screens])
        if target != frame:
            self.resize(target.size()-decoration)
            self.move(target.topLeft())

    def toggle_trace(self):
        if self.trace.active:
            self.finish_trace()
        else:
            self.trace.start()
            self.trace_start_stats = dict(self.usb_stats)
            self.trace_end = time.monotonic()+30
            self.trace_report = None
            self.trace_button.setText('타이밍 진단 종료')
            self.trace_save.setEnabled(False)
            self.trace_status.setText('30초 기록 중 · 실제 출력 그리기는 출력 창이 보일 때 측정')

    def finish_trace(self):
        self.trace.stop()
        self.trace_report = self.trace.report()
        keys = ('video_bytes','parsed_frames','frames','decode_errors','retries','invalid_packets')
        reset = any(self.usb_stats.get(k,0)<self.trace_start_stats.get(k,0) for k in keys)
        self.trace_report['usb_counters_reset_during_capture'] = reset
        self.trace_report['usb_delta'] = {k:max(0,self.usb_stats.get(k,0)-self.trace_start_stats.get(k,0)) for k in keys}
        self.trace_report['source_mode'] = self.mode
        self.trace_report['preview_mode'] = self.preview_mode.currentText()
        self.trace_button.setText('타이밍 진단 · 30초 시작')
        self.trace_save.setEnabled(True)
        summary = self.trace_report['summary']
        def fmt(key):
            result = summary[key]['p95']
            return '—' if result is None else f'{result:.1f}'
        self.trace_status.setText(f"진단 완료 · p95 대기 {fmt('queue_ms')} / 처리 {fmt('processing_ms')} / 출력 전달 {fmt('ready_to_submit_ms')} ms")

    def save_trace(self):
        if self.trace_report is None: return
        path, _ = QFileDialog.getSaveFileName(self,'타이밍 진단 저장',
            time.strftime('Goggles-timing-%Y%m%d-%H%M%S.json'),'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.trace_report,ensure_ascii=False,indent=2),encoding='utf-8')
                self.log('타이밍 진단 저장 완료')
            except OSError:
                self.log('타이밍 진단 저장 실패 · 저장 위치를 확인하세요.')

    def refresh_stutter(self):
        if self.mode in ('usb','stream'):
            self.stutter.poll(display_expected=self.output.isVisible() and not self.output.isMinimized())
        state = self.stutter.status()
        self.stutter_summary = f" · 짧은 끊김 {state['count']}회" if state['count'] else ''
        if state['pending']:
            self.stutter_summary = f" · {state['label']} 감지"
        if self.stutter_export is not None and not self.stutter_export.busy:
            self.log('끊김 기록 저장 실패 · 저장 위치를 확인하세요.' if self.stutter_export.last_error
                     else '끊김 시간 기록 저장 완료')
            self.stutter_export = None
        self.stutter_save.setEnabled(state['count']>0 and self.stutter_export is None)
        text = '자동 감지 대기 · 최근 5건의 시간 정보만 보관'
        if state['label']:
            text = f"{state['label']} · {state['gap_ms']:.0f} ms · "+('전후 기록 중' if state['pending'] else f"누적 {state['count']}회")
        if state['revision'] != self.stutter_revision:
            self.stutter_revision = state['revision']
            self.log(f"짧은 끊김 기록 완료 · {state['label']} · {state['gap_ms']:.0f} ms")
            if self.incident_store is not None:
                self.incident_store.submit(self.stutter.report())
        error = self.incident_store.last_error if self.incident_store is not None else None
        if error:
            text += ' · 자동 저장 실패 (메모리 기록 유지)'
            if error != self.stutter_save_error:
                self.log('끊김 기록 자동 저장 실패 · 진단에서 별도 저장할 수 있습니다.')
        self.stutter_save_error = error
        self.stutter_status.setText(text)

    def save_stutter(self):
        if self.stutter_export is not None: return
        report = self.stutter.report()
        if not report['incidents']: return
        path, _ = QFileDialog.getSaveFileName(self,'끊김 기록 저장',
            time.strftime('Goggles-stutter-%Y%m%d-%H%M%S.json'),'JSON (*.json)',
            options=QFileDialog.DontUseNativeDialog)
        if path:
            self.stutter_export = IncidentStore(path)
            self.stutter_export.submit(report)
            self.stutter_save.setEnabled(False)
            self.log('끊김 시간 기록 저장 중 · 영상 출력 유지')

    def log(self, message):
        self.logs.append(time.strftime('%H:%M:%S') + '  ' + message)
        self.summary_status.setToolTip(message + '\n클릭하여 상세 진단 열기')

    def show_settings(self, tab=0):
        self.settings_tabs.setCurrentIndex(tab)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def toggle_connection(self):
        if self.mode != 'idle' or (self.worker and self.worker.is_alive()):
            self.stop()
        else:
            self.start_usb()
        self.refresh_actions()

    def toggle_output(self):
        if (sys.platform=='darwin' and self.output.locked and not self.output_pending
                and (not self.output.isFullScreen() or not self.output.isVisible())
                and self.output.output_screen in QApplication.screens()):
            self.output.present(self.output.output_screen)
            self.output.activateWindow()  # Explicit user action, never automatic repair.
            self.observe_output('manual_restore')
            self.refresh_actions()
            return
        if self.output.locked or self.output_pending:
            self.release_output()
        else:
            self.open_output()
        self.refresh_actions()

    def refresh_actions(self):
        busy = bool(self.worker and self.worker.is_alive())
        stopping = busy and self.mode == 'idle'
        active = self.mode != 'idle' or busy
        self.usb_button.setText('종료 중…' if stopping else
            ('연결 해제' if self.mode == 'usb' else ('재생 중지' if active else '고글 연결')))
        self.usb_button.setEnabled(not stopping)
        self.play_button.setEnabled(not busy)
        self.pattern_button.setEnabled(not busy)
        self.stop_button.setEnabled(active)
        output_active = bool(self.output.locked or self.output_pending)
        restore = sys.platform=='darwin' and self.output.locked and (not self.output.isFullScreen() or not self.output.isVisible())
        self.output_button.setText('대기 취소' if self.output_pending else ('출력 복원' if restore else ('출력 종료' if self.output.locked else '출력 시작')))
        self.output_button.setEnabled(output_active or self.screens.count() > 0)
        self.screens.setEnabled(not output_active)
        live = bool(self.last_frame and time.monotonic()-self.last_frame < 2 and self.mode != 'idle')
        badge = '영상 수신 중' if live else ('연결 중…' if self.mode == 'usb' else '연결 대기')
        if self.mode in ('stream', 'pattern'): badge = '테스트 재생'
        self.connection_badge.setText(badge)

    def refresh_screens(self, *_):
        old = self.screens.currentData()
        if old is None and self.settings:
            old = self.settings.value('screenName', '')
        self.screens.clear()
        self.display_modes=[]
        for i, screen in enumerate(QApplication.screens()):
            reason='ok'
            if sys.platform=='darwin':
                from platform_support import mac_display_info
                info=mac_display_info(screen);width,height=info['pixels'] or (0,0);reason=info['reason']
            else:width,height=screen_pixel_size(screen)
            self.display_modes.append((screen,{'width':width,'height':height,'mode_reason':reason}))
            name = screen.model().strip() or f'화면 {i+1}'
            dimensions = f'{width}×{height} 실제 픽셀' if width and height else '픽셀 모드 확인 불가'
            self.screens.addItem(f'{name} · {dimensions}', screen.name())
            self.screens.setItemData(i, f'{screen.name()} · {dimensions}', Qt.ToolTipRole)
        idx = self.screens.findData(old)
        if idx >= 0:
            self.screens.setCurrentIndex(idx)
        elif self.screens.count() > 1:
            self.screens.setCurrentIndex(1)
        self.refresh_actions()
        self.observe_output('topology')
        if sys.platform=='darwin' and any(info['mode_reason']!='ok' for _,info in self.display_modes):
            if self.display_retry_count<3:
                self.display_retry_count+=1
                self.display_refresh_timer.start(250)
        else:self.display_retry_count=0

    def screen_removed(self, removed):
        selected = self.output.output_screen is removed if sys.platform=='darwin' else self.output_target == removed.name()
        if selected and self.output.locked:
            self.output_pending = self.output_target
            self.output.release()
            self.output_release_reason='screen_removed'
            self.observe_output('screen_removed')
            self.log('출력 화면 분리 · 같은 화면이 다시 연결될 때까지 출력 대기')
        self.refresh_screens()

    def screen_added(self, screen):
        self.refresh_screens()
        self.observe_output('screen_added')
        same=[s for s in QApplication.screens() if s.name()==self.output_pending]
        if self.output_pending == screen.name() and (sys.platform!='darwin' or len(same)==1):
            index = self.screens.findData(screen.name())
            self.screens.setCurrentIndex(index)
            QTimer.singleShot(300, self.restore_output)

    def restore_output(self):
        same=[s for s in QApplication.screens() if s.name()==self.output_pending]
        if (self.output_pending and self.screens.findData(self.output_pending) >= 0
                and (sys.platform!='darwin' or len(same)==1)):
            self.screens.setCurrentIndex(self.screens.findData(self.output_pending))
            self.open_output()

    def open_output(self):
        screens = QApplication.screens()
        index = self.screens.currentIndex()
        if not 0 <= index < len(screens):
            return
        if not self.output.lock_output():
            self.record_component_issue('output','GH-OUTPUT','output')
            self.log('Ctrl+D가 다른 프로그램에 등록되어 전체화면을 열지 못했습니다. 단축키 충돌을 해제하세요.')
            return
        if not self.output.keep_awake(True):
            self.log('화면 꺼짐 방지 요청 실패. 전원 설정을 확인하세요.')
        self.output_target = screens[index].name()
        width,height = screen_pixel_size(screens[index])
        self.support_output = {'width':width,'height':height,'refresh_hz':screens[index].refreshRate()}
        self.component_issues.pop('output',None)
        self.output_pending = None
        if sys.platform=='darwin':self.output.present(screens[index])
        else:
            self.output.setGeometry(screens[index].geometry())
            self.output.show()
            self.output.windowHandle().setScreen(screens[index])
            self.output.showFullScreen()
        if not self.output.property('screen_signal_connected'):
            self.output.windowHandle().screenChanged.connect(lambda *_:self.observe_output('window_screen'))
            self.output.setProperty('screen_signal_connected',True)
        self.output.enforce_topmost()
        self.output.activateWindow()
        if self.settings:
            self.settings.setValue('screenName',screens[index].name())
            self.settings.setValue('autoFullscreen',self.auto_fullscreen.isChecked())
        self.log('전체화면 출력 중 · 앱 활성 상태에서 Cmd+D / Escape 또는 출력 종료 버튼으로 해제합니다.' if sys.platform == 'darwin' else
                 '최상단 전체화면 유지 중. 다른 앱을 선택해도 Ctrl+D로 해제할 수 있습니다.')
        self.refresh_actions()
        self.observe_output('open')

    def release_output(self):
        self.output_release_reason='user_release'
        self.output_pending = None
        self.usb_auto_output_pending = False
        self.output.release()
        self.refresh_actions()
        self.observe_output('release')

    def observe_output(self,event):
        screens=QApplication.screens()
        target=self.output.output_screen if sys.platform=='darwin' else next((s for s in screens if s.name()==self.output_target),None)
        index=screens.index(target) if target in screens else -1
        mode=next((info for screen,info in self.display_modes if screen is target),
            {'width':0,'height':0,'mode_reason':'screen_unmatched'})
        handle=self.output.windowHandle()
        actual=handle.screen() if handle else None
        geometry=self.output.geometry()
        self.support_output.update(mode,locked=self.output.locked,visible=self.output.isVisible(),
            fullscreen=self.output.isFullScreen(),minimized=self.output.isMinimized(),pending=bool(self.output_pending),
            requested_screen=index,screen_count=len(screens),window_state=self.output.windowState().value,
            actual_screen=screens.index(actual) if actual in screens else -1,
            fullscreen_repairs=self.output.fullscreen_repairs,release_reason=self.output_release_reason,
            geometry=[geometry.x(),geometry.y(),geometry.width(),geometry.height()])
        self.support_output['refresh_hz']=target.refreshRate() if target in screens else 0
        for key in ('native_fullscreen','native_visible','native_minimized'):self.support_output.pop(key,None)
        if sys.platform=='darwin' and handle:
            from platform_support import mac_window_state
            self.support_output.update(mac_window_state(self.output))
        if event is not None:
            self.output_events.append({'event':event,**{key:self.support_output[key] for key in
                ('locked','visible','fullscreen','window_state','release_reason','requested_screen','actual_screen')},
                **{key:self.support_output[key] for key in ('native_visible','native_fullscreen') if key in self.support_output}})
        self.support_output['events']=list(self.output_events)
        self.support_output['screens']=[{'index':i,'geometry':[s.geometry().x(),s.geometry().y(),s.geometry().width(),s.geometry().height()],
            'dpr':s.devicePixelRatio(),**info} for i,(s,info) in enumerate(self.display_modes[:16])]

    def setup_usb(self):
        if sys.platform == 'darwin':
            QMessageBox.information(self,'Mac USB 연결',
                '고글과 Mac을 USB 데이터 케이블로 직접 연결하세요.\n'
                '고글에서 OTG 유선 컴퓨터 연결과 라이브뷰 공유를 켜세요.\n'
                'DJI Assistant 등 고글을 사용 중인 앱을 종료하고 고글 연결을 누르세요.\n'
                '이 버전은 Apple Silicon 하드웨어 검증 전 빌드입니다.\n'
                'USB 장치 → 인터페이스 → RNDIS → ARP → 영상 패킷 → 디코딩 순으로 확인합니다.')
            return
        import ctypes
        base = Path(__file__).resolve().parent
        script = base/'setup_goggles_network.ps1'
        if not script.is_file():
            self.log('USB 설정 스크립트가 없습니다. 배포 폴더 전체를 압축 해제하세요.')
            return
        args = f'-NoProfile -ExecutionPolicy Bypass -File "{script}"'
        result = ctypes.windll.shell32.ShellExecuteW(None,'runas','powershell.exe',args,str(base),0)
        self.log('Windows 관리자 확인 후 고글 전용 USB 주소를 설정합니다.' if result>32
                 else 'USB 설정이 취소되었거나 실행되지 않았습니다.')

    def start_usb(self):
        if self.worker and self.worker.is_alive():
            self.log('수신 중입니다. 소스를 바꾸려면 정지를 먼저 누르세요.')
            return
        self.stop_event = threading.Event()
        self.usb_stats = {'issue_history':self.support_history,'stage':'discovery'}
        with self.lock:
            self.latest = self.finished = self.usb_note = None
        self.frames = 0
        self.last_frame = 0
        self.started = time.monotonic()
        self.mode = 'usb'
        self.rate_samples.clear()
        self.receive_gaps.clear()
        self.last_received_at = None
        self.video_interruptions = 0
        self.was_live = False
        if self.diag_button.isEnabled():
            self.diagnose()
        self.usb_auto_output_pending = self.auto_fullscreen.isChecked()
        self.clear_video('고글 USB 영상 연결 대기')
        self.play_button.setEnabled(False)
        self.pattern_button.setEnabled(False)
        def state(message):
            with self.lock:
                self.usb_note = message
        self.worker = threading.Thread(target=self.receive_frames,
            args=(decode_goggles(self.stop_event,state,self.usb_stats), self.stop_event),daemon=True)
        self.worker.start()
        self.refresh_actions()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '테스트 영상 선택', '',
            '영상 (*.mp4 *.mov *.mkv *.ts *.h264 *.h265);;모든 파일 (*)')
        if path:
            self.source.setText(path)

    def play(self):
        if self.worker and self.worker.is_alive():
            self.log('기존 수신 종료 중입니다. 잠시 후 다시 누르세요.')
            return
        try:
            source = validate_source(self.source.text())
        except ValueError as exc:
            self.log(str(exc))
            return
        self.stop_event = threading.Event()
        with self.lock:
            self.latest = None
            self.finished = None
        self.frames = 0
        self.started = time.monotonic()
        self.last_frame = 0
        self.mode = 'stream'
        self.rate_samples.clear()
        self.receive_gaps.clear()
        self.last_received_at = None
        self.clear_video('영상 연결 중')
        self.status.setText('표준 영상 소스 연결 중 · Goggles USB 수신 상태가 아닙니다')
        self.play_button.setEnabled(False)
        self.pattern_button.setEnabled(False)
        self.worker = threading.Thread(target=self.receive, args=(source, self.stop_event), daemon=True)
        self.worker.start()
        self.refresh_actions()

    def receive(self, source, stop):
        self.receive_frames(decode_frames(source,stop),stop)

    def receive_frames(self, frames, stop):
        run_pipeline(self, frames, stop)

    def clear_video(self, message='영상 대기'):
        self.preview.clear(message)
        self.latest_original = None
        self.latest_preview = None
        self.output.trace_frame = None
        self.output.clear(message)
        self.last_frame = 0

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self.usb_stats['active_issue'] = None
            self.usb_stats['stage'] = 'idle'
        self.stutter.stop()
        self.mode = 'idle'
        self.refresh_stutter()
        self.usb_auto_output_pending = False
        self.was_live = False
        with self.lock:
            self.latest = None
            self.latest_meta = None
        self.clear_video()
        self.status.setText('정지됨')
        if not self.worker or not self.worker.is_alive():
            self.play_button.setEnabled(True)
            self.pattern_button.setEnabled(True)
            self.usb_button.setEnabled(True)
        self.refresh_actions()

    def pattern(self):
        if self.worker and self.worker.is_alive():
            return
        self.mode = 'pattern'
        self.started = time.monotonic()
        self.status.setText('HDMI 테스트 패턴 · 드론 영상이 아닙니다')
        self.refresh_actions()

    def make_pattern(self):
        image = QImage(1280, 720, QImage.Format_RGB32)
        p = QPainter(image)
        colors = ['#dadada','#d2c931','#37c7c7','#33bd64','#b846bd','#c95050','#3d64c9']
        for i, color in enumerate(colors):
            p.fillRect(i*1280//7, 0, (i+1)*1280//7-i*1280//7, 720, QColor(color))
        p.fillRect(0, 480, 1280, 240, QColor('#0d1522'))
        p.setPen(QColor('white'))
        p.setFont(QFont('Malgun Gothic', 28, QFont.Bold))
        p.drawText(QRect(40, 500, 1200, 80), Qt.AlignCenter, 'HDMI OUTPUT TEST · 드론 영상 아님')
        p.setFont(QFont('Malgun Gothic', 22))
        p.drawText(QRect(40, 590, 1200, 80), Qt.AlignCenter,
                   f'1280 × 720   |   {time.monotonic()-self.started:08.2f}s   |   Ctrl+D: 출력 종료')
        p.end()
        return image

    def tick(self):
        now = time.monotonic()
        if self.trace.active and now >= self.trace_end:
            self.finish_trace()
        if now-self.last_rate_update >= 1:
            self.update_health(now)
        update_status = now-self.last_status_update >= .25
        if update_status:
            self.last_status_update = now
            self.refresh_stutter()
            self.refresh_actions()
            stats = self.processing_stats
            self.grading.performance.setText(
                f"처리 {stats['ms']:.1f} ms · 건너뜀 {stats['dropped']} · 크롭 {stats.get('crop', 0)*100:.1f}%\n{self.preview_fault or self.effect_fault or stats['tracking']}")
        with self.lock:
            frame, self.latest = self.latest, None
            original, self.latest_original = self.latest_original, None
            sequence, self.latest_meta = self.latest_meta, None
            preview_result, self.latest_preview = self.latest_preview, None
            ended, self.finished = self.finished, None
            diag, self.diag_result = self.diag_result, None
            usb_note, self.usb_note = self.usb_note, None
        if usb_note is not None and self.mode == 'usb':
            self.status.setText(usb_note)
            if usb_note != self.last_usb_note:
                self.log(usb_note)
                self.last_usb_note = usb_note
        if diag is not None:
            self.diag_report = diag
            self.diag_button.setEnabled(True)
            self.log(diag)
            try:
                devices = json.loads(diag).get('DJIDevices', [])
                self.usb_device_state = ('인식됨' if any(d.get('ErrorCode') == 0 for d in devices)
                                         else ('장치 오류' if devices else '미인식'))
            except (ValueError, TypeError):
                self.usb_device_state = '확인 실패' 
        if self.mode == 'pattern':
            frame = self.make_pattern()
        if frame is not None and self.mode != 'idle':
            # Output submission stays independent of preview rate and diagnostics overlays.
            self.output.trace_frame = sequence
            if sequence is not None:
                self.trace.add('submit',sequence)
            self.output.set_frame(frame)
            if (self.preview_active and not (self.staging or self.preview_original or self.preview_crop)
                    and now-self.last_preview_draw >= self.preview_interval):
                self.preview.zebra = self.zebra_control.isChecked()
                self.preview.crop_guide = 0.
                self.preview.set_frame(frame)
                self.last_preview_draw = now
            self.last_frame = time.monotonic()
            self.was_live = True
            if self.mode == 'usb':
                if update_status:
                    self.status.setText(f'Goggles 3 USB · {frame.width()}×{frame.height()} · {self.frames}프레임')
                if self.usb_auto_output_pending:
                    self.usb_auto_output_pending = False
                    self.open_output()
            elif self.mode == 'stream' and update_status:
                self.status.setText(f'표준 영상 재생 · {frame.width()}×{frame.height()} · {self.frames}프레임')
        if (preview_result is not None and self.mode != 'idle' and self.preview_active
                and preview_result[2] == self.preview_key()):
            self.preview.zebra = self.zebra_control.isChecked()
            self.preview.crop_guide = preview_result[1] if self.preview_crop else 0.
            self.preview.set_frame(preview_result[0])
            self.last_preview_draw = now
        if ended is not None:
            self.stutter.stop()
            self.mode = 'idle'
            self.refresh_stutter()
            self.clear_video('영상 대기')
            self.status.setText(ended)
            self.log(ended)
            self.play_button.setEnabled(True)
            self.pattern_button.setEnabled(True)
            self.usb_button.setEnabled(True)
        if self.mode in ('stream','usb') and self.last_frame and time.monotonic()-self.last_frame > 2:
            if self.was_live:
                self.video_interruptions += 1
                self.was_live = False
            self.clear_video('영상 신호 대기')
            self.status.setText('2초 이상 새 프레임 없음 · 마지막 화면을 숨겼습니다')

    def update_health(self, now):
        self.last_rate_update = now
        received, processed = self.frames, self.processing_stats['processed']
        if self.rate_samples and received < self.rate_samples[-1][1]:
            self.rate_samples.clear()
        self.rate_samples.append((now, received, processed))
        while len(self.rate_samples) > 1 and now-self.rate_samples[0][0] > 3.5:
            self.rate_samples.popleft()
        elapsed = now-self.rate_samples[0][0]
        rx = (received-self.rate_samples[0][1])/elapsed if elapsed > .5 else 0
        fx = max(0, processed-self.rate_samples[0][2])/elapsed if elapsed > .5 else 0
        live = bool(self.last_frame and now-self.last_frame < 2)
        packet_age = now-self.usb_stats.get('last_packet_time', 0)
        communicating = self.mode == 'usb' and packet_age < 3
        usb = '인식됨' if communicating else self.usb_device_state
        needs_restore = sys.platform=='darwin' and self.output.locked and (not self.output.isVisible() or not self.output.isFullScreen())
        output = '출력 복원 필요' if needs_restore else ('출력 중' if self.output.locked else ('모니터 재연결 대기' if self.output_pending else '꺼짐'))
        self.pipeline_status.setText(f'USB {usb} → 고글 통신 {"정상" if communicating else "대기"} → 영상 {"수신 중" if live else "대기"} → {output}')
        screen = next((s for s in QApplication.screens() if s.name() == self.output_target), None)
        hz = f'{screen.refreshRate():.2f}' if screen and self.output.locked else '—'
        self.rate_status.setText(f'수신 {rx:.1f} fps · 처리 {fx:.1f} fps · 출력 화면 {hz} Hz · 2초 이상 끊김 {self.video_interruptions}회')
        gaps = [gap for stamp, gap in list(self.receive_gaps) if now-stamp < 3]
        if gaps:
            self.rate_status.setText(self.rate_status.text()+f' · 수신 최대 간격 {max(gaps)*1000:.0f} ms')
        self.rate_status.setToolTip('최근 약 3초 평균입니다. 화면 주사율은 Qt 보고값이며 고유 영상 FPS나 전체 지연이 아닙니다.')
        frame = self.output.frame
        dimensions = f'{frame.width()}×{frame.height()}' if live and not frame.isNull() else '영상 대기'
        source = '고글' if self.mode == 'usb' else ('테스트' if self.mode in ('stream','pattern') else '수신 대기')
        fault = ' · 보정 오류: 진단 확인' if self.effect_fault or self.preview_fault else ''
        output_summary = 'HDMI 출력 복원 필요' if needs_restore else ('HDMI 출력 중' if self.output.locked else ('모니터 재연결 대기' if self.output_pending else 'HDMI 꺼짐'))
        if self.raw_output: output_summary += ' · 원본'
        issue = self.usb_stats.get('active_issue')
        issue_summary = (' · ['+issue['code']+'] '+issue['message']) if issue and self.mode == 'usb' else ''
        self.summary_status.setText(f'{source} · {dimensions} · 수신 {rx:.1f} fps · {output_summary}{fault}{self.stutter_summary}{issue_summary}    ›')

    def diagnose(self):
        self.diag_button.setEnabled(False)
        self.log('USB 장치와 후보 네트워크 어댑터를 읽는 중…')
        def task():
            try:
                data = device_diagnostics()
                with self.lock:
                    self.cached_diagnosis = data
                text = json.dumps(data, ensure_ascii=False, indent=2)
            except Exception as exc:
                text = '진단 실패: ' + type(exc).__name__
            with self.lock:
                self.diag_result = text
        threading.Thread(target=task, daemon=True).start()

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, '진단 결과 저장',
            time.strftime("%Y-%m-%d %H'%M 고글 연결 진단.txt"), '텍스트 (*.txt)')
        if path:
            try:
                Path(path).write_text(self.problem_report(), encoding='utf-8')
                self.log('진단 결과를 저장했습니다.')
            except OSError:
                self.log('저장 실패. 쓰기 가능한 경로를 선택하세요.')

    def record_component_issue(self,component,code,stage):
        with self.lock:
            self.component_issues[component] = issue_record(code,stage)
            record = self.component_issues[component]
        self.log('['+record['code']+'] '+record['message'])

    def problem_report(self):
        # Cheap GUI-window state only; no USB/display enumeration or subprocess.
        self.observe_output(None)
        with self.lock:
            stats = dict(self.usb_stats)
            stats['issue_history'] = list(self.support_history)
            if not stats['issue_history']:
                stats['issue_history'] = list(self.usb_stats.get('issue_history',[]))
            diagnosis = self.cached_diagnosis
            components = dict(self.component_issues)
            output = dict(self.support_output)
            last_frame,mode = self.last_frame,self.mode
        return build_report(version=CONFIG['version'],build_revision=CONFIG.get('build_revision'),mode=mode,stats=stats,diagnosis=diagnosis,
                            components=components,output=output,last_frame=last_frame)

    def copy_problem_info(self):
        QApplication.clipboard().setText(self.problem_report())
        self.log('문제 정보 복사됨 · 채팅에 붙여넣으세요')
        self.statusBar().showMessage('문제 정보 복사됨 · 채팅에 붙여넣으세요',5000)

    def show_guide(self):
        if sys.platform == 'darwin':
            QMessageBox.information(self,'Mac 사용 안내',
                'USB 설정 버튼에서 고글 연결 안내를 확인하세요.\n'
                'macOS 시스템 설정 → 디스플레이에서 HDMI 화면을 확장하고 출력 화면을 선택하세요.\n'
                '앱 활성 상태에서 Cmd+D / Ctrl+D / Escape 또는 출력 종료 버튼으로 해제합니다.\n'
                'Mac에서 고글 USB·외부 화면 실기기 검증은 아직 필요합니다.')
            return
        QMessageBox.information(self, '현재 범위와 다음 단계',
            'Avata 2와 Goggles 3를 연결하고 고글의 라이브뷰 공유를 켜세요.\n'
            '외부 Wi-Fi나 인터넷은 사용하지 않습니다. PC와 고글은 USB 데이터 케이블로 연결합니다.\n'
            '새 PC에서는 USB 최초 설정을 한 번 실행하세요. 고글 전용 어댑터에만 적용됩니다.\n'
            '고글에서 OTG 컴퓨터 연결과 라이브뷰 공유를 켠 뒤 상단의 고글 연결을 누르세요.\n'
            '수신 중 연결이 끊기면 자동 재시도합니다.\n\n'
            'HDMI: Win+P 확장 → 출력 화면 선택. Ctrl+D로 전체화면을 해제합니다.\n'
            '정지 / 신호 끊김에도 출력 창은 유지됩니다.\n'
            '고글 전원을 다시 켰을 때 공유 설정 유지 여부를 확인하세요.')

    def closeEvent(self, event):
        if self.output.locked:
            event.ignore()
            self.log('전체화면 출력 중입니다. 먼저 Ctrl+D로 출력을 해제하세요.')
            return
        self.save_look()
        self.stop_event.set()
        self.stutter.stop()
        self.refresh_stutter()
        writer = self.incident_store.worker if self.incident_store is not None else None
        if writer is not None:
            writer.join(.25)
        if self.settings:
            self.settings.setValue('autoFullscreen',self.auto_fullscreen.isChecked())
            self.settings.setValue('screenName',self.screens.currentData())
        self.output.close()
        self.watermark.timer.stop()
        self.program_panel.shutdown()
        event.accept()


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('GogglesHDMI.Next.FPV')
    app = QApplication(sys.argv)
    app.setApplicationVersion(CONFIG['version'])
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets' / 'fpv-drone.ico')))
    app.setStyle('Fusion')
    from ui_theme import STYLE
    app.setStyleSheet(STYLE)
    if '--foreground-helper' in sys.argv:
        from selftest import run_foreground_helper
        helper = run_foreground_helper(app)
        return app.exec()
    testing = any(flag in sys.argv for flag in ('--self-test', '--live-test', '--upgrade-test','--mac-smoke'))
    window = MainWindow(auto_start=not testing)
    window.show()
    if '--mac-smoke' in sys.argv:
        from macos_smoke import run
        output = sys.argv[sys.argv.index('--mac-smoke')+1]
        QTimer.singleShot(100,lambda:run(app,window,output))
    elif '--observe' in sys.argv:
        from liveqa import observe_live
        observe_live(app, window)
    if '--upgrade-test' in sys.argv:
        from nextqa import run
        QTimer.singleShot(100, lambda: run(app, window))
    elif '--self-test' in sys.argv:
        from selftest import run_selftest
        QTimer.singleShot(100, lambda: run_selftest(app, window))
    elif '--live-test' in sys.argv:
        from liveqa import run_liveqa
        QTimer.singleShot(100, lambda: run_liveqa(app, window))
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
