from dataclasses import asdict, replace
import numpy as np
import cv2
import pytest
from effects import Settings, Processor, make_lut, curve_values, crop_fraction, smooth_motion
from grading_ui import validate_preset


def test_neutral_and_bypass_preserve_pixels():
    rgb = np.random.default_rng(4).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    assert np.array_equal(Processor().process(rgb, Settings()), rgb)
    assert np.array_equal(Processor().process(rgb, Settings(exposure=3, stabilize=True, bypass=True)), rgb)
    assert np.array_equal(make_lut(Settings())[:, 0, 0], np.arange(256))


def test_crop_tracks_strength_is_capped_and_zero_preserves_pixels():
    assert [crop_fraction(v) for v in [0, .5, 1, 2, -1]] == [0, .05, .1, .1, 0]
    rgb = np.random.default_rng(6).integers(0, 256, (180, 320, 3), dtype=np.uint8)
    processor = Processor()
    for _ in range(3):
        assert np.array_equal(processor.process(rgb, Settings(stabilize=True, strength=0)), rgb)


@pytest.mark.parametrize('strength', [.1, .5, 1.])
def test_crop_geometry_matches_reported_fraction(strength):
    width, height = 640, 360
    x = np.tile(np.arange(width, dtype=np.float32), (height, 1))
    rgb = np.repeat((x/width*255).astype(np.uint8)[..., None], 3, axis=2)
    out = Processor().process(rgb, Settings(stabilize=True, strength=strength))
    expected_left = crop_fraction(strength)*255
    assert abs(float(out[height//2, 0, 0])-expected_left) < 2
    assert out.shape == rgb.shape


def test_exposure_in_linear_light_and_temperature_direction():
    plus = make_lut(Settings(exposure=1))
    # sRGB mid-gray +1 stop is about 176, not clipped 256.
    assert 174 <= int(plus[128, 0, 0]) <= 177
    warm = make_lut(Settings(temperature=100))
    assert warm[100, 0, 0] > warm[100, 0, 1] > warm[100, 0, 2]


def test_curve_interpolation_and_channel_isolation():
    points = ((0., 0.), (.25, .1), (.75, .9), (1., 1.))
    assert np.allclose(curve_values(points, np.array([0, .25, .75, 1])), [0, .1, .9, 1])
    values = curve_values(points, np.linspace(0, 1, 1000))
    assert np.all(np.diff(values) >= 0)
    curves = list(Settings().curves)
    curves[1] = ((0., 0.), (1., .5))
    lut = make_lut(Settings(curves=tuple(curves)))
    assert lut[200, 0, 0] == 100
    assert lut[200, 0, 1] == lut[200, 0, 2] == 200


def test_preset_roundtrip_and_invalid_values():
    settings = Settings(exposure=.5, temperature=-20, stabilize=True)
    assert validate_preset({'version': 1, 'settings': asdict(settings)}) == settings
    for bad in [float('nan'), 4, float('inf')]:
        data = {'version': 1, 'settings': asdict(settings)}
        data['settings']['exposure'] = bad
        with pytest.raises(ValueError):
            validate_preset(data)


def test_stabilizer_reduces_alternating_translation_and_resets_on_resize():
    rng = np.random.default_rng(5)
    base = rng.integers(0, 256, (360, 640, 3), dtype=np.uint8)
    base = cv2.GaussianBlur(base, (5, 5), 0)
    p = Processor()
    before, after = [], []
    prev_in = prev_out = None
    for i in range(30):
        dx = 5 if i % 2 else -5
        rgb = cv2.warpAffine(base, np.float32([[1, 0, dx], [0, 1, 0]]), (640, 360), borderMode=cv2.BORDER_REFLECT_101)
        out = p.process(rgb, Settings(stabilize=True))
        inp = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        result = cv2.cvtColor(out, cv2.COLOR_RGB2GRAY).astype(np.float32)
        if i > 6:
            before.append(abs(cv2.phaseCorrelate(prev_in, inp)[0][0]))
            after.append(abs(cv2.phaseCorrelate(prev_out, result)[0][0]))
        prev_in, prev_out = inp, result
    assert np.mean(after) < np.mean(before)*.5
    out = p.process(np.zeros((180, 320, 3), np.uint8), Settings(stabilize=True))
    assert out.shape == (180, 320, 3)
    assert p.stabilizer.status == '추적 약함 · 보정 완화'


def test_missed_tracking_relaxes_existing_correction_without_snap(monkeypatch):
    from effects import Stabilizer
    stabilizer = Stabilizer()
    rgb = np.full((360, 640, 3), 100, dtype=np.uint8)
    stabilizer.process(rgb, .8)
    stabilizer.path[:] = [8, 4, .01]
    stabilizer.smooth[:] = [2, 1, .002]
    before = stabilizer.smooth-stabilizer.path
    monkeypatch.setattr(cv2, 'goodFeaturesToTrack', lambda *a, **k: None)
    stabilizer.process(rgb, .8)
    after = stabilizer.smooth-stabilizer.path
    assert np.linalg.norm(after) > np.linalg.norm(before)*.8
    assert np.linalg.norm(after) <= np.linalg.norm(before)
    assert np.allclose(stabilizer.path, [8, 4, .01])


def test_motion_path_eases_in_and_out_without_overshooting():
    position, velocity = np.zeros(3), np.zeros(3)
    samples = []
    for _ in range(120):
        position, velocity = smooth_motion(position, velocity, np.ones(3), 1/60, .26)
        samples.append(position[0])
    step = np.diff(samples)
    assert np.all(step >= 0)
    assert samples[-1] > .999
    assert max(samples) <= 1
    assert step[0] < max(step)*.5
    assert step[-1] < step[0]*.01


def test_motion_target_reversal_preserves_velocity_continuity():
    pos, vel = np.zeros(3), np.zeros(3)
    for _ in range(8):
        pos, vel = smooth_motion(pos, vel, np.ones(3), 1/60, .26)
    new_pos, new_vel = smooth_motion(pos, vel, -np.ones(3), 1e-6, .26)
    assert np.max(np.abs(new_vel-vel)) < .001
    assert np.max(np.abs(new_pos-pos)) < .001


def test_fpv_tracker_handles_large_translation_and_rotation():
    from effects import Stabilizer
    rng = np.random.default_rng(28)
    gray = cv2.GaussianBlur(rng.integers(0, 256, (540, 960), dtype=np.uint8), (5, 5), 0)
    for _ in range(150):
        x, y = rng.integers([20, 20], [940, 520])
        cv2.circle(gray, (int(x), int(y)), 6, int(rng.integers(30, 220)), -1)
    transform = cv2.getRotationMatrix2D((480, 270), 10, 1)
    transform[:, 2] += [80, 10]
    moved = cv2.warpAffine(gray, transform, (960, 540), borderMode=cv2.BORDER_REFLECT_101)
    estimate = Stabilizer().estimate_motion(gray, moved)
    assert estimate is not None
    assert np.linalg.norm(estimate[:2]-[80, 10]) < 2
    assert abs(np.degrees(estimate[2])+10) < .5


def test_fpv_tracker_rejects_independent_foreground_motion():
    from effects import Stabilizer
    rng = np.random.default_rng(23)
    gray = cv2.GaussianBlur(rng.integers(0, 256, (360, 640), dtype=np.uint8), (3, 3), 0)
    moved = cv2.warpAffine(gray, np.float32([[1, 0, 18], [0, 1, 4]]), (640, 360), borderMode=cv2.BORDER_REFLECT_101)
    patch = rng.integers(0, 256, (100, 140), dtype=np.uint8)
    gray[100:200, 240:380] = patch
    moved[100:200, 210:350] = patch
    estimate = Stabilizer().estimate_motion(gray, moved)
    assert estimate is not None
    assert np.linalg.norm(estimate[:2]-[18, 4]) < 1


def test_fpv_sustained_fast_motion_increases_follow_response(monkeypatch):
    from effects import Stabilizer
    stabilizer = Stabilizer()
    rgb = np.full((180, 320, 3), 100, np.uint8)
    clock = iter(np.arange(100, 102, 1/30))
    monkeypatch.setattr('effects.time.monotonic', lambda: next(clock))
    monkeypatch.setattr(stabilizer, 'estimate_motion', lambda a, b: np.array([20., 0., .03]))
    for _ in range(20):
        stabilizer.process(rgb, .8)
    assert stabilizer.motion_level > .8


def test_disable_releases_tracker_and_does_not_run_tracking_with_color(monkeypatch):
    processor = Processor()
    rgb = np.full((180, 320, 3), 100, np.uint8)
    processor.process(rgb, Settings(stabilize=True))
    assert processor.stabilizer is not None
    def forbidden(*args, **kwargs):
        raise AssertionError('Disabled stabilization must not run')
    monkeypatch.setattr(processor.stabilizer, 'process', forbidden)
    colored = processor.process(rgb, Settings(exposure=1, stabilize=False))
    assert processor.stabilizer is None
    assert colored[0, 0, 0] > 100
    monkeypatch.setattr('effects.Stabilizer', forbidden)
    for _ in range(20):
        processor.process(rgb, Settings())
    assert processor.stabilizer is None


def test_switch_off_discards_inflight_stabilized_frame(monkeypatch):
    import threading
    import av
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from app import MainWindow
    qapp = QApplication.instance() or QApplication([])
    window = MainWindow(False)
    window.mode = 'stream'
    window.set_look(Settings(stabilize=True))
    entered, release, next_frame = threading.Event(), threading.Event(), threading.Event()
    def slow_effect(self, rgb, settings):
        entered.set()
        release.wait(2)
        return np.full_like(rgb, 240)
    monkeypatch.setattr(Processor, 'process', slow_effect)
    def frames():
        yield av.VideoFrame.from_ndarray(np.full((180, 320, 3), 100, np.uint8), format='rgb24')
        next_frame.wait(2)
        yield av.VideoFrame.from_ndarray(np.full((180, 320, 3), 100, np.uint8), format='rgb24')
        window.stop_event.wait(2)
    window.worker = threading.Thread(target=window.receive_frames, args=(frames(), window.stop_event), daemon=True)
    window.worker.start()
    try:
        assert entered.wait(2)
        window.set_look(Settings())
        release.set()
        QTest.qWait(80)
        assert window.preview.frame.isNull()
        assert window.processing_stats['processed'] == 0
        next_frame.set()
        for _ in range(50):
            QTest.qWait(10)
            if not window.preview.frame.isNull():
                break
        assert window.preview.frame.pixelColor(100, 100).red() == 100
        assert window.processing_stats['tracking'] == '꺼짐'
    finally:
        release.set()
        next_frame.set()
        window.stop()
        window.worker.join(2)
        window.close()
