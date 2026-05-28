from __future__ import annotations

import json
from datetime import datetime, timezone
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


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')


class ChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = 'operator-main'
    use_model: bool = True


class ChatClearRequest(BaseModel):
    session_id: str = 'operator-main'


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
            'content': '你是 Hermes Operator 私密会话窗口。默认中文，回答简洁、可执行、审计友好。不得索要或保存私钥、助记词、API Secret、身份证件原文。涉及真实交易、合约部署、资金划转、删除、广播、生产修改时必须提醒需要人工确认。',
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


@router.get('/messages')
def list_messages(session_id: str = 'operator-main', limit: int = 80) -> Dict[str, Any]:
    return {'status': 'ok', 'session_id': session_id, 'messages': chat_rows(session_id, limit=limit)}


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
    return {'status': status, 'session_id': req.session_id, 'user_message': user_row, 'assistant_message': assistant_row}


@router.post('/clear', dependencies=[Depends(require_key)])
def clear_chat(req: ChatClearRequest) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('DELETE FROM operator_chat_messages WHERE session_id=?', (req.session_id,))
        deleted = int(cur.rowcount or 0)
    db.audit('operator_chat_clear', 'operator_chat', req.session_id, {'deleted': deleted}, 'success', 'medium', 'not_required')
    return {'status': 'success', 'session_id': req.session_id, 'deleted': deleted}
