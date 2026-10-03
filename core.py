"""Standard-media decoder. Does NOT implement DJI's proprietary USB protocol."""
import time
from pathlib import Path
from urllib.parse import urlsplit
import av


def validate_source(source):
    source = source.strip()
    if not source:
        raise ValueError('영상 파일 또는 스트림 주소를 입력하세요.')
    if Path(source).is_file():
        return source
    parsed = urlsplit(source)
    if parsed.scheme not in ('rtsp', 'rtsps', 'srt', 'http', 'https', 'udp', 'tcp'):
        raise ValueError('존재하는 영상 파일 또는 RTSP/SRT/HTTP/UDP/TCP 주소가 필요합니다.')
    if not parsed.hostname:
        raise ValueError('스트림 주소에 호스트가 없습니다.')
    return source


def decode_frames(source, stop, pace=True):
    if stop.is_set():
        return
    source = validate_source(source)
    local = Path(source).is_file()
    options = {} if local else {'flags': 'low_delay', 'rw_timeout': '3000000'}
    if source.startswith(('rtsp://', 'rtsps://')):
        options['rtsp_transport'] = 'tcp'
    with av.open(source, options=options, timeout=(3.0, 3.0)) as container:
        if not container.streams.video:
            raise ValueError('영상 트랙이 없습니다.')
        stream = container.streams.video[0]
        stream.thread_type = 'SLICE'
        start = time.monotonic()
        first = None
        for frame in container.decode(stream):
            if stop.is_set():
                return
            if local and pace and frame.time is not None:
                if first is None:
                    first = frame.time
                delay = start + float(frame.time - first) - time.monotonic()
                if delay > 0 and stop.wait(delay):
                    return
            yield frame
