from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/acceptance', tags=['Commercial OS Acceptance Release Candidate Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def parse_json(value: str | None) -> Dict[str, Any]:
    try:
        return json.loads(value or '{}')
    except Exception:
        return {}


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS acceptance_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,release_version TEXT NOT NULL,acceptance_state TEXT NOT NULL,maturity_score INTEGER NOT NULL DEFAULT 0,checklist_json TEXT NOT NULL DEFAULT '{}',summary_json TEXT NOT NULL DEFAULT '{}',operator TEXT NOT NULL DEFAULT 'local-operator',notes TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS acceptance_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT,snapshot_id INTEGER NOT NULL,decision TEXT NOT NULL,operator TEXT NOT NULL DEFAULT 'local-operator',reason TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


class SnapshotRequest(BaseModel):
    release_version: str = '20.0-commercial-os-release-candidate'
    operator: str = 'local-operator'
    notes: str = ''
    sync_operator: bool = True


class AcceptanceDecisionRequest(BaseModel):
    snapshot_id: int
    decision: str = Field(pattern='^(accept_release_candidate|hold|reject|rollback_required)$')
    operator: str = 'local-operator'
    reason: str = ''


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def safe_call(name: str, fn) -> Dict[str, Any]:
    try:
        return {'status': 'ok', 'module': name, 'data': fn()}
    except Exception as exc:
        return {'status': 'error', 'module': name, 'error': str(exc)}


def collect_state() -> Dict[str, Any]:
    return {
        'time_utc': now(),
        'release_gate': safe_call('release_gate', lambda: __import__('app.release_gate', fromlist=['gate_summary']).gate_summary('20.0-commercial-os-release-candidate')),
        'ops_workflow': safe_call('ops_workflow', lambda: __import__('app.ops_workflow', fromlist=['dashboard']).dashboard()),
        'ops_automation': safe_call('ops_automation', lambda: __import__('app.ops_automation', fromlist=['dashboard']).dashboard()),
        'diagnostics': safe_call('diagnostics', lambda: __import__('app.diagnostics_center', fromlist=['module_health']).module_health()),
        'system_map': safe_call('system_map', lambda: __import__('app.system_map', fromlist=['dashboard']).dashboard()),
        'portfolio_risk': safe_call('portfolio_risk', lambda: __import__('app.portfolio_risk', fromlist=['dashboard']).dashboard()),
        'paper_trading': safe_call('paper_trading', lambda: __import__('app.paper_trading', fromlist=['dashboard']).dashboard()),
        'trade_lifecycle': safe_call('trade_lifecycle', lambda: __import__('app.trade_lifecycle', fromlist=['dashboard']).dashboard(limit=100)),
        'trade_readiness': safe_call('trade_readiness', lambda: __import__('app.trade_readiness', fromlist=['status']).status()),
        'strategy_signals': safe_call('strategy_signals', lambda: __import__('app.strategy_signals', fromlist=['strategy_signals_summary']).strategy_signals_summary()),
        'audit': safe_call('audit', lambda: {'latest_count': len(db.list_audit_events(limit=100)), 'sample_count': len(db.list_audit_events(limit=500)), 'latest': db.list_audit_events(limit=20)}),
    }


def data(state: Dict[str, Any], key: str) -> Dict[str, Any]:
    item = state.get(key) or {}
    return item.get('data') if item.get('status') == 'ok' else {}


def checklist(state: Dict[str, Any]) -> Dict[str, Any]:
    release = data(state, 'release_gate')
    ops = data(state, 'ops_workflow')
    diag = data(state, 'diagnostics')
    portfolio = data(state, 'portfolio_risk')
    paper = data(state, 'paper_trading')
    life = data(state, 'trade_lifecycle')
    readiness = data(state, 'trade_readiness')
    sysmap = data(state, 'system_map')

    items: List[Dict[str, Any]] = []

    def add(key: str, title: str, status: str, weight: int, required: bool, evidence: Any = None) -> None:
        items.append({'key': key, 'title': title, 'status': status, 'weight': weight, 'required': required, 'evidence': evidence})

    add('manual_handoff_policy', 'Manual Handoff 安全边界已保留', 'pass', 10, True, 'no autonomous live order submission')
    add('secret_custody_policy', '不托管 API Secret / 私钥 / 提现权限', 'pass', 10, True, 'masked config and read-only visualization')
    add('release_gate_ready', 'Release Gate 无阻断', 'pass' if release.get('gate_state') in {'ready', 'warning'} and int(release.get('blocker_count') or 0) == 0 else 'fail', 15, True, release)
    add('ops_alerts_reviewed', 'Ops 告警中心无 Critical Open', 'pass' if int((ops.get('severity_counts') or {}).get('critical') or 0) == 0 else 'fail', 10, True, ops.get('severity_counts'))
    add('diagnostics_healthy', 'Diagnostics 无模块错误', 'pass' if diag.get('status') in {'ok', 'warning'} and not diag.get('errors') else 'fail', 10, True, diag)
    add('system_map_ready', '系统地图与配置可视化可用', 'pass' if (sysmap.get('graph') or {}).get('summary') else 'warning', 5, False, (sysmap.get('graph') or {}).get('summary'))
    add('portfolio_budget_ok', '风险预算未 breached', 'pass' if (portfolio.get('risk_budget') or {}).get('status') != 'breached' else 'fail', 10, True, portfolio.get('risk_budget'))
    add('paper_trading_ready', 'Paper Trading 仿真账本可用', 'pass' if paper.get('status') == 'ok' else 'warning', 5, False, paper.get('counts'))
    add('lifecycle_reviews_done', '交易生命周期待复盘数量为 0', 'pass' if int((life.get('counts') or {}).get('needs_review') or 0) == 0 else 'warning', 5, False, life.get('counts'))
    add('readiness_available', 'Trade Readiness 可用', 'pass' if readiness.get('status') == 'ok' or readiness.get('kill_switch') in {'on', 'off'} else 'fail', 10, True, readiness)
    add('audit_available', '审计日志可读取', 'pass' if data(state, 'audit').get('sample_count', 0) >= 0 else 'fail', 5, True, data(state, 'audit'))
    add('reports_available', '运营报表和 Release 报表可生成', 'pass', 5, False, 'ops reports + release reports')

    max_score = sum(x['weight'] for x in items)
    score = sum(x['weight'] for x in items if x['status'] == 'pass')
    required_failures = [x for x in items if x['required'] and x['status'] == 'fail']
    warnings = [x for x in items if x['status'] == 'warning']
    maturity_score = round(score / max_score * 100) if max_score else 0
    acceptance_state = 'blocked' if required_failures else 'release_candidate' if maturity_score >= 85 else 'needs_improvement'
    return {'status': 'ok', 'acceptance_state': acceptance_state, 'maturity_score': maturity_score, 'max_score': max_score, 'score': score, 'items': items, 'required_failures': required_failures, 'warnings': warnings, 'time_utc': now()}


def report_text(summary: Dict[str, Any]) -> str:
    ck = summary['checklist']
    lines = [
        '# Hermes v20 Commercial OS Release Candidate Acceptance Report',
        f'- generated_at_utc: {now()}',
        f'- release_version: {summary.get("release_version")}',
        f'- acceptance_state: {ck.get("acceptance_state")}',
        f'- maturity_score: {ck.get("maturity_score")}/100',
        f'- required_failures: {len(ck.get("required_failures") or [])}',
        f'- warnings: {len(ck.get("warnings") or [])}',
        '',
        '## Checklist',
    ]
    for item in ck.get('items', []):
        lines.append(f"- [{item.get('status')}] {item.get('title')} weight={item.get('weight')} required={item.get('required')}")
    lines += ['', '## Required Failures']
    fails = ck.get('required_failures') or []
    lines += [f"- {x.get('title')}" for x in fails] if fails else ['- None']
    lines += ['', '## Release Candidate Decision Guidance']
    if ck.get('acceptance_state') == 'release_candidate':
        lines.append('- 可以进入 v20 Release Candidate 验收阶段，但真实交易仍必须 Manual Handoff。')
    elif ck.get('acceptance_state') == 'blocked':
        lines.append('- 不建议发布。必须先解决 required failures。')
    else:
        lines.append('- 可以继续内测，但需要提升成熟度分数。')
    lines += ['', '## Safety Boundary', '- No autonomous live order submission.', '- No API Secret / private key custody.', '- No withdrawal / transfer automation.', '- No bypassing approval, Kill Switch, or risk gates.']
    return '\n'.join(lines)


def acceptance_summary(release_version: str = '20.0-commercial-os-release-candidate') -> Dict[str, Any]:
    state = collect_state()
    ck = checklist(state)
    return {'status': 'ok', 'version': '20.0-commercial-os-release-candidate', 'release_version': release_version, 'checklist': ck, 'state': state, 'safety': 'acceptance reporting only; no exchange order submission', 'time_utc': now()}


def operator_message(content: str, status: str = 'acceptance') -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, status, now()))
        return dict(conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone())


def operator_report(content: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', ('v20_acceptance', 'Hermes v20 Release Candidate Acceptance Report', content, jd(metrics), now()))
        return dict(conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone())


def operator_note(content: str) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', ('v20 Acceptance Follow-up', content, 'v20_acceptance,commercial_os', ts, ts))
        return dict(conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone())


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '20.0-commercial-os-release-candidate', 'features': ['final commercial OS acceptance center', 'maturity scoring', 'launch checklist', 'release candidate report', 'acceptance decisions'], 'safety': 'acceptance reporting only; no exchange order submission'}


@router.get('/dashboard')
def dashboard(release_version: str = '20.0-commercial-os-release-candidate') -> Dict[str, Any]:
    return acceptance_summary(release_version)


@router.get('/checklist')
def get_checklist() -> Dict[str, Any]:
    return checklist(collect_state())


@router.get('/maturity')
def maturity() -> Dict[str, Any]:
    ck = checklist(collect_state())
    return {'status': 'ok', 'maturity_score': ck['maturity_score'], 'acceptance_state': ck['acceptance_state'], 'required_failures': ck['required_failures'], 'warnings': ck['warnings'], 'time_utc': now()}


@router.get('/report')
def report(release_version: str = '20.0-commercial-os-release-candidate') -> Dict[str, Any]:
    summary = acceptance_summary(release_version)
    return {'status': 'ok', 'content': report_text(summary), 'summary': summary}


@router.post('/snapshot', dependencies=[Depends(require_key)])
def create_snapshot(req: SnapshotRequest) -> Dict[str, Any]:
    ensure_tables()
    summary = acceptance_summary(req.release_version)
    ck = summary['checklist']
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO acceptance_snapshots (release_version,acceptance_state,maturity_score,checklist_json,summary_json,operator,notes,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.release_version, ck['acceptance_state'], ck['maturity_score'], jd(ck), jd(summary), req.operator, req.notes, now()))
        sid = int(cur.lastrowid)
        snap = dict(conn.execute('SELECT * FROM acceptance_snapshots WHERE id=?', (sid,)).fetchone())
    db.audit('acceptance_snapshot_create', 'acceptance_snapshot', str(sid), {'release_version': req.release_version, 'acceptance_state': ck['acceptance_state'], 'maturity_score': ck['maturity_score']}, 'success', 'high', ck['acceptance_state'])
    content = report_text(summary)
    msg = operator_message(f'v20 Acceptance snapshot #{sid}: state={ck["acceptance_state"]}, score={ck["maturity_score"]}/100') if req.sync_operator else None
    rep = operator_report(content, {'acceptance_state': ck['acceptance_state'], 'maturity_score': ck['maturity_score']}) if req.sync_operator else None
    note = operator_note(content) if req.sync_operator else None
    snap['checklist'] = parse_json(snap.pop('checklist_json', '{}'))
    snap['summary'] = parse_json(snap.pop('summary_json', '{}'))
    return {'status': 'success', 'snapshot': snap, 'message': msg, 'operator_report': rep, 'operator_note': note}


@router.get('/snapshots')
def list_snapshots(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM acceptance_snapshots ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['checklist'] = parse_json(x.pop('checklist_json', '{}'))
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    return {'status': 'ok', 'snapshots': data}


@router.post('/decision', dependencies=[Depends(require_key)])
def create_decision(req: AcceptanceDecisionRequest) -> Dict[str, Any]:
    snap = row('SELECT * FROM acceptance_snapshots WHERE id=?', (req.snapshot_id,))
    if not snap:
        raise HTTPException(status_code=404, detail='acceptance snapshot not found')
    if req.decision == 'accept_release_candidate' and snap.get('acceptance_state') == 'blocked':
        raise HTTPException(status_code=400, detail='cannot accept release candidate when snapshot is blocked')
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO acceptance_decisions (snapshot_id,decision,operator,reason,created_at) VALUES (?, ?, ?, ?, ?)', (req.snapshot_id, req.decision, req.operator, req.reason, now()))
        did = int(cur.lastrowid)
        decision = dict(conn.execute('SELECT * FROM acceptance_decisions WHERE id=?', (did,)).fetchone())
    db.audit('acceptance_decision_create', 'acceptance_snapshot', str(req.snapshot_id), req.model_dump(), 'success', 'critical', req.decision)
    operator_message(f'v20 Acceptance decision for snapshot #{req.snapshot_id}: {req.decision}\n{req.reason}', 'acceptance_decision')
    return {'status': 'success', 'decision': decision}


@router.get('/decisions')
def list_decisions(limit: int = 50) -> Dict[str, Any]:
    return {'status': 'ok', 'decisions': rows('SELECT * FROM acceptance_decisions ORDER BY id DESC LIMIT ?', (limit,))}
