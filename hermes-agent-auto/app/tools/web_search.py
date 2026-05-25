from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional
import requests

from app.config import get_settings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ''
    source: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WebSearch:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': self.settings.user_agent})

    def search(self, query: str, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        max_results = max_results or self.settings.max_search_results
        provider = self.settings.search_provider.lower()
        if provider == 'none' or max_results <= 0:
            return []
        if provider == 'brave':
            return [r.to_dict() for r in self._brave(query, max_results)]
        if provider == 'tavily':
            return [r.to_dict() for r in self._tavily(query, max_results)]
        if provider == 'serpapi':
            return [r.to_dict() for r in self._serpapi(query, max_results)]
        if provider == 'searxng':
            return [r.to_dict() for r in self._searxng(query, max_results)]
        return []

    def _brave(self, query: str, max_results: int) -> List[SearchResult]:
        if not self.settings.brave_search_api_key:
            raise RuntimeError('BRAVE_SEARCH_API_KEY is required')
        resp = self.session.get('https://api.search.brave.com/res/v1/web/search', headers={'X-Subscription-Token': self.settings.brave_search_api_key}, params={'q': query, 'count': max_results, 'safesearch': 'moderate'}, timeout=self.settings.http_timeout_seconds)
        resp.raise_for_status()
        results = resp.json().get('web', {}).get('results', [])[:max_results]
        return [SearchResult(title=i.get('title', ''), url=i.get('url', ''), snippet=i.get('description', ''), source='brave') for i in results if i.get('url')]

    def _tavily(self, query: str, max_results: int) -> List[SearchResult]:
        if not self.settings.tavily_api_key:
            raise RuntimeError('TAVILY_API_KEY is required')
        resp = self.session.post('https://api.tavily.com/search', json={'api_key': self.settings.tavily_api_key, 'query': query, 'max_results': max_results, 'search_depth': 'basic'}, timeout=self.settings.http_timeout_seconds)
        resp.raise_for_status()
        results = resp.json().get('results', [])[:max_results]
        return [SearchResult(title=i.get('title', ''), url=i.get('url', ''), snippet=i.get('content', ''), source='tavily') for i in results if i.get('url')]

    def _serpapi(self, query: str, max_results: int) -> List[SearchResult]:
        if not self.settings.serpapi_api_key:
            raise RuntimeError('SERPAPI_API_KEY is required')
        resp = self.session.get('https://serpapi.com/search.json', params={'q': query, 'api_key': self.settings.serpapi_api_key, 'num': max_results, 'engine': 'google'}, timeout=self.settings.http_timeout_seconds)
        resp.raise_for_status()
        results = resp.json().get('organic_results', [])[:max_results]
        return [SearchResult(title=i.get('title', ''), url=i.get('link', ''), snippet=i.get('snippet', ''), source='serpapi') for i in results if i.get('link')]

    def _searxng(self, query: str, max_results: int) -> List[SearchResult]:
        resp = self.session.get(self.settings.searxng_url, params={'q': query, 'format': 'json', 'language': 'all', 'safesearch': 1}, timeout=self.settings.http_timeout_seconds)
        resp.raise_for_status()
        results = resp.json().get('results', [])[:max_results]
        return [SearchResult(title=i.get('title', ''), url=i.get('url', ''), snippet=i.get('content', ''), source='searxng') for i in results if i.get('url')]
