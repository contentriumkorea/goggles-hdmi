"""Quiet periodic release checks; network work never runs on the GUI thread."""
import threading
from PySide6.QtCore import QObject, QTimer, Signal
from updates import check_online


class UpdateMonitor(QObject):
    available = Signal(object)
    completed = Signal(int, object)

    def __init__(self, parent=None, fetch=check_online):
        super().__init__(parent)
        self.fetch = fetch
        self.url = ''
        self.enabled = False
        self.checking = False
        self.generation = 0
        self.announced = set()
        self.timer = QTimer(self)
        self.timer.setInterval(15*60*1000)
        self.timer.timeout.connect(self.check_now)
        self.initial = QTimer(self)
        self.initial.setSingleShot(True)
        self.initial.timeout.connect(self.check_now)
        self.completed.connect(self.finished)

    def configure(self, url, enabled):
        self.generation += 1
        self.url, self.enabled = url, bool(enabled and url)
        self.timer.stop()
        self.initial.stop()
        if self.enabled:
            self.timer.start()
            self.initial.start(3000)

    def check_now(self):
        if not self.enabled or self.checking:
            return
        self.initial.stop()
        self.checking = True
        generation, url = self.generation, self.url
        def work():
            try:
                release = self.fetch(url)
            except Exception:
                release = None  # Offline checks retry on the next timer, without disrupting video.
            try:
                self.completed.emit(generation, release)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def finished(self, generation, release):
        self.checking = False
        if generation != self.generation or not self.enabled or release is None:
            return
        identity = (self.url, release['version'])
        if identity not in self.announced:
            self.announced.add(identity)
            self.available.emit(release)
