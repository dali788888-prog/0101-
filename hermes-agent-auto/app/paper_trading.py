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

router = APIRouter(prefix='/paper-trading', tags=['Paper Trading Simulation Ledger'])


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
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,base_currency TEXT NOT NULL DEFAULT 'USDT',starting_cash TEXT NOT NULL DEFAULT '10000',cash_balance TEXT NOT NULL DEFAULT '10000',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_orders (id INTEGER PRIMARY KEY AUTOINCREMENT,account_id INTEGER NOT NULL,signal_id TEXT,symbol TEXT NOT NULL,side TEXT NOT NULL,qty TEXT NOT NULL,entry_price TEXT NOT NULL,exit_price TEXT,status TEXT NOT NULL DEFAULT 'open',realized_pnl TEXT NOT NULL DEFAULT '0',fees TEXT NOT NULL DEFAULT '0',provider TEXT NOT NULL DEFAULT 'binance',score_snapshot TEXT NOT NULL DEFAULT '0',notes TEXT,response_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,closed_at TEXT,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_signal_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL,paper_order_id INTEGER,verdict TEXT NOT NULL,reason TEXT,metrics_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL)''')


class PaperAccountCreate(BaseModel):
    name: str = Field(default='Default Paper Account', min_length=2, max_length=120)
    base_currency: str = 'USDT'
    starting_cash: str = '10000'


class PaperOrderCreate(BaseModel):
    account_id: int
    symbol: str = Field(default='BTCUSDT', min_length=3, max_length=40)
    side: str = Field(pattern='^(buy|sell)$')
    qty: str = '0.001'
    entry_price: str = ''
    provider: str = 'binance'
    signal_id: str = ''
    notes: str = ''


class SignalPaperOrderCreate(BaseModel):
    account_id: int
    signal_id: str = Field(min_length=8, max_length=160)
    qty: str = '0.001'
    side_override: str = Field(default='', pattern='^(|buy|sell)$')
    provider: str = 'binance'
    entry_price: str = ''
    notes: str = ''


class PaperOrderClose(BaseModel):
    exit_price: str = ''
    provider: str = 'binance'
    fee_rate_pct: str = '0.10'
    notes: str = ''


class SignalFeedbackCreate(BaseModel):
    signal_id: str
    paper_order_id: Optional[int] = None
    verdict: str = Field(pattern='^(win|loss|breakeven|false_positive|useful|ignored|needs_more_data)$')
    reason: str = ''
    metrics: Dict[str, Any] = Field(default_factory=dict)


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def parse_json(value: str | None) -> Dict[str, Any]:
    try:
        return json.loads(value or '{}')
    except Exception:
        return {}


def normalize_symbol(symbol: str) -> str:
    return symbol.replace('/', '').replace('-', '').replace('_', '').upper()


def pretty_symbol(symbol: str) -> str:
    s = normalize_symbol(symbol)
    if s.endswith('USDT'):
        return s[:-4] + '/USDT'
    if s.endswith('USDC'):
        return s[:-4] + '/USDC'
    return symbol


def market_price(provider: str, symbol: str, supplied: str = '') -> Decimal:
    if supplied:
        p = dec(supplied)
        if p <= 0:
            raise HTTPException(status_code=400, detail='price must be > 0')
        return p
    data = get_price(provider, normalize_symbol(symbol))
    p = dec(data.get('price'))
    if p <= 0:
        raise HTTPException(status_code=400, detail='market price not available')
    return p


def read_signal(signal_id: str) -> Dict[str, Any]:
    try:
        with db.connect() as conn:
            r = conn.execute('SELECT * FROM strategy_signal_events WHERE signal_id=?', (signal_id,)).fetchone()
    except Exception as exc:
        raise HTTPException(status_code=404, detail='strategy signal table not found') from exc
    if not r:
        raise HTTPException(status_code=404, detail='strategy signal not found')
    item = dict(r)
    item['payload'] = parse_json(item.pop('payload_json', '{}'))
    return item


def infer_side_from_signal(signal: Dict[str, Any], override: str = '') -> str:
    if override:
        return override
    st = str(signal.get('signal_type') or '').upper()
    summary = str((signal.get('payload') or {}).get('summary') or '').lower()
    if 'SHORT' in st or '下行' in summary:
        return 'sell'
    return 'buy'


def account(account_id: int) -> Dict[str, Any]:
    item = row('SELECT * FROM paper_accounts WHERE id=?', (account_id,))
    if not item:
        raise HTTPException(status_code=404, detail='paper account not found')
    return item


def create_default_account_if_missing() -> Dict[str, Any]:
    existing = row('SELECT * FROM paper_accounts ORDER BY id ASC LIMIT 1')
    if existing:
        return existing
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO paper_accounts (name,base_currency,starting_cash,cash_balance,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)', ('Default Paper Account', 'USDT', '10000', '10000', ts, ts))
        aid = int(cur.lastrowid)
    return account(aid)


def order_to_out(item: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(item)
    item['response'] = parse_json(item.pop('response_json', '{}'))
    return item


def pnl_for(side: str, qty: Decimal, entry: Decimal, exit_price: Decimal, fee_rate_pct: Decimal) -> Dict[str, Decimal]:
    gross = (exit_price - entry) * qty if side == 'buy' else (entry - exit_price) * qty
    notional = (entry * qty) + (exit_price * qty)
    fees = notional * fee_rate_pct / Decimal('100')
    return {'gross': gross, 'fees': fees, 'net': gross - fees}


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '17.3-paper-trading-feedback-loop', 'features': ['paper ledger', 'signal simulation', 'paper pnl', 'signal accuracy scoring', 'feedback loop'], 'safety': 'simulation only; no exchange order submission'}


@router.post('/accounts', dependencies=[Depends(require_key)])
def create_account(req: PaperAccountCreate) -> Dict[str, Any]:
    ensure_tables()
    cash = dec(req.starting_cash)
    if cash <= 0:
        raise HTTPException(status_code=400, detail='starting_cash must be > 0')
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO paper_accounts (name,base_currency,starting_cash,cash_balance,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)', (req.name, req.base_currency, str(cash), str(cash), ts, ts))
        aid = int(cur.lastrowid)
    db.audit('paper_account_create', 'paper_account', str(aid), req.model_dump(), 'success', 'low', 'not_required')
    return account(aid)


@router.get('/accounts')
def accounts() -> List[Dict[str, Any]]:
    ensure_tables()
    create_default_account_if_missing()
    return rows('SELECT * FROM paper_accounts ORDER BY id DESC')


@router.post('/orders', dependencies=[Depends(require_key)])
def create_order(req: PaperOrderCreate) -> Dict[str, Any]:
    _acct = account(req.account_id)
    qty = dec(req.qty)
    if qty <= 0:
        raise HTTPException(status_code=400, detail='qty must be > 0')
    entry = market_price(req.provider, req.symbol, req.entry_price)
    ts = now()
    response = {'source': 'manual_paper_order', 'price_source': 'supplied' if req.entry_price else req.provider, 'safety': 'simulation_only'}
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO paper_orders (account_id,signal_id,symbol,side,qty,entry_price,status,provider,score_snapshot,notes,response_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.account_id, req.signal_id or None, pretty_symbol(req.symbol), req.side, str(qty), str(entry), 'open', req.provider, '0', req.notes, jd(response), ts, ts))
        oid = int(cur.lastrowid)
    db.audit('paper_order_create', 'paper_order', str(oid), req.model_dump(), 'success', 'low', 'not_required')
    return order_to_out(row('SELECT * FROM paper_orders WHERE id=?', (oid,)) or {'id': oid})


@router.get('/signals/open')
def open_signals(limit: int = 50) -> Dict[str, Any]:
    try:
        with db.connect() as conn:
            data = [dict(r) for r in conn.execute("SELECT * FROM strategy_signal_events WHERE status IN ('open','acknowledged','reviewed') ORDER BY score DESC, id DESC LIMIT ?", (limit,)).fetchall()]
        for x in data:
            x['payload'] = parse_json(x.pop('payload_json', '{}'))
        return {'status': 'ok', 'signals': data, 'safety': 'research signals only; paper simulation does not trade real funds'}
    except Exception:
        return {'status': 'ok', 'signals': [], 'safety': 'run strategy signal analysis first'}


@router.post('/signals/create-paper-order', dependencies=[Depends(require_key)])
def create_order_from_signal(req: SignalPaperOrderCreate) -> Dict[str, Any]:
    sig = read_signal(req.signal_id)
    side = infer_side_from_signal(sig, req.side_override)
    symbol = sig.get('symbol') or (sig.get('payload') or {}).get('symbol') or 'BTCUSDT'
    entry = market_price(req.provider, symbol, req.entry_price)
    qty = dec(req.qty)
    if qty <= 0:
        raise HTTPException(status_code=400, detail='qty must be > 0')
    _acct = account(req.account_id)
    ts = now()
    response = {'source_signal': sig, 'created_from_signal_at': ts, 'safety': 'simulation_only'}
    notes = '; '.join([f'source_signal_id={req.signal_id}', f'signal_type={sig.get("signal_type")}', f'signal_score={sig.get("score")}', req.notes])
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO paper_orders (account_id,signal_id,symbol,side,qty,entry_price,status,provider,score_snapshot,notes,response_json,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.account_id, req.signal_id, pretty_symbol(symbol), side, str(qty), str(entry), 'open', req.provider, str(sig.get('score') or 0), notes, jd(response), ts, ts))
        oid = int(cur.lastrowid)
    db.audit('paper_signal_to_order', 'paper_order', str(oid), {'signal_id': req.signal_id, 'side': side, 'symbol': symbol, 'account_id': req.account_id}, 'success', 'low', 'not_required')
    return {'status': 'success', 'order': order_to_out(row('SELECT * FROM paper_orders WHERE id=?', (oid,)) or {'id': oid}), 'source_signal': sig, 'safety': 'paper simulation only'}


@router.get('/orders')
def orders(limit: int = 100) -> Dict[str, Any]:
    data = [order_to_out(x) for x in rows('SELECT * FROM paper_orders ORDER BY id DESC LIMIT ?', (limit,))]
    return {'status': 'ok', 'orders': data}


@router.post('/orders/{order_id}/close', dependencies=[Depends(require_key)])
def close_order(order_id: int, req: PaperOrderClose) -> Dict[str, Any]:
    item = row('SELECT * FROM paper_orders WHERE id=?', (order_id,))
    if not item:
        raise HTTPException(status_code=404, detail='paper order not found')
    if item.get('status') != 'open':
        raise HTTPException(status_code=400, detail='paper order is not open')
    exit_price = market_price(req.provider, item['symbol'], req.exit_price)
    calc = pnl_for(item['side'], dec(item['qty']), dec(item['entry_price']), exit_price, dec(req.fee_rate_pct))
    ts = now()
    response = parse_json(item.get('response_json'))
    response['close'] = {'exit_price_source': 'supplied' if req.exit_price else req.provider, 'fee_rate_pct': req.fee_rate_pct, 'gross_pnl': str(calc['gross']), 'fees': str(calc['fees']), 'net_pnl': str(calc['net']), 'notes': req.notes}
    with db.connect() as conn:
        conn.execute('UPDATE paper_orders SET exit_price=?, status=?, realized_pnl=?, fees=?, response_json=?, closed_at=?, updated_at=? WHERE id=?', (str(exit_price), 'closed', str(calc['net']), str(calc['fees']), jd(response), ts, ts, order_id))
        conn.execute('UPDATE paper_accounts SET cash_balance=CAST(CAST(cash_balance AS REAL)+? AS TEXT), updated_at=? WHERE id=?', (float(calc['net']), ts, int(item['account_id'])))
    verdict = 'win' if calc['net'] > 0 else 'loss' if calc['net'] < 0 else 'breakeven'
    if item.get('signal_id'):
        with db.connect() as conn:
            conn.execute('INSERT INTO paper_signal_feedback (signal_id,paper_order_id,verdict,reason,metrics_json,created_at) VALUES (?, ?, ?, ?, ?, ?)', (item['signal_id'], order_id, verdict, 'auto feedback from paper close', jd({'pnl': str(calc['net']), 'entry': item['entry_price'], 'exit': str(exit_price)}), ts))
    db.audit('paper_order_close', 'paper_order', str(order_id), {'exit_price': str(exit_price), 'pnl': str(calc['net']), 'verdict': verdict}, 'success', 'low', 'not_required')
    return {'status': 'success', 'order': order_to_out(row('SELECT * FROM paper_orders WHERE id=?', (order_id,)) or {'id': order_id}), 'verdict': verdict}


@router.post('/feedback', dependencies=[Depends(require_key)])
def create_feedback(req: SignalFeedbackCreate) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO paper_signal_feedback (signal_id,paper_order_id,verdict,reason,metrics_json,created_at) VALUES (?, ?, ?, ?, ?, ?)', (req.signal_id, req.paper_order_id, req.verdict, req.reason, jd(req.metrics), ts))
        fid = int(cur.lastrowid)
        item = dict(conn.execute('SELECT * FROM paper_signal_feedback WHERE id=?', (fid,)).fetchone())
    db.audit('paper_signal_feedback_create', 'paper_signal', req.signal_id, req.model_dump(), 'success', 'low', 'not_required')
    item['metrics'] = parse_json(item.pop('metrics_json', '{}'))
    return {'status': 'success', 'feedback': item}


@router.get('/accuracy')
def accuracy() -> Dict[str, Any]:
    ensure_tables()
    feedback = rows('SELECT * FROM paper_signal_feedback ORDER BY id DESC')
    total = len(feedback)
    wins = len([x for x in feedback if x['verdict'] == 'win'])
    losses = len([x for x in feedback if x['verdict'] == 'loss'])
    false_positive = len([x for x in feedback if x['verdict'] == 'false_positive'])
    useful = len([x for x in feedback if x['verdict'] in {'win', 'useful'}])
    by_signal: Dict[str, Dict[str, Any]] = {}
    for x in feedback:
        sid = x['signal_id']
        b = by_signal.setdefault(sid, {'signal_id': sid, 'total': 0, 'wins': 0, 'losses': 0, 'false_positive': 0, 'verdicts': []})
        b['total'] += 1
        if x['verdict'] == 'win':
            b['wins'] += 1
        if x['verdict'] == 'loss':
            b['losses'] += 1
        if x['verdict'] == 'false_positive':
            b['false_positive'] += 1
        b['verdicts'].append(x['verdict'])
    for b in by_signal.values():
        b['win_rate'] = round(b['wins'] / b['total'] * 100, 2) if b['total'] else 0
    return {'status': 'ok', 'total_feedback': total, 'wins': wins, 'losses': losses, 'false_positive': false_positive, 'useful': useful, 'win_rate': round(wins / total * 100, 2) if total else 0, 'useful_rate': round(useful / total * 100, 2) if total else 0, 'by_signal': sorted(by_signal.values(), key=lambda x: x['total'], reverse=True), 'safety': 'paper accuracy only; not a prediction guarantee'}


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    ensure_tables()
    create_default_account_if_missing()
    accts = accounts()
    data = [order_to_out(x) for x in rows('SELECT * FROM paper_orders ORDER BY id DESC LIMIT 200')]
    open_orders = [x for x in data if x['status'] == 'open']
    closed = [x for x in data if x['status'] == 'closed']
    pnl = sum([float(x.get('realized_pnl') or 0) for x in closed])
    acc = accuracy()
    return {'status': 'ok', 'version': '17.3-paper-trading-feedback-loop', 'accounts': accts, 'counts': {'orders': len(data), 'open': len(open_orders), 'closed': len(closed)}, 'realized_pnl': round(pnl, 8), 'accuracy': acc, 'recent_orders': data[:30], 'safety': 'simulation only; no real trading'}
