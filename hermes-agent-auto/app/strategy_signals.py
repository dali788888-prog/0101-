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


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS strategy_signal_events (id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id TEXT NOT NULL UNIQUE,symbol TEXT NOT NULL,signal_type TEXT NOT NULL,severity TEXT NOT NULL,score REAL NOT NULL,status TEXT NOT NULL DEFAULT 'open',payload_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


def ensure_operator_workspace_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')


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


class SignalWorkspaceSyncRequest(BaseModel):
    period: str = Field(default='daily', pattern='^(daily|weekly|monthly)$')
    limit: int = Field(default=80, ge=10, le=500)
    create_high_notes: bool = True
    create_report: bool = True
    notify_operator: bool = True
    force: bool = False
    operator: str = 'local-operator'


def severity(score: float) -> str:
    if score >= 85:
        return 'high'
    if score >= 60:
        return 'medium'
    return 'low'


def signal_id(symbol: str, signal_type: str) -> str:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    return f'sig-{stamp}-{symbol}-{signal_type}'.replace('/', '-').replace('_', '-')


def current_run_id() -> Optional[str]:
    try:
        with db.connect() as conn:
            row = conn.execute("SELECT run_id FROM agent_runs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                row = conn.execute('SELECT run_id FROM agent_runs ORDER BY id DESC LIMIT 1').fetchone()
        return dict(row)['run_id'] if row else None
    except Exception:
        return None


def store_signal(item: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    sid = signal_id(item['symbol'], item['signal_type'])
    payload = dict(item)
    payload['signal_id'] = sid
    payload['review_history'] = []
    payload['source_run_id'] = current_run_id()
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
            signals.append({'symbol': opp['symbol'], 'signal_type': 'CROSS_EXCHANGE_SPREAD_WATCH', 'severity': severity(score), 'score': round(score, 2), 'summary': f"跨所价差达到 {gap_pct:.4f}%：低价参考 {opp['buy_reference']}，高价参考 {opp['sell_reference']}。", 'action': 'RESEARCH_ONLY_NO_AUTO_TRADE', 'evidence': opp, 'risk_note': '未计入手续费、滑点、资金费率、划转延迟、账户限额、交易所风控和链上拥堵，不可直接视为套利机会。'})

    for symbol, items in by_symbol.items():
        valid_changes = [float(x['change_pct']) for x in items if x.get('change_pct') is not None]
        avg_change = sum(valid_changes) / len(valid_changes) if valid_changes else 0.0
        local_spreads = [float(x['local_spread_pct']) for x in items if x.get('local_spread_pct') is not None]
        min_local_spread = min(local_spreads) if local_spreads else None
        momentum_score = min(100.0, abs(avg_change) / req.momentum_threshold_pct * 100.0) if avg_change else 0.0
        if abs(avg_change) >= req.momentum_threshold_pct:
            signals.append({'symbol': symbol, 'signal_type': 'MOMENTUM_LONG_WATCH' if avg_change > 0 else 'MOMENTUM_SHORT_WATCH', 'severity': severity(momentum_score), 'score': round(momentum_score, 2), 'summary': f"多交易所平均 24h 变化 {avg_change:.4f}%，达到动量观察阈值。", 'action': 'RESEARCH_ONLY_NO_AUTO_TRADE', 'evidence': {'avg_change_pct': round(avg_change, 6), 'venue_count': len(valid_changes), 'min_local_spread_pct': min_local_spread, 'cells': items}, 'risk_note': '24h 动量不等于入场信号；需结合短周期K线、盘口厚度、成交方向、资金费率和风险预算复核。'})
        if min_local_spread is not None and min_local_spread <= req.local_spread_max_pct:
            liq_score = max(1.0, min(100.0, (req.local_spread_max_pct - min_local_spread) / req.local_spread_max_pct * 100.0))
            signals.append({'symbol': symbol, 'signal_type': 'LIQUIDITY_HEALTHY_WATCH', 'severity': severity(liq_score), 'score': round(liq_score, 2), 'summary': f"最小盘口价差 {min_local_spread:.5f}% ，流动性状态较好。", 'action': 'RESEARCH_ONLY_NO_AUTO_TRADE', 'evidence': {'min_local_spread_pct': min_local_spread, 'cells': items}, 'risk_note': '盘口价差只代表快照状态，需继续检查深度厚度、撤单速度和成交冲击成本。'})

    signals.sort(key=lambda x: x['score'], reverse=True)
    persisted: List[Dict[str, Any]] = []
    if req.persist:
        for item in signals[:50]:
            persisted.append(store_signal(item))
    return {'status': 'success', 'mode': 'research_only_no_auto_trade', 'thresholds': req.model_dump(), 'signal_count': len(signals), 'signals': signals, 'persisted_count': len(persisted), 'persisted': persisted, 'matrix': matrix, 'time_utc': now()}


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


def signal_report_content(data: List[Dict[str, Any]], title: str = 'Strategy Signal Report') -> str:
    high = [x for x in data if x['severity'] == 'high']
    medium = [x for x in data if x['severity'] == 'medium']
    open_items = [x for x in data if x['status'] == 'open']
    reviewed = [x for x in data if x['status'] in {'acknowledged', 'reviewed', 'closed'}]
    return '\n'.join([f'# {title}', f'- created_at_utc: {now()}', f'- source_run_id: {current_run_id() or "none"}', f'- total_signals: {len(data)}', f'- open_signals: {len(open_items)}', f'- high: {len(high)}', f'- medium: {len(medium)}', f'- reviewed_or_closed: {len(reviewed)}', '', '## Top Signals', *[f"- [{x['severity']}/{x['status']}] {x['symbol']} {x['signal_type']} score={x['score']} run_id={(x.get('payload') or {}).get('source_run_id') or 'none'}" for x in data[:30]], '', '## High / Medium Signal Notes', *[f"- {x['symbol']} {x['signal_type']}: {(x.get('payload') or {}).get('summary', '')}" for x in data if x['severity'] in {'high', 'medium'}][:30], '', '## Safety', '- Research only. No automatic trading, no account connection, no key access.'])


def workspace_report_exists(day: str) -> Optional[Dict[str, Any]]:
    ensure_operator_workspace_tables()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM operator_period_reports WHERE period='signal_daily' AND title LIKE ? ORDER BY id DESC LIMIT 1", (f'%{day}%',)).fetchone()
    return dict(row) if row else None


def create_operator_note(title: str, content: str, tags: str) -> Dict[str, Any]:
    ensure_operator_workspace_tables()
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (title, content, tags, ts, ts))
        row = conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(row)


def create_operator_report(period: str, title: str, content: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    ensure_operator_workspace_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', (period, title, content, jd(metrics), now()))
        row = conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(row)


def notify_operator(content: str, status: str = 'signal_workspace_sync') -> Dict[str, Any]:
    ensure_operator_workspace_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, status, now()))
        row = conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(row)


def sync_signal_workspace(req: SignalWorkspaceSyncRequest) -> Dict[str, Any]:
    data = read_events(limit=req.limit)
    day = today_utc()
    existing = workspace_report_exists(day)
    high_items = [x for x in data if x['severity'] == 'high']
    medium_items = [x for x in data if x['severity'] == 'medium']
    created_notes: List[Dict[str, Any]] = []
    created_report: Optional[Dict[str, Any]] = None
    notification: Optional[Dict[str, Any]] = None
    if req.create_high_notes:
        for x in high_items[:20]:
            payload = x.get('payload') or {}
            sid = x.get('signal_id')
            title = f'高分策略信号：{x["symbol"]} {x["signal_type"]}'
            content = '\n'.join([f'- signal_id: {sid}', f'- severity: {x["severity"]}', f'- score: {x["score"]}', f'- status: {x["status"]}', f'- source_run_id: {payload.get("source_run_id") or "none"}', '', payload.get('summary', ''), '', payload.get('risk_note', '')])
            with db.connect() as conn:
                exists = conn.execute('SELECT id FROM operator_work_notes WHERE tags=? AND content LIKE ? LIMIT 1', ('strategy_signal_high', f'%{sid}%')).fetchone()
            if not exists or req.force:
                created_notes.append(create_operator_note(title, content, 'strategy_signal_high'))
    metrics = {'day': day, 'period': req.period, 'total_signals': len(data), 'high': len(high_items), 'medium': len(medium_items), 'open': len([x for x in data if x['status'] == 'open']), 'source_run_id': current_run_id(), 'created_notes': len(created_notes)}
    content = signal_report_content(data, title=f'Hermes Signal Daily Workspace Report {day}')
    if req.create_report and (req.force or not existing):
        created_report = create_operator_report('signal_daily', f'Signal Daily Report {day}', content, metrics)
    elif existing:
        created_report = existing
    if req.notify_operator:
        msg = '\n'.join([f'信号日报同步完成：{day}', f'- total_signals: {metrics["total_signals"]}', f'- high: {metrics["high"]}', f'- medium: {metrics["medium"]}', f'- open: {metrics["open"]}', f'- created_high_notes: {len(created_notes)}', f'- report_id: {created_report.get("id") if created_report else "none"}', '', '说明：Research Only，不自动下单。'])
        notification = notify_operator(msg)
    db.audit('strategy_signal_workspace_sync', 'strategy_signals', day, metrics, 'success', 'low', 'not_required')
    return {'status': 'success', 'day': day, 'already_had_report': bool(existing), 'report': created_report, 'created_notes': created_notes, 'notification': notification, 'metrics': metrics, 'safety': 'research_only_no_auto_trade'}


def scheduler_status_payload() -> Dict[str, Any]:
    recent = []
    try:
        recent = db.list_audit_events(limit=200)
    except Exception:
        recent = []
    signal_analysis = [x for x in recent if x.get('event_type') == 'system_signal_analysis_job'][:10]
    workspace_sync = [x for x in recent if x.get('event_type') == 'system_signal_workspace_sync_job'][:10]
    return {'status': 'ok', 'jobs': [{'id': 'system-signal-analysis-30m', 'schedule': 'every 30 minutes UTC', 'action': 'run strategy signal analysis and persist signals'}, {'id': 'system-signal-workspace-sync-daily', 'schedule': '23:55 UTC daily', 'action': 'sync signal daily report and high-signal notes into Operator workspace'}], 'recent_signal_analysis_runs': signal_analysis, 'recent_workspace_sync_runs': workspace_sync, 'time_utc': now()}


@router.get('/config')
def config() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '16.3-scheduled-signal-automation', 'signal_types': ['CROSS_EXCHANGE_SPREAD_WATCH', 'MOMENTUM_LONG_WATCH', 'MOMENTUM_SHORT_WATCH', 'LIQUIDITY_HEALTHY_WATCH'], 'statuses': ['open', 'acknowledged', 'ignored', 'reviewed', 'closed'], 'workspace_sync': ['daily_report', 'high_signal_notes', 'operator_notification', 'run_id_binding'], 'scheduler_jobs': ['system-signal-analysis-30m', 'system-signal-workspace-sync-daily'], 'safety': 'research_only_no_auto_trade'}


@router.post('/analyze', dependencies=[Depends(require_key)])
def analyze_signals(req: SignalAnalyzeRequest) -> Dict[str, Any]:
    try:
        result = analyze(req)
        db.audit('strategy_signal_analyze', 'strategy_signals', 'multi', {'symbols': req.symbols, 'providers': req.providers, 'signals': result['signal_count'], 'source_run_id': current_run_id()}, 'success', 'low', 'not_required')
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
    workspace_status = signal_workspace_status()
    return {'status': 'ok', 'counts': {'total_recent': len(data), 'open': len(open_items), 'high_open': len(high_open), 'medium_open': len(medium_open), 'reviewed_or_closed': len(reviewed)}, 'latest_high': latest_high, 'latest': data[:10], 'workspace': workspace_status, 'scheduler': scheduler_status_payload(), 'safety': 'research_only_no_auto_trade', 'time_utc': now()}


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
        review_item = {'status': req.status, 'operator': req.operator, 'note': req.note, 'time_utc': now(), 'source_run_id': payload.get('source_run_id')}
        history.append(review_item)
        payload['review_history'] = history
        conn.execute('UPDATE strategy_signal_events SET status=?, payload_json=? WHERE signal_id=?', (req.status, jd(payload), signal_id))
    if req.status in {'acknowledged', 'reviewed', 'closed'}:
        title = f'信号复盘记录：{item["symbol"]} {item["signal_type"]}'
        content = '\n'.join([f'- signal_id: {signal_id}', f'- new_status: {req.status}', f'- operator: {req.operator}', f'- source_run_id: {payload.get("source_run_id") or "none"}', f'- note: {req.note}', '', payload.get('summary', ''), '', payload.get('risk_note', '')])
        create_operator_note(title, content, 'strategy_signal_review')
    db.audit('strategy_signal_status_update', 'strategy_signal', signal_id, req.model_dump(), req.status, 'low', 'not_required')
    return {'status': 'success', 'signal_id': signal_id, 'new_status': req.status, 'payload': payload}


@router.get('/report')
def report(limit: int = 50) -> Dict[str, Any]:
    data = read_events(limit=limit)
    return {'status': 'ok', 'content': signal_report_content(data), 'events': data}


@router.get('/workspace-status')
def signal_workspace_status() -> Dict[str, Any]:
    day = today_utc()
    existing = workspace_report_exists(day)
    ensure_operator_workspace_tables()
    with db.connect() as conn:
        notes = [dict(r) for r in conn.execute("SELECT * FROM operator_work_notes WHERE tags IN ('strategy_signal_high','strategy_signal_review') ORDER BY id DESC LIMIT 20").fetchall()]
    return {'day': day, 'daily_report_generated': bool(existing), 'daily_report': existing, 'recent_signal_notes': notes, 'time_utc': now()}


@router.post('/workspace-sync', dependencies=[Depends(require_key)])
def workspace_sync(req: SignalWorkspaceSyncRequest) -> Dict[str, Any]:
    try:
        return sync_signal_workspace(req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get('/scheduler-status')
def scheduler_status() -> Dict[str, Any]:
    return scheduler_status_payload()


@router.post('/scheduler/run-analysis-now', dependencies=[Depends(require_key)])
def scheduler_run_analysis_now() -> Dict[str, Any]:
    result = analyze(SignalAnalyzeRequest(persist=True))
    db.audit('manual_signal_analysis_job', 'strategy_signals', 'manual', {'signals': result.get('signal_count', 0), 'persisted': result.get('persisted_count', 0)}, 'success', 'low', 'not_required')
    return result


@router.post('/scheduler/run-sync-now', dependencies=[Depends(require_key)])
def scheduler_run_sync_now() -> Dict[str, Any]:
    result = sync_signal_workspace(SignalWorkspaceSyncRequest(period='daily', limit=100, create_high_notes=True, create_report=True, notify_operator=True, force=False, operator='manual'))
    db.audit('manual_signal_workspace_sync_job', 'strategy_signals', 'manual', result.get('metrics', {}), 'success', 'low', 'not_required')
    return result
