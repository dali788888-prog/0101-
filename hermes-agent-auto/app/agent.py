from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.llm import OllamaClient
from app.notifier import Notifier
from app.schemas import AgentResult
from app.tools.report_writer import ReportWriter
from app.tools.web_reader import WebReader
from app.tools.web_search import WebSearch

BASE_SYSTEM_PROMPT = '''
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


def load_soul_core() -> str:
    candidates = [
        Path(__file__).resolve().parents[1] / 'SOUL.md',
        Path('/app/SOUL.md'),
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding='utf-8')
    return ''


def build_system_prompt() -> str:
    soul = load_soul_core().strip()
    if not soul:
        return BASE_SYSTEM_PROMPT
    return BASE_SYSTEM_PROMPT + '\n\n---\n\nRuntime identity and workflow overlay from SOUL.md:\n\n' + soul


ProgressCallback = Callable[[str, str, int, Dict[str, Any]], None]


class HermesAgent:
    def __init__(self) -> None:
        self.searcher = WebSearch()
        self.reader = WebReader()
        self.llm = OllamaClient()
        self.writer = ReportWriter()
        self.notifier = Notifier()
        self.system_prompt = build_system_prompt()

    def run(self, prompt: str, title: str = 'Hermes Agent Report', max_results: int = 8, notify: bool = False, progress_callback: Optional[ProgressCallback] = None) -> AgentResult:
        def emit(event_type: str, message: str, progress: int, data: Optional[Dict[str, Any]] = None) -> None:
            if progress_callback:
                progress_callback(event_type, message, progress, data or {})

        try:
            emit('start', f'Starting research: {title}', 3, {'tool': 'agent'})
            emit('tool_call', f'Calling search provider with max_results={max_results}', 10, {'tool': 'web_search', 'max_results': max_results})
            sources = self.searcher.search(prompt, max_results=max_results)
            emit('tool_result', f'Search returned {len(sources)} source(s)', 25, {'tool': 'web_search', 'sources_count': len(sources), 'sources': sources[:5]})

            pages: List[Dict[str, Any]] = []
            if sources:
                for index, source in enumerate(sources, start=1):
                    progress = 25 + int((index / max(len(sources), 1)) * 25)
                    emit('tool_call', f'Reading source {index}/{len(sources)}: {source.get("title") or source.get("url")}', progress, {'tool': 'web_reader', 'url': source.get('url')})
                    page = self.reader.read(source)
                    pages.append(page)
                    emit('tool_result', f'Read source {index}/{len(sources)}', progress, {'tool': 'web_reader', 'url': source.get('url'), 'read_error': page.get('read_error'), 'text_chars': len(page.get('text', ''))})
            else:
                emit('warn', 'No sources returned. The report will be based on task planning and available context.', 45, {'tool': 'web_search'})

            emit('tool_call', 'Calling Ollama model for report synthesis', 62, {'tool': 'ollama', 'model': self.llm.settings.ollama_model})
            report = self._summarize(prompt, pages)
            emit('tool_result', 'Model finished report synthesis', 82, {'tool': 'ollama', 'report_chars': len(report)})

            emit('tool_call', 'Writing Markdown report to storage', 88, {'tool': 'report_writer'})
            report = self._add_header(title, prompt, report, pages)
            path = self.writer.write(title, report)
            emit('tool_result', f'Report written: {path}', 94, {'tool': 'report_writer', 'report_path': path})

            if notify:
                emit('tool_call', 'Sending notification', 96, {'tool': 'notifier'})
                self.notifier.notify(f'Hermes Agent finished: {title}', report, {'report_path': path})
                emit('tool_result', 'Notification sent', 98, {'tool': 'notifier'})

            emit('success', 'Research run completed successfully', 100, {'report_path': path, 'sources_count': len(pages)})
            return AgentResult(title=title, prompt=prompt, status='success', report_markdown=report, report_path=path, sources=pages)
        except Exception as exc:  # noqa: BLE001
            emit('error', f'{type(exc).__name__}: {exc}', 100, {'error': str(exc)})
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
            {'role': 'system', 'content': self.system_prompt},
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
