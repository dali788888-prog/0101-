from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.schemas import SafeRegistryCreate, SafeRegistryOut, SafeTxDraftCreate, SafeTxDraftOut, TaskCreate, TaskOut, WalletMonitorCreate, WalletMonitorOut


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> str:
    url = get_settings().database_url
    if url.startswith('sqlite:///'):
        return url.replace('sqlite:///', '', 1)
    return '/app/storage/hermes_agent.db'


def connect() -> sqlite3.Connection:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,prompt TEXT NOT NULL,interval_minutes INTEGER NOT NULL,max_results INTEGER NOT NULL,notify INTEGER NOT NULL DEFAULT 0,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_run_at TEXT,last_status TEXT,last_report_path TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,title TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,report_path TEXT,sources_json TEXT,error TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS wallet_monitors (id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT NOT NULL,chain TEXT NOT NULL,address TEXT NOT NULL,rpc_url TEXT,poll_minutes INTEGER NOT NULL DEFAULT 5,alert_on_change INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,last_checked_at TEXT,last_block INTEGER,last_balance_wei TEXT,last_balance_native TEXT,last_status TEXT,last_error TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS wallet_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,monitor_id INTEGER NOT NULL,event_type TEXT NOT NULL,message TEXT NOT NULL,previous_balance_wei TEXT,current_balance_wei TEXT,block_number INTEGER,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS safe_registry (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,chain TEXT NOT NULL,safe_address TEXT NOT NULL,owners_json TEXT NOT NULL,threshold INTEGER NOT NULL,daily_limit_native TEXT NOT NULL DEFAULT '0',single_tx_limit_native TEXT NOT NULL DEFAULT '0',enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS safe_tx_drafts (id INTEGER PRIMARY KEY AUTOINCREMENT,safe_id INTEGER NOT NULL,title TEXT NOT NULL,to_address TEXT NOT NULL,value_native TEXT NOT NULL DEFAULT '0',token_address TEXT,calldata TEXT NOT NULL DEFAULT '0x',operation TEXT NOT NULL DEFAULT 'call',risk_tier TEXT NOT NULL,approval_state TEXT NOT NULL DEFAULT 'pending',safe_tx_hash TEXT,execution_tx_hash TEXT,risk_note TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY AUTOINCREMENT,tx_id INTEGER NOT NULL,operator TEXT NOT NULL,decision TEXT NOT NULL,note TEXT,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS signatures (id INTEGER PRIMARY KEY AUTOINCREMENT,tx_id INTEGER NOT NULL,signer_address TEXT NOT NULL,signature TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,arguments_json TEXT,result TEXT,risk_tier TEXT,approval_state TEXT,created_at TEXT NOT NULL)''')


def audit(event_type: str, entity_type: str, entity_id: Optional[str], arguments: Dict[str, Any], result: str, risk_tier: str = 'low', approval_state: str = 'not_required') -> None:
    with connect() as conn:
        conn.execute('INSERT INTO audit_events (event_type, entity_type, entity_id, arguments_json, result, risk_tier, approval_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (event_type, entity_type, entity_id, json.dumps(arguments, ensure_ascii=False), result, risk_tier, approval_state, utcnow()))


def row_to_task(row: sqlite3.Row) -> TaskOut:
    return TaskOut(id=row['id'], title=row['title'], prompt=row['prompt'], interval_minutes=row['interval_minutes'], max_results=row['max_results'], notify=bool(row['notify']), enabled=bool(row['enabled']), created_at=row['created_at'], updated_at=row['updated_at'], last_run_at=row['last_run_at'], last_status=row['last_status'], last_report_path=row['last_report_path'])


def create_task(req: TaskCreate) -> TaskOut:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute('INSERT INTO tasks (title, prompt, interval_minutes, max_results, notify, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.title, req.prompt, req.interval_minutes, req.max_results, int(req.notify), int(req.enabled), now, now))
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (int(cur.lastrowid),)).fetchone()
        return row_to_task(row)


def list_tasks() -> List[TaskOut]:
    with connect() as conn:
        return [row_to_task(row) for row in conn.execute('SELECT * FROM tasks ORDER BY id DESC').fetchall()]


def get_task(task_id: int) -> Optional[TaskOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        return row_to_task(row) if row else None


def set_task_result(task_id: int, status: str, report_path: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('UPDATE tasks SET last_run_at=?, last_status=?, last_report_path=?, updated_at=? WHERE id=?', (utcnow(), status, report_path, utcnow(), task_id))


def record_run(task_id: Optional[int], title: str, prompt: str, status: str, report_path: Optional[str], sources: List[Dict[str, Any]], error: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO runs (task_id, title, prompt, status, report_path, sources_json, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (task_id, title, prompt, status, report_path, json.dumps(sources, ensure_ascii=False), error, utcnow()))


def row_to_wallet_monitor(row: sqlite3.Row) -> WalletMonitorOut:
    return WalletMonitorOut(id=row['id'], label=row['label'], chain=row['chain'], address=row['address'], rpc_url=row['rpc_url'], poll_minutes=row['poll_minutes'], alert_on_change=bool(row['alert_on_change']), enabled=bool(row['enabled']), created_at=row['created_at'], updated_at=row['updated_at'], last_checked_at=row['last_checked_at'], last_block=row['last_block'], last_balance_wei=row['last_balance_wei'], last_balance_native=row['last_balance_native'], last_status=row['last_status'], last_error=row['last_error'])


def create_wallet_monitor(req: WalletMonitorCreate) -> WalletMonitorOut:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute('INSERT INTO wallet_monitors (label, chain, address, rpc_url, poll_minutes, alert_on_change, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.label, req.chain, req.address, req.rpc_url, req.poll_minutes, int(req.alert_on_change), int(req.enabled), now, now))
        row = conn.execute('SELECT * FROM wallet_monitors WHERE id=?', (int(cur.lastrowid),)).fetchone()
        return row_to_wallet_monitor(row)


def list_wallet_monitors() -> List[WalletMonitorOut]:
    with connect() as conn:
        return [row_to_wallet_monitor(row) for row in conn.execute('SELECT * FROM wallet_monitors ORDER BY id DESC').fetchall()]


def get_wallet_monitor(monitor_id: int) -> Optional[WalletMonitorOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM wallet_monitors WHERE id=?', (monitor_id,)).fetchone()
        return row_to_wallet_monitor(row) if row else None


def update_wallet_monitor_state(monitor_id: int, *, status: str, block_number: Optional[int], balance_wei: Optional[str], balance_native: Optional[str], error: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('UPDATE wallet_monitors SET last_checked_at=?, last_block=?, last_balance_wei=?, last_balance_native=?, last_status=?, last_error=?, updated_at=? WHERE id=?', (utcnow(), block_number, balance_wei, balance_native, status, error, utcnow(), monitor_id))


def record_wallet_alert(monitor_id: int, event_type: str, message: str, previous_balance_wei: Optional[str], current_balance_wei: Optional[str], block_number: Optional[int]) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO wallet_alerts (monitor_id, event_type, message, previous_balance_wei, current_balance_wei, block_number, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (monitor_id, event_type, message, previous_balance_wei, current_balance_wei, block_number, utcnow()))


def list_wallet_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM wallet_alerts ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]


def row_to_safe(row: sqlite3.Row) -> SafeRegistryOut:
    return SafeRegistryOut(id=row['id'], name=row['name'], chain=row['chain'], safe_address=row['safe_address'], owners_json=row['owners_json'], threshold=row['threshold'], daily_limit_native=row['daily_limit_native'], single_tx_limit_native=row['single_tx_limit_native'], enabled=bool(row['enabled']), created_at=row['created_at'], updated_at=row['updated_at'])


def create_safe(req: SafeRegistryCreate) -> SafeRegistryOut:
    now = utcnow()
    owners_json = json.dumps(req.owners, ensure_ascii=False)
    with connect() as conn:
        cur = conn.execute('INSERT INTO safe_registry (name, chain, safe_address, owners_json, threshold, daily_limit_native, single_tx_limit_native, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.name, req.chain, req.safe_address, owners_json, req.threshold, req.daily_limit_native, req.single_tx_limit_native, int(req.enabled), now, now))
        row = conn.execute('SELECT * FROM safe_registry WHERE id=?', (int(cur.lastrowid),)).fetchone()
        audit('create_safe', 'safe', str(row['id']), {'name': req.name, 'chain': req.chain, 'safe_address': req.safe_address}, 'success')
        return row_to_safe(row)


def list_safes() -> List[SafeRegistryOut]:
    with connect() as conn:
        return [row_to_safe(row) for row in conn.execute('SELECT * FROM safe_registry ORDER BY id DESC').fetchall()]


def get_safe(safe_id: int) -> Optional[SafeRegistryOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM safe_registry WHERE id=?', (safe_id,)).fetchone()
        return row_to_safe(row) if row else None


def row_to_tx(row: sqlite3.Row) -> SafeTxDraftOut:
    return SafeTxDraftOut(id=row['id'], safe_id=row['safe_id'], title=row['title'], to_address=row['to_address'], value_native=row['value_native'], token_address=row['token_address'], calldata=row['calldata'], operation=row['operation'], risk_tier=row['risk_tier'], approval_state=row['approval_state'], safe_tx_hash=row['safe_tx_hash'], execution_tx_hash=row['execution_tx_hash'], risk_note=row['risk_note'], created_at=row['created_at'], updated_at=row['updated_at'])


def create_safe_tx(req: SafeTxDraftCreate, risk_tier: str, approval_state: str, risk_note: str) -> SafeTxDraftOut:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute('INSERT INTO safe_tx_drafts (safe_id, title, to_address, value_native, token_address, calldata, operation, risk_tier, approval_state, risk_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (req.safe_id, req.title, req.to_address, req.value_native, req.token_address, req.calldata, req.operation, risk_tier, approval_state, risk_note, now, now))
        row = conn.execute('SELECT * FROM safe_tx_drafts WHERE id=?', (int(cur.lastrowid),)).fetchone()
        audit('create_safe_tx_draft', 'safe_tx', str(row['id']), {'safe_id': req.safe_id, 'to': req.to_address, 'value_native': req.value_native}, 'success', risk_tier, approval_state)
        return row_to_tx(row)


def list_safe_txs() -> List[SafeTxDraftOut]:
    with connect() as conn:
        return [row_to_tx(row) for row in conn.execute('SELECT * FROM safe_tx_drafts ORDER BY id DESC').fetchall()]


def get_safe_tx(tx_id: int) -> Optional[SafeTxDraftOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM safe_tx_drafts WHERE id=?', (tx_id,)).fetchone()
        return row_to_tx(row) if row else None


def set_tx_approval(tx_id: int, approval_state: str) -> None:
    with connect() as conn:
        conn.execute('UPDATE safe_tx_drafts SET approval_state=?, updated_at=? WHERE id=?', (approval_state, utcnow(), tx_id))


def record_approval(tx_id: int, operator: str, decision: str, note: str) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO approvals (tx_id, operator, decision, note, created_at) VALUES (?, ?, ?, ?, ?)', (tx_id, operator, decision, note, utcnow()))
        audit('approval_decision', 'safe_tx', str(tx_id), {'operator': operator, 'decision': decision, 'note': note}, 'success', 'high', decision)


def record_signature(tx_id: int, signer_address: str, signature: str) -> None:
    with connect() as conn:
        conn.execute('INSERT INTO signatures (tx_id, signer_address, signature, created_at) VALUES (?, ?, ?, ?)', (tx_id, signer_address, signature, utcnow()))
        audit('record_signature', 'safe_tx', str(tx_id), {'signer_address': signer_address}, 'success', 'high', 'approved')


def mark_tx_executed(tx_id: int, execution_tx_hash: str) -> None:
    with connect() as conn:
        conn.execute('UPDATE safe_tx_drafts SET execution_tx_hash=?, approval_state=?, updated_at=? WHERE id=?', (execution_tx_hash, 'executed', utcnow(), tx_id))
        audit('mark_executed', 'safe_tx', str(tx_id), {'execution_tx_hash': execution_tx_hash}, 'success', 'high', 'executed')


def list_audit_events(limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
