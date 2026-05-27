from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=3)
    title: str = 'Hermes Agent Report'
    max_results: int = Field(default=8, ge=0, le=20)
    notify: bool = False


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=2)
    prompt: str = Field(..., min_length=3)
    interval_minutes: int = Field(default=120, ge=5)
    max_results: int = Field(default=8, ge=0, le=20)
    notify: bool = False
    enabled: bool = True
    run_now: bool = False


class TaskOut(BaseModel):
    id: int
    title: str
    prompt: str
    interval_minutes: int
    max_results: int
    notify: bool
    enabled: bool
    created_at: str
    updated_at: str
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_report_path: Optional[str] = None


class AgentResult(BaseModel):
    title: str
    prompt: str
    status: str
    report_markdown: str
    report_path: Optional[str] = None
    sources: List[Dict[str, Any]] = []
    error: Optional[str] = None


class MultisigPlanRequest(BaseModel):
    name: str = Field(default='Hermes Treasury Multisig', min_length=2)
    chain: str = Field(default='ethereum', min_length=2)
    owners: List[str] = Field(..., min_length=2)
    threshold: int = Field(..., ge=1)


class WalletMonitorCreate(BaseModel):
    label: str = Field(..., min_length=2)
    chain: str = Field(default='ethereum', min_length=2)
    address: str = Field(..., min_length=6)
    rpc_url: Optional[str] = None
    poll_minutes: int = Field(default=5, ge=1)
    alert_on_change: bool = True
    enabled: bool = True


class WalletMonitorOut(BaseModel):
    id: int
    label: str
    chain: str
    address: str
    rpc_url: Optional[str] = None
    poll_minutes: int
    alert_on_change: bool
    enabled: bool
    created_at: str
    updated_at: str
    last_checked_at: Optional[str] = None
    last_block: Optional[int] = None
    last_balance_wei: Optional[str] = None
    last_balance_native: Optional[str] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None


class WalletRefreshResult(BaseModel):
    monitor_id: int
    status: str
    changed: bool
    block_number: Optional[int] = None
    balance_wei: Optional[str] = None
    balance_native: Optional[str] = None
    error: Optional[str] = None


class SafeRegistryCreate(BaseModel):
    name: str = Field(..., min_length=2)
    chain: str = Field(default='ethereum', min_length=2)
    safe_address: str = Field(..., min_length=6)
    owners: List[str] = Field(default=[])
    threshold: int = Field(default=2, ge=1)
    daily_limit_native: str = '0'
    single_tx_limit_native: str = '0'
    enabled: bool = True


class SafeRegistryOut(BaseModel):
    id: int
    name: str
    chain: str
    safe_address: str
    owners_json: str
    threshold: int
    daily_limit_native: str
    single_tx_limit_native: str
    enabled: bool
    created_at: str
    updated_at: str


class AssetPolicyCreate(BaseModel):
    safe_id: int
    name: str = Field(..., min_length=2)
    allowed_to_addresses: List[str] = Field(default=[])
    denied_to_addresses: List[str] = Field(default=[])
    max_single_native: str = '0'
    max_daily_native: str = '0'
    require_manual_approval: bool = True
    enabled: bool = True


class SafeTxDraftCreate(BaseModel):
    safe_id: int
    title: str = Field(..., min_length=2)
    to_address: str = Field(..., min_length=6)
    value_native: str = '0'
    token_address: Optional[str] = None
    calldata: str = '0x'
    operation: str = 'call'
    risk_note: str = ''


class SafeTxDraftOut(BaseModel):
    id: int
    safe_id: int
    title: str
    to_address: str
    value_native: str
    token_address: Optional[str]
    calldata: str
    operation: str
    risk_tier: str
    approval_state: str
    safe_tx_hash: Optional[str] = None
    execution_tx_hash: Optional[str] = None
    risk_note: str
    created_at: str
    updated_at: str


class ApprovalRequest(BaseModel):
    tx_id: int
    operator: str = Field(default='local-operator', min_length=2)
    decision: str = Field(..., pattern='^(approved|rejected)$')
    note: str = ''


class SafeSignRequest(BaseModel):
    tx_id: int
    signer_address: str = Field(..., min_length=6)
    signature: str = Field(..., min_length=10)


class SafeExecutionMark(BaseModel):
    tx_id: int
    execution_tx_hash: str = Field(..., min_length=10)
    note: str = ''


class TronPermissionKey(BaseModel):
    address: str = Field(..., min_length=10)
    weight: int = Field(default=1, ge=1)


class TronPermissionDraftCreate(BaseModel):
    name: str = Field(default='TRON Account Permission Draft', min_length=2)
    account_address: str = Field(..., min_length=10)
    owner_keys: List[TronPermissionKey] = Field(default=[])
    owner_threshold: int = Field(default=1, ge=1)
    active_keys: List[TronPermissionKey] = Field(default=[])
    active_threshold: int = Field(default=2, ge=1)
    active_permission_name: str = 'asset-ops'
    operations_hex: str = '7fff1fc0033e000000000000000000000000000000000000000000000000000000000000'
    risk_note: str = ''


class TronPermissionDraftOut(BaseModel):
    id: int
    name: str
    account_address: str
    owner_threshold: int
    owner_keys_json: str
    active_threshold: int
    active_keys_json: str
    active_permission_name: str
    operations_hex: str
    risk_tier: str
    approval_state: str
    payload_json: str
    execution_tx_hash: Optional[str] = None
    risk_note: str
    created_at: str
    updated_at: str


class TronPermissionExecutionMark(BaseModel):
    draft_id: int
    execution_tx_hash: str = Field(..., min_length=10)
    note: str = ''
