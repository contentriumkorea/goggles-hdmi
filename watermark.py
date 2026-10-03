"""Shared, lightweight preview/HDMI overlay clock, independent of video processing."""
import math
import time
from pathlib import Path
import numpy as np
from PySide6.QtCore import QObject, QTimer, Qt, QRect, Signal
from PySide6.QtGui import QImage, QPainter


def watermark_opacity(elapsed):
    phase = max(0., elapsed) % 30.
    if phase >= 5.:
        return 0.
    amount = min(1., phase/.75, (5.-phase)/.75)
    return amount*amount*(3.-2.*amount)


class Watermark(QObject):
    changed = Signal()

    def __init__(self, license_state, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.license = license_state
        self.clock = clock
        self.origin = clock()
        self.opacity = 0.
        logo = QImage(str(Path(__file__).parent/'assets/contentrium-white.png'))
        if logo.isNull():
            raise RuntimeError('콘텐츠리움 로고 파일을 읽을 수 없습니다. 프로그램을 다시 설치하세요.')
        rgba = logo.convertToFormat(QImage.Format_RGBA8888)
        pixels = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
        alpha = pixels[:, 3:rgba.width()*4:4]
        yy, xx = np.where(alpha > 0)
        self.logo = logo.copy(int(xx.min()), int(yy.min()), int(xx.max()-xx.min()+1), int(yy.max()-yy.min()+1))
        self.cache = {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.license.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self.timer.stop()
        elapsed = max(0., self.clock()-self.origin)
        self.opacity = 0. if self.license.unlocked else watermark_opacity(elapsed)
        self.changed.emit()
        if self.license.unlocked:
            return
        phase = elapsed % 30.
        # Only animate during fades; held/hidden intervals need no extra repaint.
        delay = 16 if phase < .75 or 4.25 <= phase < 5. else math.ceil(
            ((4.25-phase) if phase < 4.25 else (30.-phase))*1000)
        self.timer.start(max(1, delay))

    def paint(self, painter, video_rect):
        if self.opacity <= 0 or self.license.unlocked:
            return
        width = max(1, round(video_rect.width()*.30))
        if width not in self.cache:
            if len(self.cache) >= 6:
                self.cache.clear()
            self.cache[width] = self.logo.scaledToWidth(width, Qt.SmoothTransformation)
        logo = self.cache[width]
        target = QRect(0, 0, logo.width(), logo.height())
        target.moveCenter(video_rect.center())
        painter.save()
        painter.setOpacity(self.opacity)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(target, logo)
        painter.restore()
