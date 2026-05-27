from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any, Dict

from app import db
from app.schemas import ApprovalRequest, SafeExecutionMark, SafeRegistryCreate, SafeSignRequest, SafeTxDraftCreate
from app.wallets import validate_evm_address


def native_to_wei(value: str) -> int:
    return int(Decimal(value or '0') * Decimal(10**18))


def estimate_risk(req: SafeTxDraftCreate, safe) -> tuple[str, str, str]:
    value_wei = native_to_wei(req.value_native)
    single_limit_wei = native_to_wei(safe.single_tx_limit_native or '0')
    reasons = []
    risk = 'medium'
    approval_state = 'pending'
    if value_wei > 0:
        reasons.append('native asset transfer')
        risk = 'high'
    if req.calldata and req.calldata != '0x':
        reasons.append('non-empty calldata')
        risk = 'high'
    if req.token_address:
        reasons.append('token transaction draft')
        risk = 'high'
    if single_limit_wei and value_wei > single_limit_wei:
        reasons.append('exceeds single transaction limit')
        risk = 'critical'
    if not reasons:
        reasons.append('no direct asset movement detected')
    note = '; '.join(reasons)
    return risk, approval_state, note


def register_safe(req: SafeRegistryCreate):
    validate_evm_address(req.safe_address)
    for owner in req.owners:
        validate_evm_address(owner)
    if req.threshold > max(len(req.owners), 1):
        raise ValueError('threshold cannot exceed owners count')
    return db.create_safe(req)


def create_tx_draft(req: SafeTxDraftCreate):
    safe = db.get_safe(req.safe_id)
    if not safe:
        raise ValueError('safe not found')
    validate_evm_address(req.to_address)
    if req.token_address:
        validate_evm_address(req.token_address)
    risk, approval_state, note = estimate_risk(req, safe)
    if req.risk_note:
        note = note + '; operator note: ' + req.risk_note
    return db.create_safe_tx(req, risk, approval_state, note)


def approve_tx(req: ApprovalRequest) -> Dict[str, Any]:
    tx = db.get_safe_tx(req.tx_id)
    if not tx:
        raise ValueError('tx draft not found')
    db.record_approval(req.tx_id, req.operator, req.decision, req.note)
    db.set_tx_approval(req.tx_id, req.decision)
    return {'tx_id': req.tx_id, 'approval_state': req.decision}


def record_signature(req: SafeSignRequest) -> Dict[str, Any]:
    tx = db.get_safe_tx(req.tx_id)
    if not tx:
        raise ValueError('tx draft not found')
    if tx.approval_state != 'approved':
        raise ValueError('tx must be approved before recording signatures')
    validate_evm_address(req.signer_address)
    db.record_signature(req.tx_id, req.signer_address, req.signature)
    return {'tx_id': req.tx_id, 'signer_address': req.signer_address, 'status': 'signature_recorded'}


def mark_executed(req: SafeExecutionMark) -> Dict[str, Any]:
    tx = db.get_safe_tx(req.tx_id)
    if not tx:
        raise ValueError('tx draft not found')
    if tx.approval_state not in {'approved', 'executed'}:
        raise ValueError('tx must be approved before execution can be marked')
    db.mark_tx_executed(req.tx_id, req.execution_tx_hash)
    return {'tx_id': req.tx_id, 'execution_tx_hash': req.execution_tx_hash, 'status': 'executed'}


def build_safe_tx_payload(tx_id: int) -> Dict[str, Any]:
    tx = db.get_safe_tx(tx_id)
    if not tx:
        raise ValueError('tx draft not found')
    safe = db.get_safe(tx.safe_id)
    if not safe:
        raise ValueError('safe not found')
    payload = {
        'chain': safe.chain,
        'safe_address': safe.safe_address,
        'to': tx.to_address,
        'value_native': tx.value_native,
        'token_address': tx.token_address,
        'calldata': tx.calldata,
        'operation': tx.operation,
        'risk_tier': tx.risk_tier,
        'approval_state': tx.approval_state,
    }
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()
    return {'payload': payload, 'payload_hash': 'sha256:' + payload_hash, 'warning': 'Hermes does not store private keys. Sign and execute with Safe official app or an external wallet signer.'}
