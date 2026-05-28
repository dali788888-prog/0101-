from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.exchange_market import get_price

router = APIRouter(prefix='/portfolio-risk', tags=['Portfolio Exposure Risk Budget Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def dec(value: Any) -> Decimal:
    return Decimal(str(value or '0'))


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS portfolio_positions (id INTEGER PRIMARY KEY AUTOINCREMENT,account_label TEXT NOT NULL DEFAULT 'manual',symbol TEXT NOT NULL,qty TEXT NOT NULL,cost_basis TEXT NOT NULL DEFAULT '0',mark_price TEXT NOT NULL DEFAULT '0',provider TEXT NOT NULL DEFAULT 'binance',source TEXT NOT NULL DEFAULT 'manual',notes TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS risk_budget_entries (id INTEGER PRIMARY KEY AUTOINCREMENT,budget_date TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'daily',starting_budget_usdt TEXT NOT NULL DEFAULT '0',max_daily_loss_usdt TEXT NOT NULL DEFAULT '0',used_loss_usdt TEXT NOT NULL DEFAULT '0',reserved_risk_usdt TEXT NOT NULL DEFAULT '0',status TEXT NOT NULL DEFAULT 'open',notes TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS manual_trade_journal (id INTEGER PRIMARY KEY AUTOINCREMENT,trade_date TEXT NOT NULL,source TEXT NOT NULL DEFAULT 'manual',symbol TEXT NOT NULL,side TEXT NOT NULL,qty TEXT NOT NULL,entry_price TEXT NOT NULL DEFAULT '0',exit_price TEXT NOT NULL DEFAULT '0',pnl_usdt TEXT NOT NULL DEFAULT '0',fees_usdt TEXT NOT NULL DEFAULT '0',outcome TEXT NOT NULL DEFAULT 'open',related_ticket_id INTEGER,related_paper_order_id INTEGER,notes TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS weekly_strategy_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT,week_label TEXT NOT NULL,summary TEXT NOT NULL,signal_accuracy_json TEXT NOT NULL DEFAULT '{}',paper_pnl_json TEXT NOT NULL DEFAULT '{}',lifecycle_json TEXT NOT NULL DEFAULT '{}',action_items TEXT,created_at TEXT NOT NULL)''')


class PositionUpsert(BaseModel):
    account_label: str = 'manual'
    symbol: str = Field(default='BTCUSDT', min_length=3, max_length=40)
    qty: str = '0'
    cost_basis: str = '0'
    mark_price: str = '0'
    provider: str = 'binance'
    source: str = 'manual'
    notes: str = ''


class RiskBudgetCreate(BaseModel):
    budget_date: str = Field(default_factory=today)
    scope: str = Field(default='daily', pattern='^(daily|weekly|monthly|campaign)$')
    starting_budget_usdt: str = '100'
    max_daily_loss_usdt: str = '20'
    used_loss_usdt: str = '0'
    reserved_risk_usdt: str = '0'
    status: str = Field(default='open', pattern='^(open|locked|breached|closed)$')
    notes: str = ''


class JournalCreate(BaseModel):
    trade_date: str = Field(default_factory=today)
    source: str = Field(default='manual', pattern='^(manual|paper|readiness|lifecycle|external)$')
    symbol: str = Field(default='BTCUSDT', min_length=3, max_length=40)
    side: str = Field(pattern='^(buy|sell|long|short|close)$')
    qty: str = '0'
    entry_price: str = '0'
    exit_price: str = '0'
    pnl_usdt: str = '0'
    fees_usdt: str = '0'
    outcome: str = Field(default='open', pattern='^(open|win|loss|breakeven|cancelled|reviewed)$')
    related_ticket_id: Optional[int] = None
    related_paper_order_id: Optional[int] = None
    notes: str = ''


class WeeklyReviewCreate(BaseModel):
    week_label: str = ''
    summary: str = ''
    action_items: str = ''
    auto_generate: bool = True


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


def norm_symbol(symbol: str) -> str:
    return symbol.replace('/', '').replace('-', '').replace('_', '').upper()


def mark_price(provider: str, symbol: str, fallback: str = '0') -> Decimal:
    if dec(fallback) > 0:
        return dec(fallback)
    try:
        data = get_price(provider, norm_symbol(symbol))
        return dec(data.get('price'))
    except Exception:
        return Decimal('0')


def position_value(position: Dict[str, Any]) -> Dict[str, Any]:
    qty = dec(position.get('qty'))
    cost = dec(position.get('cost_basis'))
    mark = mark_price(position.get('provider') or 'binance', position.get('symbol') or '', position.get('mark_price') or '0')
    value = qty * mark
    cost_value = qty * cost
    unrealized = value - cost_value if cost > 0 else Decimal('0')
    out = dict(position)
    out.update({'live_mark_price': str(mark), 'market_value_usdt': str(value), 'cost_value_usdt': str(cost_value), 'unrealized_pnl_usdt': str(unrealized)})
    return out


def paper_summary() -> Dict[str, Any]:
    try:
        from app.paper_trading import dashboard as paper_dashboard
        return paper_dashboard()
    except Exception as exc:
        return {'status': 'unavailable', 'error': str(exc)}


def lifecycle_summary() -> Dict[str, Any]:
    try:
        from app.trade_lifecycle import dashboard as lifecycle_dashboard
        return lifecycle_dashboard(limit=100)
    except Exception as exc:
        return {'status': 'unavailable', 'error': str(exc)}


def accuracy_summary() -> Dict[str, Any]:
    try:
        from app.paper_trading import accuracy
        return accuracy()
    except Exception as exc:
        return {'status': 'unavailable', 'error': str(exc)}


def risk_budget_summary() -> Dict[str, Any]:
    budgets = rows('SELECT * FROM risk_budget_entries ORDER BY budget_date DESC, id DESC LIMIT 100')
    open_items = [x for x in budgets if x.get('status') == 'open']
    used = sum(float(x.get('used_loss_usdt') or 0) for x in open_items)
    reserved = sum(float(x.get('reserved_risk_usdt') or 0) for x in open_items)
    max_loss = sum(float(x.get('max_daily_loss_usdt') or 0) for x in open_items)
    remaining = max_loss - used - reserved
    status = 'breached' if remaining < 0 else 'warning' if max_loss and remaining < max_loss * 0.25 else 'ok'
    return {'status': status, 'open_budget_count': len(open_items), 'used_loss_usdt': round(used, 8), 'reserved_risk_usdt': round(reserved, 8), 'max_loss_usdt': round(max_loss, 8), 'remaining_risk_usdt': round(remaining, 8), 'budgets': budgets[:30]}


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '17.6-portfolio-risk-budget-journal', 'features': ['portfolio exposure dashboard', 'daily risk budget ledger', 'manual trade journal', 'weekly strategy review'], 'safety': 'risk bookkeeping only; no exchange order submission'}


@router.post('/positions', dependencies=[Depends(require_key)])
def create_position(req: PositionUpsert) -> Dict[str, Any]:
    qty = dec(req.qty)
    if qty == 0:
        raise HTTPException(status_code=400, detail='qty must not be zero')
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO portfolio_positions (account_label,symbol,qty,cost_basis,mark_price,provider,source,notes,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.account_label, norm_symbol(req.symbol), str(qty), req.cost_basis, req.mark_price, req.provider, req.source, req.notes, ts, ts))
        pid = int(cur.lastrowid)
    db.audit('portfolio_position_create', 'portfolio_position', str(pid), req.model_dump(), 'success', 'medium', 'not_required')
    return position_value(row('SELECT * FROM portfolio_positions WHERE id=?', (pid,)) or {'id': pid})


@router.get('/positions')
def list_positions() -> Dict[str, Any]:
    data = [position_value(x) for x in rows('SELECT * FROM portfolio_positions ORDER BY id DESC')]
    total_value = sum(float(x.get('market_value_usdt') or 0) for x in data)
    total_unrealized = sum(float(x.get('unrealized_pnl_usdt') or 0) for x in data)
    by_symbol: Dict[str, float] = {}
    for x in data:
        by_symbol[x['symbol']] = by_symbol.get(x['symbol'], 0.0) + float(x.get('market_value_usdt') or 0)
    return {'status': 'ok', 'positions': data, 'summary': {'position_count': len(data), 'total_market_value_usdt': round(total_value, 8), 'total_unrealized_pnl_usdt': round(total_unrealized, 8), 'by_symbol': by_symbol}}


@router.post('/risk-budgets', dependencies=[Depends(require_key)])
def create_risk_budget(req: RiskBudgetCreate) -> Dict[str, Any]:
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO risk_budget_entries (budget_date,scope,starting_budget_usdt,max_daily_loss_usdt,used_loss_usdt,reserved_risk_usdt,status,notes,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.budget_date, req.scope, req.starting_budget_usdt, req.max_daily_loss_usdt, req.used_loss_usdt, req.reserved_risk_usdt, req.status, req.notes, ts, ts))
        bid = int(cur.lastrowid)
    db.audit('risk_budget_create', 'risk_budget', str(bid), req.model_dump(), 'success', 'medium', 'not_required')
    return row('SELECT * FROM risk_budget_entries WHERE id=?', (bid,)) or {'id': bid}


@router.get('/risk-budgets')
def list_risk_budgets() -> Dict[str, Any]:
    return {'status': 'ok', 'summary': risk_budget_summary(), 'budgets': rows('SELECT * FROM risk_budget_entries ORDER BY budget_date DESC, id DESC LIMIT 200')}


@router.post('/journal', dependencies=[Depends(require_key)])
def create_journal(req: JournalCreate) -> Dict[str, Any]:
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO manual_trade_journal (trade_date,source,symbol,side,qty,entry_price,exit_price,pnl_usdt,fees_usdt,outcome,related_ticket_id,related_paper_order_id,notes,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.trade_date, req.source, norm_symbol(req.symbol), req.side, req.qty, req.entry_price, req.exit_price, req.pnl_usdt, req.fees_usdt, req.outcome, req.related_ticket_id, req.related_paper_order_id, req.notes, ts, ts))
        jid = int(cur.lastrowid)
    db.audit('manual_trade_journal_create', 'manual_trade_journal', str(jid), req.model_dump(), 'success', 'medium', 'reviewed')
    return row('SELECT * FROM manual_trade_journal WHERE id=?', (jid,)) or {'id': jid}


@router.get('/journal')
def list_journal(limit: int = 100) -> Dict[str, Any]:
    data = rows('SELECT * FROM manual_trade_journal ORDER BY trade_date DESC, id DESC LIMIT ?', (limit,))
    pnl = sum(float(x.get('pnl_usdt') or 0) - float(x.get('fees_usdt') or 0) for x in data)
    wins = len([x for x in data if x.get('outcome') == 'win'])
    losses = len([x for x in data if x.get('outcome') == 'loss'])
    return {'status': 'ok', 'summary': {'count': len(data), 'net_pnl_usdt': round(pnl, 8), 'wins': wins, 'losses': losses}, 'entries': data}


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    positions = list_positions()
    budgets = risk_budget_summary()
    journal = list_journal(limit=100)
    paper = paper_summary()
    lifecycle = lifecycle_summary()
    warnings: List[str] = []
    if budgets.get('status') == 'breached':
        warnings.append('risk budget breached')
    if (lifecycle.get('counts') or {}).get('blocked', 0):
        warnings.append('blocked lifecycle tickets exist')
    if (paper.get('accuracy') or {}).get('false_positive', 0):
        warnings.append('paper feedback contains false positives')
    return {'status': 'ok', 'version': '17.6-portfolio-risk-budget-journal', 'positions': positions, 'risk_budget': budgets, 'journal': journal, 'paper': paper, 'lifecycle': lifecycle, 'warnings': warnings, 'safety': 'dashboard only; no exchange order submission', 'time_utc': now()}


@router.post('/weekly-review/generate', dependencies=[Depends(require_key)])
def generate_weekly_review(req: WeeklyReviewCreate) -> Dict[str, Any]:
    ensure_tables()
    week_label = req.week_label or datetime.now(timezone.utc).strftime('%G-W%V')
    acc = accuracy_summary()
    paper = paper_summary()
    life = lifecycle_summary()
    dash = dashboard()
    summary = req.summary or '\n'.join([
        f'Week: {week_label}',
        f'Paper realized PnL: {paper.get("realized_pnl", 0)}',
        f'Signal win rate: {(acc or {}).get("win_rate", 0)}%',
        f'Lifecycle tickets: {(life.get("counts") or {}).get("total", 0)}',
        f'Risk budget status: {(dash.get("risk_budget") or {}).get("status")}',
        '结论：继续以仿真验证、人工复盘、风险预算为主，不进入自动真实下单。',
    ])
    action_items = req.action_items or '\n'.join([
        '- 复核 false_positive 信号来源',
        '- 对 blocked 票据做二次审查',
        '- 保持单笔小额与手工执行边界',
        '- 将高质量信号继续进入 Paper Trading 验证',
    ])
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO weekly_strategy_reviews (week_label,summary,signal_accuracy_json,paper_pnl_json,lifecycle_json,action_items,created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (week_label, summary, jd(acc), jd(paper), jd(life), action_items, ts))
        rid = int(cur.lastrowid)
        review = dict(conn.execute('SELECT * FROM weekly_strategy_reviews WHERE id=?', (rid,)).fetchone())
    db.audit('weekly_strategy_review_generate', 'weekly_strategy_review', str(rid), {'week_label': week_label}, 'success', 'medium', 'reviewed')
    return {'status': 'success', 'review': review, 'dashboard': dash, 'safety': 'review only; no exchange order submission'}


@router.get('/weekly-reviews')
def list_weekly_reviews(limit: int = 50) -> Dict[str, Any]:
    data = rows('SELECT * FROM weekly_strategy_reviews ORDER BY id DESC LIMIT ?', (limit,))
    for x in data:
        x['signal_accuracy'] = parse_json(x.pop('signal_accuracy_json', '{}'))
        x['paper_pnl'] = parse_json(x.pop('paper_pnl_json', '{}'))
        x['lifecycle'] = parse_json(x.pop('lifecycle_json', '{}'))
    return {'status': 'ok', 'reviews': data}


@router.get('/report')
def report() -> Dict[str, Any]:
    dash = dashboard()
    content = '\n'.join([
        '# Hermes Portfolio Risk Budget Journal Report',
        f'- created_at_utc: {now()}',
        f'- total_position_value_usdt: {dash["positions"]["summary"]["total_market_value_usdt"]}',
        f'- unrealized_pnl_usdt: {dash["positions"]["summary"]["total_unrealized_pnl_usdt"]}',
        f'- risk_budget_status: {dash["risk_budget"]["status"]}',
        f'- remaining_risk_usdt: {dash["risk_budget"]["remaining_risk_usdt"]}',
        f'- manual_journal_net_pnl_usdt: {dash["journal"]["summary"]["net_pnl_usdt"]}',
        f'- paper_realized_pnl: {dash["paper"].get("realized_pnl", 0)}',
        '',
        '## Warnings',
        *[f'- {x}' for x in dash.get('warnings', [])],
        '',
        '## Safety',
        '- Bookkeeping and review only. No autonomous live order submission.',
    ])
    return {'status': 'ok', 'content': content, 'dashboard': dash}
