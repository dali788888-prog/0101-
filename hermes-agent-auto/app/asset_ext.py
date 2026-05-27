from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/asset-os', tags=['Hermes AssetOps OS v10 extensions'])


def require_asset_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class AddressBookCreate(BaseModel):
    label: str = Field(..., min_length=2)
    chain: str = Field(default='global')
    address: str = Field(..., min_length=3)
    category: str = Field(default='counterparty')
    risk_tier: str = Field(default='medium')
    tags: List[str] = Field(default=[])
    note: str = ''
    enabled: bool = True


class AlertChannelCreate(BaseModel):
    name: str = Field(..., min_length=2)
    channel_type: str = Field(default='webhook')
    target: str = Field(..., min_length=3)
    secret_ref: str = ''
    enabled: bool = True


class AlertRuleCreate(BaseModel):
    name: str = Field(..., min_length=2)
    rule_type: str = Field(default='large_transfer')
    wallet_id: Optional[int] = None
    chain: str = Field(default='global')
    token_symbol: str = Field(default='ANY')
    direction: str = Field(default='any')
    threshold_value: str = Field(default='0')
    severity: str = Field(default='medium')
    enabled: bool = True


class ReportRequest(BaseModel):
    title: str = Field(default='Hermes AssetOps Daily Report')
    include_events: int = Field(default=50, ge=1, le=500)
    include_alerts: int = Field(default=50, ge=1, le=500)


def ensure_ext_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_address_book (id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT NOT NULL,chain TEXT NOT NULL,address TEXT NOT NULL,category TEXT NOT NULL,risk_tier TEXT NOT NULL,tags_json TEXT NOT NULL,note TEXT,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_alert_channels (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,channel_type TEXT NOT NULL,target TEXT NOT NULL,secret_ref TEXT,enabled INTEGER NOT NULL DEFAULT 1,last_status TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_alert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,rule_type TEXT NOT NULL,wallet_id INTEGER,chain TEXT NOT NULL,token_symbol TEXT NOT NULL,direction TEXT NOT NULL,threshold_value TEXT NOT NULL,severity TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_report_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,report_path TEXT NOT NULL,json_path TEXT NOT NULL,created_at TEXT NOT NULL)''')


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_ext_tables()
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_ext_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_alert(alert_type: str, severity: str, message: str, entity_type: str = '', entity_id: str = '') -> None:
    with db.connect() as conn:
        conn.execute('INSERT INTO asset_os_alerts (alert_type, severity, message, entity_type, entity_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (alert_type, severity, message, entity_type, entity_id, 'open', db.utcnow()))


@router.post('/address-book', dependencies=[Depends(require_asset_key)])
def create_address_book(req: AddressBookCreate) -> Dict[str, Any]:
    ensure_ext_tables()
    ts = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_address_book (label, chain, address, category, risk_tier, tags_json, note, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.label, req.chain, req.address, req.category, req.risk_tier, j(req.tags), req.note, int(req.enabled), ts, ts))
        address_id = int(cur.lastrowid)
    db.audit('asset_os_address_book_create', 'asset_os_address', str(address_id), req.model_dump(), 'success', req.risk_tier, 'not_required')
    return row('SELECT * FROM asset_os_address_book WHERE id=?', (address_id,)) or {'id': address_id}


@router.get('/address-book')
def list_address_book() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM asset_os_address_book ORDER BY id DESC')


@router.post('/alert-channels', dependencies=[Depends(require_asset_key)])
def create_alert_channel(req: AlertChannelCreate) -> Dict[str, Any]:
    ensure_ext_tables()
    ts = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_alert_channels (name, channel_type, target, secret_ref, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (req.name, req.channel_type, req.target, req.secret_ref, int(req.enabled), ts, ts))
        channel_id = int(cur.lastrowid)
    db.audit('asset_os_alert_channel_create', 'asset_os_alert_channel', str(channel_id), {'name': req.name, 'channel_type': req.channel_type}, 'success', 'medium', 'not_required')
    return row('SELECT * FROM asset_os_alert_channels WHERE id=?', (channel_id,)) or {'id': channel_id}


@router.get('/alert-channels')
def list_alert_channels() -> List[Dict[str, Any]]:
    return rows('SELECT id,name,channel_type,enabled,last_status,last_error,created_at,updated_at FROM asset_os_alert_channels ORDER BY id DESC')


@router.post('/alert-rules', dependencies=[Depends(require_asset_key)])
def create_alert_rule(req: AlertRuleCreate) -> Dict[str, Any]:
    ensure_ext_tables()
    ts = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_alert_rules (name, rule_type, wallet_id, chain, token_symbol, direction, threshold_value, severity, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.rule_type, req.wallet_id, req.chain, req.token_symbol, req.direction, req.threshold_value, req.severity, int(req.enabled), ts, ts))
        rule_id = int(cur.lastrowid)
    db.audit('asset_os_alert_rule_create', 'asset_os_alert_rule', str(rule_id), req.model_dump(), 'success', req.severity, 'not_required')
    return row('SELECT * FROM asset_os_alert_rules WHERE id=?', (rule_id,)) or {'id': rule_id}


@router.get('/alert-rules')
def list_alert_rules() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM asset_os_alert_rules ORDER BY id DESC')


@router.get('/portfolio')
def portfolio() -> Dict[str, Any]:
    monitors = rows('SELECT tm.*, w.chain, w.label wallet_label, w.address wallet_address FROM asset_os_token_monitors tm JOIN asset_os_wallets w ON tm.wallet_id=w.id ORDER BY tm.id DESC')
    by_symbol: Dict[str, Decimal] = {}
    wallets: List[Dict[str, Any]] = []
    for item in monitors:
        symbol = item.get('token_symbol') or 'UNKNOWN'
        try:
            value = Decimal(str(item.get('last_balance') or '0'))
        except Exception:
            value = Decimal('0')
        by_symbol[symbol] = by_symbol.get(symbol, Decimal('0')) + value
        wallets.append({'wallet_id': item['wallet_id'], 'wallet_label': item['wallet_label'], 'chain': item['chain'], 'address': item['wallet_address'], 'symbol': symbol, 'balance': str(value), 'status': item.get('last_status')})
    return {'status': 'ok', 'totals': {k: str(v) for k, v in by_symbol.items()}, 'positions': wallets}


@router.post('/rules/evaluate', dependencies=[Depends(require_asset_key)])
def evaluate_rules() -> Dict[str, Any]:
    ensure_ext_tables()
    rules = rows('SELECT * FROM asset_os_alert_rules WHERE enabled=1')
    events = rows('SELECT * FROM asset_os_tx_events ORDER BY id DESC LIMIT 500')
    addresses = rows('SELECT * FROM asset_os_address_book WHERE enabled=1')
    hits = 0
    high_risk = {a['address']: a for a in addresses if a.get('risk_tier') in {'high', 'critical'} or a.get('category') in {'blacklist', 'sanction', 'scam'}}
    for event in events:
        counterparty = event.get('counterparty') or ''
        if counterparty in high_risk:
            write_alert('high_risk_counterparty', high_risk[counterparty]['risk_tier'], f"Wallet {event.get('wallet_address')} interacted with {high_risk[counterparty]['label']} / {counterparty}", 'asset_os_tx_event', str(event.get('id')))
            hits += 1
        for rule in rules:
            if rule.get('wallet_id') and int(rule['wallet_id']) != int(event.get('wallet_id') or 0):
                continue
            if rule.get('chain') not in {'global', event.get('chain')}:
                continue
            if rule.get('token_symbol') not in {'ANY', event.get('token_symbol')}:
                continue
            if rule.get('direction') not in {'any', event.get('direction')}:
                continue
            if rule.get('rule_type') == 'large_transfer':
                try:
                    amount = Decimal(str(event.get('value_display') or '0'))
                    threshold = Decimal(str(rule.get('threshold_value') or '0'))
                except Exception:
                    continue
                if threshold > 0 and amount >= threshold:
                    write_alert('large_transfer', rule.get('severity') or 'medium', f"{event.get('direction')} {event.get('value_display')} {event.get('token_symbol')} reached rule {rule.get('name')}", 'asset_os_tx_event', str(event.get('id')))
                    hits += 1
    db.audit('asset_os_rules_evaluate', 'asset_os_rules', None, {'rules': len(rules), 'events': len(events)}, f'hits={hits}', 'medium', 'not_required')
    return {'status': 'success', 'rules': len(rules), 'events_checked': len(events), 'alerts_created': hits}


def send_to_channel(channel: Dict[str, Any], payload: Dict[str, Any]) -> tuple[str, str]:
    ctype = (channel.get('channel_type') or 'webhook').lower()
    target = channel.get('target') or ''
    if ctype == 'webhook':
        r = requests.post(target, json=payload, timeout=20)
        r.raise_for_status()
        return 'success', f'HTTP {r.status_code}'
    if ctype == 'telegram':
        # target can be a full Telegram Bot API sendMessage URL.
        r = requests.post(target, json={'text': payload.get('text') or json.dumps(payload, ensure_ascii=False)}, timeout=20)
        r.raise_for_status()
        return 'success', f'HTTP {r.status_code}'
    raise RuntimeError(f'unsupported channel_type={ctype}')


@router.post('/alerts/send-open', dependencies=[Depends(require_asset_key)])
def send_open_alerts() -> Dict[str, Any]:
    ensure_ext_tables()
    channels = rows('SELECT * FROM asset_os_alert_channels WHERE enabled=1')
    alerts = rows("SELECT * FROM asset_os_alerts WHERE status='open' ORDER BY id DESC LIMIT 50")
    if not channels:
        raise HTTPException(status_code=400, detail='no enabled alert channels')
    text = '\n'.join([f"[{a['severity']}] {a['alert_type']}: {a['message']}" for a in alerts]) or 'No open alerts.'
    payload = {'text': 'Hermes AssetOps Alerts\n' + text, 'alerts': alerts}
    results = []
    for channel in channels:
        try:
            status, detail = send_to_channel(channel, payload)
            with db.connect() as conn:
                conn.execute('UPDATE asset_os_alert_channels SET last_status=?, last_error=?, updated_at=? WHERE id=?', (status, None, db.utcnow(), channel['id']))
            results.append({'channel_id': channel['id'], 'status': status, 'detail': detail})
        except Exception as exc:
            with db.connect() as conn:
                conn.execute('UPDATE asset_os_alert_channels SET last_status=?, last_error=?, updated_at=? WHERE id=?', ('error', str(exc), db.utcnow(), channel['id']))
            results.append({'channel_id': channel['id'], 'status': 'error', 'detail': str(exc)})
    db.audit('asset_os_send_open_alerts', 'asset_os_alerts', None, {'channels': len(channels), 'alerts': len(alerts)}, 'done', 'medium', 'not_required')
    return {'status': 'done', 'results': results}


@router.post('/reports/daily', dependencies=[Depends(require_asset_key)])
def generate_daily_report(req: ReportRequest) -> Dict[str, Any]:
    ensure_ext_tables()
    settings = get_settings()
    report_dir = Path(settings.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dashboard = row('SELECT COUNT(*) c FROM asset_os_wallets') or {'c': 0}
    portfolio_data = portfolio()
    alerts = rows('SELECT * FROM asset_os_alerts ORDER BY id DESC LIMIT ?', (req.include_alerts,))
    events = rows('SELECT * FROM asset_os_tx_events ORDER BY id DESC LIMIT ?', (req.include_events,))
    txs = rows('SELECT * FROM asset_os_tx_drafts ORDER BY id DESC LIMIT 50')
    data = {'title': req.title, 'created_at': now(), 'portfolio': portfolio_data, 'alerts': alerts, 'events': events, 'tx_drafts': txs, 'wallet_count': dashboard['c']}
    md_lines = [f'# {req.title}', '', f'- created_at_utc: {data["created_at"]}', f'- wallet_count: {data["wallet_count"]}', '', '## Portfolio Totals']
    for sym, total in portfolio_data.get('totals', {}).items():
        md_lines.append(f'- {sym}: {total}')
    md_lines += ['', '## Open / Recent Alerts']
    for a in alerts:
        md_lines.append(f'- [{a.get("severity")}] {a.get("alert_type")}: {a.get("message")}')
    md_lines += ['', '## Recent Transaction Events']
    for e in events:
        md_lines.append(f'- {e.get("direction")} {e.get("value_display")} {e.get("token_symbol")} wallet={e.get("wallet_id")} tx={e.get("tx_hash")}')
    md_lines += ['', '## Recent Drafts']
    for t in txs:
        md_lines.append(f'- #{t.get("id")} {t.get("title")} risk={t.get("risk_tier")} state={t.get("approval_state")}')
    md_path = report_dir / f'assetops_daily_{stamp}.md'
    json_path = report_dir / f'assetops_daily_{stamp}.json'
    md_path.write_text('\n'.join(md_lines), encoding='utf-8')
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    with db.connect() as conn:
        conn.execute('INSERT INTO asset_os_report_runs (title, report_path, json_path, created_at) VALUES (?, ?, ?, ?)', (req.title, str(md_path), str(json_path), db.utcnow()))
    db.audit('asset_os_generate_daily_report', 'asset_os_report', None, {'report_path': str(md_path), 'json_path': str(json_path)}, 'success', 'low', 'not_required')
    return {'status': 'success', 'report_path': str(md_path), 'json_path': str(json_path)}


@router.get('/reports/daily')
def list_daily_reports() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM asset_os_report_runs ORDER BY id DESC LIMIT 100')


@router.post('/bootstrap/defaults', dependencies=[Depends(require_asset_key)])
def bootstrap_defaults() -> Dict[str, Any]:
    ensure_ext_tables()
    created = []
    if not row('SELECT id FROM asset_os_alert_rules WHERE name=?', ('Large USDT Outflow',)):
        create_alert_rule(AlertRuleCreate(name='Large USDT Outflow', rule_type='large_transfer', chain='global', token_symbol='USDT', direction='out', threshold_value='10000', severity='high'))
        created.append('alert_rule_large_usdt_outflow')
    if not row('SELECT id FROM asset_os_alert_rules WHERE name=?', ('Large USDT Inflow',)):
        create_alert_rule(AlertRuleCreate(name='Large USDT Inflow', rule_type='large_transfer', chain='global', token_symbol='USDT', direction='in', threshold_value='10000', severity='medium'))
        created.append('alert_rule_large_usdt_inflow')
    db.audit('asset_os_bootstrap_defaults', 'asset_os_defaults', None, {'created': created}, 'success', 'low', 'not_required')
    return {'status': 'success', 'created': created}
