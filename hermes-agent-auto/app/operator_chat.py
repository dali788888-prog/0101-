from __future__ import annotations

import json
from datetime import datetime, timezone, date
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/operator-chat', tags=['Operator Private Chat'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    return date.today().isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_workspace_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'daily',scheduled_time TEXT,priority TEXT NOT NULL DEFAULT 'P1',status TEXT NOT NULL DEFAULT 'todo',note TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_work_notes (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,content TEXT NOT NULL,tags TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_period_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,period TEXT NOT NULL,title TEXT NOT NULL,content TEXT NOT NULL,metrics_json TEXT NOT NULL,created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_finance_entries (id INTEGER PRIMARY KEY AUTOINCREMENT,entry_date TEXT NOT NULL,kind TEXT NOT NULL,amount REAL NOT NULL,currency TEXT NOT NULL DEFAULT 'USDT',category TEXT,description TEXT,note TEXT,created_at TEXT NOT NULL)''')


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = 'operator-main'
    use_model: bool = True


class ChatClearRequest(BaseModel):
    session_id: str = 'operator-main'


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    scope: str = Field(default='daily', pattern='^(daily|weekly|monthly|custom)$')
    scheduled_time: str = ''
    priority: str = Field(default='P1', pattern='^(P0|P1|P2)$')
    status: str = Field(default='todo', pattern='^(todo|doing|done|blocked)$')
    note: str = ''


class TaskUpdate(BaseModel):
    status: str = Field(default='todo', pattern='^(todo|doing|done|blocked)$')
    note: str = ''


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)
    tags: str = ''


class ReportGenerateRequest(BaseModel):
    period: str = Field(default='daily', pattern='^(daily|weekly|monthly)$')
    title: str = ''


class FinanceEntryCreate(BaseModel):
    kind: str = Field(pattern='^(income|expense|profit|loss|adjustment)$')
    amount: float
    currency: str = 'USDT'
    category: str = ''
    description: str = ''
    note: str = ''
    entry_date: str = Field(default_factory=today)


def chat_rows(session_id: str, limit: int = 80) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM operator_chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?', (session_id, limit)).fetchall()]
    return list(reversed(rows))


def insert_message(session_id: str, role: str, content: str, model: str = '', status: str = 'ok') -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', (session_id, role, content, model, status, now()))
        row = conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(row)


def model_reply(session_id: str, user_message: str) -> tuple[str, str]:
    settings = get_settings()
    history = chat_rows(session_id, limit=20)
    messages = [
        {
            'role': 'system',
            'content': '你是 Hermes Operator 私密会话窗口。默认中文，回答简洁、可执行、审计友好。不得索要或保存私钥、助记词、API Secret、身份证件原文。涉及真实交易、合约部署、资金划转、删除、广播、生产修改时必须提醒需要人工确认。对话记录受到保护，禁止清空或删除。',
        }
    ]
    for item in history:
        role = 'assistant' if item['role'] == 'assistant' else 'user'
        messages.append({'role': role, 'content': item['content']})
    messages.append({'role': 'user', 'content': user_message})
    try:
        resp = requests.post(
            f'{settings.ollama_base_url.rstrip("/")}/api/chat',
            json={
                'model': settings.ollama_model,
                'messages': messages,
                'stream': False,
                'options': {'temperature': settings.llm_temperature, 'num_predict': settings.llm_max_tokens},
            },
            timeout=settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get('message', {}).get('content') or data.get('response') or ''
        if not content.strip():
            content = '模型返回为空。请检查 Ollama 模型状态。'
        return content.strip(), 'ok'
    except Exception as exc:
        fallback = f'已记录你的消息，但本地模型暂时不可用：{exc}\n\n你可以继续在当前 ChatGPT 对话里让我处理，或检查 Ollama 服务与模型配置。'
        return fallback, 'model_error'


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def one(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def finance_summary() -> Dict[str, Any]:
    entries = rows('SELECT * FROM operator_finance_entries ORDER BY entry_date DESC,id DESC LIMIT 5000')
    income = sum(float(e['amount']) for e in entries if e['kind'] in {'income', 'profit'})
    expense = sum(float(e['amount']) for e in entries if e['kind'] in {'expense', 'loss'})
    adjustment = sum(float(e['amount']) for e in entries if e['kind'] == 'adjustment')
    net = income - expense + adjustment
    today_entries = [e for e in entries if e['entry_date'] == today()]
    today_income = sum(float(e['amount']) for e in today_entries if e['kind'] in {'income', 'profit'})
    today_expense = sum(float(e['amount']) for e in today_entries if e['kind'] in {'expense', 'loss'})
    return {
        'currency': entries[0]['currency'] if entries else 'USDT',
        'income_total': round(income, 8),
        'expense_total': round(expense, 8),
        'adjustment_total': round(adjustment, 8),
        'net_remaining': round(net, 8),
        'today_income': round(today_income, 8),
        'today_expense': round(today_expense, 8),
        'today_net': round(today_income - today_expense, 8),
        'entry_count': len(entries),
    }


@router.get('/messages')
def list_messages(session_id: str = 'operator-main', limit: int = 80) -> Dict[str, Any]:
    return {'status': 'ok', 'session_id': session_id, 'messages': chat_rows(session_id, limit=limit), 'record_protection': 'enabled_no_delete'}


@router.post('/send', dependencies=[Depends(require_key)])
def send_message(req: ChatSendRequest) -> Dict[str, Any]:
    ensure_tables()
    user_row = insert_message(req.session_id, 'operator', req.message)
    if req.use_model:
        content, status = model_reply(req.session_id, req.message)
    else:
        content, status = '已收到，消息已进入 Operator 私密会话记录。', 'ok'
    assistant_row = insert_message(req.session_id, 'assistant', content, get_settings().ollama_model, status)
    db.audit('operator_chat_send', 'operator_chat', req.session_id, {'message_len': len(req.message), 'use_model': req.use_model}, status, 'low', 'not_required')
    return {'status': status, 'session_id': req.session_id, 'user_message': user_row, 'assistant_message': assistant_row, 'record_protection': 'enabled_no_delete'}


@router.post('/clear', dependencies=[Depends(require_key)])
def clear_chat(req: ChatClearRequest) -> Dict[str, Any]:
    db.audit('operator_chat_clear_blocked', 'operator_chat', req.session_id, {'reason': 'record_protection_enabled'}, 'blocked', 'medium', 'not_required')
    raise HTTPException(status_code=403, detail='对话记录保护已开启：禁止清空或删除任何对话记录。')


@router.post('/workspace/bootstrap', dependencies=[Depends(require_key)])
def workspace_bootstrap() -> Dict[str, Any]:
    ensure_tables()
    defaults = [
        ('09:00', '检查首页状态卡 / Launch Gate / 风险快照', 'daily', 'P0'),
        ('10:00', '执行资产与钱包监控复盘', 'daily', 'P1'),
        ('14:00', '检查 RWA Mine Quality / FixIt / Codegen 进度', 'daily', 'P1'),
        ('18:00', '记录财务收入/支出/盈亏并生成日结', 'daily', 'P0'),
        ('周日 20:00', '生成周总结与数据复盘', 'weekly', 'P1'),
        ('每月最后一天 20:00', '生成月总结、财务总表与风险复盘', 'monthly', 'P1'),
    ]
    created = 0
    with db.connect() as conn:
        for scheduled_time, title, scope, priority in defaults:
            exists = conn.execute('SELECT id FROM operator_workspace_tasks WHERE title=? AND scope=?', (title, scope)).fetchone()
            if not exists:
                ts = now()
                conn.execute('INSERT INTO operator_workspace_tasks (title,scope,scheduled_time,priority,status,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (title, scope, scheduled_time, priority, 'todo', 'auto bootstrap', ts, ts))
                created += 1
    db.audit('operator_workspace_bootstrap', 'operator_workspace', 'default', {'created': created}, 'success', 'low', 'not_required')
    return {'status': 'success', 'created': created}


@router.get('/workspace/tasks')
def list_tasks(scope: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    clauses = ['1=1']
    args: list[Any] = []
    if scope:
        clauses.append('scope=?'); args.append(scope)
    if status:
        clauses.append('status=?'); args.append(status)
    return {'status': 'ok', 'tasks': rows(f"SELECT * FROM operator_workspace_tasks WHERE {' AND '.join(clauses)} ORDER BY scope,scheduled_time,id", tuple(args))}


@router.post('/workspace/tasks', dependencies=[Depends(require_key)])
def create_task(req: TaskCreate) -> Dict[str, Any]:
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_workspace_tasks (title,scope,scheduled_time,priority,status,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.title, req.scope, req.scheduled_time, req.priority, req.status, req.note, ts, ts))
        item = conn.execute('SELECT * FROM operator_workspace_tasks WHERE id=?', (int(cur.lastrowid),)).fetchone()
    db.audit('operator_task_create', 'operator_workspace_task', str(item['id']), req.model_dump(), 'success', 'low', 'not_required')
    return {'status': 'success', 'task': dict(item)}


@router.post('/workspace/tasks/{task_id}/status', dependencies=[Depends(require_key)])
def update_task(task_id: int, req: TaskUpdate) -> Dict[str, Any]:
    if not one('SELECT id FROM operator_workspace_tasks WHERE id=?', (task_id,)):
        raise HTTPException(status_code=404, detail='task not found')
    with db.connect() as conn:
        conn.execute('UPDATE operator_workspace_tasks SET status=?, note=?, updated_at=? WHERE id=?', (req.status, req.note, now(), task_id))
    return {'status': 'success', 'task': one('SELECT * FROM operator_workspace_tasks WHERE id=?', (task_id,))}


@router.get('/workspace/notes')
def list_notes(limit: int = 100) -> Dict[str, Any]:
    return {'status': 'ok', 'notes': rows('SELECT * FROM operator_work_notes ORDER BY id DESC LIMIT ?', (limit,))}


@router.post('/workspace/notes', dependencies=[Depends(require_key)])
def create_note(req: NoteCreate) -> Dict[str, Any]:
    ts = now()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_work_notes (title,content,tags,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (req.title, req.content, req.tags, ts, ts))
        item = conn.execute('SELECT * FROM operator_work_notes WHERE id=?', (int(cur.lastrowid),)).fetchone()
    db.audit('operator_note_create', 'operator_work_note', str(item['id']), {'title': req.title, 'tags': req.tags}, 'success', 'low', 'not_required')
    return {'status': 'success', 'note': dict(item)}


@router.post('/workspace/reports/generate', dependencies=[Depends(require_key)])
def generate_report(req: ReportGenerateRequest) -> Dict[str, Any]:
    ensure_tables()
    tasks = rows('SELECT * FROM operator_workspace_tasks ORDER BY id DESC LIMIT 200')
    notes = rows('SELECT * FROM operator_work_notes ORDER BY id DESC LIMIT 50')
    finances = finance_summary()
    open_tasks = [t for t in tasks if t['status'] != 'done']
    done_tasks = [t for t in tasks if t['status'] == 'done']
    title = req.title or f'{req.period} 总结报告与数据复盘 {now()}'
    content = '\n'.join([
        f'# {title}',
        '',
        f'- period: {req.period}',
        f'- created_at_utc: {now()}',
        f'- tasks_total: {len(tasks)}',
        f'- tasks_done: {len(done_tasks)}',
        f'- tasks_open: {len(open_tasks)}',
        f'- finance_today_net: {finances["today_net"]} {finances["currency"]}',
        f'- finance_total_remaining: {finances["net_remaining"]} {finances["currency"]}',
        '',
        '## 未完成任务',
        *[f'- [{t["priority"]}] {t["scheduled_time"] or "未排时"} {t["title"]} ({t["status"]})' for t in open_tasks[:30]],
        '',
        '## 最近工作笔记',
        *[f'- {n["title"]}: {n["content"][:160]}' for n in notes[:10]],
        '',
        '## 财务复盘',
        f'- 今日收入: {finances["today_income"]} {finances["currency"]}',
        f'- 今日支出/亏损: {finances["today_expense"]} {finances["currency"]}',
        f'- 今日净额: {finances["today_net"]} {finances["currency"]}',
        f'- 累计收入/盈利: {finances["income_total"]} {finances["currency"]}',
        f'- 累计支出/亏损: {finances["expense_total"]} {finances["currency"]}',
        f'- 当前总剩余: {finances["net_remaining"]} {finances["currency"]}',
        '',
        '## 下一步建议',
        '- 优先处理 P0 未完成任务和 Launch Gate 阻断项。',
        '- 每日收盘后记录财务流水并生成日结报告。',
    ])
    metrics = {'period': req.period, 'tasks_total': len(tasks), 'tasks_done': len(done_tasks), 'tasks_open': len(open_tasks), 'finance': finances}
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_period_reports (period,title,content,metrics_json,created_at) VALUES (?, ?, ?, ?, ?)', (req.period, title, content, jd(metrics), now()))
        item = conn.execute('SELECT * FROM operator_period_reports WHERE id=?', (int(cur.lastrowid),)).fetchone()
    insert_message('operator-main', 'assistant', content, get_settings().ollama_model, 'report_generated')
    return {'status': 'success', 'report': dict(item), 'metrics': metrics}


@router.get('/workspace/reports')
def list_reports(limit: int = 50) -> Dict[str, Any]:
    reports = rows('SELECT * FROM operator_period_reports ORDER BY id DESC LIMIT ?', (limit,))
    for r in reports:
        r['metrics'] = json.loads(r.pop('metrics_json'))
    return {'status': 'ok', 'reports': reports}


@router.post('/workspace/finance/entries', dependencies=[Depends(require_key)])
def create_finance_entry(req: FinanceEntryCreate) -> Dict[str, Any]:
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_finance_entries (entry_date,kind,amount,currency,category,description,note,created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (req.entry_date, req.kind, req.amount, req.currency, req.category, req.description, req.note, now()))
        item = conn.execute('SELECT * FROM operator_finance_entries WHERE id=?', (int(cur.lastrowid),)).fetchone()
    db.audit('operator_finance_entry_create', 'operator_finance', str(item['id']), req.model_dump(), 'success', 'low', 'not_required')
    return {'status': 'success', 'entry': dict(item), 'summary': finance_summary()}


@router.get('/workspace/finance/entries')
def list_finance_entries(limit: int = 200) -> Dict[str, Any]:
    return {'status': 'ok', 'entries': rows('SELECT * FROM operator_finance_entries ORDER BY entry_date DESC,id DESC LIMIT ?', (limit,)), 'summary': finance_summary()}


@router.get('/workspace/finance/summary')
def get_finance_summary() -> Dict[str, Any]:
    return {'status': 'ok', 'summary': finance_summary()}
