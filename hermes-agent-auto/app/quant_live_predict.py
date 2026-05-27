from __future__ import annotations

import json
import os
import statistics
from decimal import Decimal
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.quant_bot import ensure_tables as ensure_quant_tables, row, rows, now, dec
from app.quant_market import fetch_public_price

router = APIRouter(prefix='/quant-bot', tags=['Hermes Quant Live Gate and Prediction Center'])

LIVE_ARM_PHRASE = 'ARM LIVE TRADING'


def require_quant_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class LiveProfileCreate(BaseModel):
    name: str = Field(..., min_length=2)
    exchange: str = Field(default='okx')
    market_type: str = Field(default='spot')
    api_key_ref: str = ''
    api_secret_ref: str = ''
    passphrase_ref: str = ''
    executor_url: str = ''
    max_order_quote: str = '50'
    daily_limit_quote: str = '200'
    enabled: bool = True


class LiveArmRequest(BaseModel):
    profile_id: int
    operator: str = Field(default='local-operator')
    phrase: str


class LiveExecuteRequest(BaseModel):
    profile_id: int
    order_id: int
    operator: str = Field(default='local-operator')
    phrase: str = LIVE_ARM_PHRASE
    dry_run: bool = True


class MarketCollectRequest(BaseModel):
    providers: List[str] = Field(default=['binance'])
    symbols: List[str] = Field(default=['BTCUSDT'])


class PredictionRequest(BaseModel):
    provider: str = Field(default='binance')
    symbol: str = Field(default='BTCUSDT')
    window: int = Field(default=50, ge=5, le=500)
    horizon_steps: int = Field(default=3, ge=1, le=50)


class NotificationRule(BaseModel):
    provider: str = Field(default='binance')
    symbol: str = Field(default='BTCUSDT')
    min_predicted_return_pct: str = '0.15'
    min_confidence: str = '0.55'
    direction: str = Field(default='any', pattern='^(any|buy|sell)$')


def ensure_tables() -> None:
    ensure_quant_tables()
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_live_profiles (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,exchange TEXT NOT NULL,market_type TEXT NOT NULL,api_key_ref TEXT,api_secret_ref TEXT,passphrase_ref TEXT,executor_url TEXT,max_order_quote TEXT NOT NULL,daily_limit_quote TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,armed INTEGER NOT NULL DEFAULT 0,armed_by TEXT,armed_at TEXT,last_status TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_live_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,profile_id INTEGER NOT NULL,order_id INTEGER NOT NULL,symbol TEXT NOT NULL,side TEXT NOT NULL,qty TEXT NOT NULL,price TEXT,quote_value TEXT NOT NULL,status TEXT NOT NULL,executor_response TEXT,operator TEXT,risk_note TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_market_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,symbol TEXT NOT NULL,price TEXT NOT NULL,raw_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,symbol TEXT NOT NULL,last_price TEXT NOT NULL,predicted_price TEXT NOT NULL,predicted_return_pct TEXT NOT NULL,direction TEXT NOT NULL,confidence TEXT NOT NULL,window INTEGER NOT NULL,horizon_steps INTEGER NOT NULL,features_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS quant_trade_notifications (id INTEGER PRIMARY KEY AUTOINCREMENT,prediction_id INTEGER NOT NULL,provider TEXT NOT NULL,symbol TEXT NOT NULL,direction TEXT NOT NULL,message TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'new',created_at TEXT NOT NULL,ack_at TEXT)''')


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def live_env_enabled() -> bool:
    return os.getenv('HERMES_ENABLE_LIVE_TRADING', '').lower() in {'1', 'true', 'yes'}


def json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


@router.get('/live/status')
def live_status() -> Dict[str, Any]:
    ensure_tables()
    return {
        'status': 'ok',
        'version': '10.9-live-gate-prediction',
        'live_env_enabled': live_env_enabled(),
        'required_env': 'HERMES_ENABLE_LIVE_TRADING=true',
        'executor_model': 'external-executor-webhook',
        'safety': ['explicit arming phrase required', 'approved order required', 'per-order limit checked', 'secrets stored as references only'],
    }


@router.post('/live/profiles', dependencies=[Depends(require_quant_key)])
def create_live_profile(req: LiveProfileCreate) -> Dict[str, Any]:
    ensure_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_live_profiles (name, exchange, market_type, api_key_ref, api_secret_ref, passphrase_ref, executor_url, max_order_quote, daily_limit_quote, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.exchange, req.market_type, req.api_key_ref, req.api_secret_ref, req.passphrase_ref, req.executor_url, req.max_order_quote, req.daily_limit_quote, int(req.enabled), ts, ts))
        profile_id = int(cur.lastrowid)
    db.audit('quant_live_profile_create', 'quant_live_profile', str(profile_id), {'name': req.name, 'exchange': req.exchange}, 'success', 'critical', 'not_required')
    return qrow('SELECT id,name,exchange,market_type,executor_url,max_order_quote,daily_limit_quote,enabled,armed,armed_by,armed_at,last_status,last_error,created_at,updated_at FROM quant_live_profiles WHERE id=?', (profile_id,)) or {'id': profile_id}


@router.get('/live/profiles')
def list_live_profiles() -> List[Dict[str, Any]]:
    return qrows('SELECT id,name,exchange,market_type,executor_url,max_order_quote,daily_limit_quote,enabled,armed,armed_by,armed_at,last_status,last_error,created_at,updated_at FROM quant_live_profiles ORDER BY id DESC')


@router.post('/live/arm', dependencies=[Depends(require_quant_key)])
def arm_live_profile(req: LiveArmRequest) -> Dict[str, Any]:
    profile = qrow('SELECT * FROM quant_live_profiles WHERE id=?', (req.profile_id,))
    if not profile:
        raise HTTPException(status_code=404, detail='live profile not found')
    if req.phrase != LIVE_ARM_PHRASE:
        raise HTTPException(status_code=400, detail='arming phrase mismatch')
    if not live_env_enabled():
        raise HTTPException(status_code=400, detail='live trading env gate is closed. Set HERMES_ENABLE_LIVE_TRADING=true only after risk approval.')
    ts = now()
    with db.connect() as conn:
        conn.execute('UPDATE quant_live_profiles SET armed=1, armed_by=?, armed_at=?, last_status=?, last_error=?, updated_at=? WHERE id=?', (req.operator, ts, 'armed', None, ts, req.profile_id))
    db.audit('quant_live_arm', 'quant_live_profile', str(req.profile_id), {'operator': req.operator}, 'armed', 'critical', 'approved')
    return {'status': 'armed', 'profile_id': req.profile_id, 'operator': req.operator, 'armed_at': ts}


def check_live_limits(profile: Dict[str, Any], order: Dict[str, Any]) -> None:
    quote = dec(order.get('quote_value'))
    if quote <= 0:
        raise HTTPException(status_code=400, detail='order quote value is invalid')
    if quote > dec(profile.get('max_order_quote')):
        raise HTTPException(status_code=400, detail='order exceeds live profile max_order_quote')


@router.post('/live/execute-order', dependencies=[Depends(require_quant_key)])
def execute_live_order(req: LiveExecuteRequest) -> Dict[str, Any]:
    ensure_tables()
    profile = qrow('SELECT * FROM quant_live_profiles WHERE id=?', (req.profile_id,))
    order = row('SELECT * FROM quant_orders WHERE id=?', (req.order_id,))
    if not profile:
        raise HTTPException(status_code=404, detail='live profile not found')
    if not order:
        raise HTTPException(status_code=404, detail='order not found')
    if order.get('approval_state') != 'approved':
        raise HTTPException(status_code=400, detail='order must be approved before live execution')
    if req.phrase != LIVE_ARM_PHRASE:
        raise HTTPException(status_code=400, detail='execution phrase mismatch')
    check_live_limits(profile, order)
    ts = now()
    payload = {
        'exchange': profile['exchange'],
        'market_type': profile['market_type'],
        'symbol': order['symbol'],
        'side': order['side'],
        'order_type': order['order_type'],
        'qty': order['qty'],
        'price': order.get('price'),
        'quote_value': order['quote_value'],
        'api_key_ref': profile.get('api_key_ref'),
        'api_secret_ref': profile.get('api_secret_ref'),
        'passphrase_ref': profile.get('passphrase_ref'),
    }
    status = 'staged_external_executor_required'
    executor_response = {'dry_run': req.dry_run, 'live_env_enabled': live_env_enabled(), 'payload': payload}
    if not req.dry_run:
        if not live_env_enabled():
            raise HTTPException(status_code=400, detail='live trading env gate is closed')
        if not int(profile.get('armed') or 0):
            raise HTTPException(status_code=400, detail='live profile is not armed')
        if not profile.get('executor_url'):
            raise HTTPException(status_code=400, detail='executor_url is required for live execution')
        response = requests.post(profile['executor_url'], json=payload, timeout=25)
        executor_response = {'status_code': response.status_code, 'text': response.text[:2000]}
        response.raise_for_status()
        status = 'sent_to_executor'
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_live_tickets (profile_id, order_id, symbol, side, qty, price, quote_value, status, executor_response, operator, risk_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.profile_id, req.order_id, order['symbol'], order['side'], order['qty'], order.get('price'), order['quote_value'], status, json_dump(executor_response), req.operator, 'live execution is high risk', ts, ts))
        ticket_id = int(cur.lastrowid)
    db.audit('quant_live_execute_order', 'quant_live_ticket', str(ticket_id), {'order_id': req.order_id, 'dry_run': req.dry_run, 'status': status}, status, 'critical', 'approved')
    return {'status': status, 'ticket_id': ticket_id, 'executor_response': executor_response}


@router.get('/live/tickets')
def list_live_tickets(limit: int = 100) -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_live_tickets ORDER BY id DESC LIMIT ?', (limit,))


@router.post('/market/collect', dependencies=[Depends(require_quant_key)])
def collect_market(req: MarketCollectRequest) -> Dict[str, Any]:
    ensure_tables()
    results = []
    for provider in req.providers:
        for symbol in req.symbols:
            try:
                data = fetch_public_price(provider, symbol)
                with db.connect() as conn:
                    conn.execute('INSERT INTO quant_market_snapshots (provider, symbol, price, raw_json, created_at) VALUES (?, ?, ?, ?, ?)', (data['provider'], data['symbol'], data['price'], json_dump(data['raw']), now()))
                results.append({'provider': data['provider'], 'symbol': data['symbol'], 'price': data['price'], 'status': 'success'})
            except Exception as exc:
                results.append({'provider': provider, 'symbol': symbol, 'status': 'error', 'detail': str(exc)})
    db.audit('quant_market_collect', 'quant_market_snapshot', None, {'providers': req.providers, 'symbols': req.symbols}, 'done', 'low', 'not_required')
    return {'status': 'done', 'results': results}


@router.get('/market/snapshots')
def list_snapshots(provider: str = 'binance', symbol: str = 'BTCUSDT', limit: int = 100) -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_market_snapshots WHERE provider=? AND symbol=? ORDER BY id DESC LIMIT ?', (provider, symbol, limit))


def linear_slope(values: List[Decimal]) -> Decimal:
    n = len(values)
    if n < 2:
        return Decimal('0')
    xs = [Decimal(i) for i in range(n)]
    xbar = sum(xs) / Decimal(n)
    ybar = sum(values) / Decimal(n)
    num = sum((xs[i] - xbar) * (values[i] - ybar) for i in range(n))
    den = sum((xs[i] - xbar) * (xs[i] - xbar) for i in range(n))
    return num / den if den else Decimal('0')


@router.post('/market/analyze', dependencies=[Depends(require_quant_key)])
def analyze_market(req: PredictionRequest) -> Dict[str, Any]:
    ensure_tables()
    snaps = list(reversed(qrows('SELECT * FROM quant_market_snapshots WHERE provider=? AND symbol=? ORDER BY id DESC LIMIT ?', (req.provider, req.symbol, req.window))))
    if len(snaps) < 5:
        # collect one fresh point if history is too small, then try again.
        collect_market(MarketCollectRequest(providers=[req.provider], symbols=[req.symbol]))
        snaps = list(reversed(qrows('SELECT * FROM quant_market_snapshots WHERE provider=? AND symbol=? ORDER BY id DESC LIMIT ?', (req.provider, req.symbol, req.window))))
    if len(snaps) < 2:
        raise HTTPException(status_code=400, detail='not enough market snapshots; collect more data first')
    prices = [dec(x['price']) for x in snaps]
    last = prices[-1]
    slope = linear_slope(prices)
    predicted = last + slope * Decimal(req.horizon_steps)
    ret_pct = ((predicted - last) / last) * Decimal('100') if last > 0 else Decimal('0')
    returns = [float((prices[i] - prices[i - 1]) / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
    volatility = Decimal(str(statistics.pstdev(returns))) if len(returns) > 1 else Decimal('0')
    abs_ret = abs(ret_pct)
    confidence = min(Decimal('0.95'), Decimal('0.35') + abs_ret / Decimal('2'))
    direction = 'buy' if ret_pct > 0 else 'sell' if ret_pct < 0 else 'hold'
    features = {'samples': len(prices), 'min': str(min(prices)), 'max': str(max(prices)), 'avg': str(sum(prices) / Decimal(len(prices))), 'slope_per_step': str(slope), 'volatility': str(volatility)}
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_predictions (provider, symbol, last_price, predicted_price, predicted_return_pct, direction, confidence, window, horizon_steps, features_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.provider, req.symbol, str(last), str(predicted), str(ret_pct), direction, str(confidence), req.window, req.horizon_steps, json_dump(features), now()))
        prediction_id = int(cur.lastrowid)
    db.audit('quant_market_analyze', 'quant_prediction', str(prediction_id), {'symbol': req.symbol, 'direction': direction, 'return_pct': str(ret_pct)}, 'success', 'medium', 'not_required')
    return {'status': 'success', 'prediction_id': prediction_id, 'provider': req.provider, 'symbol': req.symbol, 'last_price': str(last), 'predicted_price': str(predicted), 'predicted_return_pct': str(ret_pct), 'direction': direction, 'confidence': str(confidence), 'features': features}


@router.get('/market/predictions')
def list_predictions(provider: str = 'binance', symbol: str = 'BTCUSDT', limit: int = 100) -> List[Dict[str, Any]]:
    return qrows('SELECT * FROM quant_predictions WHERE provider=? AND symbol=? ORDER BY id DESC LIMIT ?', (provider, symbol, limit))


@router.post('/market/notify-trade', dependencies=[Depends(require_quant_key)])
def notify_trade(req: NotificationRule) -> Dict[str, Any]:
    pred = qrow('SELECT * FROM quant_predictions WHERE provider=? AND symbol=? ORDER BY id DESC LIMIT 1', (req.provider, req.symbol))
    if not pred:
        result = analyze_market(PredictionRequest(provider=req.provider, symbol=req.symbol))
        pred = qrow('SELECT * FROM quant_predictions WHERE id=?', (result['prediction_id'],))
    if not pred:
        raise HTTPException(status_code=400, detail='prediction failed')
    pred_ret = dec(pred['predicted_return_pct'])
    confidence = dec(pred['confidence'])
    min_ret = dec(req.min_predicted_return_pct)
    min_conf = dec(req.min_confidence)
    direction_ok = req.direction == 'any' or pred['direction'] == req.direction
    triggered = abs(pred_ret) >= min_ret and confidence >= min_conf and direction_ok and pred['direction'] != 'hold'
    if not triggered:
        return {'status': 'no_signal', 'prediction': pred, 'rule': req.model_dump()}
    message = f"可交易通知：{pred['provider']} {pred['symbol']} direction={pred['direction']} predicted_return={pred['predicted_return_pct']}% confidence={pred['confidence']} predicted_price={pred['predicted_price']}"
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO quant_trade_notifications (prediction_id, provider, symbol, direction, message, severity, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (pred['id'], pred['provider'], pred['symbol'], pred['direction'], message, 'high', 'new', now()))
        notification_id = int(cur.lastrowid)
    db.audit('quant_trade_notification', 'quant_prediction', str(pred['id']), {'notification_id': notification_id, 'message': message}, 'created', 'high', 'not_required')
    return {'status': 'triggered', 'notification_id': notification_id, 'message': message, 'prediction': pred}


@router.get('/market/trade-notifications')
def list_trade_notifications(status: str = 'new', limit: int = 50) -> List[Dict[str, Any]]:
    if status == 'all':
        return qrows('SELECT * FROM quant_trade_notifications ORDER BY id DESC LIMIT ?', (limit,))
    return qrows('SELECT * FROM quant_trade_notifications WHERE status=? ORDER BY id DESC LIMIT ?', (status, limit))


@router.post('/market/trade-notifications/{notification_id}/ack', dependencies=[Depends(require_quant_key)])
def ack_trade_notification(notification_id: int) -> Dict[str, Any]:
    if not qrow('SELECT id FROM quant_trade_notifications WHERE id=?', (notification_id,)):
        raise HTTPException(status_code=404, detail='notification not found')
    with db.connect() as conn:
        conn.execute('UPDATE quant_trade_notifications SET status=?, ack_at=? WHERE id=?', ('acked', now(), notification_id))
    return {'status': 'acked', 'notification_id': notification_id}


@router.post('/market/predict-cycle', dependencies=[Depends(require_quant_key)])
def predict_cycle(req: NotificationRule) -> Dict[str, Any]:
    collected = collect_market(MarketCollectRequest(providers=[req.provider], symbols=[req.symbol]))
    prediction = analyze_market(PredictionRequest(provider=req.provider, symbol=req.symbol))
    notification = notify_trade(req)
    return {'status': 'done', 'collected': collected, 'prediction': prediction, 'notification': notification}
