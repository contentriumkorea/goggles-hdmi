import threading
from pathlib import Path
import av
import pytest
from core import validate_source, decode_frames


def make_video(path):
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('mpeg4', rate=25)
        stream.width, stream.height = 160, 96
        stream.pix_fmt = 'yuv420p'
        for _ in range(12):
            frame = av.VideoFrame(160, 96, 'rgb24')
            for plane in frame.planes:
                plane.update(bytes([80]) * plane.buffer_size)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


def test_reject_empty_and_unsupported():
    for source in ['', ' ', 'ftp://example.org/a', 'concat:a|b']:
        with pytest.raises(ValueError):
            validate_source(source)


def test_urls():
    assert validate_source('rtsp://127.0.0.1:8554/live')
    with pytest.raises(ValueError):
        validate_source('rtsp:///live')


def test_decode_actual_video(tmp_path):
    path = tmp_path / '테스트.mp4'
    make_video(path)
    frames = list(decode_frames(str(path), threading.Event(), pace=False))
    assert len(frames) == 12
    assert frames[0].width == 160
    assert frames[0].height == 96
    assert frames[0].reformat(format='rgb24').planes[0].buffer_size >= 160 * 96 * 3


def test_cancel_before_decode(tmp_path):
    stop = threading.Event()
    stop.set()
    assert list(decode_frames(str(tmp_path / 'missing.mp4'), stop)) == []


def test_invalid_media(tmp_path):
    path = tmp_path / 'broken.mp4'
    path.write_bytes(b'not a movie')
    with pytest.raises(Exception):
        list(decode_frames(str(path), threading.Event()))
