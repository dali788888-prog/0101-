from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.schemas import TaskCreate, TaskOut


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
