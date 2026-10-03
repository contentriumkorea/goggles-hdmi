"""Catch missing fades, shared-overlay bypasses and accidental authentication persistence."""
import hashlib
import pytest
import sys
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

qapp = QApplication.instance() or QApplication([])


def credentials():
    salt = bytes.fromhex('c0f00d1234567890c0f00d1234567890')
    proof = hashlib.pbkdf2_hmac('sha256', b'test-only-password', salt, 1000)
    return {'salt': salt.hex(), 'iterations': 1000, 'digest': hashlib.sha256(proof).hexdigest()}


@pytest.mark.parametrize('seconds,expected', [(0,0),(.375,.5),(.75,1),(4.25,1),
                                             (4.625,.5),(5,0),(29,0),(30.375,.5),(65,0)])
def test_five_second_fade_repeats_every_thirty_seconds(seconds, expected):
    from watermark import watermark_opacity
    assert watermark_opacity(seconds) == pytest.approx(expected)


def test_password_rejects_empty_and_wrong_input():
    from licensing import verify_password
    assert not verify_password('', credentials())
    assert not verify_password('wrong', credentials())
    assert verify_password('test-only-password', credentials())


@pytest.mark.skipif(sys.platform != 'win32',reason='Native Windows DPAPI persistence; Mac backend covered separately')
def test_authentication_persists_encrypted_and_can_be_reset(tmp_path):
    from licensing import LicenseState, password_proof
    settings = QSettings(str(tmp_path/'settings.ini'), QSettings.IniFormat)
    state = LicenseState(settings, credentials())
    assert not state.unlocked
    state.activate(password_proof('test-only-password', credentials()))
    assert state.unlocked
    assert LicenseState(settings, credentials()).unlocked
    stored = settings.value('program/authentication', '')
    assert 'test-only-password' not in stored and credentials()['digest'] not in stored
    state.reset()
    assert not state.unlocked and not LicenseState(settings, credentials()).unlocked
    settings.setValue('program/authentication', 'corrupt-token')
    assert not LicenseState(settings, credentials()).unlocked


def test_preview_and_output_draw_same_logo_without_changing_video_or_raw_mode():
    from app import MainWindow
    w = MainWindow(False)
    try:
        w.timer.stop()
        w.watermark.timer.stop()
        w.watermark.clock = lambda: w.watermark.origin + 2
        w.watermark.refresh()
        frame = QImage(640, 360, QImage.Format_RGB32)
        frame.fill(QColor(20,40,60))
        original = frame.copy()
        w.preview.setFixedSize(640,360)
        w.output.setFixedSize(640,360)
        for surface in (w.preview, w.output):
            surface.set_frame(frame)
        preview = w.preview.grab().toImage().convertToFormat(QImage.Format_RGB32)
        output = w.output.grab().toImage().convertToFormat(QImage.Format_RGB32)
        assert preview == output and preview != original
        assert preview.pixelColor(5,5) == QColor(20,40,60)
        w.raw_button.click()
        assert w.output.grab().toImage().convertToFormat(QImage.Format_RGB32) == output
        assert w.preview.frame == original and w.output.frame == original
        from licensing import password_proof
        w.license.credentials = credentials()
        w.license.activate(password_proof('test-only-password', credentials()))
        assert w.preview.grab().toImage().convertToFormat(QImage.Format_RGB32) == original
        assert w.output.grab().toImage().convertToFormat(QImage.Format_RGB32) == original
        assert not w.watermark.timer.isActive()
        w.license.reset()
        assert w.output.grab().toImage().convertToFormat(QImage.Format_RGB32) != original
    finally:
        w.close()


@pytest.mark.skipif(sys.platform != 'win32',reason='Native Windows DPAPI')
def test_public_verifier_cannot_be_used_to_forge_remembered_authentication(tmp_path):
    import base64
    from licensing import LicenseState, protect_token
    settings = QSettings(str(tmp_path/'forged.ini'), QSettings.IniFormat)
    public_digest = bytes.fromhex(credentials()['digest'])
    for forged in (public_digest, b'GogglesHDMI-Next:watermark:'+public_digest):
        settings.setValue('program/authentication',base64.b64encode(protect_token(forged)).decode())
        assert not LicenseState(settings,credentials()).unlocked
    state = LicenseState(settings,credentials())
    with pytest.raises(ValueError): state.activate(public_digest)


def test_both_surfaces_clear_overlay_at_five_seconds():
    from app import MainWindow
    w = MainWindow(False)
    try:
        w.watermark.clock = lambda: w.watermark.origin + 5
        w.watermark.refresh()
        frame = QImage(640,360,QImage.Format_RGB32)
        frame.fill(QColor(20,40,60))
        for surface in (w.preview,w.output):
            surface.setFixedSize(640,360)
            surface.set_frame(frame)
            assert surface.grab().toImage().convertToFormat(QImage.Format_RGB32) == frame
        assert w.watermark.timer.interval() >= 24000
    finally:
        w.close()
