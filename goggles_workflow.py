"""Bounded edit history and opt-in timing capture; contains no video data."""
from collections import deque
from statistics import median
import threading
import time

class LookHistory:
    def __init__(self, initial, limit=80):
        self.values = [initial]
        self.index = 0
        self.limit = limit
        self.gesture = False
        self.merging = False

    def begin(self):
        self.gesture = True
        self.merging = False

    def end(self):
        self.gesture = False
        self.merging = False

    def record(self, value):
        if value == self.values[self.index]: return
        del self.values[self.index+1:]
        if self.gesture and self.merging:
            self.values[self.index] = value
        else:
            self.values.append(value)
            self.values = self.values[-self.limit:]
            self.index = len(self.values)-1
            self.merging = self.gesture

    def step(self, delta):
        self.end()
        self.index = max(0, min(len(self.values)-1, self.index+delta))
        return self.values[self.index]

class FrameTrace:
    def __init__(self, limit=18000, monitor=None):
        self.monitor = monitor
        self.lock = threading.Lock()
        self.events = deque(maxlen=limit)
        self.active = False
        self.total = 0
        self.origin = 0.
        self.run_id = 0

    def start(self, now=None):
        with self.lock:
            self.events.clear()
            self.total = 0
            self.origin = time.perf_counter() if now is None else now
            self.run_id += 1
            self.active = True

    def stop(self):
        with self.lock: self.active = False

    def add(self, stage, frame_id, now=None, **extra):
        if not self.active and self.monitor is None: return
        stamp = time.perf_counter() if now is None else now
        if self.monitor is not None:
            self.monitor.add(stage,frame_id,now=stamp,**extra)
        if not self.active: return
        with self.lock:
            if not self.active: return
            self.events.append({'stage': stage, 'frame': frame_id,
                                'ms': round((stamp-self.origin)*1000, 4), **extra})
            self.total += 1

    @staticmethod
    def distribution(values):
        if not values: return {'count': 0, 'median': None, 'p95': None, 'max': None}
        values = sorted(values)
        return {'count':len(values), 'median':round(median(values),3),
                'p95':round(values[min(len(values)-1, int((len(values)-1)*.95))],3),
                'max':round(values[-1],3)}

    def report(self):
        with self.lock:
            events = list(self.events)
            total = self.total
        stages = {}
        for event in events:
            stages.setdefault(event['frame'], {})[event['stage']] = event['ms']
        summary = {}
        for name, start, end in [('queue_ms','receive','process_start'),
                                  ('processing_ms','process_start','ready'),
                                  ('ready_to_submit_ms','ready','submit'),
                                  ('submit_to_paint_ms','submit','paint')]:
            summary[name] = self.distribution([e[end]-e[start] for e in stages.values()
                                              if start in e and end in e and e[end]>=e[start]])
        for stage in ('receive','ready','submit','paint'):
            stamps = [e['ms'] for e in events if e['stage']==stage]
            summary[stage+'_interval_ms'] = self.distribution([b-a for a,b in zip(stamps,stamps[1:])])
        return {'version':1, 'events_truncated': total>len(events), 'summary':summary, 'events':events,
                'measurement_scope':'receive is after decode; paint is Qt paint callback, not physical display scanout; no end-to-end latency measurement',
                'video_recorded':False}
