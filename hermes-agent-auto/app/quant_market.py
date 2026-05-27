from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.quant_bot import MarketTick, bot_tick, ensure_tables, row, rows, now
from app.quant_ext import fill_approved_paper_orders, approve_all_paper_orders, risk_sweep, PaperFillRequest

router = APIRouter(prefix='/quant-bot', tags=['Hermes Quant Public Market Data'])


def require_quant_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class MarketSourceCreate(BaseModel):
    name: str = Field(..., min_length=2)
    provider: str = Field(default='binance')
    base_url: str = ''
    enabled: bool = True


class PublicPriceRequest(BaseModel):
    provider: str = Field(default='binance')
    symbol: str = Field(default='BTCUSDT')


class PublicTickRequest(BaseModel):
    bot_id: int
    provider: str = Field(default='binance')
    symbol: Optional[str] = None


class AutoPilotCreate(BaseModel):
    name: str = Field(..., min_length=2)
    bot_id: int
    provider: str = Field(default='binance')
    symbol: str = Field(default='BTCUSDT')
    auto_approve_paper: bool = True
    auto_fill_paper: bool = True
    enabled: bool = True


class AutoPilotRunRequest(BaseModel):
    autopilot_id: Optional[int] = None
    bot_id: Optional[int] = None
    provider: str = Field(default='binance')
    symbol: Optional[str] = None
    auto_approve_paper: bool = True
    auto_fill_paper: bool = True


def ensure_market_tables() -> None:
    ensure_tables()
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_market_sources (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,provider TEXT NOT NULL,base_url TEXT,enabled INTEGER NOT NULL DEFAULT 1,last_price TEXT,last_status TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_autopilots (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,bot_id INTEGER NOT NULL,provider TEXT NOT NULL,symbol TEXT NOT NULL,auto_approve_paper INTEGER NOT NULL DEFAULT 1,auto_fill_paper INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,last_status TEXT,last_error TEXT,last_run_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_autopilot_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,autopilot_id INTEGER,bot_id INTEGER NOT NULL,provider TEXT NOT NULL,symbol TEXT NOT NULL,price TEXT,signal TEXT,order_id INTEGER,result_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_market_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_market_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def normalize_symbol_for_provider(provider: str, symbol: str) -> str:
    s = symbol.replace('/', '').replace('-', '').upper()
    if provider.lower() in {'binance', 'bybit', 'okx'}:
        return s
    return s


def fetch_public_price(provider: str, symbol: str) -> Dict[str, Any]:
    p = provider.lower()
    s = normalize_symbol_for_provider(p, symbol)
    if p == 'binance':
        url = f'https://api.binance.com/api/v3/ticker/price?symbol={s}'
        data = requests.get(url, timeout=20).json()
        if 'price' not in data:
            raise RuntimeError(json.dumps(data, ensure_ascii=False))
        return {'provider': p, 'symbol': s, 'price': str(data['price']), 'raw': data}
    if p == 'bybit':
        url = f'https://api.bybit.com/v5/market/tickers?category=spot&symbol={s}'
        data = requests.get(url, timeout=20).json()
        items = ((data.get('result') or {}).get('list') or [])
        if not items:
            raise RuntimeError(json.dumps(data, ensure_ascii=False))
        return {'provider': p, 'symbol': s, 'price': str(items[0].get('lastPrice')), 'raw': data}
    if p == 'okx':
        # OKX instrument format uses BTC-USDT.
        inst = symbol.replace('/', '-').upper()
        url = f'https://www.okx.com/api/v5/market/ticker?instId={inst}'
        data = requests.get(url, timeout=20).json()
        items = data.get('data') or []
        if not items:
            raise RuntimeError(json.dumps(data, ensure_ascii=False))
        return {'provider': p, 'symbol': inst, 'price': str(items[0].get('last')), 'raw': data}
    raise RuntimeError(f'unsupported provider={provider}')


@router.post('/market-sources', dependencies=[Depends(require_quant_key)])
def create_market_source(req: MarketSourceCreate) -> Dict[str, Any]:
    ensure_market_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_market_sources (name, provider, base_url, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', (req.name, req.provider.lower(), req.base_url, int(req.enabled), ts, ts))
        source_id = int(cur.lastrowid)
    db.audit('quant_create_market_source', 'quant_market_source', str(source_id), req.model_dump(), 'success', 'low', 'not_required')
    return qrow('SELECT * FROM quant_market_sources WHERE id=?', (source_id,)) or {'id': source_id}


@router.get('/market-sources')
def list_market_sources() -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_market_sources ORDER BY id DESC')


@router.post('/market/public-price', dependencies=[Depends(require_quant_key)])
def public_price(req: PublicPriceRequest) -> Dict[str, Any]:
    try:
        result = fetch_public_price(req.provider, req.symbol)
        return {'status': 'success', **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/market/tick-public', dependencies=[Depends(require_quant_key)])
def public_tick(req: PublicTickRequest) -> Dict[str, Any]:
    bot = row('SELECT * FROM quant_bots WHERE id=?', (req.bot_id,))
    if not bot:
        raise HTTPException(status_code=404, detail='bot not found')
    symbol = req.symbol or bot.get('symbol') or 'BTC/USDT'
    price_data = public_price(PublicPriceRequest(provider=req.provider, symbol=symbol))
    result = bot_tick(MarketTick(bot_id=req.bot_id, price=price_data['price']))
    return {'status': 'success', 'price': price_data, 'tick': result}


@router.post('/autopilots', dependencies=[Depends(require_quant_key)])
def create_autopilot(req: AutoPilotCreate) -> Dict[str, Any]:
    ensure_market_tables()
    if not row('SELECT id FROM quant_bots WHERE id=?', (req.bot_id,)):
        raise HTTPException(status_code=404, detail='bot not found')
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_autopilots (name, bot_id, provider, symbol, auto_approve_paper, auto_fill_paper, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.bot_id, req.provider.lower(), req.symbol, int(req.auto_approve_paper), int(req.auto_fill_paper), int(req.enabled), ts, ts))
        autopilot_id = int(cur.lastrowid)
    db.audit('quant_create_autopilot', 'quant_autopilot', str(autopilot_id), req.model_dump(), 'success', 'medium', 'not_required')
    return qrow('SELECT * FROM quant_autopilots WHERE id=?', (autopilot_id,)) or {'id': autopilot_id}


@router.get('/autopilots')
def list_autopilots() -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_autopilots ORDER BY id DESC')


def run_autopilot_once(ap: Optional[Dict[str, Any]], req: AutoPilotRunRequest) -> Dict[str, Any]:
    bot_id = int(ap['bot_id'] if ap else req.bot_id or 0)
    if bot_id <= 0:
        raise RuntimeError('bot_id is required')
    bot = row('SELECT * FROM quant_bots WHERE id=?', (bot_id,))
    if not bot:
        raise RuntimeError('bot not found')
    provider = (ap['provider'] if ap else req.provider).lower()
    symbol = ap['symbol'] if ap else (req.symbol or bot.get('symbol') or 'BTC/USDT')
    auto_approve = bool(int(ap['auto_approve_paper'])) if ap else req.auto_approve_paper
    auto_fill = bool(int(ap['auto_fill_paper'])) if ap else req.auto_fill_paper
    tick_result = public_tick(PublicTickRequest(bot_id=bot_id, provider=provider, symbol=symbol))
    sweep = risk_sweep()
    approved = None
    filled = None
    if bot.get('mode') == 'paper' and auto_approve:
        approved = approve_all_paper_orders()
    if bot.get('mode') == 'paper' and auto_fill:
        filled = fill_approved_paper_orders(PaperFillRequest(limit=50))
    signal = (tick_result.get('tick') or {}).get('signal')
    order_id = (tick_result.get('tick') or {}).get('order_id')
    result = {'tick': tick_result, 'risk_sweep': sweep, 'approved': approved, 'filled': filled}
    ts = now()
    with db.connect() as conn:
        conn.execute('INSERT INTO quant_autopilot_runs (autopilot_id, bot_id, provider, symbol, price, signal, order_id, result_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (ap['id'] if ap else req.autopilot_id, bot_id, provider, symbol, str((tick_result.get('price') or {}).get('price')), signal, order_id, json.dumps(result, ensure_ascii=False), ts))
        if ap:
            conn.execute('UPDATE quant_autopilots SET last_status=?, last_error=?, last_run_at=?, updated_at=? WHERE id=?', ('success', None, ts, ts, ap['id']))
    return {'status': 'success', 'bot_id': bot_id, 'provider': provider, 'symbol': symbol, 'signal': signal, 'order_id': order_id, 'result': result}


@router.post('/autopilots/run-once', dependencies=[Depends(require_quant_key)])
def autopilot_run_once(req: AutoPilotRunRequest) -> Dict[str, Any]:
    ensure_market_tables()
    ap = None
    if req.autopilot_id:
        ap = qrow('SELECT * FROM quant_autopilots WHERE id=?', (req.autopilot_id,))
        if not ap:
            raise HTTPException(status_code=404, detail='autopilot not found')
    try:
        result = run_autopilot_once(ap, req)
        db.audit('quant_autopilot_run_once', 'quant_autopilot', str(req.autopilot_id or req.bot_id), {'result': result.get('signal')}, 'success', 'medium', 'not_required')
        return result
    except Exception as exc:
        if ap:
            with db.connect() as conn:
                conn.execute('UPDATE quant_autopilots SET last_status=?, last_error=?, updated_at=? WHERE id=?', ('error', str(exc), now(), ap['id']))
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/autopilots/run-enabled', dependencies=[Depends(require_quant_key)])
def autopilot_run_enabled() -> Dict[str, Any]:
    ensure_market_tables()
    autopilots = qrows('SELECT * FROM quant_autopilots WHERE enabled=1 ORDER BY id')
    results = []
    for ap in autopilots:
        try:
            results.append(run_autopilot_once(ap, AutoPilotRunRequest(autopilot_id=int(ap['id']))))
        except Exception as exc:
            results.append({'autopilot_id': ap['id'], 'status': 'error', 'detail': str(exc)})
    return {'status': 'done', 'count': len(results), 'results': results}


@router.get('/autopilots/runs')
def list_autopilot_runs(limit: int = 100) -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_autopilot_runs ORDER BY id DESC LIMIT ?', (limit,))


@router.post('/market/bootstrap-defaults', dependencies=[Depends(require_quant_key)])
def bootstrap_market_defaults() -> Dict[str, Any]:
    ensure_market_tables()
    created = []
    if not qrow('SELECT id FROM quant_market_sources WHERE name=?', ('Binance Public Spot',)):
        create_market_source(MarketSourceCreate(name='Binance Public Spot', provider='binance', base_url='https://api.binance.com', enabled=True))
        created.append('binance_public_spot')
    bot = row("SELECT id, symbol FROM quant_bots WHERE name='BTC Paper Bot'") or row('SELECT id, symbol FROM quant_bots ORDER BY id LIMIT 1')
    if bot and not qrow('SELECT id FROM quant_autopilots WHERE name=?', ('BTC Public Autopilot',)):
        create_autopilot(AutoPilotCreate(name='BTC Public Autopilot', bot_id=int(bot['id']), provider='binance', symbol='BTCUSDT', auto_approve_paper=True, auto_fill_paper=True, enabled=True))
        created.append('btc_public_autopilot')
    return {'status': 'success', 'created': created}
