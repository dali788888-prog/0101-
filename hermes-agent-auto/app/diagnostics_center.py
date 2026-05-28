from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/diagnostics', tags=['Global Search Health Repair Center'])


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
        conn.execute('''CREATE TABLE IF NOT EXISTS diagnostics_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,run_type TEXT NOT NULL,status TEXT NOT NULL,summary_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS repair_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT,module TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',title TEXT NOT NULL,diagnosis TEXT NOT NULL,suggestion TEXT NOT NULL,command_hint TEXT,payload_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')


class DiagnosticsRunRequest(BaseModel):
    run_type: str = Field(default='full', pattern='^(full|module_health|release_health|repair_suggestions)$')
    create_suggestions: bool = True
    notify_operator: bool = True


class RepairSuggestionStatusRequest(BaseModel):
    status: str = Field(pattern='^(open|accepted|ignored|resolved)$')
    operator: str = 'local-operator'
    note: str = ''


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


def notify_operator(content: str, status: str = 'diagnostics') -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, status, now()))
        return dict(conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone())


def module_health() -> Dict[str, Any]:
    modules: List[Dict[str, Any]] = []
    modules.append(safe_call('release_gate', lambda: __import__('app.release_gate', fromlist=['gate_summary']).gate_summary('19.3-diagnostics-search-health-repair')))
    modules.append(safe_call('ops_automation', lambda: __import__('app.ops_automation', fromlist=['dashboard']).dashboard()))
    modules.append(safe_call('ops_workflow', lambda: __import__('app.ops_workflow', fromlist=['dashboard']).dashboard()))
    modules.append(safe_call('portfolio_risk', lambda: __import__('app.portfolio_risk', fromlist=['dashboard']).dashboard()))
    modules.append(safe_call('paper_trading', lambda: __import__('app.paper_trading', fromlist=['dashboard']).dashboard()))
    modules.append(safe_call('trade_lifecycle', lambda: __import__('app.trade_lifecycle', fromlist=['dashboard']).dashboard(limit=100)))
    modules.append(safe_call('trade_readiness', lambda: __import__('app.trade_readiness', fromlist=['status']).status()))
    modules.append(safe_call('strategy_signals', lambda: __import__('app.strategy_signals', fromlist=['strategy_signals_summary']).strategy_signals_summary()))
    modules.append(safe_call('audit_events', lambda: {'latest_count': len(db.list_audit_events(limit=100)), 'sample_count': len(db.list_audit_events(limit=500))}))

    errors = [m for m in modules if m.get('status') != 'ok']
    warnings: List[Dict[str, Any]] = []
    for m in modules:
        data = m.get('data') or {}
        if m['module'] == 'release_gate' and data.get('gate_state') in {'blocked', 'warning'}:
            warnings.append({'module': m['module'], 'message': f"gate_state={data.get('gate_state')} blockers={data.get('blocker_count')} warnings={data.get('warning_count')}"})
        if m['module'] == 'portfolio_risk' and (data.get('risk_budget') or {}).get('status') in {'breached', 'warning'}:
            warnings.append({'module': m['module'], 'message': f"risk_budget={(data.get('risk_budget') or {}).get('status')}"})
        if m['module'] == 'ops_workflow' and len(data.get('open_alerts') or []) > 0:
            warnings.append({'module': m['module'], 'message': f"open_alerts={len(data.get('open_alerts') or [])}"})
        if m['module'] == 'trade_lifecycle' and int((data.get('counts') or {}).get('needs_review') or 0) > 0:
            warnings.append({'module': m['module'], 'message': f"needs_review={(data.get('counts') or {}).get('needs_review')}"})
    overall = 'error' if errors else 'warning' if warnings else 'ok'
    return {'status': overall, 'version': '19.3-diagnostics-search-health-repair', 'modules': modules, 'errors': errors, 'warnings': warnings, 'time_utc': now(), 'safety': 'diagnostics only; no exchange order submission'}


def table_names() -> List[str]:
    try:
        with db.connect() as conn:
            data = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        return data
    except Exception:
        return []


def global_search(query: str, limit: int = 100) -> Dict[str, Any]:
    ensure_tables()
    q = (query or '').strip()
    if not q:
        raise HTTPException(status_code=400, detail='query is required')
    q_like = f'%{q}%'
    results: List[Dict[str, Any]] = []
    searchable = {
        'ops_alerts': ['title', 'message', 'alert_type', 'severity', 'entity_type', 'entity_id'],
        'ops_reports': ['title', 'content', 'period', 'source'],
        'release_inspections': ['release_version', 'gate_state', 'source'],
        'release_gate_snapshots': ['release_version', 'gate_state', 'operator', 'notes'],
        'operator_chat_messages': ['role', 'content', 'status'],
        'operator_work_notes': ['title', 'content', 'tags'],
        'operator_period_reports': ['period', 'title', 'content'],
        'paper_orders': ['symbol', 'side', 'status', 'signal_id', 'notes'],
        'paper_signal_feedback': ['signal_id', 'verdict', 'reason'],
        'trade_ready_tickets': ['symbol', 'side', 'order_type', 'approval_state', 'ticket_state', 'note', 'external_ref'],
        'trade_lifecycle_reviews': ['review_type', 'outcome', 'operator', 'summary', 'lessons', 'risk_followup'],
        'portfolio_positions': ['account_label', 'symbol', 'source', 'notes'],
        'risk_budget_entries': ['budget_date', 'scope', 'status', 'notes'],
        'manual_trade_journal': ['trade_date', 'source', 'symbol', 'side', 'outcome', 'notes'],
        'strategy_signal_events': ['signal_id', 'symbol', 'signal_type', 'severity', 'status'],
        'audit_events': ['event_type', 'entity_type', 'entity_id', 'result', 'severity'],
        'repair_suggestions': ['module', 'severity', 'status', 'title', 'diagnosis', 'suggestion', 'command_hint'],
    }
    existing = set(table_names())
    with db.connect() as conn:
        for table, columns in searchable.items():
            if table not in existing:
                continue
            where = ' OR '.join([f"CAST({c} AS TEXT) LIKE ?" for c in columns])
            try:
                data = [dict(r) for r in conn.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY id DESC LIMIT ?', tuple([q_like] * len(columns) + [max(1, min(limit, 100))])).fetchall()]
                for item in data:
                    results.append({'table': table, 'id': item.get('id'), 'match': item})
                    if len(results) >= limit:
                        break
            except Exception:
                continue
            if len(results) >= limit:
                break
    return {'status': 'ok', 'query': q, 'result_count': len(results), 'results': results, 'searched_tables': sorted(list(existing)), 'time_utc': now()}


def add_suggestion(module: str, severity: str, title: str, diagnosis: str, suggestion: str, command_hint: str = '', payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_tables()
    existing = row("SELECT * FROM repair_suggestions WHERE status='open' AND module=? AND title=? ORDER BY id DESC LIMIT 1", (module, title))
    if existing:
        return existing
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO repair_suggestions (module,severity,status,title,diagnosis,suggestion,command_hint,payload_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (module, severity, 'open', title, diagnosis, suggestion, command_hint, jd(payload or {}), ts, ts))
        sid = int(cur.lastrowid)
        created = dict(conn.execute('SELECT * FROM repair_suggestions WHERE id=?', (sid,)).fetchone())
    db.audit('repair_suggestion_create', 'repair_suggestion', str(sid), {'module': module, 'severity': severity, 'title': title}, 'success', severity, 'open')
    return created


def generate_repair_suggestions(health: Dict[str, Any]) -> List[Dict[str, Any]]:
    created: List[Dict[str, Any]] = []
    for err in health.get('errors', []):
        created.append(add_suggestion(err.get('module') or 'unknown', 'critical', f"{err.get('module')} module error", err.get('error') or 'module call failed', 'Check module import, router registration, DB tables and recent commits. Rebuild container after pulling latest code.', 'docker compose --project-name hermes_agent_auto_isolated up -d --build --force-recreate hermes-agent', err))
    for warn in health.get('warnings', []):
        module = warn.get('module') or 'unknown'
        msg = warn.get('message') or ''
        if module == 'release_gate':
            created.append(add_suggestion(module, 'high', 'Release Gate warning/blocker needs review', msg, 'Open Release Gate UI, inspect blockers/warnings, create a snapshot, resolve blockers before approval.', 'Start-Process "http://localhost:8099/release-gate-ui"', warn))
        elif module == 'portfolio_risk':
            created.append(add_suggestion(module, 'high', 'Risk budget requires review', msg, 'Review open risk budgets and reduce reserved/used risk before increasing any manual trade size.', 'Start-Process "http://localhost:8099/portfolio-risk-ui"', warn))
        elif module == 'ops_workflow':
            created.append(add_suggestion(module, 'medium', 'Open Ops alerts require acknowledgment', msg, 'Review critical/high alerts first; acknowledge only after human review.', 'Start-Process "http://localhost:8099"', warn))
        elif module == 'trade_lifecycle':
            created.append(add_suggestion(module, 'medium', 'Lifecycle tickets need post-execution review', msg, 'Open Trade Lifecycle UI and complete reviews for externally executed tickets.', 'Start-Process "http://localhost:8099/trade-lifecycle-ui"', warn))
        else:
            created.append(add_suggestion(module, 'medium', f'{module} warning', msg, 'Review module dashboard and related audit events.', '', warn))
    if not created and health.get('status') == 'ok':
        created.append(add_suggestion('system', 'low', 'System healthy baseline', 'No critical module error detected.', 'Continue scheduled reports, release inspections and alert reviews.', '', health))
    return created


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '19.3-diagnostics-search-health-repair', 'features': ['global search center', 'module health checks', 'repair suggestion center', 'diagnostics run history'], 'safety': 'diagnostics only; no exchange order submission'}


@router.get('/module-health')
def get_module_health() -> Dict[str, Any]:
    return module_health()


@router.get('/global-search')
def search(q: str = Query(..., min_length=1), limit: int = Query(default=100, ge=1, le=200)) -> Dict[str, Any]:
    return global_search(q, limit)


@router.post('/run', dependencies=[Depends(require_key)])
def run_diagnostics(req: DiagnosticsRunRequest) -> Dict[str, Any]:
    ensure_tables()
    health = module_health()
    suggestions = generate_repair_suggestions(health) if req.create_suggestions else []
    summary = {'health': health, 'suggestions_created': suggestions}
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO diagnostics_runs (run_type,status,summary_json,created_at) VALUES (?, ?, ?, ?)', (req.run_type, health['status'], jd(summary), now()))
        rid = int(cur.lastrowid)
        run = dict(conn.execute('SELECT * FROM diagnostics_runs WHERE id=?', (rid,)).fetchone())
    db.audit('diagnostics_run', 'diagnostics', str(rid), {'run_type': req.run_type, 'status': health['status'], 'suggestions': len(suggestions)}, 'success', 'medium', health['status'])
    message = notify_operator(f'Diagnostics run #{rid}: status={health["status"]}, suggestions={len(suggestions)}', 'diagnostics_run') if req.notify_operator else None
    run['summary'] = parse_json(run.pop('summary_json', '{}'))
    return {'status': 'success', 'run': run, 'message': message}


@router.get('/runs')
def list_runs(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM diagnostics_runs ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    return {'status': 'ok', 'runs': data}


@router.get('/repair-suggestions')
def list_repair_suggestions(status: str = 'open', limit: int = 100) -> Dict[str, Any]:
    if status not in {'open', 'accepted', 'ignored', 'resolved', 'all'}:
        raise HTTPException(status_code=400, detail='invalid status')
    if status == 'all':
        data = rows('SELECT * FROM repair_suggestions ORDER BY id DESC LIMIT ?', (limit,))
    else:
        data = rows('SELECT * FROM repair_suggestions WHERE status=? ORDER BY CASE severity WHEN "critical" THEN 1 WHEN "high" THEN 2 WHEN "medium" THEN 3 ELSE 4 END, id DESC LIMIT ?', (status, limit))
    for x in data:
        x['payload'] = parse_json(x.pop('payload_json', '{}'))
    return {'status': 'ok', 'suggestions': data}


@router.post('/repair-suggestions/{suggestion_id}/status', dependencies=[Depends(require_key)])
def update_repair_suggestion_status(suggestion_id: int, req: RepairSuggestionStatusRequest) -> Dict[str, Any]:
    item = row('SELECT * FROM repair_suggestions WHERE id=?', (suggestion_id,))
    if not item:
        raise HTTPException(status_code=404, detail='repair suggestion not found')
    payload = parse_json(item.get('payload_json'))
    if req.note:
        payload['operator_note'] = req.note
    ts = now()
    with db.connect() as conn:
        conn.execute('UPDATE repair_suggestions SET status=?, payload_json=?, updated_at=? WHERE id=?', (req.status, jd(payload), ts, suggestion_id))
    db.audit('repair_suggestion_status_update', 'repair_suggestion', str(suggestion_id), {'status': req.status, 'operator': req.operator, 'note': req.note}, 'success', item.get('severity') or 'medium', req.status)
    updated = row('SELECT * FROM repair_suggestions WHERE id=?', (suggestion_id,)) or {'id': suggestion_id}
    updated['payload'] = parse_json(updated.pop('payload_json', '{}'))
    return {'status': 'success', 'suggestion': updated}


@router.get('/tables')
def list_tables() -> Dict[str, Any]:
    return {'status': 'ok', 'tables': table_names(), 'time_utc': now()}
