from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.quant_bot import ensure_tables, rows, row, now, dec, StrategyCreate, create_strategy, MarketTick, bot_tick, OrderExecution, mark_order_filled

router = APIRouter(prefix='/quant-bot', tags=['Hermes Quant AI Robot Extensions'])


def require_quant_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class BatchTicks(BaseModel):
    bot_id: int
    prices: List[str] = Field(..., min_length=1, max_length=500)


class PresetRequest(BaseModel):
    symbol: str = 'BTC/USDT'


class PaperFillRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)


def insert_order(bot: Dict[str, Any], side: str, price: Decimal, reason: str) -> int:
    quote_value = min(dec(bot.get('max_order_quote')), dec(bot.get('quote_budget')))
    if quote_value <= 0 or price <= 0:
        raise ValueError('invalid quote value or price')
    qty = quote_value / price
    if side == 'sell':
        position = dec(bot.get('position_qty'))
        if position <= 0:
            raise ValueError('no position to sell')
        qty = min(qty, position)
        quote_value = qty * price
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_orders (bot_id, signal_id, symbol, side, order_type, qty, price, quote_value, risk_tier, approval_state, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (bot['id'], None, bot['symbol'], side, 'market', str(qty), str(price), str(quote_value), 'medium', 'pending', 'draft', ts, ts))
        order_id = int(cur.lastrowid)
    db.audit('quant_ext_create_risk_order', 'quant_bot', str(bot['id']), {'order_id': order_id, 'side': side, 'reason': reason}, 'success', 'medium', 'pending')
    return order_id


@router.post('/market/ticks/batch', dependencies=[Depends(require_quant_key)])
def batch_ticks(req: BatchTicks) -> Dict[str, Any]:
    ensure_tables()
    results = []
    for price in req.prices:
        try:
            results.append(bot_tick(MarketTick(bot_id=req.bot_id, price=price)))
        except HTTPException as exc:
            results.append({'status': 'error', 'detail': exc.detail, 'price': price})
    return {'status': 'done', 'count': len(results), 'results': results}


@router.post('/strategies/presets', dependencies=[Depends(require_quant_key)])
def create_strategy_presets(req: PresetRequest) -> Dict[str, Any]:
    created = []
    presets = [
        StrategyCreate(name=f'{req.symbol} MA Cross 5/20', strategy_type='ma_cross', symbol=req.symbol, timeframe='1h', params={'fast': 5, 'slow': 20}),
        StrategyCreate(name=f'{req.symbol} RSI Reversion', strategy_type='rsi_reversion', symbol=req.symbol, timeframe='1h', params={'period': 14, 'buy_below': 30, 'sell_above': 70}),
        StrategyCreate(name=f'{req.symbol} Breakout 20', strategy_type='breakout', symbol=req.symbol, timeframe='1h', params={'lookback': 20}),
    ]
    for preset in presets:
        if not row('SELECT id FROM quant_strategies WHERE name=?', (preset.name,)):
            created.append(create_strategy(preset))
    return {'status': 'success', 'created': created}


@router.post('/risk/sweep', dependencies=[Depends(require_quant_key)])
def risk_sweep() -> Dict[str, Any]:
    ensure_tables()
    bots = rows("SELECT * FROM quant_bots WHERE enabled=1 AND state='running'")
    created_orders = []
    for bot in bots:
        position = dec(bot.get('position_qty'))
        avg_entry = dec(bot.get('avg_entry'))
        last_price = dec(bot.get('last_price'))
        if position <= 0 or avg_entry <= 0 or last_price <= 0:
            continue
        stop = dec(bot.get('stop_loss_pct'))
        take = dec(bot.get('take_profit_pct'))
        change = (last_price - avg_entry) / avg_entry
        if stop > 0 and change <= -stop:
            try:
                created_orders.append({'bot_id': bot['id'], 'order_id': insert_order(bot, 'sell', last_price, f'stop_loss {change}')})
            except Exception as exc:
                created_orders.append({'bot_id': bot['id'], 'error': str(exc)})
        elif take > 0 and change >= take:
            try:
                created_orders.append({'bot_id': bot['id'], 'order_id': insert_order(bot, 'sell', last_price, f'take_profit {change}')})
            except Exception as exc:
                created_orders.append({'bot_id': bot['id'], 'error': str(exc)})
    return {'status': 'success', 'checked_bots': len(bots), 'created_orders': created_orders}


@router.get('/performance')
def performance() -> Dict[str, Any]:
    ensure_tables()
    bots = rows('SELECT * FROM quant_bots ORDER BY id DESC')
    orders = rows('SELECT * FROM quant_orders ORDER BY id DESC LIMIT 500')
    total_realized = sum([dec(b.get('realized_pnl')) for b in bots], Decimal('0'))
    filled = [o for o in orders if o.get('status') == 'filled']
    pending = [o for o in orders if o.get('approval_state') == 'pending']
    by_bot = []
    for b in bots:
        last_price = dec(b.get('last_price'))
        position = dec(b.get('position_qty'))
        avg_entry = dec(b.get('avg_entry'))
        unrealized = (last_price - avg_entry) * position if last_price > 0 and avg_entry > 0 else Decimal('0')
        by_bot.append({'bot_id': b['id'], 'name': b['name'], 'state': b['state'], 'position_qty': b['position_qty'], 'avg_entry': b['avg_entry'], 'last_price': b.get('last_price'), 'realized_pnl': b['realized_pnl'], 'unrealized_pnl': str(unrealized)})
    return {'status': 'ok', 'total_realized_pnl': str(total_realized), 'filled_orders': len(filled), 'pending_orders': len(pending), 'bots': by_bot}


@router.post('/orders/approve-all-paper', dependencies=[Depends(require_quant_key)])
def approve_all_paper_orders() -> Dict[str, Any]:
    ensure_tables()
    pending = rows("SELECT o.* FROM quant_orders o JOIN quant_bots b ON o.bot_id=b.id WHERE o.approval_state='pending' AND b.mode='paper' ORDER BY o.id LIMIT 200")
    ts = now()
    with db.connect() as conn:
        for order in pending:
            conn.execute('UPDATE quant_orders SET approval_state=?, updated_at=? WHERE id=?', ('approved', ts, order['id']))
    db.audit('quant_approve_all_paper_orders', 'quant_order', None, {'count': len(pending)}, 'success', 'medium', 'approved')
    return {'status': 'success', 'approved': len(pending)}


@router.post('/orders/fill-approved-paper', dependencies=[Depends(require_quant_key)])
def fill_approved_paper_orders(req: PaperFillRequest) -> Dict[str, Any]:
    approved = rows("SELECT o.* FROM quant_orders o JOIN quant_bots b ON o.bot_id=b.id WHERE o.approval_state='approved' AND o.status='draft' AND b.mode='paper' ORDER BY o.id LIMIT ?", (req.limit,))
    results = []
    for order in approved:
        bot = row('SELECT * FROM quant_bots WHERE id=?', (order['bot_id'],))
        price = order.get('price') or (bot or {}).get('last_price') or '0'
        if dec(price) <= 0:
            results.append({'order_id': order['id'], 'status': 'error', 'detail': 'missing fill price'})
            continue
        try:
            results.append(mark_order_filled(OrderExecution(order_id=int(order['id']), external_order_id=f'paper-{order["id"]}', fill_price=str(price), fill_qty=order['qty'], note='auto paper fill')))
        except HTTPException as exc:
            results.append({'order_id': order['id'], 'status': 'error', 'detail': exc.detail})
    return {'status': 'done', 'filled': len([r for r in results if isinstance(r, dict) and r.get('status') == 'filled']), 'results': results}


@router.get('/backtests/{backtest_id}/equity')
def backtest_equity(backtest_id: int) -> Dict[str, Any]:
    bt = row('SELECT * FROM quant_backtests WHERE id=?', (backtest_id,))
    if not bt:
        raise HTTPException(status_code=404, detail='backtest not found')
    return {'backtest_id': backtest_id, 'summary': json.loads(bt['summary_json']), 'equity_curve': json.loads(bt['equity_curve_json'])}
