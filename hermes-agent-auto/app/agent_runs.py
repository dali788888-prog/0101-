from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/agent-runs', tags=['Live Agent Execution Trace Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,operator TEXT NOT NULL DEFAULT 'local-operator',risk_tier TEXT NOT NULL DEFAULT 'low',status TEXT NOT NULL DEFAULT 'queued',progress INTEGER NOT NULL DEFAULT 0,current_step TEXT,metadata_json TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_run_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,step_no INTEGER NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'running',detail TEXT,result_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


class RunStart(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    operator: str = 'local-operator'
    risk_tier: str = Field(default='low', pattern='^(low|medium|high|critical)$')
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunStep(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    status: str = Field(default='running', pattern='^(queued|running|success|failed|blocked|info)$')
    detail: str = ''
    result: Dict[str, Any] = Field(default_factory=dict)
    progress: Optional[int] = Field(default=None, ge=0, le=100)


class RunFinish(BaseModel):
    status: str = Field(default='success', pattern='^(success|failed|blocked|cancelled)$')
    detail: str = ''
    result: Dict[str, Any] = Field(default_factory=dict)


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def run_payload(run: Dict[str, Any]) -> Dict[str, Any]:
    run = dict(run)
    run['metadata'] = json.loads(run.pop('metadata_json') or '{}')
    run['steps'] = rows('SELECT * FROM agent_run_steps WHERE run_id=? ORDER BY step_no,id', (run['run_id'],))
    for s in run['steps']:
        s['result'] = json.loads(s.pop('result_json') or '{}')
    return run


@router.post('/start', dependencies=[Depends(require_key)])
def start_run(req: RunStart) -> Dict[str, Any]:
    ensure_tables()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run_id = f'run-{stamp}'
    ts = now()
    with db.connect() as conn:
        conn.execute('INSERT INTO agent_runs (run_id,title,operator,risk_tier,status,progress,current_step,metadata_json,created_at,started_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (run_id, req.title, req.operator, req.risk_tier, 'running', 1, 'started', jd(req.metadata), ts, ts, ts))
    db.audit('agent_run_start', 'agent_run', run_id, req.model_dump(), 'running', req.risk_tier, 'not_required')
    return {'status': 'running', 'run': run_payload(row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,)) or {})}


@router.post('/{run_id}/step', dependencies=[Depends(require_key)])
def add_step(run_id: str, req: RunStep) -> Dict[str, Any]:
    ensure_tables()
    run = row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail='run not found')
    last = row('SELECT MAX(step_no) n FROM agent_run_steps WHERE run_id=?', (run_id,)) or {'n': 0}
    step_no = int(last.get('n') or 0) + 1
    progress = req.progress if req.progress is not None else min(99, max(int(run.get('progress') or 0), step_no * 10))
    ts = now()
    with db.connect() as conn:
        conn.execute('INSERT INTO agent_run_steps (run_id,step_no,title,status,detail,result_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (run_id, step_no, req.title, req.status, req.detail, jd(req.result), ts, ts))
        conn.execute('UPDATE agent_runs SET status=?, progress=?, current_step=?, updated_at=? WHERE run_id=?', ('running' if req.status not in {'failed','blocked'} else req.status, progress, req.title, ts, run_id))
    db.audit('agent_run_step', 'agent_run', run_id, {'step_no': step_no, **req.model_dump()}, req.status, run.get('risk_tier') or 'low', 'not_required')
    return {'status': 'success', 'run': run_payload(row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,)) or {})}


@router.post('/{run_id}/finish', dependencies=[Depends(require_key)])
def finish_run(run_id: str, req: RunFinish) -> Dict[str, Any]:
    ensure_tables()
    run = row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail='run not found')
    ts = now()
    progress = 100 if req.status == 'success' else int(run.get('progress') or 0)
    with db.connect() as conn:
        conn.execute('UPDATE agent_runs SET status=?, progress=?, current_step=?, finished_at=?, updated_at=? WHERE run_id=?', (req.status, progress, req.detail or req.status, ts, ts, run_id))
        last = conn.execute('SELECT MAX(step_no) n FROM agent_run_steps WHERE run_id=?', (run_id,)).fetchone()
        step_no = int((dict(last).get('n') if last else 0) or 0) + 1
        conn.execute('INSERT INTO agent_run_steps (run_id,step_no,title,status,detail,result_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (run_id, step_no, 'finish', req.status, req.detail, jd(req.result), ts, ts))
    db.audit('agent_run_finish', 'agent_run', run_id, req.model_dump(), req.status, run.get('risk_tier') or 'low', 'not_required')
    return {'status': req.status, 'run': run_payload(row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,)) or {})}


@router.get('/current')
def current_run() -> Dict[str, Any]:
    run = row("SELECT * FROM agent_runs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1")
    if not run:
        run = row('SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1')
    return {'status': 'ok', 'run': run_payload(run) if run else None}


@router.get('')
def list_runs(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?', (limit,))
    for r in data:
        r['metadata'] = json.loads(r.pop('metadata_json') or '{}')
    return {'status': 'ok', 'runs': data}


@router.get('/{run_id}')
def get_run(run_id: str) -> Dict[str, Any]:
    run = row('SELECT * FROM agent_runs WHERE run_id=?', (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail='run not found')
    return {'status': 'ok', 'run': run_payload(run)}
