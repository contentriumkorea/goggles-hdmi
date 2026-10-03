"""Color controls and an editable RGB curve graph."""
import json
from dataclasses import asdict
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QShortcut, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QComboBox, QPushButton, QCheckBox, QFileDialog, QMessageBox, QDoubleSpinBox, QInputDialog,
    QTabWidget, QToolButton, QMenu)
from goggles_workflow import LookHistory
from effects import Settings, IDENTITY, curve_values, crop_fraction


class CurveEditor(QWidget):
    changed = Signal()
    edit_started = Signal()
    edit_finished = Signal()

    def __init__(self):
        super().__init__()
        self.curves = [list(IDENTITY) for _ in range(4)]
        self.history = []
        self.channel = 0
        self.selected = None
        self.setMinimumSize(260, 220)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip('클릭: 점 추가 · 드래그: 이동 · 우클릭/Delete: 점 삭제')

    def graph(self):
        return QRectF(22, 12, self.width()-36, self.height()-36)

    def pos(self, point):
        r = self.graph()
        return QPointF(r.left()+point[0]*r.width(), r.bottom()-point[1]*r.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.graph()
        p.fillRect(self.rect(), QColor('#181818'))
        p.setPen(QPen(QColor('#363636'), 1))
        for i in range(5):
            x, y = r.left()+r.width()*i/4, r.top()+r.height()*i/4
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(QColor('#707070'), 1, Qt.DashLine))
        p.drawLine(r.bottomLeft(), r.topRight())
        colors = ['#eeeeee', '#ff727e', '#6edd9b', '#72a7ff']
        x = np.linspace(0, 1, 256)
        y = curve_values(self.curves[self.channel], x)
        path = QPainterPath(self.pos((x[0], y[0])))
        for point in zip(x[1:], y[1:]):
            path.lineTo(self.pos(point))
        p.setPen(QPen(QColor(colors[self.channel]), 2))
        p.drawPath(path)
        for i, point in enumerate(self.curves[self.channel]):
            p.setBrush(QColor('#f9ba66' if i == self.selected else colors[self.channel]))
            p.drawEllipse(self.pos(point), 4.5, 4.5)

    def mouseReleaseEvent(self, event):
        self.edit_finished.emit()
        super().mouseReleaseEvent(event)

    def nearest(self, pos):
        points = self.curves[self.channel]
        distances = [(self.pos(v)-pos).manhattanLength() for v in points]
        i = int(np.argmin(distances))
        return i if distances[i] < 18 else None

    def mousePressEvent(self, event):
        self.edit_started.emit()
        self.setFocus()
        self.remember()
        self.selected = self.nearest(event.position())
        if event.button() == Qt.RightButton:
            self.delete_point()
            return
        if event.button() != Qt.LeftButton:
            return
        if self.selected is None and self.graph().contains(event.position()):
            r = self.graph()
            x = (event.position().x()-r.left())/r.width()
            points = self.curves[self.channel]
            if len(points) < 16 and all(abs(x-v[0]) > .015 for v in points):
                points.append((x, (r.bottom()-event.position().y())/r.height()))
                points.sort()
                self.selected = next(i for i, v in enumerate(points) if v[0] == x)
                self.changed.emit()
        self.update()

    def mouseMoveEvent(self, event):
        if self.selected is None or not event.buttons() & Qt.LeftButton:
            return
        r = self.graph()
        points, i = self.curves[self.channel], self.selected
        x = (event.position().x()-r.left())/r.width()
        y = max(0., min(1., (r.bottom()-event.position().y())/r.height()))
        if i in (0, len(points)-1):
            x = points[i][0]
        else:
            x = max(points[i-1][0]+.01, min(points[i+1][0]-.01, x))
        points[i] = (x, y)
        self.changed.emit()
        self.update()

    def remember(self):
        snapshot = tuple(tuple(c) for c in self.curves)
        if not self.history or self.history[-1] != snapshot:
            self.history.append(snapshot)
            self.history = self.history[-40:]

    def undo(self):
        current = tuple(tuple(c) for c in self.curves)
        while self.history:
            previous = self.history.pop()
            if previous != current:
                self.curves = [list(c) for c in previous]
                self.selected = None
                self.changed.emit()
                self.update()
                return

    def delete_point(self):
        points = self.curves[self.channel]
        if self.selected is not None and 0 < self.selected < len(points)-1:
            self.remember()
            points.pop(self.selected)
            self.selected = None
            self.changed.emit()
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            self.undo()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_point()
        else:
            super().keyPressEvent(event)


def validate_preset(data):
    if data.get('version') != 1:
        raise ValueError('지원하지 않는 프리셋 버전입니다.')
    s = data['settings']
    temperature, exposure, strength = float(s['temperature']), float(s['exposure']), float(s['strength'])
    if not (-100 <= temperature <= 100 and -3 <= exposure <= 3 and 0 <= strength <= 1):
        raise ValueError('조절값 범위가 올바르지 않습니다.')
    curves = tuple(tuple(tuple(float(v) for v in p) for p in c) for c in s['curves'])
    if len(curves) != 4:
        raise ValueError('RGB 커브 네 개가 필요합니다.')
    for c in curves:
        if not (2 <= len(c) <= 16 and all(len(p) == 2 for p in c) and
                c[0][0] == 0 and c[-1][0] == 1 and
                all(0 <= v <= 1 for p in c for v in p) and
                all(c[i+1][0]-c[i][0] >= .009 for i in range(len(c)-1))):
            raise ValueError('커브 조절점이 올바르지 않습니다.')
    if type(s['stabilize']) is not bool or type(s['bypass']) is not bool:
        raise ValueError('잘못된 활성화 값입니다.')
    max_crop = float(s.get('max_crop', .1))
    adaptive = s.get('adaptive_crop', False)
    if not 0 <= max_crop <= .1 or type(adaptive) is not bool:
        raise ValueError('크롭 설정 범위가 올바르지 않습니다.')
    return Settings(temperature, exposure, curves, s['stabilize'], strength, s['bypass'], max_crop, adaptive)


class GradingPanel(QWidget):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.history = LookHistory(Settings())
        self._applying = False
        self.setFixedWidth(326)
        self.setObjectName('gradingPanel')
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(14)
        self.mode_row = QHBoxLayout()
        self.mode_row.addWidget(QLabel('영상 보정'), 1)
        layout.addLayout(self.mode_row)
        preset_row = QHBoxLayout()
        self.preset_names = QComboBox()
        self.preset_names.setPlaceholderText('프리셋 선택')
        self.preset_names.setToolTip('선택하면 보정값을 불러옵니다.')
        self.preset_names.activated.connect(self.load_named)
        preset_row.addWidget(self.preset_names, 1)
        self.preset_menu = QToolButton()
        self.preset_menu.setText('⋯')
        self.preset_menu.setToolTip('프리셋 저장 · 가져오기 · 초기화')
        self.preset_menu.setAccessibleName('프리셋 메뉴')
        self.preset_menu.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self)
        for title, callback in [('현재 값 이름으로 저장…', self.save_named),
                                ('파일에서 가져오기…', self.load), ('파일로 내보내기…', self.save)]:
            menu.addAction(title, callback)
        menu.addSeparator()
        menu.addAction('모든 보정 초기화', self.reset)
        self.preset_menu.setMenu(menu)
        preset_row.addWidget(self.preset_menu)
        layout.addLayout(preset_row)
        self.bypass = QCheckBox('모든 보정 끄기')
        self.bypass.setToolTip('색상·커브·안정화 보정을 모두 끕니다. HDMI 출력에도 적용됩니다.')
        layout.addWidget(self.bypass)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        def page(title):
            widget = QWidget()
            widget.setObjectName('gradingPage')
            box = QVBoxLayout(widget)
            box.setContentsMargins(2, 18, 2, 4)
            box.setSpacing(14)
            self.tabs.addTab(widget, title)
            return box
        color = page('색상')
        self.temperature = self.slider(color, '색온도', -100, 100, 0, 1, '', reset=True)
        self.temperature.setToolTip('왼쪽: 차갑게 · 오른쪽: 따뜻하게\n더블클릭: 0으로 초기화')
        self.exposure = self.slider(color, '노출', -300, 300, 0, 100, ' EV', reset=True)
        color.addStretch()
        curves = page('커브')
        self.channels = QComboBox()
        self.channels.addItems(['전체 RGB', 'Red', 'Green', 'Blue'])
        curves.addWidget(self.channels)
        self.editor = CurveEditor()
        self.editor.setToolTip('클릭: 점 추가 · 드래그: 이동 · 우클릭: 삭제\nCtrl+Z: 실행 취소')
        curves.addWidget(self.editor, 1)
        curve_reset = QPushButton('선택 채널 초기화')
        curve_reset.clicked.connect(self.reset_channel)
        curves.addWidget(curve_reset)
        stabilization = page('안정화')
        self.stabilize = QCheckBox('흔들림 보정')
        self.stabilize.setToolTip('실험 기능 · 끄면 추적과 크롭 연산을 완전히 중지합니다.')
        stabilization.addWidget(self.stabilize)
        self.strength = self.slider(stabilization, '보정 강도', 0, 100, 80, 1, '%')
        self.max_crop = self.slider(stabilization, '최대 크롭', 0, 100, 100, 10, '%')
        self.max_crop.setToolTip('가장자리당 크롭 비율입니다. 10%는 가로·세로 길이의 80%를 유지합니다.')
        self.adaptive_crop = QCheckBox('움직임에 맞춰 자동 크롭')
        stabilization.addWidget(self.adaptive_crop)
        self.crop_info = QLabel()
        self.crop_info.setObjectName('muted')
        self.crop_info.setWordWrap(True)
        stabilization.addWidget(self.crop_info)
        stabilization.addStretch()
        # These controls are hosted by the main window's settings dialog.
        self.restore_stabilizer = QCheckBox('다음 실행에도 흔들림 보정 켜짐 복원', self)
        self.restore_stabilizer.hide()
        self.performance = QLabel('처리 대기', self)
        self.performance.setWordWrap(True)
        self.performance.hide()
        self.footer = QVBoxLayout()
        self.footer.setSpacing(8)
        layout.addLayout(self.footer)
        undo_row = QHBoxLayout()
        self.undo_button = QPushButton('실행 취소')
        self.redo_button = QPushButton('다시 실행')
        for button, callback in [(self.undo_button,self.undo_settings),(self.redo_button,self.redo_settings)]:
            button.clicked.connect(callback)
            undo_row.addWidget(button)
        self.undo_button.setToolTip('Ctrl+Z')
        self.redo_button.setToolTip('Ctrl+Shift+Z')
        self.footer.addLayout(undo_row)
        for keys, callback in [('Ctrl+Z',self.undo_settings),('Ctrl+Shift+Z',self.redo_settings)]:
            shortcut = QShortcut(QKeySequence(keys),self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
        self.preset_store = None
        self.channels.currentIndexChanged.connect(self.channel_changed)
        self.editor.changed.connect(self.emit_settings)
        self.editor.edit_started.connect(self.history.begin)
        self.editor.edit_finished.connect(self.history.end)
        for control in [self.bypass, self.stabilize, self.adaptive_crop]:
            control.toggled.connect(self.emit_settings)
        for control in [self.temperature, self.exposure, self.strength, self.max_crop]:
            control.valueChanged.connect(self.emit_settings)
            control.sliderPressed.connect(self.history.begin)
            control.sliderReleased.connect(self.history.end)
        self.update_crop_label()
        self.update_history_buttons()

    def slider(self, layout, title, low, high, initial, divisor, suffix, reset=False):
        label = QLabel()
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.setValue(initial)
        slider.setSingleStep(1)
        if reset:
            for widget in (label, slider):
                widget.setToolTip('더블클릭: 기본값 0으로 초기화')
                widget.installEventFilter(self)
                widget.reset_slider = slider
                widget.reset_value = initial
        def update(v):
            label.setText(title)
        slider.valueChanged.connect(update)
        update(initial)
        row = QHBoxLayout()
        row.addWidget(label, 1)
        number = QDoubleSpinBox()
        number.setRange(low/divisor, high/divisor)
        number.setDecimals(2 if divisor == 100 else (1 if divisor == 10 else 0))
        number.setSingleStep(1/divisor)
        number.setSuffix(suffix)
        number.setValue(initial/divisor)
        number.setKeyboardTracking(False)
        number.valueChanged.connect(lambda v: slider.setValue(round(v*divisor)))
        slider.valueChanged.connect(lambda v: number.setValue(v/divisor))
        slider.number = number
        if reset:
            for widget in (number, number.lineEdit()):
                widget.installEventFilter(self)
                widget.reset_slider = slider
                widget.reset_value = initial
                widget.setToolTip('더블클릭: 기본값 0으로 초기화')
        row.addWidget(number)
        layout.addLayout(row)
        layout.addWidget(slider)
        return slider

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.MouseButtonDblClick and
                event.button() == Qt.LeftButton and hasattr(watched, 'reset_slider')):
            watched.reset_slider.setSliderDown(False)
            watched.reset_slider.setValue(watched.reset_value)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def settings(self):
        return Settings(self.temperature.value(), self.exposure.value()/100,
                        tuple(tuple(c) for c in self.editor.curves), self.stabilize.isChecked(),
                        self.strength.value()/100, self.bypass.isChecked(),
                        self.max_crop.value()/1000, self.adaptive_crop.isChecked())

    def emit_settings(self, *_):
        if self._applying: return
        value = self.settings()
        self.history.record(value)
        self.update_history_buttons()
        self.update_crop_label()
        self.changed.emit(value)

    def update_history_buttons(self):
        self.undo_button.setEnabled(self.history.index > 0)
        self.redo_button.setEnabled(self.history.index < len(self.history.values)-1)

    def undo_settings(self):
        self.apply(self.history.step(-1), record=False)

    def redo_settings(self):
        self.apply(self.history.step(1), record=False)

    def update_crop_label(self):
        enabled = self.stabilize.isChecked() and not self.bypass.isChecked()
        self.strength.setEnabled(enabled)
        self.strength.number.setEnabled(enabled)
        self.max_crop.setEnabled(enabled)
        self.max_crop.number.setEnabled(enabled)
        self.adaptive_crop.setEnabled(enabled)
        crop = self.max_crop.value()/10 if enabled else 0
        self.crop_info.setText(f'크롭 상한 {crop:.1f}% · 가로·세로 최소 {100-2*crop:.0f}% 유지' if enabled else '스테빌라이저 완전 중지 · 크롭 0%')

    def channel_changed(self, index):
        self.editor.channel = index
        self.editor.selected = None
        self.editor.update()

    def reset_channel(self):
        self.editor.remember()
        self.editor.curves[self.editor.channel] = list(IDENTITY)
        self.editor.selected = None
        self.editor.update()
        self.emit_settings()

    def apply(self, settings, record=True):
        self._applying = True
        controls = [self.temperature, self.exposure, self.strength, self.bypass, self.stabilize]
        controls += [self.max_crop, self.adaptive_crop]
        for control in controls:
            control.blockSignals(True)
        self.editor.remember()
        self.temperature.setValue(round(settings.temperature))
        self.exposure.setValue(round(settings.exposure*100))
        self.strength.setValue(round(settings.strength*100))
        self.bypass.setChecked(settings.bypass)
        self.stabilize.setChecked(settings.stabilize)
        self.max_crop.setValue(round(settings.max_crop*1000))
        self.adaptive_crop.setChecked(settings.adaptive_crop)
        self.editor.curves = [list(c) for c in settings.curves]
        self.editor.selected = None
        self.editor.update()
        for control in controls:
            control.blockSignals(False)
            if isinstance(control, QSlider):
                control.valueChanged.emit(control.value())
        self._applying = False
        if record:
            self.history.end()
            self.history.record(self.settings())
        self.update_history_buttons()
        self.update_crop_label()
        self.changed.emit(self.settings())

    def reset(self):
        self.apply(Settings())

    def save(self):
        name, _ = QFileDialog.getSaveFileName(self, '보정 프리셋 저장', 'Goggles-look.json', 'JSON (*.json)')
        if name:
            try:
                Path(name).write_text(json.dumps({'version': 1, 'settings': asdict(self.settings())}, indent=2), encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self, '저장 실패', str(exc))

    def load(self):
        name, _ = QFileDialog.getOpenFileName(self, '보정 프리셋 불러오기', '', 'JSON (*.json)')
        if name:
            try:
                if Path(name).stat().st_size > 65536:
                    raise ValueError('프리셋 파일이 너무 큽니다.')
                settings = validate_preset(json.loads(Path(name).read_text(encoding='utf-8')))
                self.apply(settings)
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                QMessageBox.warning(self, '불러오기 실패', str(exc))

    def attach_presets(self, store):
        self.preset_store = store
        self.refresh_presets()

    def refresh_presets(self):
        self.preset_names.clear()
        if self.preset_store:
            try:
                names = json.loads(self.preset_store.value('namedLooks', '{}'))
                self.preset_names.addItems(sorted(names))
            except (ValueError, TypeError):
                pass
        self.preset_names.setCurrentIndex(-1)

    def save_named(self):
        if self.preset_store is None:
            return
        name, ok = QInputDialog.getText(self, '프리셋 저장', '이름 (같은 이름은 덮어씁니다)')
        if not ok or not name.strip():
            return
        try:
            presets = json.loads(self.preset_store.value('namedLooks', '{}'))
        except (ValueError, TypeError):
            presets = {}
        presets[name.strip()[:80]] = {'version': 1, 'settings': asdict(self.settings())}
        self.preset_store.setValue('namedLooks', json.dumps(presets))
        self.refresh_presets()
        self.preset_names.setCurrentText(name.strip()[:80])

    def load_named(self):
        if self.preset_store is None:
            return
        try:
            presets = json.loads(self.preset_store.value('namedLooks', '{}'))
            self.apply(validate_preset(presets[self.preset_names.currentText()]))
        except (ValueError, TypeError, KeyError):
            QMessageBox.warning(self, '프리셋', '선택한 프리셋을 불러올 수 없습니다.')
