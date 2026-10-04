import pytest


def test_identity_reacquires_new_qscreen_with_changed_name_and_id():
    from platform_support import match_mac_display
    old_identity={'uuid':'target-uuid','identifier':10,'builtin':False}
    new=object();other=object()
    candidates=[(other,{'uuid':'laptop-uuid','identifier':1,'builtin':True,'pixels':(2880,1800)}),
                (new,{'uuid':'target-uuid','identifier':22,'builtin':False,'pixels':(1920,1080)})]
    assert match_mac_display(old_identity,candidates)==(new,'matched_uuid')


@pytest.mark.parametrize('replacement',[
    {'uuid':None,'identifier':10,'builtin':False,'pixels':(1920,1080)},
    {'uuid':'wrong-uuid','identifier':10,'builtin':False,'pixels':(1920,1080)},
    {'uuid':'target-uuid','identifier':22,'builtin':True,'pixels':(1920,1080)}])
def test_known_uuid_never_falls_back_to_same_id_or_geometry(replacement):
    from platform_support import match_mac_display
    assert match_mac_display({'uuid':'target-uuid','identifier':10,'builtin':False},[(object(),replacement)])[0] is None


def test_duplicate_identity_and_unknown_mode_fail_closed():
    from platform_support import match_mac_display
    target={'uuid':'target-uuid','identifier':10,'builtin':False}
    info=dict(target,pixels=(1920,1080))
    assert match_mac_display(target,[(object(),info),(object(),info)])[1]=='identity_ambiguous'
    assert match_mac_display(target,[(object(),dict(info,pixels=None))])[1]=='mode_unavailable'
    assert match_mac_display(target,[(object(),info),(object(),dict(info,builtin=True))])[1]=='identity_ambiguous'


def test_missing_uuid_from_start_allows_only_same_unique_in_session_id():
    from platform_support import match_mac_display
    target={'uuid':None,'identifier':10,'builtin':False};screen=object()
    assert match_mac_display(target,[(screen,dict(target,pixels=(1920,1080)))])==(screen,'matched_id')
    assert match_mac_display(target,[(screen,dict(target,identifier=11,pixels=(1920,1080)))])[0] is None
    assert match_mac_display({},[(screen,dict(target,pixels=(1920,1080)))])[1]=='identity_unknown'


@pytest.fixture
def topology_window(monkeypatch):
    import app,platform_support
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication
    qt=QApplication.instance() or QApplication([])
    window=app.MainWindow(False);window.timer.stop();window.output.winId()
    class Screen:
        def __init__(self,name,identifier,uuid,builtin=False):
            self.label=name;self.identifier=identifier;self.uuid=uuid;self.builtin=builtin;self.ready=True
        def name(self):return self.label
        def model(self):return 'same model'
        def geometry(self):return QRect(0 if self.builtin else 1440,0,1440 if self.builtin else 1920,900 if self.builtin else 1080)
        def refreshRate(self):return 60
        def devicePixelRatio(self):return 2 if self.builtin else 1
    laptop=Screen('same name',1,'laptop',True);target=Screen('same name',10,'target')
    current=[laptop,target];activated=[];presented=[]
    monkeypatch.setattr(app.sys,'platform','darwin')
    monkeypatch.setattr(QApplication,'screens',staticmethod(lambda:current[:]))
    from PySide6.QtCore import Qt
    monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationActive))
    def info(screen):
        return {'pixels':(2880,1800) if screen.builtin else ((1920,1080) if screen.ready else None),
            'reason':'ok' if screen.ready else 'screen_unmatched','identifier':screen.identifier,
            'uuid':screen.uuid,'builtin':screen.builtin}
    monkeypatch.setattr(platform_support,'mac_display_info',info)
    monkeypatch.setattr(window.output,'keep_awake',lambda *_:True)
    monkeypatch.setattr(window.output,'activateWindow',lambda:activated.append(True))
    def present(screen):
        window.output.output_screen=screen;presented.append(screen)
    monkeypatch.setattr(window.output,'present',present)
    window.refresh_screens();window.screens.setCurrentIndex(1);window.open_output()
    try:yield window,current,target,Screen,activated,presented
    finally:window.release_output();window.close();qt.processEvents()


def test_remove_add_new_qscreen_restores_cached_physical_target_not_name(topology_window):
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    assert window.output_pending and not window.output.locked
    new=Screen('changed name',22,'target');current.append(new);window.screen_added(new)
    window.restore_output()
    assert window.output.locked and not window.output_pending and window.output.output_screen is new
    assert len(activated)==1 and presented==[old,new]


def test_same_name_and_id_with_wrong_uuid_never_restores(topology_window):
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    new=Screen(old.name(),10,'wrong');current.append(new);window.screen_added(new);window.restore_output()
    assert not window.output.locked and window.output_pending and presented==[old]


def test_user_cancel_invalidates_queued_topology_restore(topology_window):
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    new=Screen('changed name',22,'target');current.append(new);window.screen_added(new)
    window.release_output();window.restore_output()
    assert not window.output.locked and not window.output_pending and not window.output_requested
    assert presented==[old] and not window.output_topology_timer.isActive()


def test_mode_settles_after_added_event_and_duplicate_identity_stays_pending(topology_window):
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    new=Screen('changed name',22,'target');new.ready=False;current.append(new)
    window.screen_added(new);window.restore_output()
    assert not window.output.locked and window.output_pending
    new.ready=True;window.refresh_screens();window.restore_output()
    assert window.output.locked and window.output.output_screen is new
    current.remove(new);window.screen_removed(new)
    first=Screen('first',30,'target');second=Screen('second',31,'target');current.extend([first,second])
    window.screen_added(first);window.restore_output()
    assert not window.output.locked and window.output_pending and presented==[old,new]


def test_restore_attempts_are_bounded_until_a_new_topology_event(topology_window):
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    for _ in range(8):window.restore_output()
    assert window.output_topology_attempts==5 and not window.output_topology_timer.isActive()
    new=Screen('changed name',22,'target');current.append(new);window.screen_added(new)
    window.restore_output()
    assert window.output.locked and window.output.output_screen is new and len(activated)==1


def test_topology_report_has_safe_decision_and_timing_without_native_identity(topology_window):
    import json
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old);window.restore_output()
    text=window.problem_report();output=json.loads(text.split('\n',1)[1])['output']
    assert output['requested'] is True and output['pending'] is True
    assert output['restore_reason']=='target_missing' and output['restore_attempts']==1
    assert all(type(event['elapsed_ms']) is int for event in output['events'])
    assert 'uuid' not in text and 'identifier' not in text and 'same name' not in text


def test_inactive_app_defers_restore_and_activation_resumes_bounded_matching(topology_window,monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old)
    new=Screen('changed name',22,'target');current.append(new);window.screen_added(new)
    monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationInactive))
    window.restore_output()
    assert not window.output.locked and window.output_pending and window.output_restore_reason=='app_inactive'
    assert presented==[old] and not window.output_topology_timer.isActive()
    monkeypatch.setattr(QApplication,'applicationState',staticmethod(lambda:Qt.ApplicationActive))
    window.resume_output_restore(Qt.ApplicationActive);window.restore_output()
    assert window.output.locked and presented==[old,new] and len(activated)==1


def test_latest_topology_event_restarts_the_settle_delay(topology_window):
    from PySide6.QtTest import QTest
    window,current,old,Screen,activated,presented=topology_window
    current.remove(old);window.screen_removed(old);QTest.qWait(100)
    assert 0<window.output_topology_timer.remainingTime()<250
    new=Screen('changed name',22,'target');current.append(new);window.screen_added(new)
    assert window.output_topology_timer.remainingTime()>250
