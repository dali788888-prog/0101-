from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app import db
from app.schemas import TronPermissionDraftCreate, TronPermissionDraftOut, TronPermissionExecutionMark

TRON_BASE58_RE = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')


def validate_tron_address(address: str) -> str:
    address = address.strip()
    if not TRON_BASE58_RE.match(address):
        raise ValueError('Invalid TRON address. Expected Base58Check address starting with T.')
    return address


def ensure_tron_tables() -> None:
    with db.connect() as conn:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS tron_permission_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            account_address TEXT NOT NULL,
            owner_threshold INTEGER NOT NULL,
            owner_keys_json TEXT NOT NULL,
            active_threshold INTEGER NOT NULL,
            active_keys_json TEXT NOT NULL,
            active_permission_name TEXT NOT NULL,
            operations_hex TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            approval_state TEXT NOT NULL DEFAULT 'pending',
            payload_json TEXT NOT NULL,
            execution_tx_hash TEXT,
            risk_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        ''')


def _row_to_tron(row) -> TronPermissionDraftOut:
    return TronPermissionDraftOut(
        id=row['id'], name=row['name'], account_address=row['account_address'],
        owner_threshold=row['owner_threshold'], owner_keys_json=row['owner_keys_json'],
        active_threshold=row['active_threshold'], active_keys_json=row['active_keys_json'],
        active_permission_name=row['active_permission_name'], operations_hex=row['operations_hex'],
        risk_tier=row['risk_tier'], approval_state=row['approval_state'], payload_json=row['payload_json'],
        execution_tx_hash=row['execution_tx_hash'], risk_note=row['risk_note'] or '',
        created_at=row['created_at'], updated_at=row['updated_at'],
    )


def build_tron_permission_payload(req: TronPermissionDraftCreate) -> Dict[str, Any]:
    account = validate_tron_address(req.account_address)
    owner_keys = []
    for key in req.owner_keys:
        owner_keys.append({'address': validate_tron_address(key.address), 'weight': key.weight})
    active_keys = []
    for key in req.active_keys:
        active_keys.append({'address': validate_tron_address(key.address), 'weight': key.weight})
    if owner_keys and sum(k['weight'] for k in owner_keys) < req.owner_threshold:
        raise ValueError('owner key weights cannot satisfy owner_threshold')
    if active_keys and sum(k['weight'] for k in active_keys) < req.active_threshold:
        raise ValueError('active key weights cannot satisfy active_threshold')
    if not active_keys:
        raise ValueError('active_keys is required for TRON account permission management')
    return {
        'account_address': account,
        'owner_permission': {
            'type': 'Owner',
            'permission_name': 'owner',
            'threshold': req.owner_threshold,
            'keys': owner_keys,
        },
        'active_permissions': [
            {
                'type': 'Active',
                'permission_name': req.active_permission_name,
                'threshold': req.active_threshold,
                'operations': req.operations_hex,
                'keys': active_keys,
            }
        ],
        'warning': 'Draft only. Hermes does not store private keys and does not sign or broadcast TRON AccountPermissionUpdate transactions.',
    }


def estimate_tron_risk(req: TronPermissionDraftCreate) -> tuple[str, str]:
    reasons = ['TRON Account Permission Management changes account authority']
    risk = 'critical'
    if req.owner_keys:
        reasons.append('owner permission is included')
    if req.active_threshold <= 1 and len(req.active_keys) > 1:
        reasons.append('active threshold is 1; not recommended for treasury custody')
    if req.risk_note:
        reasons.append('operator note: ' + req.risk_note)
    return risk, '; '.join(reasons)


def create_tron_permission_draft(req: TronPermissionDraftCreate) -> TronPermissionDraftOut:
    ensure_tron_tables()
    payload = build_tron_permission_payload(req)
    risk_tier, risk_note = estimate_tron_risk(req)
    now = db.utcnow()
    with db.connect() as conn:
        cur = conn.execute('''
        INSERT INTO tron_permission_drafts
        (name, account_address, owner_threshold, owner_keys_json, active_threshold, active_keys_json, active_permission_name, operations_hex, risk_tier, approval_state, payload_json, risk_note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            req.name, payload['account_address'], req.owner_threshold,
            json.dumps(payload['owner_permission']['keys'], ensure_ascii=False),
            req.active_threshold, json.dumps(payload['active_permissions'][0]['keys'], ensure_ascii=False),
            req.active_permission_name, req.operations_hex, risk_tier, 'pending',
            json.dumps(payload, ensure_ascii=False), risk_note, now, now,
        ))
        row = conn.execute('SELECT * FROM tron_permission_drafts WHERE id=?', (int(cur.lastrowid),)).fetchone()
    db.audit('create_tron_permission_draft', 'tron_permission', str(row['id']), {'account_address': req.account_address}, 'success', risk_tier, 'pending')
    return _row_to_tron(row)


def list_tron_permission_drafts() -> List[TronPermissionDraftOut]:
    ensure_tron_tables()
    with db.connect() as conn:
        return [_row_to_tron(row) for row in conn.execute('SELECT * FROM tron_permission_drafts ORDER BY id DESC').fetchall()]


def get_tron_permission_payload(draft_id: int) -> Dict[str, Any]:
    ensure_tron_tables()
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM tron_permission_drafts WHERE id=?', (draft_id,)).fetchone()
    if not row:
        raise ValueError('TRON permission draft not found')
    return json.loads(row['payload_json'])


def approve_tron_permission_draft(draft_id: int, decision: str, operator: str = 'local-operator', note: str = '') -> Dict[str, Any]:
    ensure_tron_tables()
    if decision not in {'approved', 'rejected'}:
        raise ValueError('decision must be approved or rejected')
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM tron_permission_drafts WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise ValueError('TRON permission draft not found')
        conn.execute('UPDATE tron_permission_drafts SET approval_state=?, updated_at=? WHERE id=?', (decision, db.utcnow(), draft_id))
    db.audit('tron_permission_approval', 'tron_permission', str(draft_id), {'operator': operator, 'decision': decision, 'note': note}, 'success', 'critical', decision)
    return {'draft_id': draft_id, 'approval_state': decision}


def mark_tron_permission_executed(req: TronPermissionExecutionMark) -> Dict[str, Any]:
    ensure_tron_tables()
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM tron_permission_drafts WHERE id=?', (req.draft_id,)).fetchone()
        if not row:
            raise ValueError('TRON permission draft not found')
        if row['approval_state'] != 'approved':
            raise ValueError('TRON permission draft must be approved before execution can be marked')
        conn.execute('UPDATE tron_permission_drafts SET execution_tx_hash=?, approval_state=?, updated_at=? WHERE id=?', (req.execution_tx_hash, 'executed', db.utcnow(), req.draft_id))
    db.audit('tron_permission_mark_executed', 'tron_permission', str(req.draft_id), {'execution_tx_hash': req.execution_tx_hash, 'note': req.note}, 'success', 'critical', 'executed')
    return {'draft_id': req.draft_id, 'execution_tx_hash': req.execution_tx_hash, 'status': 'executed'}
