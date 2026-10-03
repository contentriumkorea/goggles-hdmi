"""Bounded CPU image adjustments and causal, experimental stabilization."""
from dataclasses import dataclass
import math
import os
import time
import cv2
import numpy as np

cv2.setNumThreads(min(6, max(2, (os.cpu_count() or 4)//2)))
# This pipeline uses CPU arrays. Avoid lazy OpenCL device discovery/compilation
# causing a one-time pause when the rotation fallback is first invoked.
cv2.ocl.setUseOpenCL(False)
IDENTITY = ((0.0, 0.0), (1.0, 1.0))


def crop_fraction(strength):
    """Fraction removed at each edge: 0% at zero strength, up to 10%."""
    return .10 * min(1., max(0., float(strength)))


def smooth_motion(position, velocity, target, dt, response):
    """Exact critically damped motion: preserve velocity through target changes."""
    omega = 2 / response
    offset = position-target
    tangent = velocity+omega*offset
    decay = math.exp(-omega*dt)
    return (target+(offset+tangent*dt)*decay,
            (velocity-omega*tangent*dt)*decay)


@dataclass(frozen=True)
class Settings:
    temperature: float = 0
    exposure: float = 0
    curves: tuple = (IDENTITY, IDENTITY, IDENTITY, IDENTITY)
    stabilize: bool = False
    strength: float = .8
    bypass: bool = False
    max_crop: float = .1
    adaptive_crop: bool = False


def curve_values(points, x):
    """Shape-preserving cubic Hermite curve, including non-monotonic edits."""
    p = np.asarray(points, dtype=float)
    h = np.diff(p[:, 0])
    if len(p) < 2 or np.any(h <= 0) or not np.isfinite(p).all():
        raise ValueError('Curve points must have distinct increasing inputs')
    d = np.diff(p[:, 1]) / h
    m = np.zeros(len(p))
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(p)-1):
        if d[i-1] * d[i] > 0:
            w1, w2 = 2*h[i]+h[i-1], h[i]+2*h[i-1]
            m[i] = (w1+w2)/(w1/d[i-1]+w2/d[i])
    idx = np.clip(np.searchsorted(p[:, 0], x, side='right')-1, 0, len(p)-2)
    t = np.clip((x-p[idx, 0])/h[idx], 0, 1)
    y = ((2*t**3-3*t**2+1)*p[idx, 1] + (t**3-2*t**2+t)*h[idx]*m[idx]
         + (-2*t**3+3*t**2)*p[idx+1, 1] + (t**3-t**2)*h[idx]*m[idx+1])
    return np.clip(y, 0, 1)


def make_lut(settings):
    x = np.arange(256, dtype=float)/255
    linear = np.where(x <= .04045, x/12.92, ((x+.055)/1.055)**2.4)
    warmth = settings.temperature/100
    gain = np.array([2**(.5*warmth), 1., 2**(-.5*warmth)]) * 2**settings.exposure
    channels = []
    for c in range(3):
        y = np.clip(linear*gain[c], 0, 1)
        y = np.where(y <= .0031308, 12.92*y, 1.055*y**(1/2.4)-.055)
        y = curve_values(settings.curves[0], y)
        y = curve_values(settings.curves[c+1], y)
        channels.append(np.rint(y*255).astype(np.uint8))
    return np.stack(channels, axis=1).reshape(256, 1, 3)


class Stabilizer:
    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = None
        self.path = np.zeros(3)
        self.smooth = np.zeros(3)
        self.velocity = np.zeros(3)
        self.last_time = None
        self.status = '대기'
        self.tracked = 0
        self.confidence = 0.
        self.method = '대기'
        self.hann = None
        self.coarse_hann = None
        self.motion_level = 0.
        self.motion_velocity = np.zeros(3)
        self.orb = None
        self.current_crop = 0.

    def accept_transform(self, matrix, src, inliers, shape, method):
        if matrix is None or inliers is None:
            return None
        hh, ww = shape
        accepted = src[inliers.ravel() == 1].reshape(-1, 2)
        count, confidence = len(accepted), float(inliers.mean())
        coverage = np.ptp(accepted, axis=0) if count else np.zeros(2)
        scale = math.hypot(matrix[0, 0], matrix[1, 0])
        angle = math.atan2(matrix[1, 0], matrix[0, 0])
        center = np.array([ww/2, hh/2])
        shift = matrix[:, :2]@center+matrix[:, 2]-center
        if (count >= 16 and confidence >= .45 and coverage[0] > ww*.25 and
                coverage[1] > hh*.20 and .85 < scale < 1.15 and abs(angle) < .35 and
                np.linalg.norm(shift) < ww*.35):
            self.tracked, self.confidence, self.method = count, confidence, method
            return np.array([shift[0], shift[1], angle])
        return None

    def estimate_motion(self, previous, gray):
        """Distributed corners, bidirectional flow, robust global-motion consensus."""
        hh, ww = gray.shape
        self.tracked, self.confidence, self.method = 0, 0., '대기'
        corners = []
        # Avoid one detailed foreground object consuming the whole feature budget.
        # Leave edge OSD out of the camera-motion estimate.
        for row in range(3):
            for col in range(4):
                x0, x1 = int(ww*(.08+.84*col/4)), int(ww*(.08+.84*(col+1)/4))
                y0, y1 = int(hh*(.08+.84*row/3)), int(hh*(.08+.84*(row+1)/3))
                points = cv2.goodFeaturesToTrack(previous[y0:y1, x0:x1], 60, .006, 7,
                                                blockSize=5)
                if points is not None:
                    points += np.float32([x0, y0])
                    corners.append(points)
        if corners:
            points = np.concatenate(corners)
            # Seed fast FPV translations at a coarse scale before local tracking.
            cw = min(320, ww)
            ch = max(16, round(hh*cw/ww))
            old_small = cv2.resize(previous, (cw, ch), interpolation=cv2.INTER_AREA).astype(np.float32)
            new_small = cv2.resize(gray, (cw, ch), interpolation=cv2.INTER_AREA).astype(np.float32)
            if self.coarse_hann is None or self.coarse_hann.shape != old_small.shape:
                self.coarse_hann = cv2.createHanningWindow((cw, ch), cv2.CV_32F)
            coarse_shift, coarse_quality = cv2.phaseCorrelate(old_small, new_small, self.coarse_hann)
            initial = None
            flags = 0
            if coarse_quality > .25 and np.isfinite(coarse_shift).all() and np.linalg.norm(coarse_shift) < cw*.35:
                initial = points + np.float32([coarse_shift[0]*ww/cw, coarse_shift[1]*hh/ch])
                flags = cv2.OPTFLOW_USE_INITIAL_FLOW
            lk = dict(winSize=(31, 31), maxLevel=3,
                      criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, .01),
                      minEigThreshold=1e-5)
            tracked, forward, error = cv2.calcOpticalFlowPyrLK(previous, gray, points, initial, flags=flags, **lk)
            if tracked is not None:
                returned, backward, _ = cv2.calcOpticalFlowPyrLK(
                    gray, previous, tracked, points.copy(), flags=cv2.OPTFLOW_USE_INITIAL_FLOW, **lk)
                if returned is not None:
                    fb = np.linalg.norm(points-returned, axis=2).ravel()
                    good = ((forward.ravel() == 1) & (backward.ravel() == 1) &
                            (fb < .8) & (error.ravel() < 35) & np.isfinite(fb))
                    if good.sum() >= 16:
                        src, dst = points[good], tracked[good]
                        matrix, inliers = cv2.estimateAffinePartial2D(
                            src, dst, method=cv2.RANSAC, ransacReprojThreshold=1.5,
                            maxIters=2000, confidence=.995, refineIters=15)
                        estimate = self.accept_transform(matrix, src, inliers, gray.shape, '정밀 추적')
                        if estimate is not None:
                            return estimate
        # Rotation-tolerant descriptor matching rescues frames beyond LK's basin.
        if self.orb is None:
            self.orb = cv2.ORB_create(nfeatures=600, fastThreshold=8, edgeThreshold=15)
        orb_ratio = min(1., 480/ww)
        ow, oh = round(ww*orb_ratio), round(hh*orb_ratio)
        mask = np.zeros((oh, ow), np.uint8)
        mask[int(oh*.08):int(oh*.92), int(ow*.08):int(ow*.92)] = 255
        old_keys, old_desc, new_keys, new_desc = [], None, [], None
        if corners and sum(len(c) for c in corners) >= 16:
            old_keys, old_desc = self.orb.detectAndCompute(cv2.resize(previous, (ow, oh)), mask)
            new_keys, new_desc = self.orb.detectAndCompute(cv2.resize(gray, (ow, oh)), mask)
        if old_desc is not None and new_desc is not None and len(new_desc) >= 2:
            matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(old_desc, new_desc, k=2)
            reliable = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .72*pair[1].distance]
            if len(reliable) >= 20:
                src = np.float32([old_keys[m.queryIdx].pt for m in reliable]).reshape(-1, 1, 2)/orb_ratio
                dst = np.float32([new_keys[m.trainIdx].pt for m in reliable]).reshape(-1, 1, 2)/orb_ratio
                matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                    ransacReprojThreshold=2, maxIters=2000, confidence=.995, refineIters=15)
                estimate = self.accept_transform(matrix, src, inliers, gray.shape, '큰 회전 추적')
                if estimate is not None:
                    return estimate
        # Translation-only fallback for weak texture. Reject ambiguous correlation.
        if gray.std() > 3 and previous.std() > 3:
            if self.hann is None or self.hann.shape != (oh, ow):
                self.hann = cv2.createHanningWindow((ow, oh), cv2.CV_32F)
            shift, response = cv2.phaseCorrelate(cv2.resize(previous, (ow, oh)).astype(np.float32),
                                                cv2.resize(gray, (ow, oh)).astype(np.float32), self.hann)
            if np.isfinite(shift).all() and response > .45 and np.linalg.norm(shift) < ow*.12:
                self.method = '위상 보조'
                self.confidence = min(1., max(0., response))
                return np.array([shift[0]/orb_ratio, shift[1]/orb_ratio, 0.])
        return None

    def process(self, rgb, strength, max_crop=.1, adaptive_crop=False):
        height, width = rgb.shape[:2]
        strength = min(1., max(0., float(strength)))
        crop = min(.1, max(0., max_crop))
        if not adaptive_crop:
            crop *= strength
        if crop == 0 or strength == 0:
            self.reset()
            self.status = '강도 0 · 원본'
            return rgb
        ratio = min(1., 960/width)
        small = cv2.resize(rgb, (round(width*ratio), round(height*ratio)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        now = time.monotonic()
        dt = min(.1, max(1/120, now-self.last_time)) if self.last_time is not None else 1/30
        if (self.previous is None or self.previous.shape != gray.shape or
                (self.last_time is not None and now-self.last_time > .5)):
            self.reset()
        delta = np.zeros(3)
        valid = False
        if self.previous is not None:
            estimate = self.estimate_motion(self.previous, gray)
            if estimate is not None:
                valid = True
                delta = estimate.copy()
                delta[:2] /= ratio
        # A missed optical-flow estimate is not a new shot. Keep the trajectory
        # and let the same smoothing filter gently relax the correction, rather
        # than snapping back to zero for a single low-texture/blurry frame.
        self.path += delta
        # Smooth the desired camera path, NOT the cancelling correction; filtering
        # the latter would reintroduce high-frequency shake. The second-order path
        # keeps velocity continuous on loss/recovery and on direction changes.
        # FPV: follow deliberate high-speed translation/turning promptly while
        # smoothing small vibrations. Vary response continuously, keeping velocity.
        # Signed velocity distinguishes sustained panning from alternating shake.
        self.motion_velocity += (1-math.exp(-dt/.06))*(delta/dt-self.motion_velocity)
        speed = max(np.linalg.norm(self.motion_velocity[:2])/width, abs(self.motion_velocity[2]))
        desired_level = float(np.clip((speed-.25)/1.25, 0, 1))
        self.motion_level += (1-math.exp(-dt/.08))*(desired_level-self.motion_level)
        response = (.10+.20*strength)*(1-self.motion_level) + .065*self.motion_level
        self.smooth, self.velocity = smooth_motion(
            self.smooth, self.velocity, self.path, dt, response)
        correction = self.smooth-self.path
        if adaptive_crop:
            needed = min(crop, max(abs(correction[0])/width, abs(correction[1])/height)*2.5
                         + abs(correction[2])*max(width/height, height/width))
            # Gradual zoom: limit correction to the crop currently available.
            response_crop = .35 if needed > self.current_crop else 2.5
            self.current_crop += (1-math.exp(-dt/response_crop))*(needed-self.current_crop)
            crop = min(crop, max(0., self.current_crop))
        self.current_crop = crop
        crop = max(crop, 1e-8)
        # Keep translation and rotation inside the strength-dependent crop budget.
        bounds = np.array([width, height]) * crop * .45
        correction[:2] = bounds*np.tanh(correction[:2]/bounds)
        angle_limit = min(.04*strength, .4*crop*min(width/height, height/width))
        correction[2] = angle_limit*math.tanh(correction[2]/angle_limit)
        zoom = 1/(1-2*crop)
        a = correction[2]
        rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])*zoom
        center = np.array([width/2, height/2])
        matrix = np.column_stack([rot, center-rot@center+correction[:2]*zoom])
        result = cv2.warpAffine(rgb, matrix, (width, height), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT_101)
        self.previous, self.last_time = gray, now
        self.status = (f'{self.method} · {self.tracked}점 · 신뢰도 {self.confidence:.0%}'
                       if valid else '추적 약함 · 보정 완화')
        return result


class Processor:
    def __init__(self):
        self.stabilizer = None
        self.cached = None
        self.lut = None
        self.ms = 0

    def process(self, rgb, settings):
        start = time.perf_counter()
        if settings.bypass:
            self.stabilizer = None
            self.ms = 0
            return rgb
        if settings.stabilize and settings.strength > 0:
            if self.stabilizer is None:
                self.stabilizer = Stabilizer()
            rgb = self.stabilizer.process(rgb, settings.strength, settings.max_crop, settings.adaptive_crop)
        else:
            self.stabilizer = None
        if settings.temperature or settings.exposure or settings.curves != Settings().curves:
            if self.cached != settings:
                self.lut = make_lut(settings)
                self.cached = settings
            rgb = cv2.LUT(rgb, self.lut)
        self.ms = (time.perf_counter()-start)*1000
        return rgb
