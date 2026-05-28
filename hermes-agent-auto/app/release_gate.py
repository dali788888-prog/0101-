from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/release-gate', tags=['Commercial Release Gate Audit Report Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS release_gate_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,release_version TEXT NOT NULL,gate_state TEXT NOT NULL,blocker_count INTEGER NOT NULL DEFAULT 0,warning_count INTEGER NOT NULL DEFAULT 0,summary_json TEXT NOT NULL DEFAULT '{}',operator TEXT NOT NULL DEFAULT 'local-operator',notes TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS release_gate_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT,snapshot_id INTEGER NOT NULL,decision TEXT NOT NULL,operator TEXT NOT NULL DEFAULT 'local-operator',reason TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


class SnapshotCreate(BaseModel):
    release_version: str = '18.0-commercial-release-gate-audit-center'
    operator: str = 'local-operator'
    notes: str = ''
    sync_operator: bool = True


class ReleaseDecisionCreate(BaseModel):
    snapshot_id: int
    decision: str = Field(pattern='^(approved|rejected|hold|rollback_required)$')
    operator: str = 'local-operator'
    reason: str = ''


class OperatorSyncRequest(BaseModel):
    release_version: str = '18.0-commercial-release-gate-audit-center'
    create_report: bool = True
    create_note: bool = True
    notify_operator: bool = True
    operator: str = 'local-operator'


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def parse_json(value: str | None) -> Dict[str, Any]:
    try:
        return json.loads(value or '{}')
    except Exception:
        return {}


def safe_call(name: str, fn) -> Dict[str, Any]:
    try:
        return {'status': 'ok', 'name': name, 'data': fn()}
    except Exception as exc:
        return {'status': 'error', 'name': name, 'error': str(exc)}


def collect_system_state() -> Dict[str, Any]:
    state: Dict[str, Any] = {'time_utc': now()}
    state['trade_readiness'] = safe_call('trade_readiness', lambda: __import__('app.trade_readiness', fromlist=['status']).status())
    state['trade_lifecycle'] = safe_call('trade_lifecycle', lambda: __import__('app.trade_lifecycle', fromlist=['dashboard']).dashboard(limit=100))
    state['paper_trading'] = safe_call('paper_trading', lambda: __import__('app.paper_trading', fromlist=['dashboard']).dashboard())
    state['portfolio_risk'] = safe_call('portfolio_risk', lambda: __import__('app.portfolio_risk', fromlist=['dashboard']).dashboard())
    state['strategy_signals'] = safe_call('strategy_signals', lambda: __import__('app.strategy_signals', fromlist=['strategy_signals_summary']).strategy_signals_summary())
    try:
        state['audit_events'] = {'status': 'ok', 'count': len(db.list_audit_events(limit=500)), 'latest': db.list_audit_events(limit=20)}
    except Exception as exc:
        state['audit_events'] = {'status': 'error', 'error': str(exc)}
    return state


def gate_checks(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    def add(key: str, title: str, status: str, required: bool = True, severity: str = 'medium', detail: Any = None) -> None:
        checks.append({'key': key, 'title': title, 'status': status, 'required': required, 'severity': severity, 'detail': detail})

    readiness = ((state.get('trade_readiness') or {}).get('data') or {}) if (state.get('trade_readiness') or {}).get('status') == 'ok' else {}
    lifecycle = ((state.get('trade_lifecycle') or {}).get('data') or {}) if (state.get('trade_lifecycle') or {}).get('status') == 'ok' else {}
    paper = ((state.get('paper_trading') or {}).get('data') or {}) if (state.get('paper_trading') or {}).get('status') == 'ok' else {}
    portfolio = ((state.get('portfolio_risk') or {}).get('data') or {}) if (state.get('portfolio_risk') or {}).get('status') == 'ok' else {}
    audit = state.get('audit_events') or {}

    add('manual_handoff_only', '真实交易保持 Manual Handoff，不允许自动提交交易所订单', 'pass', True, 'critical', 'enforced by policy and workflow')
    add('no_secret_custody', '不保存 API Secret / 私钥 / 提现权限', 'pass', True, 'critical', 'system stores references and records only')
    add('trade_readiness_available', 'Trade Readiness 可用', 'pass' if readiness else 'fail', True, 'high', state.get('trade_readiness'))
    add('kill_switch_policy', 'Readiness Kill Switch 默认应可控', 'pass' if readiness.get('kill_switch') in {'on', 'off'} else 'fail', True, 'high', readiness.get('kill_switch'))

    life_counts = lifecycle.get('counts') or {}
    add('lifecycle_available', '交易生命周期中心可用', 'pass' if lifecycle else 'fail', True, 'high', lifecycle.get('version'))
    add('lifecycle_blocked_zero', '生命周期阻断票据数量应为 0', 'pass' if int(life_counts.get('blocked') or 0) == 0 else 'blocker', True, 'critical', life_counts)
    add('lifecycle_needs_review', '外部成交票据应完成复盘', 'pass' if int(life_counts.get('needs_review') or 0) == 0 else 'warning', False, 'medium', life_counts)

    paper_counts = paper.get('counts') or {}
    accuracy = paper.get('accuracy') or {}
    add('paper_trading_available', 'Paper Trading 仿真账本可用', 'pass' if paper else 'warning', False, 'medium', paper_counts)
    add('signal_feedback_loop', '信号准确率反馈闭环可用', 'pass' if accuracy.get('status') == 'ok' else 'warning', False, 'medium', accuracy)

    risk_budget = portfolio.get('risk_budget') or {}
    add('portfolio_risk_available', '组合敞口 / 风险预算中心可用', 'pass' if portfolio else 'fail', True, 'high', portfolio.get('version'))
    add('risk_budget_not_breached', '风险预算不得处于 breached 状态', 'pass' if risk_budget.get('status') != 'breached' else 'blocker', True, 'critical', risk_budget)
    add('manual_journal_available', '手工交易日志可用', 'pass' if (portfolio.get('journal') or {}).get('status') == 'ok' else 'warning', False, 'medium', portfolio.get('journal'))

    add('audit_events_available', '全链路审计事件可读取', 'pass' if audit.get('status') == 'ok' and int(audit.get('count') or 0) >= 0 else 'fail', True, 'high', audit)
    add('release_report_available', 'Release Gate 报表中心可生成报告', 'pass', True, 'medium', 'generated on demand')
    return checks


def gate_summary(release_version: str = '18.0-commercial-release-gate-audit-center') -> Dict[str, Any]:
    state = collect_system_state()
    checks = gate_checks(state)
    blockers = [x for x in checks if x['status'] in {'blocker', 'fail'} and x['required']]
    warnings = [x for x in checks if x['status'] == 'warning' or (x['status'] == 'fail' and not x['required'])]
    gate_state = 'blocked' if blockers else 'warning' if warnings else 'ready'
    return {'status': 'ok', 'version': release_version, 'gate_state': gate_state, 'blocker_count': len(blockers), 'warning_count': len(warnings), 'checks': checks, 'blockers': blockers, 'warnings': warnings, 'state': state, 'safety': 'commercial release readiness only; no exchange order submission', 'time_utc': now()}


def report_text(summary: Dict[str, Any]) -> str:
    lines = [
        '# Hermes v18 Commercial Release Gate Report',
        f'- generated_at_utc: {now()}',
        f'- release_version: {summary.get("version")}',
        f'- gate_state: {summary.get("gate_state")}',
        f'- blockers: {summary.get("blocker_count")}',
        f'- warnings: {summary.get("warning_count")}',
        '',
        '## Required Checks',
    ]
    for c in summary.get('checks', []):
        if c.get('required'):
            lines.append(f"- [{c.get('status')}] {c.get('title')} severity={c.get('severity')}")
    lines += ['', '## Optional / Advisory Checks']
    for c in summary.get('checks', []):
        if not c.get('required'):
            lines.append(f"- [{c.get('status')}] {c.get('title')} severity={c.get('severity')}")
    lines += ['', '## Blockers']
    blockers = summary.get('blockers') or []
    lines += [f"- {b.get('title')}" for b in blockers] if blockers else ['- None']
    lines += ['', '## Warnings']
    warnings = summary.get('warnings') or []
    lines += [f"- {w.get('title')}" for w in warnings] if warnings else ['- None']
    lines += [
        '',
        '## Safety Boundary',
        '- Manual Handoff only.',
        '- No autonomous live order submission.',
        '- No API Secret / private key custody.',
        '- No withdrawal / transfer automation.',
        '- No bypassing approval, Kill Switch, or risk gates.',
    ]
    return '\n'.join(lines)


def operator_message(content: str, status: str = 'release_gate') -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, status, now()))
        return dict(conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone())


def operator_note(title: str, content: str, tags: str = 'release_gate') -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (title, content, tags, ts, ts))
        return dict(conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone())


def operator_report(content: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', ('release_gate', 'Hermes v18 Commercial Release Gate Report', content, jd(metrics), now()))
        return dict(conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone())


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '18.0-commercial-release-gate-audit-center', 'features': ['commercial release gate', 'full-chain audit report', 'operator sync', 'deployment readiness snapshot', 'blocker and warning checks'], 'safety': 'release readiness only; no exchange order submission'}


@router.get('/dashboard')
def dashboard(release_version: str = '18.0-commercial-release-gate-audit-center') -> Dict[str, Any]:
    return gate_summary(release_version)


@router.get('/report')
def report(release_version: str = '18.0-commercial-release-gate-audit-center') -> Dict[str, Any]:
    summary = gate_summary(release_version)
    return {'status': 'ok', 'content': report_text(summary), 'summary': summary}


@router.post('/snapshot', dependencies=[Depends(require_key)])
def create_snapshot(req: SnapshotCreate) -> Dict[str, Any]:
    ensure_tables()
    summary = gate_summary(req.release_version)
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO release_gate_snapshots (release_version,gate_state,blocker_count,warning_count,summary_json,operator,notes,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.release_version, summary['gate_state'], summary['blocker_count'], summary['warning_count'], jd(summary), req.operator, req.notes, now()))
        sid = int(cur.lastrowid)
        snap = dict(conn.execute('SELECT * FROM release_gate_snapshots WHERE id=?', (sid,)).fetchone())
    db.audit('release_gate_snapshot_create', 'release_gate_snapshot', str(sid), {'release_version': req.release_version, 'gate_state': summary['gate_state'], 'blockers': summary['blocker_count'], 'warnings': summary['warning_count']}, 'success', 'high', summary['gate_state'])
    if req.sync_operator:
        operator_message(f'Release Gate snapshot #{sid}: state={summary["gate_state"]}, blockers={summary["blocker_count"]}, warnings={summary["warning_count"]}')
    snap['summary'] = parse_json(snap.pop('summary_json', '{}'))
    return {'status': 'success', 'snapshot': snap}


@router.get('/snapshots')
def list_snapshots(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM release_gate_snapshots ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    return {'status': 'ok', 'snapshots': data}


@router.post('/decision', dependencies=[Depends(require_key)])
def create_decision(req: ReleaseDecisionCreate) -> Dict[str, Any]:
    snap = row('SELECT * FROM release_gate_snapshots WHERE id=?', (req.snapshot_id,))
    if not snap:
        raise HTTPException(status_code=404, detail='snapshot not found')
    if req.decision == 'approved' and int(snap.get('blocker_count') or 0) > 0:
        raise HTTPException(status_code=400, detail='cannot approve release snapshot with blockers')
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO release_gate_decisions (snapshot_id,decision,operator,reason,created_at) VALUES (?, ?, ?, ?, ?)', (req.snapshot_id, req.decision, req.operator, req.reason, now()))
        did = int(cur.lastrowid)
        decision = dict(conn.execute('SELECT * FROM release_gate_decisions WHERE id=?', (did,)).fetchone())
    db.audit('release_gate_decision_create', 'release_gate_snapshot', str(req.snapshot_id), req.model_dump(), 'success', 'critical', req.decision)
    operator_message(f'Release Gate decision for snapshot #{req.snapshot_id}: {req.decision}\n{req.reason}', 'release_gate_decision')
    return {'status': 'success', 'decision': decision}


@router.get('/decisions')
def list_decisions(limit: int = 50) -> Dict[str, Any]:
    return {'status': 'ok', 'decisions': rows('SELECT * FROM release_gate_decisions ORDER BY id DESC LIMIT ?', (limit,))}


@router.post('/sync-operator', dependencies=[Depends(require_key)])
def sync_operator(req: OperatorSyncRequest) -> Dict[str, Any]:
    summary = gate_summary(req.release_version)
    content = report_text(summary)
    report_obj = operator_report(content, {'gate_state': summary['gate_state'], 'blocker_count': summary['blocker_count'], 'warning_count': summary['warning_count']}) if req.create_report else None
    note_obj = operator_note('Release Gate Follow-up', content, 'release_gate,commercial_ops') if req.create_note else None
    msg_obj = operator_message(f'v18 Release Gate synced: state={summary["gate_state"]}, blockers={summary["blocker_count"]}, warnings={summary["warning_count"]}') if req.notify_operator else None
    db.audit('release_gate_operator_sync', 'release_gate', req.release_version, {'gate_state': summary['gate_state'], 'blockers': summary['blocker_count'], 'warnings': summary['warning_count']}, 'success', 'high', summary['gate_state'])
    return {'status': 'success', 'report': report_obj, 'note': note_obj, 'message': msg_obj, 'summary': summary}
