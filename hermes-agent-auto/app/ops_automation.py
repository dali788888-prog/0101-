from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/ops-automation', tags=['Commercial Ops Reports Release Inspection Alerts'])


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
        conn.execute('''CREATE TABLE IF NOT EXISTS ops_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL DEFAULT '{}',source TEXT NOT NULL DEFAULT 'manual',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ops_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,alert_type TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',title TEXT NOT NULL,message TEXT NOT NULL,entity_type TEXT,entity_id TEXT,payload_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,acknowledged_at TEXT,acknowledged_by TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS release_inspections (id INTEGER PRIMARY KEY AUTOINCREMENT,release_version TEXT NOT NULL,gate_state TEXT NOT NULL,blocker_count INTEGER NOT NULL DEFAULT 0,warning_count INTEGER NOT NULL DEFAULT 0,summary_json TEXT NOT NULL DEFAULT '{}',source TEXT NOT NULL DEFAULT 'manual',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')


class OpsReportRequest(BaseModel):
    period: str = Field(default='daily', pattern='^(daily|weekly|monthly|release)$')
    source: str = Field(default='manual', pattern='^(manual|scheduler|release_gate|operator)$')
    sync_operator: bool = True
    notify_operator: bool = True
    title: str = ''


class ReleaseInspectionRequest(BaseModel):
    release_version: str = '18.6-ops-reports-release-inspection-alerts'
    source: str = Field(default='manual', pattern='^(manual|scheduler|operator)$')
    create_alerts: bool = True
    sync_operator: bool = True


class AlertAckRequest(BaseModel):
    acknowledged_by: str = 'local-operator'
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
        return {'status': 'ok', 'name': name, 'data': fn()}
    except Exception as exc:
        return {'status': 'error', 'name': name, 'error': str(exc)}


def collect_metrics() -> Dict[str, Any]:
    metrics: Dict[str, Any] = {'time_utc': now()}
    metrics['release_gate'] = safe_call('release_gate', lambda: __import__('app.release_gate', fromlist=['gate_summary']).gate_summary('18.6-ops-reports-release-inspection-alerts'))
    metrics['portfolio_risk'] = safe_call('portfolio_risk', lambda: __import__('app.portfolio_risk', fromlist=['dashboard']).dashboard())
    metrics['paper_trading'] = safe_call('paper_trading', lambda: __import__('app.paper_trading', fromlist=['dashboard']).dashboard())
    metrics['trade_lifecycle'] = safe_call('trade_lifecycle', lambda: __import__('app.trade_lifecycle', fromlist=['dashboard']).dashboard(limit=100))
    metrics['trade_readiness'] = safe_call('trade_readiness', lambda: __import__('app.trade_readiness', fromlist=['status']).status())
    metrics['strategy_signals'] = safe_call('strategy_signals', lambda: __import__('app.strategy_signals', fromlist=['strategy_signals_summary']).strategy_signals_summary())
    metrics['audit'] = safe_call('audit', lambda: {'latest': db.list_audit_events(limit=50), 'count_sample': len(db.list_audit_events(limit=500))})
    metrics['alerts'] = {'open': rows("SELECT * FROM ops_alerts WHERE status='open' ORDER BY id DESC LIMIT 100")}
    return metrics


def metric_data(metrics: Dict[str, Any], key: str) -> Dict[str, Any]:
    item = metrics.get(key) or {}
    return item.get('data') if item.get('status') == 'ok' else {}


def build_report(period: str, metrics: Dict[str, Any], title: str = '') -> str:
    rg = metric_data(metrics, 'release_gate')
    portfolio = metric_data(metrics, 'portfolio_risk')
    paper = metric_data(metrics, 'paper_trading')
    lifecycle = metric_data(metrics, 'trade_lifecycle')
    signals = metric_data(metrics, 'strategy_signals')
    risk_budget = portfolio.get('risk_budget') or {}
    position_summary = ((portfolio.get('positions') or {}).get('summary') or {})
    paper_accuracy = paper.get('accuracy') or {}
    life_counts = lifecycle.get('counts') or {}
    sig_counts = signals.get('counts') or {}
    open_alerts = metrics.get('alerts', {}).get('open', [])
    lines = [
        f'# Hermes Commercial Ops {period.title()} Report',
        f'- generated_at_utc: {now()}',
        f'- title: {title or "Commercial Ops Automated Report"}',
        f'- release_gate_state: {rg.get("gate_state", "unknown")}',
        f'- release_blockers: {rg.get("blocker_count", 0)}',
        f'- release_warnings: {rg.get("warning_count", 0)}',
        f'- portfolio_value_usdt: {position_summary.get("total_market_value_usdt", 0)}',
        f'- portfolio_unrealized_pnl_usdt: {position_summary.get("total_unrealized_pnl_usdt", 0)}',
        f'- risk_budget_status: {risk_budget.get("status", "unknown")}',
        f'- remaining_risk_usdt: {risk_budget.get("remaining_risk_usdt", 0)}',
        f'- paper_realized_pnl: {paper.get("realized_pnl", 0)}',
        f'- paper_win_rate: {paper_accuracy.get("win_rate", 0)}%',
        f'- lifecycle_total_tickets: {life_counts.get("total", 0)}',
        f'- lifecycle_needs_review: {life_counts.get("needs_review", 0)}',
        f'- open_signals: {sig_counts.get("open", 0)}',
        f'- high_open_signals: {sig_counts.get("high_open", 0)}',
        f'- open_ops_alerts: {len(open_alerts)}',
        '',
        '## Alerts',
    ]
    if open_alerts:
        for a in open_alerts[:20]:
            lines.append(f"- [{a.get('severity')}] {a.get('title')} — {a.get('message')}")
    else:
        lines.append('- None')
    lines += [
        '',
        '## Required Follow-up',
        '- Resolve release blockers before approval.',
        '- Review risk budget warnings before increasing size.',
        '- Backtest/paper-trade high signals before any manual handoff.',
        '- Complete lifecycle review for externally executed tickets.',
        '',
        '## Safety Boundary',
        '- Manual Handoff only.',
        '- No autonomous live order submission.',
        '- No API Secret / private key custody.',
        '- No withdrawal / transfer automation.',
    ]
    return '\n'.join(lines)


def upsert_alert(alert_type: str, severity: str, title: str, message: str, entity_type: str = '', entity_id: str = '', payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ensure_tables()
    existing = row("SELECT * FROM ops_alerts WHERE status='open' AND alert_type=? AND title=? AND entity_id=? ORDER BY id DESC LIMIT 1", (alert_type, title, entity_id))
    if existing:
        return existing
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO ops_alerts (alert_type,severity,status,title,message,entity_type,entity_id,payload_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (alert_type, severity, 'open', title, message, entity_type, entity_id, jd(payload or {}), ts, ts))
        aid = int(cur.lastrowid)
        created = dict(conn.execute('SELECT * FROM ops_alerts WHERE id=?', (aid,)).fetchone())
    db.audit('ops_alert_create', 'ops_alert', str(aid), {'alert_type': alert_type, 'severity': severity, 'title': title}, 'success', severity, 'not_required')
    return created


def evaluate_alerts(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    created: List[Dict[str, Any]] = []
    rg = metric_data(metrics, 'release_gate')
    if rg.get('gate_state') == 'blocked':
        created.append(upsert_alert('release_gate_blocked', 'critical', 'Release Gate blocked', f"blockers={rg.get('blocker_count')}, warnings={rg.get('warning_count')}", 'release_gate', 'latest', rg))
    elif rg.get('gate_state') == 'warning':
        created.append(upsert_alert('release_gate_warning', 'high', 'Release Gate has warnings', f"warnings={rg.get('warning_count')}", 'release_gate', 'latest', rg))

    portfolio = metric_data(metrics, 'portfolio_risk')
    risk_budget = portfolio.get('risk_budget') or {}
    if risk_budget.get('status') == 'breached':
        created.append(upsert_alert('risk_budget_breached', 'critical', 'Risk budget breached', f"remaining={risk_budget.get('remaining_risk_usdt')}", 'risk_budget', 'open', risk_budget))
    elif risk_budget.get('status') == 'warning':
        created.append(upsert_alert('risk_budget_warning', 'high', 'Risk budget warning', f"remaining={risk_budget.get('remaining_risk_usdt')}", 'risk_budget', 'open', risk_budget))

    lifecycle = metric_data(metrics, 'trade_lifecycle')
    counts = lifecycle.get('counts') or {}
    if int(counts.get('needs_review') or 0) > 0:
        created.append(upsert_alert('lifecycle_needs_review', 'medium', 'Lifecycle tickets need review', f"needs_review={counts.get('needs_review')}", 'trade_lifecycle', 'needs_review', counts))
    if int(counts.get('blocked') or 0) > 0:
        created.append(upsert_alert('lifecycle_blocked', 'critical', 'Blocked lifecycle tickets exist', f"blocked={counts.get('blocked')}", 'trade_lifecycle', 'blocked', counts))

    signals = metric_data(metrics, 'strategy_signals')
    sig_counts = signals.get('counts') or {}
    if int(sig_counts.get('high_open') or 0) > 0:
        created.append(upsert_alert('high_open_signals', 'medium', 'High severity signals open', f"high_open={sig_counts.get('high_open')}", 'strategy_signals', 'high_open', sig_counts))
    return created


def notify_operator(content: str, status: str = 'ops_automation') -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, status, now()))
        return dict(conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone())


def create_operator_report(period: str, content: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', (period, f'Hermes Commercial Ops {period.title()} Report', content, jd(metrics), now()))
        return dict(conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone())


def create_operator_note(title: str, content: str, tags: str = 'ops_automation') -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (title, content, tags, ts, ts))
        return dict(conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone())


def save_ops_report(period: str, title: str, content: str, metrics: Dict[str, Any], source: str) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO ops_reports (period,title,content,metrics_json,source,created_at) VALUES (?, ?, ?, ?, ?, ?)', (period, title, content, jd(metrics), source, now()))
        return dict(conn.execute('SELECT * FROM ops_reports WHERE id=?', (int(cur.lastrowid),)).fetchone())


def generate_report(period: str = 'daily', source: str = 'manual', sync_operator: bool = True, notify: bool = True, title: str = '') -> Dict[str, Any]:
    metrics = collect_metrics()
    alerts = evaluate_alerts(metrics)
    metrics['new_alerts'] = alerts
    report_title = title or f'Hermes Commercial Ops {period.title()} Report'
    content = build_report(period, metrics, report_title)
    report = save_ops_report(period, report_title, content, metrics, source)
    operator_report = create_operator_report(period, content, metrics) if sync_operator else None
    note = None
    if alerts:
        note = create_operator_note('Ops Alert Follow-up', '\n'.join([f"- [{a.get('severity')}] {a.get('title')}: {a.get('message')}" for a in alerts]), 'ops_alerts,commercial_ops')
    msg = notify_operator(f'{report_title} generated: alerts={len(alerts)}, source={source}') if notify else None
    db.audit('ops_report_generate', 'ops_report', str(report['id']), {'period': period, 'source': source, 'alert_count': len(alerts)}, 'success', 'medium', 'not_required')
    return {'status': 'success', 'report': report, 'operator_report': operator_report, 'note': note, 'message': msg, 'alerts_created': alerts, 'content': content}


def run_release_inspection_job(release_version: str = '18.6-ops-reports-release-inspection-alerts', source: str = 'scheduler', create_alerts: bool = True, sync_operator: bool = True) -> Dict[str, Any]:
    ensure_tables()
    from app.release_gate import gate_summary

    summary = gate_summary(release_version)
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO release_inspections (release_version,gate_state,blocker_count,warning_count,summary_json,source,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (release_version, summary['gate_state'], summary['blocker_count'], summary['warning_count'], jd(summary), source, ts))
        iid = int(cur.lastrowid)
        inspection = dict(conn.execute('SELECT * FROM release_inspections WHERE id=?', (iid,)).fetchone())
    alerts: List[Dict[str, Any]] = []
    if create_alerts:
        if summary['gate_state'] == 'blocked':
            alerts.append(upsert_alert('scheduled_release_blocked', 'critical', 'Scheduled release inspection blocked', f"blockers={summary['blocker_count']}", 'release_inspection', str(iid), summary))
        elif summary['gate_state'] == 'warning':
            alerts.append(upsert_alert('scheduled_release_warning', 'high', 'Scheduled release inspection warning', f"warnings={summary['warning_count']}", 'release_inspection', str(iid), summary))
    msg = notify_operator(f'Scheduled Release Inspection #{iid}: state={summary["gate_state"]}, blockers={summary["blocker_count"]}, warnings={summary["warning_count"]}', 'release_inspection') if sync_operator else None
    db.audit('release_inspection_run', 'release_inspection', str(iid), {'gate_state': summary['gate_state'], 'blockers': summary['blocker_count'], 'warnings': summary['warning_count'], 'source': source}, 'success', 'high', summary['gate_state'])
    inspection['summary'] = parse_json(inspection.pop('summary_json', '{}'))
    return {'status': 'success', 'inspection': inspection, 'alerts_created': alerts, 'message': msg}


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '18.6-ops-reports-release-inspection-alerts', 'features': ['daily weekly commercial ops reports', 'scheduled release gate inspection', 'operator alert center', 'operator report sync'], 'safety': 'operations monitoring only; no exchange order submission'}


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    metrics = collect_metrics()
    open_alerts = rows("SELECT * FROM ops_alerts WHERE status='open' ORDER BY id DESC LIMIT 100")
    latest_reports = rows('SELECT * FROM ops_reports ORDER BY id DESC LIMIT 20')
    latest_inspections = rows('SELECT * FROM release_inspections ORDER BY id DESC LIMIT 20')
    for x in latest_reports:
        x['metrics'] = parse_json(x.pop('metrics_json', '{}'))
    for x in latest_inspections:
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    return {'status': 'ok', 'version': '18.6-ops-reports-release-inspection-alerts', 'metrics': metrics, 'open_alerts': open_alerts, 'latest_reports': latest_reports, 'latest_inspections': latest_inspections, 'time_utc': now()}


@router.post('/reports/generate', dependencies=[Depends(require_key)])
def generate_ops_report(req: OpsReportRequest) -> Dict[str, Any]:
    return generate_report(req.period, req.source, req.sync_operator, req.notify_operator, req.title)


@router.get('/reports')
def list_ops_reports(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM ops_reports ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['metrics'] = parse_json(x.pop('metrics_json', '{}'))
    return {'status': 'ok', 'reports': data}


@router.get('/reports/{report_id}')
def get_ops_report(report_id: int) -> Dict[str, Any]:
    item = row('SELECT * FROM ops_reports WHERE id=?', (report_id,))
    if not item:
        raise HTTPException(status_code=404, detail='ops report not found')
    item['metrics'] = parse_json(item.pop('metrics_json', '{}'))
    return {'status': 'ok', 'report': item}


@router.post('/release-inspection/run', dependencies=[Depends(require_key)])
def run_release_inspection(req: ReleaseInspectionRequest) -> Dict[str, Any]:
    return run_release_inspection_job(req.release_version, req.source, req.create_alerts, req.sync_operator)


@router.get('/release-inspections')
def list_release_inspections(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM release_inspections ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    return {'status': 'ok', 'inspections': data}


@router.get('/alerts')
def list_alerts(status: str = 'open', limit: int = 100) -> Dict[str, Any]:
    if status not in {'open', 'acknowledged', 'closed', 'all'}:
        raise HTTPException(status_code=400, detail='invalid status')
    if status == 'all':
        data = rows('SELECT * FROM ops_alerts ORDER BY id DESC LIMIT ?', (limit,))
    else:
        data = rows('SELECT * FROM ops_alerts WHERE status=? ORDER BY id DESC LIMIT ?', (status, limit))
    for x in data:
        x['payload'] = parse_json(x.pop('payload_json', '{}'))
    return {'status': 'ok', 'alerts': data}


@router.post('/alerts/evaluate', dependencies=[Depends(require_key)])
def evaluate_alerts_endpoint() -> Dict[str, Any]:
    metrics = collect_metrics()
    alerts = evaluate_alerts(metrics)
    return {'status': 'success', 'alerts_created': alerts, 'open_alerts': list_alerts('open', 100)['alerts']}


@router.post('/alerts/{alert_id}/ack', dependencies=[Depends(require_key)])
def ack_alert(alert_id: int, req: AlertAckRequest) -> Dict[str, Any]:
    item = row('SELECT * FROM ops_alerts WHERE id=?', (alert_id,))
    if not item:
        raise HTTPException(status_code=404, detail='alert not found')
    payload = parse_json(item.get('payload_json'))
    if req.note:
        payload['ack_note'] = req.note
    ts = now()
    with db.connect() as conn:
        conn.execute('UPDATE ops_alerts SET status=?, acknowledged_at=?, acknowledged_by=?, payload_json=?, updated_at=? WHERE id=?', ('acknowledged', ts, req.acknowledged_by, jd(payload), ts, alert_id))
    db.audit('ops_alert_ack', 'ops_alert', str(alert_id), {'acknowledged_by': req.acknowledged_by, 'note': req.note}, 'success', item.get('severity') or 'medium', 'acknowledged')
    updated = row('SELECT * FROM ops_alerts WHERE id=?', (alert_id,)) or {'id': alert_id}
    updated['payload'] = parse_json(updated.pop('payload_json', '{}'))
    return {'status': 'success', 'alert': updated}


@router.get('/schedule-status')
def schedule_status() -> Dict[str, Any]:
    return {'status': 'ok', 'jobs': [{'id': 'system-ops-daily-report', 'schedule': 'daily 23:50 UTC'}, {'id': 'system-ops-weekly-report', 'schedule': 'weekly Sunday 23:40 UTC'}, {'id': 'system-release-inspection-hourly', 'schedule': 'hourly'}, {'id': 'system-ops-alert-eval-15m', 'schedule': 'every 15 minutes'}], 'safety': 'monitoring and reporting only'}
