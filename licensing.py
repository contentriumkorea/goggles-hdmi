"""Offline watermark authentication; no feature gates and no plaintext password storage."""
import base64
import ctypes
import hashlib
import hmac
import json
import re
import sys
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, Signal

CONFIG = json.loads((Path(__file__).parent/('release_config_macos.json' if sys.platform == 'darwin' else 'release_config.json')).read_text(encoding='utf-8'))


def verify_password(password, credentials=None):
    return password_proof(password, credentials) is not None


def password_proof(password, credentials=None):
    credentials = credentials or CONFIG['password']
    if not isinstance(password, str) or not password or len(password) > 256:
        return None
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                               bytes.fromhex(credentials['salt']), credentials['iterations'])
    return actual if hmac.compare_digest(hashlib.sha256(actual).hexdigest(), credentials['digest']) else None


def protect_token(data, decrypt=False):
    """Windows DPAPI binds remembered authentication to this Windows account."""
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


class LicenseState(QObject):
    changed = Signal()

    def __init__(self, settings=None, credentials=None, parent=None, *, platform=None, backend=None):
        super().__init__(parent)
        self.settings = settings
        self.credentials = credentials or CONFIG['password']
        self.unlocked = False
        self.platform = platform or sys.platform
        self.backend = backend
        self.persistence_error = ''
        if settings is not None:
            try:
                stored = settings.value('program/authentication', '')
                if self.platform == 'darwin':
                    self.unlocked = bool(self.valid_reference(stored)) and self.valid_proof(self.keychain().get(stored))
                else:
                    self.unlocked = bool(stored) and self.valid_proof(
                        protect_token(base64.b64decode(stored, validate=True), decrypt=True))
            except (OSError, ValueError, TypeError):
                self.unlocked = False

    @staticmethod
    def valid_reference(stored):
        return isinstance(stored,str) and re.fullmatch(r'keychain-v1:[0-9a-f]{32}',stored)

    def keychain(self):
        if self.backend is None:
            from macos_keychain import Keychain
            self.backend = Keychain()
        return self.backend

    def valid_proof(self, proof):
        return isinstance(proof, bytes) and len(proof) == 32 and hmac.compare_digest(
            hashlib.sha256(proof).hexdigest(), self.credentials['digest'])

    def activate(self, proof):
        # Called on the GUI thread only after password verification succeeds.
        if not self.valid_proof(proof):
            raise ValueError('유효한 패스워드 인증이 필요합니다.')
        if self.settings is not None:
            if self.platform == 'darwin':
                reference = self.settings.value('program/authentication','')
                if not self.valid_reference(reference):
                    reference = 'keychain-v1:'+uuid.uuid4().hex
                try:
                    self.keychain().set(reference,proof)
                    self.settings.setValue('program/authentication',reference)
                    self.settings.sync()
                    self.persistence_error = ''
                except OSError:
                    self.persistence_error = 'Keychain 인증 저장 실패 · 이번 실행에서만 인증됩니다.'
            else:
                encrypted = base64.b64encode(protect_token(proof)).decode('ascii')
                self.settings.setValue('program/authentication', encrypted)
                self.settings.sync()
        self.unlocked = True
        self.changed.emit()

    def reset(self):
        if self.settings is not None:
            if self.platform == 'darwin':
                reference = self.settings.value('program/authentication','')
                if self.valid_reference(reference):
                    try:
                        self.keychain().delete(reference)
                    except OSError:
                        self.persistence_error = 'Keychain 항목 삭제 실패 · 저장 참조는 해제되었습니다.'
            self.settings.remove('program/authentication')
            self.settings.sync()
        self.unlocked = False
        self.changed.emit()
