"""Controlled pipeline timing, independent of wall-clock and connected hardware."""
import importlib.util
import json
import time
import pytest


def monitor(**kwargs):
    assert importlib.util.find_spec('stutter'), 'Automatic timing capture is missing'
    from stutter import StutterMonitor
    return StutterMonitor(**kwargs)


def feed(m, start, count, fps=30, stages=('receive','process_start','ready','submit','paint')):
    offsets = {'receive':0, 'process_start':.001, 'ready':.006, 'submit':.008, 'paint':.010}
    for i in range(count):
        stamp = start+i/fps
        for stage in stages:
            m.add(stage, round(stamp*10000), now=stamp+offsets[stage])
        m.poll(now=stamp+.011)
    return start+count/fps


@pytest.mark.parametrize('fps', [15, 29.97, 60])
def test_normal_cadence_has_no_stall(fps):
    m = monitor()
    m.start(now=0)
    m.poll(now=0, display_expected=True)
    feed(m,0,round(fps*8),fps)
    assert m.report()['incidents']==[]


@pytest.mark.parametrize('reason', ['receive','processing','display'])
def test_stall_classification_and_before_after_context(reason):
    m = monitor()
    m.start(now=0)
    m.poll(now=0, display_expected=True)
    stamp = feed(m,0,45)
    if reason=='receive':
        m.poll(now=stamp+.22)
        stamp += .25
    else:
        stages = ('receive',) if reason=='processing' else ('receive','process_start','ready')
        stamp = feed(m,stamp,8,stages=stages)
    feed(m,stamp,75)
    result = m.report()
    assert len(result['incidents'])==1
    incident = result['incidents'][0]
    assert incident['reason']==reason
    assert incident['gap_ms'] >= 100
    assert any(e['ms']<incident['trigger_ms'] for e in incident['events'])
    assert any(e['ms']>incident['trigger_ms'] for e in incident['events'])
    assert result['video_recorded'] is False


def test_hidden_output_and_intentional_stop_do_not_report_stalls():
    m = monitor()
    m.start(now=0)
    stamp=feed(m,0,60,stages=('receive','process_start','ready','submit'))
    m.stop(now=stamp)
    m.poll(now=100)
    m.start(now=100)
    feed(m,100,60,stages=('receive','process_start','ready','submit'))
    assert m.report()['incidents']==[]


def test_retention_is_bounded_and_does_not_cross_source_sessions():
    m = monitor(event_limit=200, incident_limit=2)
    for run in range(3):
        start=run*20
        m.start(now=start)
        stamp=feed(m,start,45)
        m.poll(now=stamp+.25)
        feed(m,stamp+.3,75)
        m.stop(now=stamp+3)
    result=m.report()
    assert len(result['incidents'])==2
    assert result['incident_count']==3
    assert all(len(i['events'])<=200 for i in result['incidents'])
    assert result['incidents'][0]['session'] != result['incidents'][1]['session']


def test_automatic_monitor_does_not_require_or_reset_manual_trace():
    from goggles_workflow import FrameTrace
    m=monitor()
    trace=FrameTrace(monitor=m)
    m.start(now=0)
    for i in range(50):
        for stage,offset in [('receive',0),('ready',.006),('submit',.008)]:
            trace.add(stage,i,now=i/30+offset)
    assert not trace.active and trace.report()['events']==[]
    trace.start(now=2)
    trace.add('receive',51,now=2)
    trace.stop()
    feed(m,2.04,75)
    assert len(m.report()['incidents'])==1
    assert len(trace.report()['events'])==1


def test_store_failure_does_not_escape_and_next_save_recovers(tmp_path, monkeypatch):
    monitor()
    import stutter
    path=tmp_path/'diagnostics'/'stutter-latest.json'
    store=stutter.IncidentStore(path)
    replace=stutter.os.replace
    def denied(*args): raise PermissionError('synthetic write failure')
    monkeypatch.setattr(stutter.os,'replace',denied)
    store.submit({'revision':1,'incidents':[{'reason':'receive'}]})
    deadline=time.monotonic()+2
    while store.busy and time.monotonic()<deadline: time.sleep(.005)
    assert store.last_error and not path.exists()
    monkeypatch.setattr(stutter.os,'replace',replace)
    store.submit({'revision':2,'incidents':[{'reason':'display'}]})
    deadline=time.monotonic()+2
    while store.busy and time.monotonic()<deadline: time.sleep(.005)
    assert not store.last_error
    assert json.loads(path.read_text(encoding='utf-8'))['revision']==2
