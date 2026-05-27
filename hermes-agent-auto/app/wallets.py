from __future__ import annotations

import re
from decimal import Decimal, getcontext
from typing import Any, Dict, Optional

import requests

from app import db
from app.notifier import Notifier
from app.runtime import run_store
from app.schemas import MultisigPlanRequest, WalletMonitorOut, WalletRefreshResult

getcontext().prec = 80

EVM_ADDRESS_RE = re.compile(r'^0x[a-fA-F0-9]{40}$')

DEFAULT_PUBLIC_RPCS = {
    'ethereum': 'https://ethereum-rpc.publicnode.com',
    'eth': 'https://ethereum-rpc.publicnode.com',
    'polygon': 'https://polygon-bor-rpc.publicnode.com',
    'matic': 'https://polygon-bor-rpc.publicnode.com',
    'bsc': 'https://bsc-rpc.publicnode.com',
    'bnb': 'https://bsc-rpc.publicnode.com',
    'arbitrum': 'https://arbitrum-one-rpc.publicnode.com',
    'arb': 'https://arbitrum-one-rpc.publicnode.com',
    'base': 'https://base-rpc.publicnode.com',
    'optimism': 'https://optimism-rpc.publicnode.com',
    'op': 'https://optimism-rpc.publicnode.com',
}

SAFE_CHAIN_SLUGS = {
    'ethereum': 'eth',
    'eth': 'eth',
    'polygon': 'matic',
    'matic': 'matic',
    'bsc': 'bnb',
    'bnb': 'bnb',
    'arbitrum': 'arb1',
    'arb': 'arb1',
    'base': 'base',
    'optimism': 'oeth',
    'op': 'oeth',
}


def normalize_chain(chain: str) -> str:
    return chain.strip().lower()


def validate_evm_address(address: str) -> str:
    address = address.strip()
    if not EVM_ADDRESS_RE.match(address):
        raise ValueError('Invalid EVM address. Expected 0x followed by 40 hex characters.')
    return address


def rpc_url_for(chain: str, rpc_url: Optional[str]) -> str:
    if rpc_url:
        return rpc_url.strip()
    normalized = normalize_chain(chain)
    if normalized not in DEFAULT_PUBLIC_RPCS:
        raise ValueError(f'No default RPC configured for chain={chain}. Provide rpc_url manually.')
    return DEFAULT_PUBLIC_RPCS[normalized]


def rpc_call(rpc_url: str, method: str, params: list[Any]) -> Any:
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
    response = requests.post(rpc_url, json=payload, timeout=20)
    response.raise_for_status()
    body = response.json()
    if 'error' in body:
        raise RuntimeError(body['error'])
    return body.get('result')


def wei_to_native(value_wei: int) -> str:
    native = Decimal(value_wei) / Decimal(10 ** 18)
    return format(native.normalize(), 'f')


def create_multisig_plan(req: MultisigPlanRequest) -> Dict[str, Any]:
    owners = [validate_evm_address(owner) for owner in req.owners]
    if req.threshold > len(owners):
        raise ValueError('threshold cannot be greater than owners count')
    if req.threshold < 2 and len(owners) > 1:
        raise ValueError('threshold=1 is not recommended for multisig. Use at least 2 for team/treasury custody.')
    chain = normalize_chain(req.chain)
    safe_slug = SAFE_CHAIN_SLUGS.get(chain, chain)
    return {
        'name': req.name,
        'chain': chain,
        'owners': owners,
        'threshold': req.threshold,
        'scheme': f'{req.threshold}/{len(owners)}',
        'safe_url': f'https://app.safe.global/new-safe/create?chain={safe_slug}',
        'steps': [
            'Open Safe official app and connect the deployer wallet.',
            f'Select chain: {chain}.',
            f'Create Safe Account named: {req.name}.',
            'Add every owner address exactly as listed.',
            f'Set threshold to {req.threshold} out of {len(owners)}.',
            'Review chain, owners, and threshold before deployment.',
            'Deploy the Safe with a small gas-paying wallet.',
            'Send a small test transfer to the Safe, then execute a test outgoing transfer with required signatures.',
        ],
        'safety': [
            'This module generates a deployment plan only; it never handles private keys or signs transactions.',
            'Do not paste seed phrases, private keys, or hardware wallet recovery data into Hermes Agent.',
            'Migrate production assets only after a successful small-value test transaction.',
        ],
    }


def refresh_wallet_monitor(monitor: WalletMonitorOut) -> WalletRefreshResult:
    previous_balance = monitor.last_balance_wei
    try:
        address = validate_evm_address(monitor.address)
        rpc_url = rpc_url_for(monitor.chain, monitor.rpc_url)
        block_hex = rpc_call(rpc_url, 'eth_blockNumber', [])
        balance_hex = rpc_call(rpc_url, 'eth_getBalance', [address, 'latest'])
        block_number = int(block_hex, 16)
        balance_int = int(balance_hex, 16)
        balance_wei = str(balance_int)
        balance_native = wei_to_native(balance_int)
        changed = previous_balance is not None and previous_balance != balance_wei
        db.update_wallet_monitor_state(monitor.id, status='success', block_number=block_number, balance_wei=balance_wei, balance_native=balance_native, error=None)
        if changed and monitor.alert_on_change:
            message = f'Wallet balance changed: {monitor.label} {monitor.chain} {monitor.address} {previous_balance} -> {balance_wei} wei'
            db.record_wallet_alert(monitor.id, 'balance_changed', message, previous_balance, balance_wei, block_number)
            run_store.emit('wallet-monitor', 'wallet_alert', message, progress=100, status='success', data={'tool': 'wallet_monitor', 'monitor_id': monitor.id, 'address': monitor.address, 'chain': monitor.chain})
            try:
                Notifier().notify('Hermes wallet monitor alert', message, {'monitor_id': monitor.id, 'chain': monitor.chain, 'address': monitor.address})
            except Exception:
                pass
        return WalletRefreshResult(monitor_id=monitor.id, status='success', changed=changed, block_number=block_number, balance_wei=balance_wei, balance_native=balance_native)
    except Exception as exc:  # noqa: BLE001
        db.update_wallet_monitor_state(monitor.id, status='error', block_number=None, balance_wei=previous_balance, balance_native=monitor.last_balance_native, error=str(exc))
        return WalletRefreshResult(monitor_id=monitor.id, status='error', changed=False, error=str(exc))


def refresh_wallet_monitor_by_id(monitor_id: int) -> WalletRefreshResult:
    monitor = db.get_wallet_monitor(monitor_id)
    if not monitor:
        return WalletRefreshResult(monitor_id=monitor_id, status='error', changed=False, error='monitor not found')
    return refresh_wallet_monitor(monitor)


def refresh_all_wallet_monitors() -> list[WalletRefreshResult]:
    results = []
    for monitor in db.list_wallet_monitors():
        if monitor.enabled:
            results.append(refresh_wallet_monitor(monitor))
    return results
