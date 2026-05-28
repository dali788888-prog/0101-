from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/trade-readiness', tags=['Trade Readiness Gate'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def dec(value: Any) -> Decimal:
    return Decimal(str(value or '0'))


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS trade_ready_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,exchange TEXT NOT NULL,market_type TEXT NOT NULL,api_key_ref TEXT,mode TEXT NOT NULL DEFAULT 'live',withdraw_permission TEXT NOT NULL DEFAULT 'disabled',ip_allowlist TEXT NOT NULL DEFAULT 'required',enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS trade_ready_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,account_id INTEGER NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,order_type TEXT NOT NULL,amount TEXT NOT NULL,price TEXT,max_quote_value TEXT NOT NULL,estimated_quote_value TEXT NOT NULL,risk_tier TEXT NOT NULL DEFAULT 'critical',approval_state TEXT NOT NULL DEFAULT 'pending',ticket_state TEXT NOT NULL DEFAULT 'draft',external_ref TEXT,response_json TEXT NOT NULL DEFAULT '{}',risk_note TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS trade_ready_control (key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)''')


class ReadinessAccountCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    exchange: str = Field(default='binance', pattern='^(binance|okx|bybit|gate|other)$')
    market_type: str = Field(default='spot', pattern='^(spot|swap|future)$')
    api_key_ref: str = ''
    mode: str = Field(default='live', pattern='^(sandbox|live)$')
    withdraw_permission: str = Field(default='disabled', pattern='^(disabled|unknown)$')
    ip_allowlist: str = Field(default='required', pattern='^(required|configured|unknown)$')
    enabled: bool = True


class ReadinessTicketCreate(BaseModel):
    account_id: int
    symbol: str = Field(default='BTC/USDT', min_length=3, max_length=40)
    side: str = Field(pattern='^(buy|sell)$')
    order_type: str = Field(default='market', pattern='^(market|limit)$')
    amount: str = Field(default='0')
    price: str = ''
    max_quote_value: str = '20'
    note: str = ''


class ReadinessDecision(BaseModel):
    decision: str = Field(pattern='^(approved|rejected)$')
    operator: str = 'local-operator'
    note: str = ''


class ReadinessTicketIssue(BaseModel):
    operator: str = 'local-operator'
    confirm_phrase: str = ''


class ReadinessTicketMark(BaseModel):
    external_ref: str = Field(min_length=2, max_length=200)
    fill_price: str = ''
    fill_amount: str = ''
    operator: str = 'local-operator'
    note: str = ''


class ReadinessKillSwitch(BaseModel):
    enabled: bool
    operator: str = 'local-operator'
    confirm_phrase: str = ''


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def get_control(key: str, default: str) -> str:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute('SELECT value FROM trade_ready_control WHERE key=?', (key,)).fetchone()
    return dict(r)['value'] if r else default


def set_control(key: str, value: str) -> None:
    ensure_tables()
    with db.connect() as conn:
        conn.execute('INSERT INTO trade_ready_control (key,value,updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at', (key, value, now()))


def kill_switch_on() -> bool:
    return get_control('kill_switch', 'on') == 'on'


def quote_estimate(req: ReadinessTicketCreate) -> Decimal:
    amount = dec(req.amount)
    price = dec(req.price) if req.price else Decimal('0')
    max_quote = dec(req.max_quote_value)
    if amount <= 0:
        raise HTTPException(status_code=400, detail='amount must be > 0')
    if req.order_type == 'limit' and price <= 0:
        raise HTTPException(status_code=400, detail='limit ticket requires price')
    estimated = amount * price if price > 0 else max_quote
    if estimated <= 0:
        raise HTTPException(status_code=400, detail='max_quote_value or price must be > 0')
    hard_limit = Decimal('20')
    if estimated > hard_limit:
        raise HTTPException(status_code=400, detail=f'estimated quote value {estimated} exceeds first-stage hard limit {hard_limit}')
    if estimated > max_quote:
        raise HTTPException(status_code=400, detail=f'estimated quote value {estimated} exceeds max_quote_value {max_quote}')
    return estimated


def readiness_checks(account: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    checks = [
        {'key': 'sub_account', 'title': '使用交易所子账户', 'required': True, 'status': 'manual_check'},
        {'key': 'withdraw_disabled', 'title': 'API 禁止提现权限', 'required': True, 'status': 'pass' if account and account.get('withdraw_permission') == 'disabled' else 'manual_check'},
        {'key': 'ip_allowlist', 'title': 'API 固定 IP 白名单', 'required': True, 'status': 'pass' if account and account.get('ip_allowlist') == 'configured' else 'manual_check'},
        {'key': 'small_size', 'title': '单笔首阶段不超过 20 USDT', 'required': True, 'status': 'enforced'},
        {'key': 'manual_approval', 'title': '每笔需要人工审批', 'required': True, 'status': 'enforced'},
        {'key': 'kill_switch', 'title': '紧急停止开关', 'required': True, 'status': 'on' if kill_switch_on() else 'off'},
        {'key': 'no_auto_submit', 'title': '系统不自动提交交易所订单', 'required': True, 'status': 'enforced'},
    ]
    return checks


@router.get('/status')
def status() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'version': '16.4-trade-readiness-gate',
        'kill_switch': 'on' if kill_switch_on() else 'off',
        'hard_limit_usdt_first_stage': '20',
        'readiness_checks': readiness_checks(),
        'scope': 'approval tickets and manual handoff only',
        'not_supported': ['autonomous order submission', 'custody of secrets', 'withdrawals', 'bypass approvals'],
    }


@router.post('/accounts', dependencies=[Depends(require_key)])
def create_account(req: ReadinessAccountCreate) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO trade_ready_accounts (name,exchange,market_type,api_key_ref,mode,withdraw_permission,ip_allowlist,enabled,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.exchange, req.market_type, req.api_key_ref, req.mode, req.withdraw_permission, req.ip_allowlist, int(req.enabled), ts, ts))
        account_id = int(cur.lastrowid)
    db.audit('trade_ready_account_create', 'trade_ready_account', str(account_id), {'name': req.name, 'exchange': req.exchange, 'mode': req.mode}, 'success', 'high', 'not_required')
    return row('SELECT * FROM trade_ready_accounts WHERE id=?', (account_id,)) or {'id': account_id}


@router.get('/accounts')
def list_accounts() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM trade_ready_accounts ORDER BY id DESC')


@router.get('/accounts/{account_id}/checklist')
def account_checklist(account_id: int) -> Dict[str, Any]:
    account = row('SELECT * FROM trade_ready_accounts WHERE id=?', (account_id,))
    if not account:
        raise HTTPException(status_code=404, detail='account not found')
    return {'status': 'ok', 'account': account, 'checks': readiness_checks(account)}


@router.post('/tickets', dependencies=[Depends(require_key)])
def create_ticket(req: ReadinessTicketCreate) -> Dict[str, Any]:
    account = row('SELECT * FROM trade_ready_accounts WHERE id=?', (req.account_id,))
    if not account:
        raise HTTPException(status_code=404, detail='account not found')
    if not account['enabled']:
        raise HTTPException(status_code=400, detail='account disabled')
    estimated = quote_estimate(req)
    risk_note = '; '.join(['trade readiness ticket', 'manual approval required', 'manual external execution required', 'system will not submit orders', req.note])
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO trade_ready_tickets (account_id,symbol,side,order_type,amount,price,max_quote_value,estimated_quote_value,risk_tier,approval_state,ticket_state,response_json,risk_note,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.account_id, req.symbol, req.side, req.order_type, req.amount, req.price, req.max_quote_value, str(estimated), 'critical', 'pending', 'draft', '{}', risk_note, ts, ts))
        ticket_id = int(cur.lastrowid)
    db.audit('trade_ready_ticket_create', 'trade_ready_ticket', str(ticket_id), req.model_dump(), 'success', 'critical', 'pending')
    return row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,)) or {'id': ticket_id}


@router.get('/tickets')
def list_tickets(limit: int = 100) -> List[Dict[str, Any]]:
    return rows('SELECT * FROM trade_ready_tickets ORDER BY id DESC LIMIT ?', (limit,))


@router.post('/tickets/{ticket_id}/decision', dependencies=[Depends(require_key)])
def decide_ticket(ticket_id: int, req: ReadinessDecision) -> Dict[str, Any]:
    ticket = row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,))
    if not ticket:
        raise HTTPException(status_code=404, detail='ticket not found')
    with db.connect() as conn:
        conn.execute('UPDATE trade_ready_tickets SET approval_state=?, updated_at=? WHERE id=?', (req.decision, now(), ticket_id))
    db.audit('trade_ready_ticket_decision', 'trade_ready_ticket', str(ticket_id), req.model_dump(), 'success', 'critical', req.decision)
    return row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,)) or {'id': ticket_id}


@router.post('/tickets/{ticket_id}/issue-manual-ticket', dependencies=[Depends(require_key)])
def issue_manual_ticket(ticket_id: int, req: ReadinessTicketIssue) -> Dict[str, Any]:
    ticket = row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,))
    if not ticket:
        raise HTTPException(status_code=404, detail='ticket not found')
    if ticket['approval_state'] != 'approved':
        raise HTTPException(status_code=400, detail='ticket must be approved first')
    if kill_switch_on():
        raise HTTPException(status_code=400, detail='kill switch is ON')
    phrase = f'ISSUE MANUAL TICKET {ticket_id}'
    if req.confirm_phrase != phrase:
        raise HTTPException(status_code=400, detail=f'exact confirmation phrase required: {phrase}')
    account = row('SELECT * FROM trade_ready_accounts WHERE id=?', (ticket['account_id'],)) or {}
    manual_ticket = {
        'ticket_id': ticket_id,
        'exchange': account.get('exchange'),
        'market_type': account.get('market_type'),
        'symbol': ticket['symbol'],
        'side': ticket['side'],
        'order_type': ticket['order_type'],
        'amount': ticket['amount'],
        'price': ticket['price'],
        'max_quote_value': ticket['max_quote_value'],
        'risk_note': ticket['risk_note'],
        'operator': req.operator,
        'created_at_utc': now(),
        'after_manual_action': 'Record external reference with /mark-external-done.',
    }
    with db.connect() as conn:
        conn.execute('UPDATE trade_ready_tickets SET ticket_state=?, response_json=?, updated_at=? WHERE id=?', ('manual_ticket_issued', jd(manual_ticket), now(), ticket_id))
    db.audit('trade_ready_manual_ticket_issued', 'trade_ready_ticket', str(ticket_id), {'operator': req.operator}, 'success', 'critical', 'approved')
    return {'status': 'success', 'manual_ticket': manual_ticket}


@router.post('/tickets/{ticket_id}/mark-external-done', dependencies=[Depends(require_key)])
def mark_external_done(ticket_id: int, req: ReadinessTicketMark) -> Dict[str, Any]:
    ticket = row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,))
    if not ticket:
        raise HTTPException(status_code=404, detail='ticket not found')
    payload = {'external_ref': req.external_ref, 'fill_price': req.fill_price, 'fill_amount': req.fill_amount, 'operator': req.operator, 'note': req.note, 'marked_at_utc': now()}
    with db.connect() as conn:
        conn.execute('UPDATE trade_ready_tickets SET ticket_state=?, external_ref=?, response_json=?, updated_at=? WHERE id=?', ('external_done', req.external_ref, jd(payload), now(), ticket_id))
    db.audit('trade_ready_external_done', 'trade_ready_ticket', str(ticket_id), payload, 'success', 'critical', 'external_done')
    return row('SELECT * FROM trade_ready_tickets WHERE id=?', (ticket_id,)) or {'id': ticket_id}


@router.post('/kill-switch', dependencies=[Depends(require_key)])
def update_kill_switch(req: ReadinessKillSwitch) -> Dict[str, Any]:
    phrase = 'ENABLE READINESS HANDOFF' if not req.enabled else 'STOP READINESS HANDOFF'
    if req.confirm_phrase != phrase:
        raise HTTPException(status_code=400, detail=f'exact confirmation phrase required: {phrase}')
    set_control('kill_switch', 'on' if req.enabled else 'off')
    db.audit('trade_ready_kill_switch', 'trade_ready_control', 'kill_switch', req.model_dump(), 'success', 'critical', 'approved')
    return status()
