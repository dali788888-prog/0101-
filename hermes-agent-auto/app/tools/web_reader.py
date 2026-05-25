from __future__ import annotations

from typing import Any, Dict, List
import requests
import trafilatura
from bs4 import BeautifulSoup

from app.config import get_settings


class WebReader:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.settings.user_agent})

    def read_many(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pages: List[Dict[str, Any]] = []
        for source in sources:
            pages.append(self.read(source))
        return pages

    def read(self, source: Dict[str, Any]) -> Dict[str, Any]:
        url = source.get('url', '')
        page = dict(source)
        if not url:
            page['read_error'] = 'missing url'
            return page
        try:
            response = self.session.get(url, timeout=self.settings.http_timeout_seconds)
            response.raise_for_status()
            html = response.text
            extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
            if not extracted:
                soup = BeautifulSoup(html, 'html.parser')
                title = soup.title.string.strip() if soup.title and soup.title.string else page.get('title', '')
                text = ' '.join(soup.get_text(' ').split())
                page['title'] = page.get('title') or title
                page['text'] = text[:12000]
            else:
                page['text'] = extracted[:12000]
            page['status_code'] = response.status_code
        except Exception as exc:  # noqa: BLE001
            page['text'] = ''
            page['read_error'] = f'{type(exc).__name__}: {exc}'
        return page
