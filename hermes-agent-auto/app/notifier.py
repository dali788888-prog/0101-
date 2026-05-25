from __future__ import annotations

from typing import Any, Dict
import requests

from app.config import get_settings


class Notifier:
    def __init__(self) -> None:
        self.settings = get_settings()

    def notify(self, title: str, body: str, meta: Dict[str, Any]) -> None:
        text = f'{title}\n\n{body[:3500]}'
        if self.settings.webhook_url:
            requests.post(self.settings.webhook_url, json={'title': title, 'body': body, 'meta': meta}, timeout=15)
        if self.settings.telegram_bot_token and self.settings.telegram_chat_id:
            url = f'https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage'
            requests.post(url, json={'chat_id': self.settings.telegram_chat_id, 'text': text}, timeout=15)
