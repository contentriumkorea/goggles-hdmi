"""Latest-frame output pipeline with independent, bounded draft preview work."""
import queue
import threading
import time
from dataclasses import replace
import numpy as np
from PySide6.QtGui import QImage
from effects import Settings, Processor


def to_image(rgb):
    return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()


def raw_image(frame):
    rgb = frame.reformat(format='rgb24')
    return QImage(memoryview(rgb.planes[0]), rgb.width, rgb.height,
                  rgb.planes[0].line_size, QImage.Format_RGB888).copy()


def put_latest(q, value):
    dropped = 0
    try:
        q.put_nowait(value)
    except queue.Full:
        try:
            q.get_nowait()
            dropped = 1
        except queue.Empty:
            pass
        q.put_nowait(value)
    return dropped


def receive_frames(window, frames, stop):
    window.stutter.start()
    pending, preview_pending = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
    producer_done, output_done = threading.Event(), threading.Event()
    window.processing_stats = {'ms':0., 'dropped':0, 'superseded':0, 'processed':0, 'tracking':'꺼짐', 'crop':0.}
    window.preview_stats = {'processed':0, 'ms':0.}
    window.effect_fault = None
    window.preview_fault = None

    def preview_frames():
        processor = Processor()
        failed_key = None
        while not stop.is_set():
            try:
                rgb, settings, key, original, crop, staged = preview_pending.get(timeout=.05)
            except queue.Empty:
                if output_done.is_set(): return
                continue
            with window.lock:
                if not window.preview_active or key != window.preview_key(): continue
            if key == failed_key: continue
            started = time.perf_counter()
            try:
                adjusted = processor.process(rgb, settings) if staged else rgb
                preview = to_image(rgb if original else adjusted)
                if staged:
                    crop = processor.stabilizer.current_crop if processor.stabilizer else 0.
                with window.lock:
                    if stop.is_set(): return
                    if key != window.preview_key() or not window.preview_active: continue
                    window.latest_preview = (preview, crop, key)
                    window.preview_stats['processed'] += 1
                    window.preview_stats['ms'] = (time.perf_counter()-started)*1000
                    window.preview_fault = None
            except Exception:
                failed_key = key
                window.preview_fault = '미리보기 보정 오류 · HDMI 출력 유지 중'

    def process_frames():
        processor = Processor()
        failed_revision = None
        last_preview = 0.
        try:
            while not stop.is_set():
                try:
                    frame, sequence, received_at = pending.get(timeout=.05)
                except queue.Empty:
                    if producer_done.is_set(): return
                    continue
                started = time.perf_counter()
                window.trace.add('process_start',sequence,now=started)
                with window.lock:
                    settings, revision = window.look_settings, window.look_revision
                    epoch = window.output_epoch
                    if window.raw_output:
                        settings = replace(settings, bypass=True)
                    key = window.preview_key()
                    staged = window.staging
                    preview_settings = window.draft_settings
                    original = window.preview_original or window.preview_crop
                    special = staged or original
                    need_preview = (window.preview_active and special and
                                    started-last_preview >= max(1/15,window.preview_interval))
                fallback = failed_revision == revision
                neutral = (fallback or settings.bypass or
                           (not (settings.stabilize and settings.strength > 0) and
                            not settings.temperature and not settings.exposure and settings.curves==Settings().curves))
                pixels = None
                try:
                    if neutral:
                        processor.stabilizer = None
                        if need_preview:
                            pixels = frame.to_ndarray(format='rgb24')
                            image = to_image(pixels)
                        else:
                            image = raw_image(frame)
                    else:
                        pixels = frame.to_ndarray(format='rgb24')
                        image = to_image(processor.process(pixels,settings))
                except Exception:
                    failed_revision = revision
                    fallback = True
                    processor = Processor()
                    window.effect_fault = '보정 오류 · 원본 출력 중 (보정값 변경 시 재시도)'
                    try:
                        image = raw_image(frame)
                    except Exception:
                        window.usb_note = '프레임 변환 실패 · 다음 프레임 수신 대기'
                        continue
                ready = time.perf_counter()
                crop = processor.stabilizer.current_crop if processor.stabilizer else 0.
                with window.lock:
                    if stop.is_set(): return
                    if epoch != window.output_epoch:
                        window.trace.add('superseded',sequence)
                        window.processing_stats['superseded'] += 1
                        continue
                    if window.latest is not None and window.latest_meta is not None:
                        window.trace.add('submit_overwrite',window.latest_meta)
                    window.latest = image
                    window.latest_meta = sequence
                    window.processing_stats['processed'] += 1
                    window.processing_stats['ms'] = (ready-started)*1000
                    window.processing_stats['crop'] = crop
                    window.processing_stats['tracking'] = (processor.stabilizer.status if processor.stabilizer
                                                           else ('오류로 중지' if fallback else '꺼짐'))
                    if not fallback: window.effect_fault = None
                window.trace.add('ready',sequence,now=ready)
                if need_preview and pixels is not None:
                    last_preview = started
                    put_latest(preview_pending,(pixels,preview_settings,key,original,crop,staged))
        finally:
            output_done.set()

    output_worker = threading.Thread(target=process_frames,daemon=True)
    preview_worker = threading.Thread(target=preview_frames,daemon=True)
    output_worker.start()
    preview_worker.start()
    message = '재생 종료'
    try:
        for frame in frames:
            if stop.is_set(): break
            arrived = time.monotonic()
            if window.last_received_at is not None:
                window.receive_gaps.append((arrived,arrived-window.last_received_at))
            window.last_received_at = arrived
            window.trace_frame_id += 1
            sequence = window.trace_frame_id
            window.trace.add('receive',sequence,
                             pts=frame.pts, time_base=str(frame.time_base) if frame.time_base else None,
                             width=frame.width,height=frame.height)
            dropped = put_latest(pending,(frame,sequence,arrived))
            window.processing_stats['dropped'] += dropped
            if dropped:
                window.trace.add('queue_overwrite',sequence)
            with window.lock: window.frames += 1
    except Exception:
        message = '영상 수신 실패: 주소·파일·네트워크 및 코덱을 확인하세요.'
    finally:
        producer_done.set()
        # Do not allow an old preview/output worker to survive a new session.
        output_worker.join()
        preview_worker.join()
        window.stutter.stop()
        with window.lock:
            window.finished = '정지됨' if stop.is_set() else message
