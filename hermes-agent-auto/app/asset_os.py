from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/asset-os', tags=['Hermes AssetOps OS v10'])


def require_asset_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class ChainCreate(BaseModel):
    chain: str = Field(..., min_length=2)
    chain_type: str = Field(default='evm')
    rpc_url: Optional[str] = None
    explorer_url: Optional[str] = None
    enabled: bool = True


class WalletCreate(BaseModel):
    label: str = Field(..., min_length=2)
    chain: str = Field(..., min_length=2)
    address: str = Field(..., min_length=6)
    wallet_type: str = Field(default='watch')
    custody_model: str = Field(default='non-custodial')
    risk_tier: str = Field(default='medium')
    enabled: bool = True


class PolicyCreate(BaseModel):
    name: str = Field(..., min_length=2)
    scope: str = Field(default='global')
    allowed_addresses: List[str] = Field(default=[])
    denied_addresses: List[str] = Field(default=[])
    token_allowlist: List[str] = Field(default=[])
    method_allowlist: List[str] = Field(default=[])
    max_single_native: str = '0'
    max_daily_native: str = '0'
    require_approval_count: int = Field(default=1, ge=1)
    enabled: bool = True


class TxDraftCreate(BaseModel):
    title: str = Field(..., min_length=2)
    chain: str = Field(..., min_length=2)
    source_wallet: str = Field(..., min_length=6)
    to_address: str = Field(..., min_length=6)
    value_native: str = '0'
    token_address: Optional[str] = None
    calldata: str = '0x'
    method_name: str = 'transfer'
    purpose: str = ''
    policy_id: Optional[int] = None


class ApprovalCreate(BaseModel):
    tx_id: int
    operator: str = Field(default='local-operator', min_length=2)
    decision: str = Field(..., pattern='^(approved|rejected)$')
    note: str = ''


class ExecutionMark(BaseModel):
    tx_id: int
    tx_hash: str = Field(..., min_length=10)
    block_number: Optional[int] = None
    note: str = ''


class EvidenceCreate(BaseModel):
    entity_type: str = Field(..., min_length=2)
    entity_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=2)
    content: str = ''
    source_ref: str = ''
    risk_tier: str = 'medium'


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_chains (id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT NOT NULL UNIQUE, chain_type TEXT NOT NULL, rpc_url TEXT, explorer_url TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_wallets (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT NOT NULL, chain TEXT NOT NULL, address TEXT NOT NULL, wallet_type TEXT NOT NULL, custody_model TEXT NOT NULL, risk_tier TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_seen_at TEXT, last_balance TEXT, last_status TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_policies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, scope TEXT NOT NULL, allowed_addresses_json TEXT NOT NULL, denied_addresses_json TEXT NOT NULL, token_allowlist_json TEXT NOT NULL, method_allowlist_json TEXT NOT NULL, max_single_native TEXT NOT NULL, max_daily_native TEXT NOT NULL, require_approval_count INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_tx_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, chain TEXT NOT NULL, source_wallet TEXT NOT NULL, to_address TEXT NOT NULL, value_native TEXT NOT NULL, token_address TEXT, calldata TEXT NOT NULL, method_name TEXT NOT NULL, purpose TEXT, policy_id INTEGER, risk_tier TEXT NOT NULL, risk_reasons_json TEXT NOT NULL, approval_state TEXT NOT NULL, payload_hash TEXT NOT NULL, execution_tx_hash TEXT, execution_block INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, tx_id INTEGER NOT NULL, operator TEXT NOT NULL, decision TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, title TEXT NOT NULL, content TEXT, source_ref TEXT, evidence_hash TEXT NOT NULL, risk_tier TEXT NOT NULL, created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS asset_os_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, alert_type TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL, entity_type TEXT, entity_id TEXT, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL)''')


def _rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(query, args).fetchall()]


def _row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        row = conn.execute(query, args).fetchone()
        return dict(row) if row else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def evaluate_risk(req: TxDraftCreate, policy: Optional[Dict[str, Any]] = None) -> tuple[str, List[str]]:
    risk = 'medium'
    reasons: List[str] = []
    try:
        value = Decimal(req.value_native or '0')
    except Exception:
        value = Decimal('0')
        reasons.append('invalid value_native format')
        risk = 'high'
    if value > 0:
        reasons.append('native asset movement')
        risk = 'high'
    if req.token_address:
        reasons.append('token-related draft')
        risk = 'high'
    if req.calldata and req.calldata != '0x':
        reasons.append('non-empty calldata')
        risk = 'high'
    high_risk_methods = {'approve', 'setapprovalforall', 'transferownership', 'upgrade', 'upgradeto', 'permit', 'delegatecall'}
    if req.method_name.lower() in high_risk_methods:
        reasons.append(f'high-risk method: {req.method_name}')
        risk = 'critical'
    if policy:
        denied = json.loads(policy.get('denied_addresses_json') or '[]')
        allowed = json.loads(policy.get('allowed_addresses_json') or '[]')
        if req.to_address in denied:
            reasons.append('destination is explicitly denied')
            risk = 'critical'
        if allowed and req.to_address not in allowed:
            reasons.append('destination is not in allowlist')
            risk = 'high' if risk != 'critical' else risk
        max_single = Decimal(policy.get('max_single_native') or '0')
        if max_single > 0 and value > max_single:
            reasons.append('value exceeds max_single_native policy')
            risk = 'critical'
    if not reasons:
        reasons.append('no direct high-risk signal detected')
        risk = 'low'
    return risk, reasons


def payload_hash(payload: Dict[str, Any]) -> str:
    return 'sha256:' + hashlib.sha256(_json(payload).encode('utf-8')).hexdigest()


@router.get('/status')
def asset_os_status() -> Dict[str, Any]:
    ensure_tables()
    return {
        'status': 'ok',
        'version': '10.0-assetops-os',
        'custody_model': 'non-custodial',
        'private_key_storage': 'disabled',
        'signing': 'external-wallet-or-hsm-required',
        'broadcasting': 'approval-required-external-executor',
        'modules': ['chains', 'wallets', 'policies', 'tx_drafts', 'approvals', 'evidence', 'alerts', 'audit'],
    }


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        counts = {
            'chains': conn.execute('SELECT COUNT(*) c FROM asset_os_chains').fetchone()['c'],
            'wallets': conn.execute('SELECT COUNT(*) c FROM asset_os_wallets').fetchone()['c'],
            'policies': conn.execute('SELECT COUNT(*) c FROM asset_os_policies').fetchone()['c'],
            'tx_drafts': conn.execute('SELECT COUNT(*) c FROM asset_os_tx_drafts').fetchone()['c'],
            'open_alerts': conn.execute("SELECT COUNT(*) c FROM asset_os_alerts WHERE status='open'").fetchone()['c'],
        }
    return {'status': 'ok', 'counts': counts, 'risk_boundary': 'Hermes never stores seed phrases or private keys.'}


@router.post('/chains', dependencies=[Depends(require_asset_key)])
def create_chain(req: ChainCreate) -> Dict[str, Any]:
    ensure_tables()
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT OR REPLACE INTO asset_os_chains (id, chain, chain_type, rpc_url, explorer_url, enabled, created_at, updated_at) VALUES ((SELECT id FROM asset_os_chains WHERE chain=?), ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM asset_os_chains WHERE chain=?), ?), ?)', (req.chain, req.chain, req.chain_type, req.rpc_url, req.explorer_url, int(req.enabled), req.chain, now, now))
    db.audit('asset_os_create_chain', 'asset_os_chain', req.chain, req.model_dump(), 'success', 'low', 'not_required')
    return {'status': 'success', 'chain': req.chain}


@router.get('/chains')
def list_chains() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_chains ORDER BY id DESC')


@router.post('/wallets', dependencies=[Depends(require_asset_key)])
def create_wallet(req: WalletCreate) -> Dict[str, Any]:
    ensure_tables()
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_wallets (label, chain, address, wallet_type, custody_model, risk_tier, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.label, req.chain, req.address, req.wallet_type, req.custody_model, req.risk_tier, int(req.enabled), now, now))
        wallet_id = int(cur.lastrowid)
    db.audit('asset_os_create_wallet', 'asset_os_wallet', str(wallet_id), req.model_dump(), 'success', req.risk_tier, 'not_required')
    return _row('SELECT * FROM asset_os_wallets WHERE id=?', (wallet_id,)) or {'id': wallet_id}


@router.get('/wallets')
def list_wallets() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_wallets ORDER BY id DESC')


@router.post('/policies', dependencies=[Depends(require_asset_key)])
def create_policy(req: PolicyCreate) -> Dict[str, Any]:
    ensure_tables()
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_policies (name, scope, allowed_addresses_json, denied_addresses_json, token_allowlist_json, method_allowlist_json, max_single_native, max_daily_native, require_approval_count, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.scope, _json(req.allowed_addresses), _json(req.denied_addresses), _json(req.token_allowlist), _json(req.method_allowlist), req.max_single_native, req.max_daily_native, req.require_approval_count, int(req.enabled), now, now))
        policy_id = int(cur.lastrowid)
    db.audit('asset_os_create_policy', 'asset_os_policy', str(policy_id), req.model_dump(), 'success', 'medium', 'not_required')
    return _row('SELECT * FROM asset_os_policies WHERE id=?', (policy_id,)) or {'id': policy_id}


@router.get('/policies')
def list_policies() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_policies ORDER BY id DESC')


@router.post('/tx-drafts', dependencies=[Depends(require_asset_key)])
def create_tx_draft(req: TxDraftCreate) -> Dict[str, Any]:
    ensure_tables()
    policy = _row('SELECT * FROM asset_os_policies WHERE id=?', (req.policy_id,)) if req.policy_id else None
    risk, reasons = evaluate_risk(req, policy)
    approval_state = 'pending'
    payload = req.model_dump()
    ph = payload_hash({'payload': payload, 'risk': risk, 'reasons': reasons})
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_tx_drafts (title, chain, source_wallet, to_address, value_native, token_address, calldata, method_name, purpose, policy_id, risk_tier, risk_reasons_json, approval_state, payload_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.title, req.chain, req.source_wallet, req.to_address, req.value_native, req.token_address, req.calldata, req.method_name, req.purpose, req.policy_id, risk, _json(reasons), approval_state, ph, now, now))
        tx_id = int(cur.lastrowid)
    db.audit('asset_os_create_tx_draft', 'asset_os_tx', str(tx_id), {'payload_hash': ph, 'title': req.title}, 'success', risk, approval_state)
    return _row('SELECT * FROM asset_os_tx_drafts WHERE id=?', (tx_id,)) or {'id': tx_id}


@router.get('/tx-drafts')
def list_tx_drafts() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_tx_drafts ORDER BY id DESC')


@router.get('/tx-drafts/{tx_id}/payload')
def get_tx_payload(tx_id: int) -> Dict[str, Any]:
    tx = _row('SELECT * FROM asset_os_tx_drafts WHERE id=?', (tx_id,))
    if not tx:
        raise HTTPException(status_code=404, detail='tx draft not found')
    return {'tx': tx, 'payload_hash': tx['payload_hash'], 'warning': 'Draft only. Sign and broadcast through external wallet, Safe, TronLink, HSM, or MPC executor.'}


@router.post('/tx-drafts/approve', dependencies=[Depends(require_asset_key)])
def approve_tx_draft(req: ApprovalCreate) -> Dict[str, Any]:
    ensure_tables()
    tx = _row('SELECT * FROM asset_os_tx_drafts WHERE id=?', (req.tx_id,))
    if not tx:
        raise HTTPException(status_code=404, detail='tx draft not found')
    now = db.utcnow()
    with db.connect() as conn:
        conn.execute('INSERT INTO asset_os_approvals (tx_id, operator, decision, note, created_at) VALUES (?, ?, ?, ?, ?)', (req.tx_id, req.operator, req.decision, req.note, now))
        conn.execute('UPDATE asset_os_tx_drafts SET approval_state=?, updated_at=? WHERE id=?', (req.decision, now, req.tx_id))
    db.audit('asset_os_approval', 'asset_os_tx', str(req.tx_id), req.model_dump(), 'success', tx['risk_tier'], req.decision)
    return {'tx_id': req.tx_id, 'approval_state': req.decision}


@router.post('/tx-drafts/mark-executed', dependencies=[Depends(require_asset_key)])
def mark_executed(req: ExecutionMark) -> Dict[str, Any]:
    tx = _row('SELECT * FROM asset_os_tx_drafts WHERE id=?', (req.tx_id,))
    if not tx:
        raise HTTPException(status_code=404, detail='tx draft not found')
    if tx['approval_state'] != 'approved':
        raise HTTPException(status_code=400, detail='tx must be approved before execution can be recorded')
    now = db.utcnow()
    with db.connect() as conn:
        conn.execute('UPDATE asset_os_tx_drafts SET execution_tx_hash=?, execution_block=?, approval_state=?, updated_at=? WHERE id=?', (req.tx_hash, req.block_number, 'executed', now, req.tx_id))
    db.audit('asset_os_mark_executed', 'asset_os_tx', str(req.tx_id), req.model_dump(), 'success', tx['risk_tier'], 'executed')
    return {'tx_id': req.tx_id, 'tx_hash': req.tx_hash, 'status': 'executed'}


@router.post('/evidence', dependencies=[Depends(require_asset_key)])
def create_evidence(req: EvidenceCreate) -> Dict[str, Any]:
    ensure_tables()
    evidence_hash = payload_hash(req.model_dump())
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO asset_os_evidence (entity_type, entity_id, title, content, source_ref, evidence_hash, risk_tier, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.entity_type, req.entity_id, req.title, req.content, req.source_ref, evidence_hash, req.risk_tier, now))
        evidence_id = int(cur.lastrowid)
    db.audit('asset_os_create_evidence', req.entity_type, req.entity_id, {'evidence_id': evidence_id, 'evidence_hash': evidence_hash}, 'success', req.risk_tier, 'not_required')
    return _row('SELECT * FROM asset_os_evidence WHERE id=?', (evidence_id,)) or {'id': evidence_id}


@router.get('/evidence')
def list_evidence() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_evidence ORDER BY id DESC LIMIT 100')


@router.get('/alerts')
def list_alerts() -> List[Dict[str, Any]]:
    return _rows('SELECT * FROM asset_os_alerts ORDER BY id DESC LIMIT 100')
