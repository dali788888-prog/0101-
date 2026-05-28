from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.trade_readiness import evaluate_ticket_risk

router = APIRouter(prefix='/trade-lifecycle', tags=['Trade Lifecycle Review Center'])


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
        conn.execute('''CREATE TABLE IF NOT EXISTS trade_lifecycle_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id INTEGER NOT NULL,review_type TEXT NOT NULL DEFAULT 'post_execution',outcome TEXT NOT NULL DEFAULT 'open',operator TEXT NOT NULL DEFAULT 'local-operator',summary TEXT NOT NULL,lessons TEXT,risk_followup TEXT,metrics_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


class ReviewCreate(BaseModel):
    ticket_id: int
    review_type: str = Field(default='post_execution', pattern='^(pre_handoff|post_execution|risk_review|operator_note)$')
    outcome: str = Field(default='open', pattern='^(open|win|loss|breakeven|blocked|cancelled|needs_followup)$')
    operator: str = 'local-operator'
    summary: str = Field(min_length=2, max_length=4000)
    lessons: str = ''
    risk_followup: str = ''
    metrics: Dict[str, Any] = Field(default_factory=dict)
    sync_operator: bool = True


class ReportSyncRequest(BaseModel):
    limit: int = Field(default=100, ge=10, le=500)
    sync_operator: bool = True
    create_report: bool = True
    create_notes: bool = True
    operator: str = 'local-operator'


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def parse_json(value: str | None) -> Dict[str, Any]:
    try:
        return json.loads(value or '{}')
    except Exception:
        return {}


def ticket(ticket_id: int) -> Dict[str, Any]:
    item = row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,))
    if not item:
        raise HTTPException(status_code=404, detail='ticket not found')
    item['response'] = parse_json(item.get('response_json'))
    return item


def ticket_account(ticket_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return row('SELECT * FROM trade_ready_accounts WHERE id=?', (ticket_item.get('account_id'),))


def ticket_reviews(ticket_id: int) -> List[Dict[str, Any]]:
    ensure_tables()
    data = rows('SELECT * FROM trade_lifecycle_reviews WHERE ticket_id=? ORDER BY id ASC', (ticket_id,))
    for x in data:
        x['metrics'] = parse_json(x.pop('metrics_json', '{}'))
    return data


def ticket_audits(ticket_id: int) -> List[Dict[str, Any]]:
    data = rows("SELECT * FROM audit_events WHERE entity_type IN ('trade_ready_ticket','trade_ready_control') AND (entity_id=? OR arguments_json LIKE ?) ORDER BY id ASC", (str(ticket_id), f'%"ticket_id": {ticket_id}%'))
    for x in data:
        x['arguments'] = parse_json(x.pop('arguments_json', '{}'))
    return data


def timeline(ticket_id: int) -> Dict[str, Any]:
    t = ticket(ticket_id)
    account = ticket_account(t)
    risk = evaluate_ticket_risk(ticket_id)
    reviews = ticket_reviews(ticket_id)
    audits = ticket_audits(ticket_id)
    events: List[Dict[str, Any]] = []
    events.append({'phase': 'draft', 'title': '票据草稿创建', 'status': t.get('approval_state'), 'time_utc': t.get('created_at'), 'payload': t})
    for audit in audits:
        events.append({'phase': audit.get('event_type'), 'title': audit.get('event_type'), 'status': audit.get('result'), 'time_utc': audit.get('created_at'), 'payload': audit})
    for review in reviews:
        events.append({'phase': 'review', 'title': f"复盘记录：{review['review_type']}", 'status': review.get('outcome'), 'time_utc': review.get('created_at'), 'payload': review})
    if t.get('external_ref'):
        events.append({'phase': 'external_done', 'title': '外部成交已回填', 'status': t.get('ticket_state'), 'time_utc': t.get('updated_at'), 'payload': {'external_ref': t.get('external_ref'), 'response': t.get('response')}})
    events.sort(key=lambda x: x.get('time_utc') or '')
    return {'status': 'ok', 'ticket_id': ticket_id, 'ticket': t, 'account': account, 'risk': risk, 'reviews': reviews, 'audits': audits, 'timeline': events, 'safety': 'lifecycle review only; no exchange order submission', 'time_utc': now()}


def dashboard(limit: int = 100) -> Dict[str, Any]:
    tickets = rows('SELECT * FROM trade_ready_tickets ORDER BY id DESC LIMIT ?', (limit,))
    evaluated: List[Dict[str, Any]] = []
    for t in tickets:
        tid = int(t['id'])
        try:
            risk = evaluate_ticket_risk(tid)
        except Exception as exc:
            risk = {'status': 'error', 'error': str(exc)}
        reviews = ticket_reviews(tid)
        evaluated.append({'ticket': t, 'risk': risk, 'reviews': reviews, 'review_count': len(reviews)})
    pending = [x for x in evaluated if x['ticket'].get('approval_state') == 'pending']
    approved = [x for x in evaluated if x['ticket'].get('approval_state') == 'approved']
    external_done = [x for x in evaluated if x['ticket'].get('ticket_state') == 'external_done']
    needs_review = [x for x in evaluated if x['ticket'].get('ticket_state') == 'external_done' and not x['reviews']]
    blocked = [x for x in evaluated if x['risk'].get('recommendation') == 'BLOCK_MANUAL_HANDOFF']
    return {
        'status': 'ok',
        'version': '17.0-trade-lifecycle-review-center',
        'counts': {'total': len(evaluated), 'pending': len(pending), 'approved': len(approved), 'external_done': len(external_done), 'needs_review': len(needs_review), 'blocked': len(blocked)},
        'items': evaluated,
        'safety': 'manual handoff lifecycle tracking only',
        'time_utc': now(),
    }


def report_content(data: Dict[str, Any]) -> str:
    counts = data.get('counts') or {}
    lines = [
        '# Hermes Trade Lifecycle Review Report',
        f'- created_at_utc: {now()}',
        f'- total_tickets: {counts.get("total", 0)}',
        f'- pending: {counts.get("pending", 0)}',
        f'- approved: {counts.get("approved", 0)}',
        f'- external_done: {counts.get("external_done", 0)}',
        f'- needs_review: {counts.get("needs_review", 0)}',
        f'- blocked: {counts.get("blocked", 0)}',
        '',
        '## Ticket Summary',
    ]
    for item in data.get('items', [])[:40]:
        t = item.get('ticket') or {}
        r = item.get('risk') or {}
        lines.append(f"- ticket #{t.get('id')} {t.get('symbol')} {t.get('side')} {t.get('order_type')} approval={t.get('approval_state')} state={t.get('ticket_state')} risk={r.get('risk_score')}/{r.get('risk_level')} recommendation={r.get('recommendation')} reviews={item.get('review_count')}")
    lines += ['', '## Safety', '- Manual handoff only. No autonomous live order submission. No API secret custody.']
    return '\n'.join(lines)


def create_operator_note(title: str, content: str, tags: str) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (title, content, tags, ts, ts))
        r = conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(r)


def create_operator_report(content: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', ('trade_lifecycle', 'Trade Lifecycle Review Report', content, jd(metrics), now()))
        r = conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(r)


def notify_operator(content: str) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, 'trade_lifecycle_sync', now()))
        r = conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(r)


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '17.0-trade-lifecycle-review-center', 'features': ['timeline', 'post_execution_review', 'review_report', 'operator_sync', 'risk_dashboard_summary'], 'safety': 'manual handoff only; no exchange order submission'}


@router.get('/dashboard')
def lifecycle_dashboard(limit: int = 100) -> Dict[str, Any]:
    return dashboard(limit=limit)


@router.get('/tickets/{ticket_id}/timeline')
def ticket_timeline(ticket_id: int) -> Dict[str, Any]:
    return timeline(ticket_id)


@router.post('/reviews', dependencies=[Depends(require_key)])
def create_review(req: ReviewCreate) -> Dict[str, Any]:
    ensure_tables()
    _ = ticket(req.ticket_id)
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO trade_lifecycle_reviews (ticket_id,review_type,outcome,operator,summary,lessons,risk_followup,metrics_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.ticket_id, req.review_type, req.outcome, req.operator, req.summary, req.lessons, req.risk_followup, jd(req.metrics), ts, ts))
        review_id = int(cur.lastrowid)
        review_row = dict(conn.execute('SELECT * FROM trade_lifecycle_reviews WHERE id=?', (review_id,)).fetchone())
    db.audit('trade_lifecycle_review_create', 'trade_ready_ticket', str(req.ticket_id), req.model_dump(), 'success', 'high', 'reviewed')
    if req.sync_operator:
        create_operator_note(f'Trade Ticket Review #{req.ticket_id}', f'{req.summary}\n\nLessons:\n{req.lessons}\n\nFollow-up:\n{req.risk_followup}', 'trade_lifecycle_review')
        notify_operator(f'交易票据 #{req.ticket_id} 已新增复盘：{req.outcome}\n{req.summary}')
    review_row['metrics'] = parse_json(review_row.pop('metrics_json', '{}'))
    return {'status': 'success', 'review': review_row, 'timeline': timeline(req.ticket_id)}


@router.get('/reviews')
def list_reviews(limit: int = 100) -> Dict[str, Any]:
    ensure_tables()
    data = rows('SELECT * FROM trade_lifecycle_reviews ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['metrics'] = parse_json(x.pop('metrics_json', '{}'))
    return {'status': 'ok', 'reviews': data}


@router.get('/report')
def lifecycle_report(limit: int = 100) -> Dict[str, Any]:
    data = dashboard(limit=limit)
    return {'status': 'ok', 'content': report_content(data), 'dashboard': data}


@router.post('/sync-operator', dependencies=[Depends(require_key)])
def sync_operator(req: ReportSyncRequest) -> Dict[str, Any]:
    data = dashboard(limit=req.limit)
    content = report_content(data)
    report = create_operator_report(content, data.get('counts') or {}) if req.create_report else None
    note_count = 0
    if req.create_notes:
        for item in data.get('items', [])[:30]:
            t = item.get('ticket') or {}
            r = item.get('risk') or {}
            if r.get('recommendation') in {'BLOCK_MANUAL_HANDOFF', 'NEEDS_SECOND_REVIEW'} or (t.get('ticket_state') == 'external_done' and item.get('review_count') == 0):
                create_operator_note(f'Trade Lifecycle Follow-up #{t.get("id")}', f'ticket={t}\nrisk={r}\nreview_count={item.get("review_count")}', 'trade_lifecycle_followup')
                note_count += 1
    notification = notify_operator(f'交易生命周期报表已同步：total={data["counts"]["total"]}, needs_review={data["counts"]["needs_review"]}, blocked={data["counts"]["blocked"]}') if req.sync_operator else None
    db.audit('trade_lifecycle_operator_sync', 'trade_lifecycle', 'summary', {'counts': data.get('counts'), 'note_count': note_count}, 'success', 'high', 'not_required')
    return {'status': 'success', 'report': report, 'note_count': note_count, 'notification': notification, 'dashboard': data, 'safety': 'report sync only; no exchange order submission'}
