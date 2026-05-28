from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(prefix='/system-map', tags=['System Map Config Visualization'])


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mask_secret(value: Any) -> str:
    s = str(value or '')
    if not s:
        return ''
    if len(s) <= 6:
        return '***'
    return s[:3] + '***' + s[-3:]


def config_snapshot() -> Dict[str, Any]:
    settings = get_settings()
    return {
        'app': {
            'app_name': settings.app_name,
            'app_host': settings.app_host,
            'app_port': settings.app_port,
            'log_level': settings.log_level,
        },
        'llm': {
            'ollama_base_url': settings.ollama_base_url,
            'ollama_model': settings.ollama_model,
            'ollama_timeout_seconds': settings.ollama_timeout_seconds,
            'llm_temperature': settings.llm_temperature,
            'llm_max_tokens': settings.llm_max_tokens,
        },
        'search': {
            'search_provider': settings.search_provider,
            'max_search_results': settings.max_search_results,
            'brave_search_api_key': mask_secret(settings.brave_search_api_key),
            'tavily_api_key': mask_secret(settings.tavily_api_key),
            'serpapi_api_key': mask_secret(settings.serpapi_api_key),
            'searxng_url': settings.searxng_url,
        },
        'storage': {
            'database_url': settings.database_url.replace('/app/storage/hermes_agent.db', '/app/storage/***'),
            'report_dir': settings.report_dir,
        },
        'notifications': {
            'telegram_bot_token': mask_secret(settings.telegram_bot_token),
            'telegram_chat_id': mask_secret(settings.telegram_chat_id),
            'webhook_url': mask_secret(settings.webhook_url),
        },
        'live_gate': {
            'live_gate_enabled': settings.live_gate_enabled,
            'live_gate_max_order_usdt': settings.live_gate_max_order_usdt,
            'live_gate_daily_limit_usdt': settings.live_gate_daily_limit_usdt,
            'policy': 'display only; live trading remains manual handoff protected',
        },
        'safety': 'secrets are masked; config center is read-only',
    }


def system_nodes() -> List[Dict[str, Any]]:
    return [
        {'id': 'home', 'label': 'Unified Home', 'group': 'ui', 'path': '/'},
        {'id': 'ops_workflow', 'label': 'Ops Workflow', 'group': 'ops', 'path': '/ops-workflow'},
        {'id': 'ops_automation', 'label': 'Ops Automation', 'group': 'ops', 'path': '/ops-automation'},
        {'id': 'release_gate', 'label': 'Release Gate', 'group': 'ops', 'path': '/release-gate'},
        {'id': 'diagnostics', 'label': 'Diagnostics', 'group': 'ops', 'path': '/diagnostics'},
        {'id': 'system_map', 'label': 'System Map / Config', 'group': 'ops', 'path': '/system-map'},
        {'id': 'portfolio_risk', 'label': 'Portfolio Risk', 'group': 'risk', 'path': '/portfolio-risk'},
        {'id': 'paper_trading', 'label': 'Paper Trading', 'group': 'simulation', 'path': '/paper-trading'},
        {'id': 'trade_lifecycle', 'label': 'Trade Lifecycle', 'group': 'trade', 'path': '/trade-lifecycle'},
        {'id': 'trade_readiness', 'label': 'Trade Readiness', 'group': 'trade', 'path': '/trade-readiness'},
        {'id': 'strategy_signals', 'label': 'Strategy Signals', 'group': 'signal', 'path': '/strategy-signals'},
        {'id': 'exchange_market', 'label': 'Exchange Market', 'group': 'market', 'path': '/exchange-market'},
        {'id': 'operator_chat', 'label': 'Operator Chat', 'group': 'operator', 'path': '/operator-chat'},
        {'id': 'audit_events', 'label': 'Audit Events', 'group': 'audit', 'path': '/audit-events'},
        {'id': 'scheduler', 'label': 'Scheduler', 'group': 'automation', 'path': 'internal'},
        {'id': 'db', 'label': 'SQLite Storage', 'group': 'storage', 'path': 'internal'},
    ]


def system_edges() -> List[Dict[str, Any]]:
    return [
        {'from': 'home', 'to': 'ops_workflow', 'label': 'command'},
        {'from': 'home', 'to': 'release_gate', 'label': 'status'},
        {'from': 'home', 'to': 'portfolio_risk', 'label': 'risk status'},
        {'from': 'home', 'to': 'paper_trading', 'label': 'simulation status'},
        {'from': 'home', 'to': 'trade_lifecycle', 'label': 'review status'},
        {'from': 'home', 'to': 'diagnostics', 'label': 'health status'},
        {'from': 'ops_workflow', 'to': 'ops_automation', 'label': 'quick-run'},
        {'from': 'ops_workflow', 'to': 'release_gate', 'label': 'inspection'},
        {'from': 'ops_automation', 'to': 'release_gate', 'label': 'scheduled inspection'},
        {'from': 'ops_automation', 'to': 'operator_chat', 'label': 'alerts/report sync'},
        {'from': 'release_gate', 'to': 'trade_readiness', 'label': 'readiness checks'},
        {'from': 'release_gate', 'to': 'trade_lifecycle', 'label': 'lifecycle checks'},
        {'from': 'release_gate', 'to': 'paper_trading', 'label': 'paper metrics'},
        {'from': 'release_gate', 'to': 'portfolio_risk', 'label': 'risk budget'},
        {'from': 'diagnostics', 'to': 'release_gate', 'label': 'health'},
        {'from': 'diagnostics', 'to': 'ops_workflow', 'label': 'health'},
        {'from': 'diagnostics', 'to': 'portfolio_risk', 'label': 'health'},
        {'from': 'diagnostics', 'to': 'trade_lifecycle', 'label': 'health'},
        {'from': 'paper_trading', 'to': 'strategy_signals', 'label': 'signal simulation'},
        {'from': 'trade_readiness', 'to': 'strategy_signals', 'label': 'signal-to-ticket'},
        {'from': 'strategy_signals', 'to': 'exchange_market', 'label': 'market data'},
        {'from': 'portfolio_risk', 'to': 'paper_trading', 'label': 'paper PnL'},
        {'from': 'portfolio_risk', 'to': 'trade_lifecycle', 'label': 'manual journal context'},
        {'from': 'scheduler', 'to': 'ops_automation', 'label': 'reports/alerts'},
        {'from': 'scheduler', 'to': 'strategy_signals', 'label': 'analysis'},
        {'from': 'audit_events', 'to': 'db', 'label': 'persist'},
        {'from': 'ops_workflow', 'to': 'audit_events', 'label': 'audit'},
        {'from': 'release_gate', 'to': 'audit_events', 'label': 'audit'},
        {'from': 'diagnostics', 'to': 'audit_events', 'label': 'audit'},
    ]


def ui_routes() -> List[Dict[str, str]]:
    return [
        {'path': '/', 'title': 'Unified Home v19.6'},
        {'path': '/diagnostics-ui', 'title': 'Diagnostics UI'},
        {'path': '/system-map-ui', 'title': 'System Map / Config UI'},
        {'path': '/ops-automation-ui', 'title': 'Ops Automation UI'},
        {'path': '/release-gate-ui', 'title': 'Release Gate UI'},
        {'path': '/portfolio-risk-ui', 'title': 'Portfolio Risk UI'},
        {'path': '/paper-trading-ui', 'title': 'Paper Trading UI'},
        {'path': '/trade-lifecycle-ui', 'title': 'Trade Lifecycle UI'},
        {'path': '/trade-readiness-ui', 'title': 'Trade Readiness UI'},
        {'path': '/strategy-signals-ui', 'title': 'Strategy Signals UI'},
        {'path': '/market-ws-ui', 'title': 'Market WebSocket UI'},
        {'path': '/asset-os-ui', 'title': 'Asset OS UI'},
        {'path': '/commercial-os-ui', 'title': 'Commercial OS UI'},
    ]


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '19.6-system-map-config-visualization', 'features': ['system map', 'module graph', 'ui route catalog', 'masked config snapshot'], 'safety': 'read-only visualization; no secret exposure and no exchange order submission'}


@router.get('/graph')
def graph() -> Dict[str, Any]:
    nodes = system_nodes()
    edges = system_edges()
    return {'status': 'ok', 'version': '19.6-system-map-config-visualization', 'nodes': nodes, 'edges': edges, 'summary': {'nodes': len(nodes), 'edges': len(edges)}, 'time_utc': now()}


@router.get('/config')
def config() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '19.6-system-map-config-visualization', 'config': config_snapshot(), 'time_utc': now()}


@router.get('/ui-routes')
def routes() -> Dict[str, Any]:
    return {'status': 'ok', 'routes': ui_routes(), 'count': len(ui_routes()), 'time_utc': now()}


@router.get('/dashboard')
def dashboard() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '19.6-system-map-config-visualization', 'graph': graph(), 'config': config_snapshot(), 'ui_routes': ui_routes(), 'time_utc': now()}
