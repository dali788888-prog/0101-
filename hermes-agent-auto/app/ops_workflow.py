from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.ops_automation import collect_metrics, evaluate_alerts, generate_report, list_alerts, run_release_inspection_job

router = APIRouter(prefix='/ops-workflow', tags=['Ops One Click Workflow'])


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


class QuickRunRequest(BaseModel):
    release_version: str = '19.0-ops-command-workflow-center'
    report_period: str = Field(default='daily', pattern='^(daily|weekly|monthly|release)$')
    run_release_inspection: bool = True
    run_alert_evaluation: bool = True
    generate_report: bool = True
    sync_operator: bool = True
    notify_operator: bool = True
    operator: str = 'local-operator'


class AckAllRequest(BaseModel):
    acknowledged_by: str = 'local-operator'
    note: str = 'bulk acknowledged from v19 ops workflow center'
    severity: str = Field(default='all', pattern='^(all|critical|high|medium|low)$')
    max_alerts: int = Field(default=100, ge=1, le=500)


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def open_alerts(limit: int = 100) -> List[Dict[str, Any]]:
    data = rows("SELECT * FROM ops_alerts WHERE status='open' ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, id DESC LIMIT ?", (limit,))
    for x in data:
        x['payload'] = parse_json(x.pop('payload_json', '{}'))
    return data


@router.get('/status')
def status() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'version': '19.0-ops-command-workflow-center',
        'features': ['one click release inspection', 'one click alert evaluation', 'one click ops report', 'bulk alert acknowledgment', 'homepage command workflow'],
        'safety': 'workflow automation for monitoring and reporting only; no exchange order submission',
    }


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    metrics = collect_metrics()
    alerts = open_alerts(100)
    reports = rows('SELECT * FROM ops_reports ORDER BY id DESC LIMIT 20')
    inspections = rows('SELECT * FROM release_inspections ORDER BY id DESC LIMIT 20')
    for x in reports:
        x['metrics'] = parse_json(x.pop('metrics_json', '{}'))
    for x in inspections:
        x['summary'] = parse_json(x.pop('summary_json', '{}'))
    severity_counts: Dict[str, int] = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    for a in alerts:
        severity_counts[a.get('severity', 'low')] = severity_counts.get(a.get('severity', 'low'), 0) + 1
    return {
        'status': 'ok',
        'version': '19.0-ops-command-workflow-center',
        'metrics': metrics,
        'open_alerts': alerts,
        'severity_counts': severity_counts,
        'latest_reports': reports,
        'latest_inspections': inspections,
        'time_utc': now(),
    }


@router.post('/quick-run', dependencies=[Depends(require_key)])
def quick_run(req: QuickRunRequest) -> Dict[str, Any]:
    outputs: Dict[str, Any] = {}
    if req.run_release_inspection:
        outputs['release_inspection'] = run_release_inspection_job(req.release_version, 'operator', True, req.sync_operator)
    if req.run_alert_evaluation:
        metrics = collect_metrics()
        outputs['alert_evaluation'] = {'status': 'success', 'alerts_created': evaluate_alerts(metrics)}
    if req.generate_report:
        outputs['ops_report'] = generate_report(req.report_period, 'operator', req.sync_operator, req.notify_operator, f'v19 One Click {req.report_period.title()} Ops Report')
    latest = dashboard()
    db.audit('ops_workflow_quick_run', 'ops_workflow', req.release_version, {'operator': req.operator, 'steps': list(outputs.keys()), 'open_alerts': len(latest.get('open_alerts') or [])}, 'success', 'high', 'not_required')
    return {'status': 'success', 'outputs': outputs, 'dashboard': latest, 'safety': 'monitoring/reporting workflow only; no trading action was executed'}


@router.post('/alerts/ack-all', dependencies=[Depends(require_key)])
def ack_all(req: AckAllRequest) -> Dict[str, Any]:
    if req.severity == 'all':
        candidates = rows("SELECT * FROM ops_alerts WHERE status='open' ORDER BY id DESC LIMIT ?", (req.max_alerts,))
    else:
        candidates = rows("SELECT * FROM ops_alerts WHERE status='open' AND severity=? ORDER BY id DESC LIMIT ?", (req.severity, req.max_alerts))
    ts = now()
    acknowledged: List[Dict[str, Any]] = []
    with db.connect() as conn:
        for item in candidates:
            payload = parse_json(item.get('payload_json'))
            payload['ack_note'] = req.note
            payload['bulk_ack'] = True
            conn.execute('UPDATE ops_alerts SET status=?, acknowledged_at=?, acknowledged_by=?, payload_json=?, updated_at=? WHERE id=?', ('acknowledged', ts, req.acknowledged_by, jd(payload), ts, item['id']))
            acknowledged.append({'id': item['id'], 'title': item['title'], 'severity': item['severity']})
    db.audit('ops_alert_bulk_ack', 'ops_alert', 'bulk', {'count': len(acknowledged), 'severity': req.severity, 'acknowledged_by': req.acknowledged_by, 'note': req.note}, 'success', req.severity if req.severity != 'all' else 'medium', 'acknowledged')
    return {'status': 'success', 'acknowledged_count': len(acknowledged), 'acknowledged': acknowledged, 'open_alerts': open_alerts(100)}


@router.get('/runbook')
def runbook() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'steps': [
            '1. Run quick-run to perform release inspection, alert evaluation and ops report generation.',
            '2. Review open alerts by severity. Critical alerts should be resolved, not blindly acknowledged.',
            '3. Use bulk ack only after human review.',
            '4. Sync reports to Operator workspace for daily/weekly records.',
            '5. Keep Manual Handoff boundary: no automatic real trading, no API secret custody, no withdrawal automation.',
        ],
        'safety': 'runbook only; no exchange order submission',
    }
