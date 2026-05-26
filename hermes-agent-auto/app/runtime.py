from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._events: List[Dict[str, Any]] = []
        self._max_events = 2000

    def create_run(self, title: str, prompt: str, kind: str = 'manual', task_id: Optional[int] = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = utcnow()
        with self._lock:
            self._runs[run_id] = {
                'run_id': run_id,
                'title': title,
                'prompt': prompt,
                'kind': kind,
                'task_id': task_id,
                'status': 'queued',
                'progress': 0,
                'current_step': 'Queued',
                'started_at': now,
                'updated_at': now,
                'finished_at': None,
                'report_path': None,
                'error': None,
                'sources_count': 0,
            }
        self.emit(run_id, 'queued', 'Run queued', progress=0, status='queued')
        return run_id

    def emit(self, run_id: str, event_type: str, message: str, *, progress: Optional[int] = None, status: Optional[str] = None, data: Optional[Dict[str, Any]] = None) -> None:
        now = utcnow()
        event = {
            'id': len(self._events) + 1,
            'run_id': run_id,
            'type': event_type,
            'message': message,
            'progress': progress,
            'status': status,
            'data': data or {},
            'created_at': now,
        }
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run['updated_at'] = now
                run['current_step'] = message
                if progress is not None:
                    run['progress'] = max(0, min(100, int(progress)))
                if status:
                    run['status'] = status
                if data:
                    if 'report_path' in data:
                        run['report_path'] = data['report_path']
                    if 'error' in data:
                        run['error'] = data['error']
                    if 'sources_count' in data:
                        run['sources_count'] = data['sources_count']
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

    def finish(self, run_id: str, status: str, *, report_path: Optional[str] = None, error: Optional[str] = None, sources_count: int = 0) -> None:
        progress = 100 if status == 'success' else 100
        data = {'report_path': report_path, 'error': error, 'sources_count': sources_count}
        self.emit(run_id, status, 'Run finished' if status == 'success' else 'Run failed', progress=progress, status=status, data=data)
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run['finished_at'] = utcnow()

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            runs = list(self._runs.values())
        runs.sort(key=lambda item: item.get('updated_at') or '', reverse=True)
        return [dict(run) for run in runs[:limit]]

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            run = self._runs.get(run_id)
            return dict(run) if run else None

    def events_after(self, last_id: int = 0, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            events = [event for event in self._events if event['id'] > last_id]
        if run_id:
            events = [event for event in events if event['run_id'] == run_id]
        return events

    def sse_stream(self, run_id: Optional[str] = None):
        last_id = 0
        while True:
            events = self.events_after(last_id, run_id=run_id)
            for event in events:
                last_id = max(last_id, event['id'])
                yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
            time.sleep(1)


run_store = RunStore()
