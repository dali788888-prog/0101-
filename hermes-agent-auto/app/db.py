from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.schemas import TaskCreate, TaskOut, WalletMonitorCreate, WalletMonitorOut


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
        conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            max_results INTEGER NOT NULL,
            notify INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_run_at TEXT,
            last_status TEXT,
            last_report_path TEXT
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            title TEXT NOT NULL,
            prompt TEXT NOT NULL,
            status TEXT NOT NULL,
            report_path TEXT,
            sources_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS wallet_monitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            chain TEXT NOT NULL,
            address TEXT NOT NULL,
            rpc_url TEXT,
            poll_minutes INTEGER NOT NULL DEFAULT 5,
            alert_on_change INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_checked_at TEXT,
            last_block INTEGER,
            last_balance_wei TEXT,
            last_balance_native TEXT,
            last_status TEXT,
            last_error TEXT
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS wallet_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitor_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            previous_balance_wei TEXT,
            current_balance_wei TEXT,
            block_number INTEGER,
            created_at TEXT NOT NULL
        )
        ''')


def row_to_task(row: sqlite3.Row) -> TaskOut:
    return TaskOut(
        id=row['id'], title=row['title'], prompt=row['prompt'],
        interval_minutes=row['interval_minutes'], max_results=row['max_results'],
        notify=bool(row['notify']), enabled=bool(row['enabled']),
        created_at=row['created_at'], updated_at=row['updated_at'],
        last_run_at=row['last_run_at'], last_status=row['last_status'],
        last_report_path=row['last_report_path'],
    )


def create_task(req: TaskCreate) -> TaskOut:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute('''
        INSERT INTO tasks (title, prompt, interval_minutes, max_results, notify, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (req.title, req.prompt, req.interval_minutes, req.max_results, int(req.notify), int(req.enabled), now, now))
        task_id = int(cur.lastrowid)
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        return row_to_task(row)


def list_tasks() -> List[TaskOut]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM tasks ORDER BY id DESC').fetchall()
        return [row_to_task(row) for row in rows]


def get_task(task_id: int) -> Optional[TaskOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        return row_to_task(row) if row else None


def set_task_result(task_id: int, status: str, report_path: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('UPDATE tasks SET last_run_at=?, last_status=?, last_report_path=?, updated_at=? WHERE id=?',
                     (utcnow(), status, report_path, utcnow(), task_id))


def record_run(task_id: Optional[int], title: str, prompt: str, status: str, report_path: Optional[str], sources: List[Dict[str, Any]], error: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('''
        INSERT INTO runs (task_id, title, prompt, status, report_path, sources_json, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (task_id, title, prompt, status, report_path, json.dumps(sources, ensure_ascii=False), error, utcnow()))


def row_to_wallet_monitor(row: sqlite3.Row) -> WalletMonitorOut:
    return WalletMonitorOut(
        id=row['id'], label=row['label'], chain=row['chain'], address=row['address'], rpc_url=row['rpc_url'],
        poll_minutes=row['poll_minutes'], alert_on_change=bool(row['alert_on_change']), enabled=bool(row['enabled']),
        created_at=row['created_at'], updated_at=row['updated_at'], last_checked_at=row['last_checked_at'],
        last_block=row['last_block'], last_balance_wei=row['last_balance_wei'], last_balance_native=row['last_balance_native'],
        last_status=row['last_status'], last_error=row['last_error'],
    )


def create_wallet_monitor(req: WalletMonitorCreate) -> WalletMonitorOut:
    now = utcnow()
    with connect() as conn:
        cur = conn.execute('''
        INSERT INTO wallet_monitors (label, chain, address, rpc_url, poll_minutes, alert_on_change, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (req.label, req.chain, req.address, req.rpc_url, req.poll_minutes, int(req.alert_on_change), int(req.enabled), now, now))
        monitor_id = int(cur.lastrowid)
        row = conn.execute('SELECT * FROM wallet_monitors WHERE id=?', (monitor_id,)).fetchone()
        return row_to_wallet_monitor(row)


def list_wallet_monitors() -> List[WalletMonitorOut]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM wallet_monitors ORDER BY id DESC').fetchall()
        return [row_to_wallet_monitor(row) for row in rows]


def get_wallet_monitor(monitor_id: int) -> Optional[WalletMonitorOut]:
    with connect() as conn:
        row = conn.execute('SELECT * FROM wallet_monitors WHERE id=?', (monitor_id,)).fetchone()
        return row_to_wallet_monitor(row) if row else None


def update_wallet_monitor_state(monitor_id: int, *, status: str, block_number: Optional[int], balance_wei: Optional[str], balance_native: Optional[str], error: Optional[str]) -> None:
    with connect() as conn:
        conn.execute('''
        UPDATE wallet_monitors
        SET last_checked_at=?, last_block=?, last_balance_wei=?, last_balance_native=?, last_status=?, last_error=?, updated_at=?
        WHERE id=?
        ''', (utcnow(), block_number, balance_wei, balance_native, status, error, utcnow(), monitor_id))


def record_wallet_alert(monitor_id: int, event_type: str, message: str, previous_balance_wei: Optional[str], current_balance_wei: Optional[str], block_number: Optional[int]) -> None:
    with connect() as conn:
        conn.execute('''
        INSERT INTO wallet_alerts (monitor_id, event_type, message, previous_balance_wei, current_balance_wei, block_number, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (monitor_id, event_type, message, previous_balance_wei, current_balance_wei, block_number, utcnow()))


def list_wallet_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute('SELECT * FROM wallet_alerts ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row) for row in rows]
