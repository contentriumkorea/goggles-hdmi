import json
import threading
from PySide6.QtWidgets import QApplication
qapp=QApplication.instance() or QApplication([])


def test_report_before_connection_is_bounded_and_uses_only_allowlisted_fields():
    from support_report import build_report,record_issue,record_success
    secret='PASSWORD-PROOF-SECRET https://user:pass@example.com/private /Users/alice/private C:\\Users\\Alice USB\\VID_2CA3&SERIAL'
    stats={'frames':2,'video_bytes':42,'last_error':secret,'password':secret,'issue_history':[{'message':secret}]}
    record_issue(stats,'GH-USB-MISSING','discovery')
    text=build_report(version='1.2.0',platform='darwin',os_version='15.6',architecture='arm64',mode='idle',stats=stats,
        diagnosis={'raw':secret,'DJIDevices':[{'Name':secret,'Hardware':secret}],'interfaces':[]},logs=secret)
    assert text.startswith('Goggles HDMI support report\n') and len(text.encode())<16384
    assert secret not in text and 'SERIAL' not in text and 'password' not in text
    data=json.loads(text.split('\n',1)[1]);assert data['active_issue']['code']=='GH-USB-MISSING'
    record_success(stats,'decode')
    data=json.loads(build_report(version='1.2.0',stats=stats).split('\n',1)[1])
    assert data['active_issue'] is None and data['recent_issues'][-1]['code']=='GH-USB-MISSING'


def test_copy_before_diagnose_works_without_probe_or_restart(monkeypatch):
    import app
    window=app.MainWindow(False)
    try:
        def forbidden(*args,**kwargs):raise AssertionError('Copy must use cached state only')
        monkeypatch.setattr(app,'device_diagnostics',forbidden)
        monkeypatch.setattr(window,'start_usb',forbidden)
        window.diag_report='private paths /Users/alice/secret'
        window.logs.append('secret://password')
        window.copy_problem_button.click()
        text=QApplication.clipboard().text()
        assert text.startswith('Goggles HDMI support report\n') and 'secret' not in text
        data=json.loads(text.split('\n',1)[1]);assert data['stage']=='idle' and data['device_detail']=='not_collected'
        assert window.copy_problem_button.isEnabled()
    finally:window.close()


def test_error_code_survives_usb_wrapping_and_stopping_is_normal():
    from support_report import SupportError
    from macos_usb import usb_error
    error=SupportError('GH-ARP-TIMEOUT','arp')
    assert usb_error(error).code=='GH-ARP-TIMEOUT' and usb_error(error).stage=='arp'
    import goggles
    stop=threading.Event();stop.set();stats={}
    assert list(goggles.decode_goggles(stop,stats=stats))==[] and not stats.get('active_issue')


def test_history_is_bounded_and_component_issues_do_not_replace_usb():
    from support_report import build_report,record_issue
    stats={}
    for i in range(20):
        stats['session_attempts']=i
        record_issue(stats,'GH-USB-ACCESS','claim',OSError(13,'/private/secret'))
    text=build_report(version='1.2.0',stats=stats,components={'update':{'code':'GH-UPDATE-VERIFY','stage':'update','message':'secret'}})
    data=json.loads(text.split('\n',1)[1])
    assert len(data['recent_issues'])==10 and data['active_issue']['code']=='GH-USB-ACCESS'
    assert data['components']['update']['code']=='GH-UPDATE-VERIFY' and 'secret' not in text


def test_stop_clears_active_fault_and_retains_recent_failure():
    from app import MainWindow
    from support_report import record_issue
    window=MainWindow(False)
    try:
        window.mode='usb';window.usb_stats={'issue_history':window.support_history}
        record_issue(window.usb_stats,'GH-USB-ACCESS','claim')
        window.stop()
        data=json.loads(window.problem_report().split('\n',1)[1])
        assert data['source_mode']=='idle' and data['active_issue'] is None
        assert data['recent_issues'][-1]['code']=='GH-USB-ACCESS'
    finally:window.close()


def test_framing_unknown_diagnostics_are_safe_and_repeated_startup_errors_are_bounded(monkeypatch):
    import support_report
    from support_report import build_report,issue_from_exception,record_issue
    from usb_network import RNDISFramingError
    monkeypatch.setattr(support_report,'utc_now',lambda:'2026-10-04T00:38:36+00:00')
    stats={'session_attempts':7,'usb_read_bytes':512,'rndis_partial_reads':3}
    error=RNDISFramingError('data_bounds',buffered_bytes=512,expected_bytes=1558)
    issue=issue_from_exception(error,'arp',operation='rndis_parse')
    record_issue(stats,issue.code,issue.stage,issue)
    for _ in range(6):record_issue(stats,'GH-DECODE','decode',ValueError('/Users/alice/private-password'),active=False)
    report=json.loads(build_report(version='1.2.1',stats=stats).split('\n',1)[1])
    framing=report['recent_issues'][0]
    assert framing['code']=='GH-RNDIS-FRAMING' and framing['reason']=='data_bounds'
    assert framing['exception_class']=='RNDISFramingError' and framing['operation']=='rndis_parse'
    assert framing['context']=={'buffered_bytes':512,'expected_bytes':1558}
    assert report['recent_issues'][-1]['repeat_count']==6 and len(report['recent_issues'])==2
    assert report['counters']['rndis_partial_reads']==3 and 'private-password' not in json.dumps(report)
    unsafe=type('SecretPasswordException',(ValueError,),{})('https://user:secret@example.com')
    record_issue(stats,'GH-UNKNOWN','video',unsafe)
    last=json.loads(build_report(version='1.2.1',stats=stats).split('\n',1)[1])['recent_issues'][-1]
    assert last['exception_class']=='OtherError' and 'secret' not in json.dumps(last).lower()
