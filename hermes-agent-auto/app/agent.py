from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.llm import OllamaClient
from app.notifier import Notifier
from app.schemas import AgentResult
from app.tools.report_writer import ReportWriter
from app.tools.web_reader import WebReader
from app.tools.web_search import WebSearch

SYSTEM_PROMPT = '''
You are Hermes Agent, a local-first autonomous research and reporting agent.

Mission:
- Execute legal public-source research tasks.
- Search the web when sources are provided.
- Read retrieved content.
- Produce concise, source-aware, practical reports in the user's requested language.

Safety boundary:
- Do not help with unauthorized intrusion, exploit execution, credential theft, bypassing access controls, malware, phishing, or exchange/system probing.
- For cybersecurity topics, stay at defensive, educational, and public documentation level.
- For finance/crypto topics, avoid claiming guaranteed profit. Flag risk and uncertainty.

Output format:
- Markdown.
- Start with an executive summary.
- Include key findings.
- Include practical next steps.
- Include source list with URLs when available.
'''.strip()


class HermesAgent:
    def __init__(self) -> None:
        self.searcher = WebSearch()
        self.reader = WebReader()
        self.llm = OllamaClient()
        self.writer = ReportWriter()
        self.notifier = Notifier()

    def run(self, prompt: str, title: str = 'Hermes Agent Report', max_results: int = 8, notify: bool = False) -> AgentResult:
        try:
            sources = self.searcher.search(prompt, max_results=max_results)
            pages = self.reader.read_many(sources) if sources else []
            report = self._summarize(prompt, pages)
            report = self._add_header(title, prompt, report, pages)
            path = self.writer.write(title, report)
            if notify:
                self.notifier.notify(f'Hermes Agent finished: {title}', report, {'report_path': path})
            return AgentResult(title=title, prompt=prompt, status='success', report_markdown=report, report_path=path, sources=pages)
        except Exception as exc:  # noqa: BLE001
            error_report = self._error_report(title, prompt, exc)
            path = self.writer.write(f'{title}-ERROR', error_report)
            if notify:
                self.notifier.notify(f'Hermes Agent error: {title}', error_report, {})
            return AgentResult(title=title, prompt=prompt, status='error', report_markdown=error_report, report_path=path, sources=[], error=str(exc))

    def _summarize(self, prompt: str, pages: List[Dict[str, Any]]) -> str:
        if pages:
            source_block = json.dumps([
                {'title': p.get('title'), 'url': p.get('url'), 'snippet': p.get('snippet'), 'text': p.get('text', '')[:3000], 'read_error': p.get('read_error')}
                for p in pages
            ], ensure_ascii=False, indent=2)
            user = f'''用户任务：\n{prompt}\n\n以下是公开网页搜索和读取到的资料。请基于这些资料写一份中文报告，不要编造未在资料中出现的确定性事实；不确定处要标注“待核实”。\n\n资料：\n{source_block}'''
        else:
            user = f'''用户任务：\n{prompt}\n\n当前没有配置搜索供应商或没有搜索结果。请基于用户提供的任务生成执行计划、资料清单、对接步骤和下一步配置建议，明确说明缺少实时互联网搜索结果。'''

        return self.llm.chat([
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user},
        ])

    def _add_header(self, title: str, prompt: str, report: str, pages: List[Dict[str, Any]]) -> str:
        created = datetime.now(timezone.utc).isoformat()
        source_lines = []
        for index, page in enumerate(pages, start=1):
            if page.get('url'):
                source_lines.append(f'{index}. [{page.get("title") or page.get("url")}]({page.get("url")})')
        sources_md = '\n'.join(source_lines) if source_lines else 'No external sources captured. Configure SEARCH_PROVIDER for live web search.'
        return f'''# {title}\n\n- Generated at: `{created}`\n- Prompt: `{prompt}`\n- Sources captured: `{len(pages)}`\n\n---\n\n{report}\n\n---\n\n## Sources\n\n{sources_md}\n'''

    def _error_report(self, title: str, prompt: str, exc: Exception) -> str:
        return f'''# {title} - ERROR\n\n- Generated at: `{datetime.now(timezone.utc).isoformat()}`\n- Prompt: `{prompt}`\n- Error: `{type(exc).__name__}: {exc}`\n\n## Fix checklist\n\n1. Check `.env` search provider settings.\n2. Check Ollama is running and `OLLAMA_MODEL` exists.\n3. Check network access from the container.\n4. Check logs with `docker compose logs -f hermes-agent`.\n'''
