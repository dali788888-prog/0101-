from __future__ import annotations

from typing import Dict, List
import requests

from app.config import get_settings


class OllamaClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def chat(self, messages: List[Dict[str, str]]) -> str:
        endpoint = self.settings.ollama_base_url.rstrip('/') + '/api/chat'
        payload = {
            'model': self.settings.ollama_model,
            'messages': messages,
            'stream': False,
            'options': {'temperature': self.settings.llm_temperature},
        }
        response = requests.request('POST', endpoint, json=payload, timeout=self.settings.ollama_timeout_seconds)
        response.raise_for_status()
        body = response.json()
        return body.get('message', {}).get('content', '') or body.get('response', '')
