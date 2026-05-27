from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.quant_bot import ensure_tables as ensure_quant_tables, rows, row, now

router = APIRouter(prefix='/quant-bot', tags=['Hermes Quant Emergency Risk Prevention'])

EMERGENCY_PHRASE = 'EMERGENCY STOP'


def require_quant_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class EmergencyPlanCreate(BaseModel):
    name: str = Field(..., min_length=2)
    trigger_note: str = 'manual emergency plan'
    stop_bots: bool = True
    disable_autopilots: bool = True
    reject_pending_orders: bool = True
    disable_live_profiles: bool = True
    create_report: bool = True
    enabled: bool = True


class EmergencyExecuteRequest(BaseModel):
    plan_id: Optional[int] = None
    operator: str = 'local-operator'
    phrase: str
    reason: str = 'manual emergency stop'


class EmergencyAckRequest(BaseModel):
    event_id: int
    operator: str = 'local-operator'
    note: str = ''


def ensure_tables() -> None:
    ensure_quant_tables()
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_emergency_plans (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,trigger_note TEXT,stop_bots INTEGER NOT NULL DEFAULT 1,disable_autopilots INTEGER NOT NULL DEFAULT 1,reject_pending_orders INTEGER NOT NULL DEFAULT 1,disable_live_profiles INTEGER NOT NULL DEFAULT 1,create_report INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_emergency_events (id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id INTEGER,operator TEXT NOT NULL,reason TEXT NOT NULL,actions_json TEXT NOT NULL,snapshot_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open',created_at TEXT NOT NULL,ack_by TEXT,ack_at TEXT,ack_note TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_emergency_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER,summary_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def snapshot_state() -> Dict[str, Any]:
    ensure_tables()
    def safe_select(query: str) -> List[Dict[str, Any]]:
        try:
            return qrows(query)
        except Exception:
            return []
    return {
        'created_at': now(),
        'running_bots': safe_select("SELECT * FROM quant_bots WHERE state='running' ORDER BY id"),
        'pending_orders': safe_select("SELECT * FROM quant_orders WHERE approval_state='pending' ORDER BY id"),
        'approved_draft_orders': safe_select("SELECT * FROM quant_orders WHERE approval_state='approved' AND status='draft' ORDER BY id"),
        'autopilots': safe_select("SELECT * FROM quant_autopilots WHERE enabled=1 ORDER BY id"),
        'live_profiles': safe_select("SELECT id,name,exchange,market_type,enabled,armed,armed_by,armed_at,last_status,last_error FROM quant_live_profiles WHERE enabled=1 OR armed=1 ORDER BY id"),
        'latest_predictions': safe_select("SELECT * FROM quant_predictions ORDER BY id DESC LIMIT 20"),
        'trade_notifications': safe_select("SELECT * FROM quant_trade_notifications WHERE status='new' ORDER BY id DESC LIMIT 20"),
    }


@router.get('/emergency/status')
def emergency_status() -> Dict[str, Any]:
    ensure_tables()
    snap = snapshot_state()
    return {
        'status': 'ok',
        'version': '10.10-emergency-risk-prevention',
        'required_phrase': EMERGENCY_PHRASE,
        'running_bots': len(snap['running_bots']),
        'pending_orders': len(snap['pending_orders']),
        'approved_draft_orders': len(snap['approved_draft_orders']),
        'enabled_autopilots': len(snap['autopilots']),
        'armed_or_enabled_live_profiles': len(snap['live_profiles']),
        'open_trade_notifications': len(snap['trade_notifications']),
    }


@router.post('/emergency/plans', dependencies=[Depends(require_quant_key)])
def create_emergency_plan(req: EmergencyPlanCreate) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_emergency_plans (name, trigger_note, stop_bots, disable_autopilots, reject_pending_orders, disable_live_profiles, create_report, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.trigger_note, int(req.stop_bots), int(req.disable_autopilots), int(req.reject_pending_orders), int(req.disable_live_profiles), int(req.create_report), int(req.enabled), ts, ts))
        plan_id = int(cur.lastrowid)
    db.audit('quant_emergency_plan_create', 'quant_emergency_plan', str(plan_id), req.model_dump(), 'success', 'critical', 'not_required')
    return qrow('SELECT * FROM quant_emergency_plans WHERE id=?', (plan_id,)) or {'id': plan_id}


@router.get('/emergency/plans')
def list_emergency_plans() -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_emergency_plans ORDER BY id DESC')


def default_plan() -> Dict[str, Any]:
    return {
        'id': None,
        'name': 'Default Emergency Stop',
        'stop_bots': 1,
        'disable_autopilots': 1,
        'reject_pending_orders': 1,
        'disable_live_profiles': 1,
        'create_report': 1,
    }


@router.post('/emergency/execute', dependencies=[Depends(require_quant_key)])
def execute_emergency(req: EmergencyExecuteRequest) -> Dict[str, Any]:
    ensure_tables()
    if req.phrase != EMERGENCY_PHRASE:
        raise HTTPException(status_code=400, detail='emergency phrase mismatch')
    plan = default_plan()
    if req.plan_id:
        found = qrow('SELECT * FROM quant_emergency_plans WHERE id=?', (req.plan_id,))
        if not found:
            raise HTTPException(status_code=404, detail='emergency plan not found')
        if not int(found.get('enabled') or 0):
            raise HTTPException(status_code=400, detail='emergency plan is disabled')
        plan = found
    before = snapshot_state()
    actions: Dict[str, Any] = {'plan': plan.get('name'), 'operator': req.operator, 'reason': req.reason, 'updates': {}}
    ts = now()
    with db.connect() as conn:
        if int(plan.get('stop_bots') or 0):
            cur = conn.execute("UPDATE quant_bots SET state='stopped', updated_at=? WHERE state='running'", (ts,))
            actions['updates']['stopped_bots'] = cur.rowcount
        if int(plan.get('disable_autopilots') or 0):
            try:
                cur = conn.execute('UPDATE quant_autopilots SET enabled=0,last_status=?,updated_at=? WHERE enabled=1', ('emergency_disabled', ts))
                actions['updates']['disabled_autopilots'] = cur.rowcount
            except Exception as exc:
                actions['updates']['disabled_autopilots_error'] = str(exc)
        if int(plan.get('reject_pending_orders') or 0):
            cur = conn.execute("UPDATE quant_orders SET approval_state='rejected', status='emergency_blocked', updated_at=? WHERE approval_state IN ('pending','approved') AND status='draft'", (ts,))
            actions['updates']['blocked_orders'] = cur.rowcount
        if int(plan.get('disable_live_profiles') or 0):
            try:
                cur = conn.execute("UPDATE quant_live_profiles SET enabled=0, armed=0, last_status='emergency_disabled', updated_at=? WHERE enabled=1 OR armed=1", (ts,))
                actions['updates']['disabled_live_profiles'] = cur.rowcount
            except Exception as exc:
                actions['updates']['disabled_live_profiles_error'] = str(exc)
        cur = conn.execute('INSERT INTO quant_emergency_events (plan_id, operator, reason, actions_json, snapshot_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (req.plan_id, req.operator, req.reason, jd(actions), jd(before), 'open', ts))
        event_id = int(cur.lastrowid)
        if int(plan.get('create_report') or 0):
            conn.execute('INSERT INTO quant_emergency_snapshots (event_id, summary_json, created_at) VALUES (?, ?, ?)', (event_id, jd({'before': before, 'actions': actions}), ts))
    db.audit('quant_emergency_execute', 'quant_emergency_event', str(event_id), actions, 'emergency_executed', 'critical', 'approved')
    return {'status': 'emergency_executed', 'event_id': event_id, 'actions': actions, 'snapshot_before': before}


@router.get('/emergency/events')
def list_emergency_events(limit: int = 100) -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_emergency_events ORDER BY id DESC LIMIT ?', (limit,))


@router.get('/emergency/snapshot')
def current_emergency_snapshot() -> Dict[str, Any]:
    return snapshot_state()


@router.post('/emergency/events/ack', dependencies=[Depends(require_quant_key)])
def ack_emergency_event(req: EmergencyAckRequest) -> Dict[str, Any]:
    event = qrow('SELECT * FROM quant_emergency_events WHERE id=?', (req.event_id,))
    if not event:
        raise HTTPException(status_code=404, detail='emergency event not found')
    with db.connect() as conn:
        conn.execute("UPDATE quant_emergency_events SET status='acked', ack_by=?, ack_at=?, ack_note=? WHERE id=?", (req.operator, now(), req.note, req.event_id))
    db.audit('quant_emergency_ack', 'quant_emergency_event', str(req.event_id), req.model_dump(), 'acked', 'medium', 'approved')
    return {'status': 'acked', 'event_id': req.event_id}


@router.post('/emergency/bootstrap-defaults', dependencies=[Depends(require_quant_key)])
def bootstrap_emergency_defaults() -> Dict[str, Any]:
    ensure_tables()
    if not qrow('SELECT id FROM quant_emergency_plans WHERE name=?', ('Full Emergency Stop',)):
        return create_emergency_plan(EmergencyPlanCreate(name='Full Emergency Stop', trigger_note='Stop bots, disable autopilots, block draft orders, disarm live profiles.', stop_bots=True, disable_autopilots=True, reject_pending_orders=True, disable_live_profiles=True, create_report=True, enabled=True))
    return {'status': 'exists', 'plan': qrow('SELECT * FROM quant_emergency_plans WHERE name=?', ('Full Emergency Stop',))}
