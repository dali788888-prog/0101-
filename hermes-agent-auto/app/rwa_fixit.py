from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/rwa-mine', tags=['RWA Mine Fix Suggestion Engine'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class FixGenerateRequest(BaseModel):
    scan_id: Optional[int] = None
    only_blocking: bool = False
    create_project_tasks: bool = True
    prefix: str = Field(default='FIX', min_length=2, max_length=12)


class FixTaskUpdate(BaseModel):
    status: str = Field(default='todo', pattern='^(todo|doing|blocked|done|rejected)$')
    note: str = ''
    evidence: str = ''


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_fix_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,fix_id TEXT NOT NULL UNIQUE,scan_id INTEGER NOT NULL,finding_code TEXT NOT NULL,severity TEXT NOT NULL,area TEXT NOT NULL,source_path TEXT,message TEXT NOT NULL,recommendation TEXT,project_task_id TEXT,status TEXT NOT NULL DEFAULT 'todo',note TEXT,evidence TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def latest_or_selected_scan(scan_id: Optional[int]) -> Dict[str, Any]:
    ensure_tables()
    if scan_id:
        rec = qrow('SELECT * FROM rwa_mine_quality_scans WHERE id=?', (scan_id,))
    else:
        rec = qrow('SELECT * FROM rwa_mine_quality_scans ORDER BY id DESC LIMIT 1')
    if not rec:
        raise HTTPException(status_code=404, detail='quality scan not found. Run POST /rwa-mine/quality/scan first.')
    return rec


def area_to_module(area: str) -> str:
    if area in {'frontend'}:
        return 'frontend'
    if area in {'contracts'}:
        return 'contract'
    if area in {'backend'}:
        return 'backend'
    return 'backend'


def severity_to_priority(severity: str) -> str:
    if severity == 'blocking':
        return 'P0'
    if severity == 'warning':
        return 'P1'
    return 'P2'


def normalize_prefix(prefix: str) -> str:
    return re.sub(r'[^A-Z0-9_-]+', '', prefix.upper())[:12] or 'FIX'


def next_fix_id(prefix: str) -> str:
    row = qrow("SELECT fix_id FROM rwa_mine_fix_tasks WHERE fix_id LIKE ? ORDER BY id DESC LIMIT 1", (prefix + '-%',))
    if not row:
        return f'{prefix}-001'
    try:
        n = int(str(row['fix_id']).split('-')[-1]) + 1
    except Exception:
        n = 1
    return f'{prefix}-{n:03d}'


def create_project_task(conn, fix_id: str, finding: Dict[str, Any], create: bool) -> Optional[str]:
    if not create:
        return None
    module = area_to_module(finding.get('area', 'backend'))
    priority = severity_to_priority(finding.get('severity', 'info'))
    task_id = fix_id
    exists = conn.execute('SELECT task_id FROM rwa_mine_tasks WHERE task_id=?', (task_id,)).fetchone()
    if exists:
        return task_id
    description = f"修复质量扫描问题：{finding.get('code')} — {finding.get('message')}"
    note = f"source_path={finding.get('path','')}; recommendation={finding.get('recommendation','')}"
    ts = now()
    conn.execute('INSERT INTO rwa_mine_tasks (task_id,module,sprint,description,priority,estimate_days,owner,status,note,evidence,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (task_id, module, 'Quality Fix', description, priority, 1, '安全/工程', 'todo', note, '', ts, ts))
    return task_id


@router.post('/fixit/from-quality-scan', dependencies=[Depends(require_key)])
def generate_fix_tasks(req: FixGenerateRequest) -> Dict[str, Any]:
    ensure_tables()
    scan = latest_or_selected_scan(req.scan_id)
    scan_id = int(scan['id'])
    findings = json.loads(scan.get('findings_json') or '[]')
    if req.only_blocking:
        findings = [f for f in findings if f.get('severity') == 'blocking']
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    prefix = normalize_prefix(req.prefix)
    with db.connect() as conn:
        for f in findings:
            code = f.get('code', 'UNKNOWN')
            path = f.get('path', '') or ''
            duplicate = conn.execute('SELECT * FROM rwa_mine_fix_tasks WHERE scan_id=? AND finding_code=? AND IFNULL(source_path,"")=?', (scan_id, code, path)).fetchone()
            if duplicate:
                skipped.append(dict(duplicate))
                continue
            fix_id = next_fix_id(prefix)
            # protect against same transaction next_fix_id not seeing pending row on some sqlite modes
            while conn.execute('SELECT fix_id FROM rwa_mine_fix_tasks WHERE fix_id=?', (fix_id,)).fetchone():
                prefix_part, n_part = fix_id.rsplit('-', 1)
                fix_id = f'{prefix_part}-{int(n_part)+1:03d}'
            project_task_id = create_project_task(conn, fix_id, f, req.create_project_tasks)
            ts = now()
            conn.execute('INSERT INTO rwa_mine_fix_tasks (fix_id,scan_id,finding_code,severity,area,source_path,message,recommendation,project_task_id,status,note,evidence,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (fix_id, scan_id, code, f.get('severity','info'), f.get('area','unknown'), path, f.get('message',''), f.get('recommendation',''), project_task_id, 'todo', '', '', ts, ts))
            created.append({'fix_id': fix_id, 'project_task_id': project_task_id, 'finding': f})
    db.audit('rwa_mine_fixit_generate', 'rwa_mine_quality_scan', str(scan_id), {'created': len(created), 'skipped': len(skipped), 'only_blocking': req.only_blocking}, 'success', 'medium', 'not_required')
    return {'status': 'success', 'scan_id': scan_id, 'created_count': len(created), 'skipped_count': len(skipped), 'created': created, 'skipped': skipped}


@router.get('/fixit/tasks')
def list_fix_tasks(status: Optional[str] = None, severity: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_tables()
    clauses = ['1=1']
    args: list[Any] = []
    if status:
        clauses.append('status=?'); args.append(status)
    if severity:
        clauses.append('severity=?'); args.append(severity)
    args.append(limit)
    return qrows(f"SELECT * FROM rwa_mine_fix_tasks WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", tuple(args))


@router.get('/fixit/summary')
def fixit_summary() -> Dict[str, Any]:
    ensure_tables()
    rows = qrows('SELECT severity,status,COUNT(*) c FROM rwa_mine_fix_tasks GROUP BY severity,status')
    total = qrow('SELECT COUNT(*) c FROM rwa_mine_fix_tasks') or {'c': 0}
    open_count = qrow("SELECT COUNT(*) c FROM rwa_mine_fix_tasks WHERE status IN ('todo','doing','blocked')") or {'c': 0}
    done_count = qrow("SELECT COUNT(*) c FROM rwa_mine_fix_tasks WHERE status='done'") or {'c': 0}
    blocking_open = qrow("SELECT COUNT(*) c FROM rwa_mine_fix_tasks WHERE severity='blocking' AND status!='done'") or {'c': 0}
    return {'status': 'ok', 'total': int(total['c']), 'open': int(open_count['c']), 'done': int(done_count['c']), 'blocking_open': int(blocking_open['c']), 'breakdown': rows}


@router.post('/fixit/tasks/{fix_id}/status', dependencies=[Depends(require_key)])
def update_fix_task(fix_id: str, req: FixTaskUpdate) -> Dict[str, Any]:
    ensure_tables()
    task = qrow('SELECT * FROM rwa_mine_fix_tasks WHERE fix_id=?', (fix_id,))
    if not task:
        raise HTTPException(status_code=404, detail='fix task not found')
    ts = now()
    with db.connect() as conn:
        conn.execute('UPDATE rwa_mine_fix_tasks SET status=?, note=?, evidence=?, updated_at=? WHERE fix_id=?', (req.status, req.note, req.evidence, ts, fix_id))
        if task.get('project_task_id'):
            mapped_status = 'done' if req.status == 'done' else 'blocked' if req.status == 'blocked' else 'doing' if req.status == 'doing' else 'todo'
            conn.execute('UPDATE rwa_mine_tasks SET status=?, note=?, evidence=?, updated_at=? WHERE task_id=?', (mapped_status, req.note, req.evidence, ts, task['project_task_id']))
    db.audit('rwa_mine_fixit_update', 'rwa_mine_fix_task', fix_id, req.model_dump(), 'success', 'low', 'not_required')
    return qrow('SELECT * FROM rwa_mine_fix_tasks WHERE fix_id=?', (fix_id,)) or {'fix_id': fix_id}
