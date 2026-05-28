from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.exchange_market import MatrixRequest, build_matrix

router = APIRouter(prefix='/strategy-signals', tags=['Strategy Research Signal Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS strategy_signal_events (id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL UNIQUE,symbol TEXT NOT NULL,signal_type TEXT NOT NULL,severity TEXT NOT NULL,score REAL NOT NULL,status TEXT NOT NULL DEFAULT 'open',payload_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


class SignalAnalyzeRequest(BaseModel):
    providers: List[str] = Field(default_factory=lambda: ['binance', 'okx', 'bybit', 'gate'])
    symbols: List[str] = Field(default_factory=lambda: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'])
    spread_threshold_pct: float = Field(default=0.20, ge=0.01, le=10)
    momentum_threshold_pct: float = Field(default=1.50, ge=0.1, le=50)
    local_spread_max_pct: float = Field(default=0.15, ge=0.01, le=10)
    persist: bool = True


class SignalStatusUpdate(BaseModel):
    status: str = Field(pattern='^(open|acknowledged|ignored|reviewed|closed)$')
    operator: str = 'local-operator'
    note: str = ''


def severity(score: float) -> str:
    if score >= 85:
        return 'high'
    if score >= 60:
        return 'medium'
    return 'low'


def signal_id(symbol: str, signal_type: str) -> str:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return f'sig-{stamp}-{symbol}-{signal_type}'.replace('/', '-').replace('_', '-')


def store_signal(item: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    sid = signal_id(item['symbol'], item['signal_type'])
    payload = dict(item)
    payload['signal_id'] = sid
    payload['review_history'] = []
    ts = now()
    with db.connect() as conn:
        conn.execute('INSERT INTO strategy_signal_events (signal_id,symbol,signal_type,severity,score,status,payload_json,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (sid, item['symbol'], item['signal_type'], item['severity'], float(item['score']), 'open', jd(payload), ts))
    return payload


def analyze(req: SignalAnalyzeRequest) -> Dict[str, Any]:
    matrix = build_matrix(MatrixRequest(providers=req.providers, symbols=req.symbols, include_depth=True, depth_limit=5))
    cells = matrix.get('cells') or []
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for c in cells:
        by_symbol.setdefault(c['symbol'], []).append(c)

    signals: List[Dict[str, Any]] = []
    for opp in matrix.get('opportunities') or []:
        gap_pct = float(opp.get('gap_pct') or 0)
        score = min(100.0, gap_pct / req.spread_threshold_pct * 100.0)
        if gap_pct >= req.spread_threshold_pct:
            signals.append({
                'symbol': opp['symbol'],
                'signal_type': 'CROSS_EXCHANGE_SPREAD_WATCH',
                'severity': severity(score),
                'score': round(score, 2),
                'summary': f"跨所价差达到 {gap_pct:.4f}%：低价参考 {opp['buy_reference']}，高价参考 {opp['sell_reference']}。",
                'action': 'RESEARCH_ONLY_NO_AUTO_TRADE',
                'evidence': opp,
                'risk_note': '未计入手续费、滑点、资金费率、划转延迟、账户限额、交易所风控和链上拥堵，不可直接视为套利机会。',
            })

    for symbol, items in by_symbol.items():
        valid_changes = [float(x['change_pct']) for x in items if x.get('change_pct') is not None]
        avg_change = sum(valid_changes) / len(valid_changes) if valid_changes else 0.0
        local_spreads = [float(x['local_spread_pct']) for x in items if x.get('local_spread_pct') is not None]
        min_local_spread = min(local_spreads) if local_spreads else None
        momentum_score = min(100.0, abs(avg_change) / req.momentum_threshold_pct * 100.0) if avg_change else 0.0
        if abs(avg_change) >= req.momentum_threshold_pct:
            signals.append({
                'symbol': symbol,
                'signal_type': 'MOMENTUM_LONG_WATCH' if avg_change > 0 else 'MOMENTUM_SHORT_WATCH',
                'severity': severity(momentum_score),
                'score': round(momentum_score, 2),
                'summary': f"多交易所平均 24h 变化 {avg_change:.4f}%，达到动量观察阈值。",
                'action': 'RESEARCH_ONLY_NO_AUTO_TRADE',
                'evidence': {'avg_change_pct': round(avg_change, 6), 'venue_count': len(valid_changes), 'min_local_spread_pct': min_local_spread, 'cells': items},
                'risk_note': '24h 动量不等于入场信号；需结合短周期K线、盘口厚度、成交方向、资金费率和风险预算复核。',
            })
        if min_local_spread is not None and min_local_spread <= req.local_spread_max_pct:
            liq_score = max(1.0, min(100.0, (req.local_spread_max_pct - min_local_spread) / req.local_spread_max_pct * 100.0))
            signals.append({
                'symbol': symbol,
                'signal_type': 'LIQUIDITY_HEALTHY_WATCH',
                'severity': severity(liq_score),
                'score': round(liq_score, 2),
                'summary': f"最小盘口价差 {min_local_spread:.5f}% ，流动性状态较好。",
                'action': 'RESEARCH_ONLY_NO_AUTO_TRADE',
                'evidence': {'min_local_spread_pct': min_local_spread, 'cells': items},
                'risk_note': '盘口价差只代表快照状态，需继续检查深度厚度、撤单速度和成交冲击成本。',
            })

    signals.sort(key=lambda x: x['score'], reverse=True)
    persisted: List[Dict[str, Any]] = []
    if req.persist:
        for item in signals[:50]:
            persisted.append(store_signal(item))
    return {
        'status': 'success',
        'mode': 'research_only_no_auto_trade',
        'thresholds': req.model_dump(),
        'signal_count': len(signals),
        'signals': signals,
        'persisted_count': len(persisted),
        'persisted': persisted,
        'matrix': matrix,
        'time_utc': now(),
    }


def read_events(limit: int = 100, status: Optional[str] = None, severity_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_tables()
    clauses = ['1=1']
    args: List[Any] = []
    if status:
        clauses.append('status=?')
        args.append(status)
    if severity_filter:
        clauses.append('severity=?')
        args.append(severity_filter)
    args.append(limit)
    with db.connect() as conn:
        data = [dict(r) for r in conn.execute(f"SELECT * FROM strategy_signal_events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", tuple(args)).fetchall()]
    for r in data:
        r['payload'] = json.loads(r.pop('payload_json') or '{}')
    return data


@router.get('/config')
def config() -> Dict[str, Any]:
    return {
        'status': 'ok',
        'version': '16.1-signal-alert-command-center',
        'signal_types': ['CROSS_EXCHANGE_SPREAD_WATCH', 'MOMENTUM_LONG_WATCH', 'MOMENTUM_SHORT_WATCH', 'LIQUIDITY_HEALTHY_WATCH'],
        'statuses': ['open', 'acknowledged', 'ignored', 'reviewed', 'closed'],
        'safety': 'research_only_no_auto_trade',
    }


@router.post('/analyze', dependencies=[Depends(require_key)])
def analyze_signals(req: SignalAnalyzeRequest) -> Dict[str, Any]:
    try:
        result = analyze(req)
        db.audit('strategy_signal_analyze', 'strategy_signals', 'multi', {'symbols': req.symbols, 'providers': req.providers, 'signals': result['signal_count']}, 'success', 'low', 'not_required')
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/events')
def events(limit: int = 100, status: Optional[str] = None, severity: Optional[str] = None) -> Dict[str, Any]:
    return {'status': 'ok', 'events': read_events(limit=limit, status=status, severity_filter=severity)}


@router.get('/summary')
def summary(limit: int = 200) -> Dict[str, Any]:
    data = read_events(limit=limit)
    open_items = [x for x in data if x['status'] == 'open']
    high_open = [x for x in open_items if x['severity'] == 'high']
    medium_open = [x for x in open_items if x['severity'] == 'medium']
    reviewed = [x for x in data if x['status'] in {'acknowledged', 'reviewed', 'closed'}]
    latest_high = high_open[0] if high_open else None
    return {
        'status': 'ok',
        'counts': {
            'total_recent': len(data),
            'open': len(open_items),
            'high_open': len(high_open),
            'medium_open': len(medium_open),
            'reviewed_or_closed': len(reviewed),
        },
        'latest_high': latest_high,
        'latest': data[:10],
        'safety': 'research_only_no_auto_trade',
        'time_utc': now(),
    }


@router.post('/events/{signal_id}/status', dependencies=[Depends(require_key)])
def update_signal_status(signal_id: str, req: SignalStatusUpdate) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM strategy_signal_events WHERE signal_id=?', (signal_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='signal not found')
        item = dict(row)
        payload = json.loads(item.get('payload_json') or '{}')
        history = payload.get('review_history') or []
        history.append({'status': req.status, 'operator': req.operator, 'note': req.note, 'time_utc': now()})
        payload['review_history'] = history
        conn.execute('UPDATE strategy_signal_events SET status=?, payload_json=? WHERE signal_id=?', (req.status, jd(payload), signal_id))
    db.audit('strategy_signal_status_update', 'strategy_signal', signal_id, req.model_dump(), req.status, 'low', 'not_required')
    return {'status': 'success', 'signal_id': signal_id, 'new_status': req.status, 'payload': payload}


@router.get('/report')
def report(limit: int = 50) -> Dict[str, Any]:
    data = read_events(limit=limit)
    high = [x for x in data if x['severity'] == 'high']
    medium = [x for x in data if x['severity'] == 'medium']
    open_items = [x for x in data if x['status'] == 'open']
    content = '\n'.join([
        '# Strategy Signal Report',
        f'- created_at_utc: {now()}',
        f'- total_signals: {len(data)}',
        f'- open_signals: {len(open_items)}',
        f'- high: {len(high)}',
        f'- medium: {len(medium)}',
        '',
        '## Top Signals',
        *[f"- [{x['severity']}/{x['status']}] {x['symbol']} {x['signal_type']} score={x['score']}" for x in data[:20]],
        '',
        '## Safety',
        '- Research only. No automatic trading, no account connection, no key access.',
    ])
    return {'status': 'ok', 'content': content, 'events': data}
