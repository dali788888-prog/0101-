from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/quant-bot', tags=['Hermes API Quant AI Robot'])


def require_quant_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class ExchangeCreate(BaseModel):
    name: str = Field(..., min_length=2)
    exchange: str = Field(default='paper')
    market_type: str = Field(default='spot')
    api_key_ref: str = ''
    api_secret_ref: str = ''
    passphrase_ref: str = ''
    base_url: str = ''
    mode: str = Field(default='paper', pattern='^(paper|sandbox|live)$')
    enabled: bool = True


class StrategyCreate(BaseModel):
    name: str = Field(..., min_length=2)
    strategy_type: str = Field(default='ma_cross')
    symbol: str = Field(default='BTC/USDT')
    timeframe: str = Field(default='1h')
    params: Dict[str, Any] = Field(default={'fast': 5, 'slow': 20})
    enabled: bool = True


class BotCreate(BaseModel):
    name: str = Field(..., min_length=2)
    exchange_id: int
    strategy_id: int
    symbol: str = Field(default='BTC/USDT')
    mode: str = Field(default='paper', pattern='^(paper|sandbox|live)$')
    quote_budget: str = '1000'
    max_position: str = '0.05'
    max_order_quote: str = '100'
    stop_loss_pct: str = '0.03'
    take_profit_pct: str = '0.06'
    enabled: bool = True


class MarketTick(BaseModel):
    bot_id: int
    price: str
    timestamp: Optional[str] = None


class BacktestRequest(BaseModel):
    strategy_id: int
    prices: List[str] = Field(default=[])
    initial_cash: str = '10000'
    fee_bps: str = '10'


class OrderDecision(BaseModel):
    order_id: int
    operator: str = 'local-operator'
    decision: str = Field(..., pattern='^(approved|rejected)$')
    note: str = ''


class OrderExecution(BaseModel):
    order_id: int
    external_order_id: str = ''
    fill_price: str
    fill_qty: str
    note: str = ''


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_exchanges (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,exchange TEXT NOT NULL,market_type TEXT NOT NULL,api_key_ref TEXT,api_secret_ref TEXT,passphrase_ref TEXT,base_url TEXT,mode TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,last_status TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_strategies (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,strategy_type TEXT NOT NULL,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,params_json TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_bots (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,exchange_id INTEGER NOT NULL,strategy_id INTEGER NOT NULL,symbol TEXT NOT NULL,mode TEXT NOT NULL,quote_budget TEXT NOT NULL,max_position TEXT NOT NULL,max_order_quote TEXT NOT NULL,stop_loss_pct TEXT NOT NULL,take_profit_pct TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,state TEXT NOT NULL DEFAULT 'stopped',position_qty TEXT NOT NULL DEFAULT '0',avg_entry TEXT NOT NULL DEFAULT '0',realized_pnl TEXT NOT NULL DEFAULT '0',last_price TEXT,last_signal TEXT,last_run_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_market_ticks (id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,symbol TEXT NOT NULL,price TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_signals (id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,strategy_id INTEGER NOT NULL,symbol TEXT NOT NULL,signal TEXT NOT NULL,confidence TEXT NOT NULL,reason TEXT NOT NULL,price TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_orders (id INTEGER PRIMARY KEY AUTOINCREMENT,bot_id INTEGER NOT NULL,signal_id INTEGER,symbol TEXT NOT NULL,side TEXT NOT NULL,order_type TEXT NOT NULL,qty TEXT NOT NULL,price TEXT,quote_value TEXT NOT NULL,risk_tier TEXT NOT NULL,approval_state TEXT NOT NULL DEFAULT 'pending',status TEXT NOT NULL DEFAULT 'draft',external_order_id TEXT,fill_price TEXT,fill_qty TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_backtests (id INTEGER PRIMARY KEY AUTOINCREMENT,strategy_id INTEGER NOT NULL,summary_json TEXT NOT NULL,equity_curve_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def dec(value: Any) -> Decimal:
    return Decimal(str(value or '0'))


def moving_average(values: List[Decimal], window: int) -> Optional[Decimal]:
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / Decimal(window)


def calc_rsi(values: List[Decimal], period: int = 14) -> Optional[Decimal]:
    if len(values) <= period:
        return None
    gains = Decimal('0')
    losses = Decimal('0')
    diffs = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    for d in diffs:
        if d > 0:
            gains += d
        else:
            losses += abs(d)
    if losses == 0:
        return Decimal('100')
    rs = gains / losses
    return Decimal('100') - (Decimal('100') / (Decimal('1') + rs))


def generate_strategy_signal(strategy: Dict[str, Any], prices: List[Decimal]) -> tuple[str, Decimal, str]:
    if not prices:
        return 'hold', Decimal('0'), 'no price data'
    params = json.loads(strategy.get('params_json') or '{}')
    stype = strategy.get('strategy_type') or 'ma_cross'
    if stype == 'ma_cross':
        fast = int(params.get('fast', 5))
        slow = int(params.get('slow', 20))
        fast_ma = moving_average(prices, fast)
        slow_ma = moving_average(prices, slow)
        if fast_ma is None or slow_ma is None:
            return 'hold', Decimal('0.2'), 'not enough samples for moving averages'
        if fast_ma > slow_ma:
            return 'buy', Decimal('0.65'), f'fast_ma {fast_ma} > slow_ma {slow_ma}'
        if fast_ma < slow_ma:
            return 'sell', Decimal('0.65'), f'fast_ma {fast_ma} < slow_ma {slow_ma}'
        return 'hold', Decimal('0.35'), 'moving averages are flat'
    if stype == 'rsi_reversion':
        period = int(params.get('period', 14))
        low = dec(params.get('buy_below', 30))
        high = dec(params.get('sell_above', 70))
        rsi = calc_rsi(prices, period)
        if rsi is None:
            return 'hold', Decimal('0.2'), 'not enough samples for RSI'
        if rsi < low:
            return 'buy', Decimal('0.7'), f'RSI {rsi} below {low}'
        if rsi > high:
            return 'sell', Decimal('0.7'), f'RSI {rsi} above {high}'
        return 'hold', Decimal('0.4'), f'RSI neutral {rsi}'
    if stype == 'breakout':
        lookback = int(params.get('lookback', 20))
        if len(prices) <= lookback:
            return 'hold', Decimal('0.2'), 'not enough samples for breakout'
        previous = prices[-lookback - 1:-1]
        if prices[-1] > max(previous):
            return 'buy', Decimal('0.72'), 'price broke above lookback high'
        if prices[-1] < min(previous):
            return 'sell', Decimal('0.72'), 'price broke below lookback low'
        return 'hold', Decimal('0.35'), 'inside breakout channel'
    return 'hold', Decimal('0.1'), f'unknown strategy type {stype}'


@router.get('/status')
def status() -> Dict[str, Any]:
    ensure_tables()
    return {'status': 'ok', 'version': '1.0-paper-first', 'live_trading_default': 'blocked', 'secret_storage': 'references-only', 'modules': ['exchanges', 'strategies', 'bots', 'signals', 'orders', 'backtests']}


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        counts = {
            'exchanges': conn.execute('SELECT COUNT(*) c FROM quant_exchanges').fetchone()['c'],
            'strategies': conn.execute('SELECT COUNT(*) c FROM quant_strategies').fetchone()['c'],
            'bots': conn.execute('SELECT COUNT(*) c FROM quant_bots').fetchone()['c'],
            'running_bots': conn.execute("SELECT COUNT(*) c FROM quant_bots WHERE state='running'").fetchone()['c'],
            'signals': conn.execute('SELECT COUNT(*) c FROM quant_signals').fetchone()['c'],
            'orders': conn.execute('SELECT COUNT(*) c FROM quant_orders').fetchone()['c'],
        }
    return {'status': 'ok', 'counts': counts, 'risk_boundary': 'Paper/sandbox first. Live order execution is not enabled by default.'}


@router.post('/exchanges', dependencies=[Depends(require_quant_key)])
def create_exchange(req: ExchangeCreate) -> Dict[str, Any]:
    ensure_tables()
    if req.mode == 'live':
        raise HTTPException(status_code=400, detail='live mode is blocked in this build; use paper or sandbox')
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_exchanges (name, exchange, market_type, api_key_ref, api_secret_ref, passphrase_ref, base_url, mode, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.exchange, req.market_type, req.api_key_ref, req.api_secret_ref, req.passphrase_ref, req.base_url, req.mode, int(req.enabled), ts, ts))
        exchange_id = int(cur.lastrowid)
    db.audit('quant_create_exchange', 'quant_exchange', str(exchange_id), {'name': req.name, 'exchange': req.exchange, 'mode': req.mode}, 'success', 'medium', 'not_required')
    return row('SELECT id,name,exchange,market_type,base_url,mode,enabled,last_status,last_error,created_at,updated_at FROM quant_exchanges WHERE id=?', (exchange_id,)) or {'id': exchange_id}


@router.get('/exchanges')
def list_exchanges() -> List[Dict[str, Any]]:
    return rows('SELECT id,name,exchange,market_type,base_url,mode,enabled,last_status,last_error,created_at,updated_at FROM quant_exchanges ORDER BY id DESC')


@router.post('/strategies', dependencies=[Depends(require_quant_key)])
def create_strategy(req: StrategyCreate) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_strategies (name, strategy_type, symbol, timeframe, params_json, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.strategy_type, req.symbol, req.timeframe, j(req.params), int(req.enabled), ts, ts))
        strategy_id = int(cur.lastrowid)
    db.audit('quant_create_strategy', 'quant_strategy', str(strategy_id), req.model_dump(), 'success', 'low', 'not_required')
    return row('SELECT * FROM quant_strategies WHERE id=?', (strategy_id,)) or {'id': strategy_id}


@router.get('/strategies')
def list_strategies() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM quant_strategies ORDER BY id DESC')


@router.post('/bots', dependencies=[Depends(require_quant_key)])
def create_bot(req: BotCreate) -> Dict[str, Any]:
    ensure_tables()
    if req.mode == 'live':
        raise HTTPException(status_code=400, detail='live mode is blocked in this build; use paper or sandbox')
    if not row('SELECT id FROM quant_exchanges WHERE id=?', (req.exchange_id,)):
        raise HTTPException(status_code=404, detail='exchange not found')
    if not row('SELECT id FROM quant_strategies WHERE id=?', (req.strategy_id,)):
        raise HTTPException(status_code=404, detail='strategy not found')
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_bots (name, exchange_id, strategy_id, symbol, mode, quote_budget, max_position, max_order_quote, stop_loss_pct, take_profit_pct, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.exchange_id, req.strategy_id, req.symbol, req.mode, req.quote_budget, req.max_position, req.max_order_quote, req.stop_loss_pct, req.take_profit_pct, int(req.enabled), ts, ts))
        bot_id = int(cur.lastrowid)
    db.audit('quant_create_bot', 'quant_bot', str(bot_id), req.model_dump(), 'success', 'medium', 'not_required')
    return row('SELECT * FROM quant_bots WHERE id=?', (bot_id,)) or {'id': bot_id}


@router.get('/bots')
def list_bots() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM quant_bots ORDER BY id DESC')


@router.post('/bots/{bot_id}/start', dependencies=[Depends(require_quant_key)])
def start_bot(bot_id: int) -> Dict[str, Any]:
    bot = row('SELECT * FROM quant_bots WHERE id=?', (bot_id,))
    if not bot:
        raise HTTPException(status_code=404, detail='bot not found')
    with db.connect() as conn:
        conn.execute("UPDATE quant_bots SET state='running', updated_at=? WHERE id=?", (now(), bot_id))
    db.audit('quant_start_bot', 'quant_bot', str(bot_id), {'mode': bot['mode']}, 'success', 'medium', 'not_required')
    return row('SELECT * FROM quant_bots WHERE id=?', (bot_id,)) or {'id': bot_id}


@router.post('/bots/{bot_id}/stop', dependencies=[Depends(require_quant_key)])
def stop_bot(bot_id: int) -> Dict[str, Any]:
    if not row('SELECT id FROM quant_bots WHERE id=?', (bot_id,)):
        raise HTTPException(status_code=404, detail='bot not found')
    with db.connect() as conn:
        conn.execute("UPDATE quant_bots SET state='stopped', updated_at=? WHERE id=?", (now(), bot_id))
    db.audit('quant_stop_bot', 'quant_bot', str(bot_id), {}, 'success', 'low', 'not_required')
    return row('SELECT * FROM quant_bots WHERE id=?', (bot_id,)) or {'id': bot_id}


def create_order_from_signal(bot: Dict[str, Any], signal_id: int, signal: str, price: Decimal) -> Optional[int]:
    if signal not in {'buy', 'sell'}:
        return None
    quote_value = min(dec(bot['max_order_quote']), dec(bot['quote_budget']))
    if quote_value <= 0 or price <= 0:
        return None
    side = signal
    qty = quote_value / price
    if side == 'sell':
        position = dec(bot.get('position_qty'))
        if position <= 0:
            return None
        qty = min(qty, position)
        quote_value = qty * price
    risk = 'medium' if bot['mode'] in {'paper', 'sandbox'} else 'high'
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_orders (bot_id, signal_id, symbol, side, order_type, qty, price, quote_value, risk_tier, approval_state, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (bot['id'], signal_id, bot['symbol'], side, 'market', str(qty), str(price), str(quote_value), risk, 'pending', 'draft', ts, ts))
        return int(cur.lastrowid)


@router.post('/bots/tick', dependencies=[Depends(require_quant_key)])
def bot_tick(req: MarketTick) -> Dict[str, Any]:
    ensure_tables()
    bot = row('SELECT * FROM quant_bots WHERE id=?', (req.bot_id,))
    if not bot:
        raise HTTPException(status_code=404, detail='bot not found')
    if bot['state'] != 'running':
        raise HTTPException(status_code=400, detail='bot is not running')
    strategy = row('SELECT * FROM quant_strategies WHERE id=?', (bot['strategy_id'],))
    if not strategy:
        raise HTTPException(status_code=404, detail='strategy not found')
    ts = req.timestamp or now()
    price = dec(req.price)
    with db.connect() as conn:
        conn.execute('INSERT INTO quant_market_ticks (bot_id, symbol, price, created_at) VALUES (?, ?, ?, ?)', (req.bot_id, bot['symbol'], str(price), ts))
    recent = rows('SELECT price FROM quant_market_ticks WHERE bot_id=? ORDER BY id DESC LIMIT 200', (req.bot_id,))
    prices = [dec(x['price']) for x in reversed(recent)]
    signal, confidence, reason = generate_strategy_signal(strategy, prices)
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_signals (bot_id, strategy_id, symbol, signal, confidence, reason, price, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.bot_id, bot['strategy_id'], bot['symbol'], signal, str(confidence), reason, str(price), ts))
        signal_id = int(cur.lastrowid)
        conn.execute('UPDATE quant_bots SET last_price=?, last_signal=?, last_run_at=?, updated_at=? WHERE id=?', (str(price), signal, ts, now(), req.bot_id))
    order_id = create_order_from_signal(bot, signal_id, signal, price)
    db.audit('quant_bot_tick', 'quant_bot', str(req.bot_id), {'price': str(price), 'signal': signal, 'confidence': str(confidence), 'order_id': order_id}, 'success', 'medium', 'pending' if order_id else 'not_required')
    return {'status': 'success', 'signal_id': signal_id, 'signal': signal, 'confidence': str(confidence), 'reason': reason, 'order_id': order_id}


@router.get('/signals')
def list_signals(limit: int = 100) -> List[Dict[str, Any]]:
    return rows('SELECT * FROM quant_signals ORDER BY id DESC LIMIT ?', (limit,))


@router.get('/orders')
def list_orders(limit: int = 100) -> List[Dict[str, Any]]:
    return rows('SELECT * FROM quant_orders ORDER BY id DESC LIMIT ?', (limit,))


@router.post('/orders/approve', dependencies=[Depends(require_quant_key)])
def approve_order(req: OrderDecision) -> Dict[str, Any]:
    order = row('SELECT * FROM quant_orders WHERE id=?', (req.order_id,))
    if not order:
        raise HTTPException(status_code=404, detail='order not found')
    with db.connect() as conn:
        conn.execute('UPDATE quant_orders SET approval_state=?, updated_at=? WHERE id=?', (req.decision, now(), req.order_id))
    db.audit('quant_order_approval', 'quant_order', str(req.order_id), req.model_dump(), 'success', order['risk_tier'], req.decision)
    return row('SELECT * FROM quant_orders WHERE id=?', (req.order_id,)) or {'id': req.order_id}


@router.post('/orders/mark-filled', dependencies=[Depends(require_quant_key)])
def mark_order_filled(req: OrderExecution) -> Dict[str, Any]:
    order = row('SELECT * FROM quant_orders WHERE id=?', (req.order_id,))
    if not order:
        raise HTTPException(status_code=404, detail='order not found')
    if order['approval_state'] != 'approved':
        raise HTTPException(status_code=400, detail='order must be approved first')
    bot = row('SELECT * FROM quant_bots WHERE id=?', (order['bot_id'],))
    if not bot:
        raise HTTPException(status_code=404, detail='bot not found')
    fill_qty = dec(req.fill_qty)
    fill_price = dec(req.fill_price)
    position = dec(bot['position_qty'])
    avg_entry = dec(bot['avg_entry'])
    realized = dec(bot['realized_pnl'])
    if order['side'] == 'buy':
        new_position = position + fill_qty
        new_avg = ((position * avg_entry) + (fill_qty * fill_price)) / new_position if new_position > 0 else Decimal('0')
    else:
        sell_qty = min(fill_qty, position)
        realized += (fill_price - avg_entry) * sell_qty
        new_position = position - sell_qty
        new_avg = avg_entry if new_position > 0 else Decimal('0')
    with db.connect() as conn:
        conn.execute('UPDATE quant_orders SET status=?, external_order_id=?, fill_price=?, fill_qty=?, updated_at=? WHERE id=?', ('filled', req.external_order_id, str(fill_price), str(fill_qty), now(), req.order_id))
        conn.execute('UPDATE quant_bots SET position_qty=?, avg_entry=?, realized_pnl=?, updated_at=? WHERE id=?', (str(new_position), str(new_avg), str(realized), now(), bot['id']))
    db.audit('quant_order_mark_filled', 'quant_order', str(req.order_id), req.model_dump(), 'success', order['risk_tier'], 'executed')
    return row('SELECT * FROM quant_orders WHERE id=?', (req.order_id,)) or {'id': req.order_id}


@router.post('/backtests/run', dependencies=[Depends(require_quant_key)])
def run_backtest(req: BacktestRequest) -> Dict[str, Any]:
    strategy = row('SELECT * FROM quant_strategies WHERE id=?', (req.strategy_id,))
    if not strategy:
        raise HTTPException(status_code=404, detail='strategy not found')
    prices = [dec(x) for x in req.prices]
    if not prices:
        prices = [Decimal('100') + Decimal(str(math.sin(i / 5) * 5 + i * 0.1)) for i in range(120)]
    cash = dec(req.initial_cash)
    position = Decimal('0')
    fee = dec(req.fee_bps) / Decimal('10000')
    equity_curve = []
    trades = 0
    sample: List[Decimal] = []
    for price in prices:
        sample.append(price)
        signal, _, _ = generate_strategy_signal(strategy, sample)
        if signal == 'buy' and cash > 0:
            spend = cash * Decimal('0.25')
            qty = (spend * (Decimal('1') - fee)) / price
            cash -= spend
            position += qty
            trades += 1
        elif signal == 'sell' and position > 0:
            qty = position * Decimal('0.25')
            cash += qty * price * (Decimal('1') - fee)
            position -= qty
            trades += 1
        equity_curve.append(str(cash + position * price))
    final_equity = dec(equity_curve[-1]) if equity_curve else dec(req.initial_cash)
    pnl = final_equity - dec(req.initial_cash)
    summary = {'initial_cash': req.initial_cash, 'final_equity': str(final_equity), 'pnl': str(pnl), 'trades': trades, 'samples': len(prices)}
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_backtests (strategy_id, summary_json, equity_curve_json, created_at) VALUES (?, ?, ?, ?)', (req.strategy_id, j(summary), j(equity_curve), ts))
        backtest_id = int(cur.lastrowid)
    db.audit('quant_backtest_run', 'quant_strategy', str(req.strategy_id), {'backtest_id': backtest_id}, 'success', 'low', 'not_required')
    return {'status': 'success', 'backtest_id': backtest_id, 'summary': summary, 'equity_curve_tail': equity_curve[-10:]}


@router.get('/backtests')
def list_backtests() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM quant_backtests ORDER BY id DESC LIMIT 50')


@router.post('/bootstrap/defaults', dependencies=[Depends(require_quant_key)])
def bootstrap_defaults() -> Dict[str, Any]:
    ensure_tables()
    created = []
    if not row('SELECT id FROM quant_exchanges WHERE name=?', ('Paper Exchange',)):
        create_exchange(ExchangeCreate(name='Paper Exchange', exchange='paper', market_type='spot', mode='paper'))
        created.append('paper_exchange')
    if not row('SELECT id FROM quant_strategies WHERE name=?', ('BTC MA Cross',)):
        create_strategy(StrategyCreate(name='BTC MA Cross', strategy_type='ma_cross', symbol='BTC/USDT', timeframe='1h', params={'fast': 5, 'slow': 20}))
        created.append('ma_cross_strategy')
    exchange = row('SELECT id FROM quant_exchanges WHERE name=?', ('Paper Exchange',))
    strategy = row('SELECT id FROM quant_strategies WHERE name=?', ('BTC MA Cross',))
    if exchange and strategy and not row('SELECT id FROM quant_bots WHERE name=?', ('BTC Paper Bot',)):
        create_bot(BotCreate(name='BTC Paper Bot', exchange_id=int(exchange['id']), strategy_id=int(strategy['id']), symbol='BTC/USDT', mode='paper'))
        created.append('paper_bot')
    return {'status': 'success', 'created': created}
