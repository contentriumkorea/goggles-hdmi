"""Frozen native smoke check. Explicitly excludes hardware compatibility claims."""
import json
import platform
import sys
from fractions import Fraction
from pathlib import Path


def run(app,window,output):
    import av
    import numpy as np
    from PySide6.QtGui import QImage
    from effects import Processor,Settings
    from licensing import CONFIG,LicenseState,password_proof
    from macos_usb import usb_backend,find_device
    from usb_network import NetworkPeer,rndis_packet,rndis_frames,RNDISStream,RNDISFramingError
    report = {'architecture':platform.machine(),'platform':sys.platform,'hardware_verified':False}
    try:
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            raise AssertionError('Native Apple Silicon runner required')
        encoder = av.CodecContext.create('libx264','w')
        encoder.width,encoder.height,encoder.pix_fmt = 320,180,'yuv420p'
        encoder.time_base = Fraction(1,30)
        encoder.options = {'preset':'ultrafast','tune':'zerolatency'}
        decoder = av.CodecContext.create('h264','r')
        image = np.random.default_rng(7).integers(0,255,(180,320,3),dtype=np.uint8)
        frames = []
        for tick in range(5):
            frame = av.VideoFrame.from_ndarray(image,format='rgb24');frame.pts = tick
            for packet in encoder.encode(frame):
                frames.extend(decoder.decode(packet))
        for packet in encoder.encode(None):
            frames.extend(decoder.decode(packet))
        assert frames and frames[0].width == 320 and frames[0].height == 180
        processor = Processor()
        corrected = processor.process(frames[0].to_ndarray(format='rgb24'),Settings(exposure=.5,stabilize=True))
        assert corrected.shape == image.shape
        window.preview.set_frame(QImage(corrected.data,320,180,corrected.strides[0],QImage.Format_RGB888).copy())
        assert not window.preview.grab().isNull()
        assert window.preview.watermark is window.output.watermark
        assert usb_backend() is not None
        try:
            find_device()
        except OSError:
            report['no_device_handled'] = True
        else:
            raise AssertionError('Unexpected physical DJI device on CI')
        a = NetworkPeer('192.168.60.1','192.168.60.2',12346,9003,b'\x02aaaaa')
        b = NetworkPeer('192.168.60.2','192.168.60.1',9003,12346,b'\x02bbbbb')
        _,reply = b.receive(a.arp_request());a.receive(reply)
        assert a.receive(rndis_frames(rndis_packet(b.datagram(b'video')))[0])[0] == b'video'
        message=rndis_packet(b.datagram(b'\0'*1500));stream=RNDISStream()
        assert stream.feed(message[:31])==[]
        assert a.receive(stream.feed(message[31:])[0])[0]==b'\0'*1500
        stream=RNDISStream();assert stream.feed(b'\x01')==[]
        try:stream.feed(rndis_packet(b'\0'*1514))
        except RNDISFramingError as error:
            assert error.context['header_type']==257 and error.context['pending_prefix_value']==1
            report['framing_header_diagnostics']=True
        else:raise AssertionError('Malformed prefix must remain rejected')
        stream=RNDISStream();packet=rndis_packet(b'\0'*1514)
        assert stream.feed(packet)==[b'\0'*1514]
        assert stream.feed(b'\xd8')==[] and stream.feed(b'')==[]
        assert stream.feed(packet)==[b'\0'*1514] and stream.recovery_count==1
        report['one_byte_boundary_recovery']=True
        assert app.platformName() == 'cocoa'
        assert window.copy_problem_button.isEnabled()
        window.copy_problem_button.click()
        copied = app.clipboard().text()
        assert copied.startswith('Goggles HDMI support report\n')
        support = json.loads(copied.split('\n',1)[1])
        assert support['source_mode'] == 'idle'
        import re
        assert support['build']==CONFIG['build_revision']
        assert support['build']=='unknown' or re.fullmatch('[0-9a-f]{40}',support['build'])
        import os
        if os.environ.get('GITHUB_ACTIONS')=='true':assert support['build']==os.environ['GITHUB_SHA']
        report['build_revision']=support['build']
        from platform_support import mac_display_info,mac_window_state,match_mac_display
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        import time
        report['stage']='native_display_modes'
        modes=[mac_display_info(screen) for screen in QApplication.screens()]
        assert modes and all(info['reason']=='ok' and min(info['pixels'])>0 for info in modes)
        candidates=list(zip(QApplication.screens(),modes))
        for screen,info in candidates:
            assert type(info['identifier']) is int and info['identifier']>0 and type(info['builtin']) is bool
            if info['uuid']:assert re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',info['uuid'])
            assert match_mac_display(info,candidates)[0] is screen
        report['native_display_identity']=True
        report['native_uuid_count']=sum(bool(info['uuid']) for info in modes)
        report['native_display_modes']=True
        def wait_native(predicate):
            until=time.monotonic()+5
            while time.monotonic()<until:
                QTest.qWait(50)
                if predicate():return
            raise AssertionError('Native output state did not settle')
        report['stage']='fullscreen_enter'
        window.open_output()
        wait_native(lambda:mac_window_state(window.output).get('native_fullscreen') is True)
        report['fullscreen_enter_state']=mac_window_state(window.output)
        report['stage']='topology_reacquire'
        # Simulate the notification with an existing verified native display;
        # no physical monitor is detached on the build runner.
        window.screen_removed(window.output.output_screen)
        wait_native(lambda:window.output.locked and not window.output_pending
            and mac_window_state(window.output).get('native_fullscreen') is True
            and mac_window_state(window.output).get('native_visible') is True)
        assert window.output_restore_reason in ('matched_uuid','matched_id')
        report['topology_reacquire_simulated']=True
        report['stage']='clipboard_fullscreen'
        window.copy_problem_button.click()
        current=json.loads(app.clipboard().text().split('\n',1)[1])['output']
        assert current['native_fullscreen'] is True and current['native_visible'] is True
        assert window.output.windowHandle().screen()==QApplication.screens()[window.screens.currentIndex()]
        # Leaving native fullscreen used to retain a locked but windowed output.
        report['stage']='fullscreen_repair'
        window.output.showNormal()
        # DidEnter from the original entry remains true while exiting. Observe
        # DidExit before accepting a new completed entry as the repair result.
        wait_native(lambda:mac_window_state(window.output).get('native_fullscreen') is False)
        report['fullscreen_exit_state']=mac_window_state(window.output)
        wait_native(lambda:window.output.fullscreen_repairs==1 and mac_window_state(window.output).get('native_fullscreen') is True)
        report['fullscreen_repair_state']=mac_window_state(window.output)
        report['stage']='escape_release'
        QTest.keyClick(window.output,Qt.Key_Escape)
        wait_native(lambda:not window.output.isVisible() and not window.output.locked
            and mac_window_state(window.output).get('native_visible') is False)
        QTest.qWait(400)
        assert not window.output.isVisible() and not window.output.fullscreen_repair.isActive()
        report['stage']='clipboard_release'
        window.copy_problem_button.click()
        current=json.loads(app.clipboard().text().split('\n',1)[1])['output']
        assert current['locked'] is False and current['visible'] is False and current['native_visible'] is False
        report.update(native_fullscreen=True,native_fullscreen_repair=True,native_output_release=True)
        # Exercise the actual frozen updater against the live signed GitHub feed
        # and redirected package host, with no default interpreter CA available.
        import certifi
        import ssl
        import tempfile
        from updates import open_https,parse_manifest,stage_online,verify_installer,MAX_MANIFEST
        report['stage']='https'
        ca = Path(certifi.where())
        assert ca.is_file() and ca.resolve().is_relative_to(Path(sys._MEIPASS).resolve().parent)
        context = ssl.create_default_context(cafile=str(ca))
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.cert_store_stats()['x509_ca'] > 0
        default_factory = ssl._create_default_https_context
        ssl._create_default_https_context = lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            with open_https(CONFIG['update_url']) as response:
                release = parse_manifest(response.read(MAX_MANIFEST+1),current_version='0.0.0',
                    require_newer=False,platform='darwin')
            with tempfile.TemporaryDirectory(prefix='goggles-https-smoke-') as folder:
                staged = stage_online(release,folder)
                verify_installer(staged,platform='darwin')
                report['https_download_bytes'] = staged.path.stat().st_size
            report.update(https_feed=True,https_download=True,https_signature=True,
                bundled_ca=True,ca_count=context.cert_store_stats()['x509_ca'],
                https_feed_version=release['version'])
        finally:
            ssl._create_default_https_context = default_factory
        report.update(ok=True,decoded_frames=len(frames),pipeline=True,gui=True,qt_platform=app.platformName(),libusb=True,clipboard=True,partial_usb_stream=True)
        code = 0
    except Exception as exc:
        if report.get('stage','').startswith(('fullscreen','clipboard','escape','topology')):
            from platform_support import mac_window_state
            report['output_state']={**mac_window_state(window.output),'qt_fullscreen':window.output.isFullScreen(),
                'qt_visible':window.output.isVisible(),'locked':window.output.locked,
                'repairs':window.output.fullscreen_repairs,'application_active':app.applicationState().value}
        report.update(ok=False,error=type(exc).__name__+': '+str(exc));code = 1
    Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    window.release_output();window.close();app.exit(code)
