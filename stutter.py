"""Bounded timing-only stall capture and asynchronous, atomic diagnostic storage."""
from collections import deque
from copy import deepcopy
from statistics import median
import json
import os
from pathlib import Path
import threading
import tempfile
import time


LABELS = {'receive':'수신 구간 지연', 'processing':'보정 처리 구간 지연', 'display':'화면 표시 구간 지연'}
SCOPE = ('Receive is after decode; display is application submission/Qt paint, not physical '
         'HDMI scanout. Labels identify an observed pipeline boundary, not a hardware root '
         'cause or end-to-end latency. No video is recorded.')


class StutterMonitor:
    def __init__(self, event_limit=3600, incident_limit=5):
        self.lock = threading.Lock()
        self.events = deque(maxlen=event_limit)
        self.incidents = deque(maxlen=incident_limit)
        self.intervals = deque(maxlen=90)
        self.last = {}
        self.active = False
        self.display_expected = False
        self.display_since = 0.
        self.origin = 0.
        self.session = 0
        self.revision = 0
        self.incident_count = 0
        self.pending = None
        self.cooldown_until = 0.

    def start(self, now=None):
        now = time.perf_counter() if now is None else now
        with self.lock:
            self._finish(now)
            self.events.clear()
            self.intervals.clear()
            self.last.clear()
            self.session += 1
            self.origin = now
            self.first_received = None
            self.cooldown_until = now
            self.active = True

    def stop(self, now=None):
        now = time.perf_counter() if now is None else now
        with self.lock:
            self._finish(now)
            self.active = False

    def _threshold(self):
        return max(.100, 3.5*median(self.intervals)) if self.intervals else .100

    def _trigger(self, reason, gap, stamp):
        if stamp-self.origin < 1 or len(self.intervals)<8: return
        if self.pending:
            if self.pending['reason']==reason:
                self.pending['gap_ms'] = max(self.pending['gap_ms'], round(gap*1000,2))
            return
        if stamp < self.cooldown_until: return
        self.pending = {'reason':reason, 'label':LABELS[reason], 'session':self.session,
                        'trigger_ms':round((stamp-self.origin)*1000,3),
                        'gap_ms':round(gap*1000,2), 'threshold_ms':round(self._threshold()*1000,2),
                        'trigger_at':stamp, 'captured_at':time.strftime('%Y-%m-%d %H:%M:%S')}

    def _finish(self, now):
        if self.pending is None: return
        incident = self.pending
        self.pending = None
        start_ms = incident['trigger_ms']-3000
        incident.pop('trigger_at')
        incident['events'] = [dict(e) for e in self.events if e['ms']>=start_ms]
        incident['context_truncated'] = bool(len(self.events)==self.events.maxlen and
                                             self.events[0]['ms']>max(0,start_ms))
        incident['post_context_ms'] = round((now-self.origin)*1000-incident['trigger_ms'],2)
        self.incidents.append(incident)
        self.incident_count += 1
        self.revision += 1
        self.cooldown_until = now+5

    def add(self, stage, frame_id, now=None, **extra):
        stamp = time.perf_counter() if now is None else now
        with self.lock:
            if not self.active: return
            if self.pending and stamp >= self.pending['trigger_at']+2:
                self._finish(stamp)
            previous = self.last.get(stage)
            if stage=='receive':
                if self.first_received is None: self.first_received = stamp
                if previous is not None and 0 < stamp-previous < 2:
                    self.intervals.append(stamp-previous)
            if previous is not None and stamp-previous > self._threshold():
                reason = {'receive':'receive','ready':'processing','submit':'display','paint':'display'}.get(stage)
                if reason and (stage!='paint' or self.display_expected):
                    self._trigger(reason,stamp-previous,stamp)
            self.last[stage] = stamp
            # Deliberately whitelist timing fields; never retain images or source payloads.
            self.events.append({'stage':stage, 'frame':frame_id, 'ms':round((stamp-self.origin)*1000,3)})

    def poll(self, now=None, display_expected=None):
        now = time.perf_counter() if now is None else now
        with self.lock:
            if display_expected is not None and display_expected != self.display_expected:
                self.display_expected = display_expected
                self.display_since = now
                self.last.pop('paint',None)
            if not self.active: return
            if self.pending and now >= self.pending['trigger_at']+2:
                self._finish(now)
            if self.first_received is None: return
            for stage,reason in [('receive','receive'),('ready','processing'),('submit','display')]:
                gap = now-self.last.get(stage,self.first_received)
                if gap > self._threshold():
                    self._trigger(reason,gap,now)
                    return
            if self.display_expected:
                gap = now-self.last.get('paint',max(self.first_received,self.display_since))
                if gap > self._threshold(): self._trigger('display',gap,now)

    def status(self):
        with self.lock:
            latest = self.pending or (self.incidents[-1] if self.incidents else None)
            return {'revision':self.revision, 'count':self.incident_count,
                    'pending':self.pending is not None,
                    'label':latest['label'] if latest else '',
                    'gap_ms':latest['gap_ms'] if latest else 0}

    def report(self):
        with self.lock:
            return {'version':1, 'revision':self.revision, 'incident_count':self.incident_count,
                    'incidents':deepcopy(list(self.incidents)), 'video_recorded':False,
                    'measurement_scope':SCOPE, 'retained_limit':self.incidents.maxlen}


class IncidentStore:
    """One writer and one pending snapshot; no file I/O on the producer/UI path."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.pending = None
        self.worker = None
        self.last_error = None

    @property
    def busy(self):
        with self.lock: return self.worker is not None

    def submit(self, report):
        with self.lock:
            self.pending = report
            if self.worker is not None: return
            self.worker = threading.Thread(target=self._write,daemon=False)
            self.worker.start()

    def _write(self):
        while True:
            with self.lock:
                if self.pending is None:
                    self.worker = None
                    return
                report, self.pending = self.pending, None
            temporary = None
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=self.path.stem+'-',suffix='.tmp',dir=self.path.parent)
                os.close(descriptor)
                temporary = Path(name)
                temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                os.replace(temporary,self.path)
                self.last_error = None
            except OSError as exc:
                self.last_error = type(exc).__name__
            finally:
                if temporary is not None:
                    try: temporary.unlink(missing_ok=True)
                    except OSError: pass
