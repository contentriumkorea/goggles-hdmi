"""Authentication and operator-initiated updates, away from live video controls."""
import os
import subprocess
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QLineEdit, QFileDialog, QProgressBar, QApplication, QCheckBox)
from licensing import CONFIG, password_proof
from update_monitor import UpdateMonitor
from updates import (stage_bundle, check_online, stage_online, verify_installer,
                     update_directory, validate_https, discard_staged, prune_staged, StagedUpdate)
from updates import launch_installer as handoff_installer


class ProgramPanel(QWidget):
    completed = Signal(object, object)
    transferred = Signal(int, int)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.busy = False
        self.pending = None
        self.online_release = None
        self.callback = None
        self.closing = False
        self.installing = False
        self.one_click = False
        self.queued_release = None
        threading.Thread(target=lambda: prune_staged(update_directory()),daemon=True).start()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16,18,16,12)
        layout.setSpacing(14)
        title = QLabel('Goggles HDMI  '+CONFIG['version'])
        title.setObjectName('brand')
        layout.addWidget(title)
        group = QGroupBox('워터마크 인증')
        auth = QVBoxLayout(group)
        self.auth_status = QLabel()
        auth.addWidget(self.auth_status)
        note = QLabel('미인증 상태에서도 모든 기능을 사용할 수 있습니다.\n두 영상 화면 중앙에 30초마다 5초간 로고가 표시됩니다.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        auth.addWidget(note)
        row = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMaxLength(256)
        self.password.setPlaceholderText('프로그램 패스워드')
        self.password.setAccessibleName('프로그램 패스워드')
        self.authenticate = QPushButton('인증')
        self.reset = QPushButton('인증 해제')
        for widget in (self.password,self.authenticate,self.reset): row.addWidget(widget)
        auth.addLayout(row)
        layout.addWidget(group)
        group = QGroupBox('프로그램 업데이트')
        update = QVBoxLayout(group)
        self.select_file = QPushButton('업데이트 파일 선택…')
        update.addWidget(self.select_file)
        self.address = QLineEdit()
        self.address.setPlaceholderText('온라인 업데이트 주소 (선택 · https://…)')
        self.address.setAccessibleName('온라인 업데이트 주소')
        default_url = CONFIG.get('update_url','')
        self.address.setText((window.settings.value('program/updateUrl','') or default_url) if window.settings is not None else default_url)
        update.addWidget(self.address)
        self.automatic = QCheckBox('자동 확인 · 시작 시 / 15분마다')
        self.automatic.setChecked(window.settings.value('program/autoUpdate',True,type=bool) if window.settings is not None else True)
        update.addWidget(self.automatic)
        row = QHBoxLayout()
        self.check = QPushButton('새 버전 확인')
        self.download = QPushButton('새 버전 다운로드')
        self.download.setEnabled(False)
        row.addWidget(self.check)
        row.addWidget(self.download)
        update.addLayout(row)
        self.update_status = QLabel('GitHub에서 새 버전을 자동 확인합니다. 파일로도 업데이트할 수 있습니다.')
        self.update_status.setTextFormat(Qt.PlainText)
        self.update_status.setWordWrap(True)
        update.addWidget(self.update_status)
        self.progress = QProgressBar()
        self.progress.hide()
        update.addWidget(self.progress)
        self.install = QPushButton('설치 시작 · 프로그램 종료')
        if sys.platform == 'darwin':
            self.install.setText('Apple 설치 프로그램 열기 · 앱 종료')
        self.install.setEnabled(False)
        update.addWidget(self.install)
        note = QLabel('설치를 시작하면 영상 출력을 종료합니다. 보정값과 인증은 유지됩니다.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        update.addWidget(note)
        layout.addWidget(group)
        layout.addStretch()
        self.authenticate.clicked.connect(self.authenticate_password)
        self.password.returnPressed.connect(self.authenticate_password)
        self.reset.clicked.connect(window.license.reset)
        self.select_file.clicked.connect(self.choose_bundle)
        self.check.clicked.connect(self.check_update)
        self.download.clicked.connect(self.download_update)
        self.install.clicked.connect(self.install_update)
        self.completed.connect(self.finish_job)
        self.transferred.connect(self.show_progress)
        window.license.changed.connect(self.refresh_auth)
        self.monitor = UpdateMonitor(self)
        self.monitor.available.connect(self.offer_release)
        self.automatic.toggled.connect(self.configure_monitor)
        self.address.editingFinished.connect(self.configure_monitor)
        self.configure_monitor()
        self.refresh_auth()

    def configure_monitor(self):
        url = self.address.text().strip()
        enabled = self.automatic.isChecked()
        if self.window.settings is not None:
            self.window.settings.setValue('program/updateUrl',url)
            self.window.settings.setValue('program/autoUpdate',enabled)
        self.monitor.configure(url, enabled and self.window.settings is not None and not self.closing)

    def refresh_update_controls(self):
        ready = not self.busy and not self.closing
        for control in (self.select_file,self.check,self.address): control.setEnabled(ready)
        self.download.setEnabled(ready and self.online_release is not None)
        self.install.setEnabled(ready and self.pending is not None and getattr(sys,'frozen',False))
        self.window.update_now.setEnabled(ready and self.online_release is not None)

    def offer_release(self, release):
        if self.closing: return
        if self.busy:
            self.queued_release = release
            return
        self.online_release = release
        message = '새 버전 '+release['version']+' 사용 가능'
        self.update_status.setText(message+'\n'+release.get('notes',''))
        self.window.update_label.setText(message+' · 설치 시 출력이 종료되고 프로그램이 다시 시작됩니다.')
        self.window.update_notice.show()
        self.refresh_update_controls()

    def update_now(self):
        if self.busy or self.closing or not self.online_release: return
        self.one_click = True
        self.queued_release = None
        if self.pending and self.pending.release == self.online_release:
            self.install_update()
        else:
            self.download_update()

    def refresh_auth(self):
        unlocked = self.window.license.unlocked
        self.auth_status.setText('인증 완료 · 워터마크 없음' if unlocked else '미인증 · 워터마크 표시')
        if self.window.license.persistence_error:
            self.auth_status.setText(self.auth_status.text()+'\n'+self.window.license.persistence_error)
        self.password.setEnabled(not unlocked and not self.busy)
        self.authenticate.setEnabled(not unlocked and not self.busy)
        self.reset.setEnabled(unlocked and not self.busy)

    def run_job(self, work, callback):
        if self.busy: return
        self.busy, self.callback = True, callback
        self.refresh_update_controls()
        self.refresh_auth()
        def execute():
            result, error = None, None
            try: result = work()
            except Exception as exc: error = exc
            try: self.completed.emit(result,error)
            except RuntimeError: pass  # The program was closed during the background operation.
        threading.Thread(target=execute,daemon=True).start()

    def finish_job(self, result, error):
        if self.closing:
            if isinstance(result, StagedUpdate): discard_staged(result)
            return
        self.busy = False
        self.progress.hide()
        callback, self.callback = self.callback, None
        self.refresh_auth()
        callback(result,error)
        self.refresh_update_controls()
        if not self.busy and not self.closing and self.queued_release is not None:
            release, self.queued_release = self.queued_release, None
            self.offer_release(release)

    def authenticate_password(self):
        if self.busy or self.window.license.unlocked: return
        password = self.password.text()
        self.password.clear()
        self.auth_status.setText('패스워드 확인 중…')
        self.run_job(lambda: password_proof(password,self.window.license.credentials), self.password_checked)

    def password_checked(self, accepted, error):
        if error or not accepted:
            self.auth_status.setText('패스워드가 일치하지 않습니다. 다시 입력하세요.')
        else:
            try: self.window.license.activate(accepted)
            except OSError:
                self.auth_status.setText('인증을 저장하지 못했습니다. 다시 시도하세요.')

    def choose_bundle(self):
        path, _ = QFileDialog.getOpenFileName(self,'업데이트 파일 선택','',
            'Goggles HDMI 업데이트 (*.ghupdate)',options=QFileDialog.DontUseNativeDialog)
        if path: self.load_bundle(path)

    def load_bundle(self, path):
        if self.busy: return
        self.one_click = False
        self.window.update_notice.hide()
        discard_staged(self.pending)
        self.pending = None
        self.online_release = None
        self.update_status.setText('업데이트 파일 검증 중…')
        self.progress.setValue(0)
        self.progress.show()
        self.run_job(lambda: stage_bundle(path,update_directory(),progress=self.transferred.emit), self.staged)

    def check_update(self):
        if self.busy: return
        self.one_click = False
        url = self.address.text().strip()
        try: validate_https(url)
        except ValueError as exc:
            self.update_status.setText(str(exc))
            return
        if self.window.settings is not None:
            self.window.settings.setValue('program/updateUrl',url)
        self.update_status.setText('새 버전 확인 중…')
        self.run_job(lambda: check_online(url),self.checked)

    def checked(self, release, error):
        if error:
            self.window.record_component_issue('update','GH-UPDATE-VERIFY' if isinstance(error,ValueError) else 'GH-UPDATE-IO','update')
            self.update_status.setText('업데이트 확인: '+str(error))
        elif release is None:
            self.window.component_issues.pop('update',None)
            self.online_release = None
            discard_staged(self.pending)
            self.pending = None
            self.window.update_notice.hide()
            self.update_status.setText('최신 버전입니다. ('+CONFIG['version']+')')
        else:
            self.offer_release(release)

    def download_update(self):
        if not self.online_release or self.busy: return
        release = self.online_release
        discard_staged(self.pending)
        self.pending = None
        self.update_status.setText('새 버전 다운로드 및 검증 중…')
        self.window.update_label.setText('새 버전 다운로드 및 검증 중… · 영상 출력은 계속됩니다.')
        self.progress.setValue(0)
        self.progress.show()
        self.run_job(lambda: stage_online(release,update_directory(),self.transferred.emit),self.staged)

    def show_progress(self, received, total):
        self.progress.setValue(round(received*100/total))
        if self.one_click:
            self.window.update_label.setText(f'업데이트 다운로드 {round(received*100/total)}% · 영상 출력은 계속됩니다.')

    def staged(self, staged, error):
        if error:
            self.window.record_component_issue('update','GH-UPDATE-VERIFY' if isinstance(error,ValueError) else 'GH-UPDATE-IO','update')
            self.one_click = False
            self.pending = None
            self.update_status.setText('업데이트 준비 실패: '+str(error))
            self.window.update_label.setText('업데이트 준비 실패 · 다시 시도하세요. '+str(error))
        else:
            self.window.component_issues.pop('update',None)
            self.pending = staged
            message = staged.release['version']+' 버전 준비 완료 · 서명과 파일 검증 완료'
            if not getattr(sys,'frozen',False): message += '\n설치된 프로그램에서 설치를 시작하세요.'
            self.update_status.setText(message)
            if self.one_click:
                if getattr(sys,'frozen',False): self.install_update()
                else:
                    self.one_click = False
                    self.window.update_label.setText(message)

    def install_update(self):
        if not self.pending or self.busy or not getattr(sys,'frozen',False): return
        staged = self.pending
        self.update_status.setText('설치 전 파일 최종 확인 중…')
        self.run_job(lambda: verify_installer(staged),lambda result,error: self.launch_installer(staged,error))

    def launch_installer(self, staged, error):
        if error:
            self.window.record_component_issue('update','GH-UPDATE-VERIFY','update')
            discard_staged(staged)
            self.pending = None
            self.update_status.setText('설치를 시작하지 못했습니다: '+str(error))
            self.window.update_label.setText(self.update_status.text())
            return
        try:
            handoff_installer(staged)
        except (OSError,ValueError) as exc:
            self.window.record_component_issue('update','GH-UPDATE-VERIFY' if isinstance(exc,ValueError) else 'GH-UPDATE-IO','update')
            self.update_status.setText('설치 프로그램 실행 실패: '+str(exc))
            self.window.update_label.setText(self.update_status.text())
            return
        self.installing = True
        self.window.release_output()
        self.window.stop()
        self.window.settings_dialog.close()
        self.window.close()
        QApplication.instance().quit()

    def shutdown(self):
        self.closing = True
        self.monitor.configure('',False)
        if not self.installing:
            discard_staged(self.pending)
