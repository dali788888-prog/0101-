from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/system-selftest', tags=['Full System Self Test'])


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


class SelfTestRequest(BaseModel):
    level: str = Field(default='deep', pattern='^(quick|deep)$')
    include_network_market: bool = False
    include_protected_writes: bool = False
    sync_operator: bool = True


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS system_selftest_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,level TEXT NOT NULL,status TEXT NOT NULL,summary_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS operator_chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,model TEXT,status TEXT NOT NULL DEFAULT 'ok',created_at TEXT NOT NULL)''')


def safe(name: str, fn: Callable[[], Any], required: bool = True) -> Dict[str, Any]:
    try:
        data = fn()
        if isinstance(data, dict) and data.get('status') in {'error', 'failed'}:
            return {'name': name, 'status': 'fail' if required else 'warn', 'required': required, 'error': data.get('error') or data.get('detail'), 'data': data}
        return {'name': name, 'status': 'pass', 'required': required, 'data': data}
    except Exception as exc:
        return {'name': name, 'status': 'fail' if required else 'warn', 'required': required, 'error': str(exc)}


def file_exists(name: str) -> Dict[str, Any]:
    p = Path(__file__).with_name(name)
    return {'file': name, 'exists': p.exists(), 'size': p.stat().st_size if p.exists() else 0}


def home_ui_dom_check() -> Dict[str, Any]:
    p = Path(__file__).with_name('home_ui_v202.html')
    if not p.exists():
        return {'status': 'fail', 'error': 'home_ui_v202.html missing'}
    html = p.read_text(encoding='utf-8')
    required_ids = [
        'version', 'acceptanceState', 'maturityScore', 'requiredFails', 'rcWarnings', 'openAlerts',
        'gateState', 'gateBlockers', 'diagState', 'portfolioValue', 'paperPnl', 'winRate',
        'acceptanceItems', 'opsAlerts', 'diagModules', 'releaseChecks', 'portfolioWarnings', 'paperOrders',
        'lifeItems', 'signalList', 'chatMessages', 'chatInput', 'apiKeyInput', 'toolGrid', 'keyRow',
        'operatorAcceptance', 'operatorGate', 'operatorOps', 'operatorDiag', 'operatorLife', 'operatorSignal',
    ]
    missing_ids = [x for x in required_ids if f'id="{x}"' not in html]
    required_functions = [
        'loadAcceptance', 'loadOps', 'loadDiagnostics', 'loadRelease', 'loadPortfolio', 'loadPaper',
        'loadLifecycle', 'loadSignals', 'quickRun', 'runDiagnostics', 'createAcceptanceSnapshot',
        'sendChat', 'loadChat', 'showTasks', 'bootstrapTasks', 'genWorkspaceReport', 'financeSummary',
    ]
    missing_functions = [x for x in required_functions if f'function {x}' not in html and f'async function {x}' not in html]
    broken_references = []
    for ref in re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\.textContent', html):
        if ref not in required_ids and ref not in {'toolArrow', 'keyArrow', 'chatQuickOut', 'healthText'}:
            if f'id="{ref}"' not in html:
                broken_references.append(ref)
    return {'status': 'pass' if not missing_ids and not missing_functions and not broken_references else 'fail', 'missing_ids': missing_ids, 'missing_functions': missing_functions, 'broken_references': sorted(set(broken_references)), 'file_size': p.stat().st_size}


def ui_files_check() -> Dict[str, Any]:
    files = [
        'home_ui_v202.html', 'acceptance_ui.html', 'ops_automation_ui.html', 'diagnostics_ui.html',
        'system_map_ui.html', 'release_gate_ui.html', 'portfolio_risk_ui.html', 'paper_trading_ui.html',
        'trade_lifecycle_ui.html', 'trade_readiness_ui_v168.html', 'strategy_signals_ui.html',
        'signal_workspace_ui.html', 'market_ws_ui.html', 'market_matrix_ui.html', 'asset_os_ui.html',
        'tron_ui.html', 'quant_ui.html', 'quant_risk_ui.html', 'commercial_os_ui.html',
        'rwa_mine_ui.html', 'rwa_scaffold_ui.html',
    ]
    results = [file_exists(x) for x in files]
    missing = [x['file'] for x in results if not x['exists']]
    return {'status': 'pass' if not missing else 'fail', 'files': results, 'missing': missing}


def route_manifest() -> List[Dict[str, str]]:
    return [
        {'name': 'health', 'method': 'GET', 'path': '/health'},
        {'name': 'acceptance_dashboard', 'method': 'GET', 'path': '/acceptance/dashboard'},
        {'name': 'ops_workflow_dashboard', 'method': 'GET', 'path': '/ops-workflow/dashboard'},
        {'name': 'ops_automation_dashboard', 'method': 'GET', 'path': '/ops-automation/dashboard'},
        {'name': 'diagnostics_module_health', 'method': 'GET', 'path': '/diagnostics/module-health'},
        {'name': 'system_map_dashboard', 'method': 'GET', 'path': '/system-map/dashboard'},
        {'name': 'release_gate_dashboard', 'method': 'GET', 'path': '/release-gate/dashboard'},
        {'name': 'portfolio_risk_dashboard', 'method': 'GET', 'path': '/portfolio-risk/dashboard'},
        {'name': 'paper_trading_dashboard', 'method': 'GET', 'path': '/paper-trading/dashboard'},
        {'name': 'trade_readiness_status', 'method': 'GET', 'path': '/trade-readiness/status'},
        {'name': 'trade_lifecycle_dashboard', 'method': 'GET', 'path': '/trade-lifecycle/dashboard'},
        {'name': 'strategy_signals_summary', 'method': 'GET', 'path': '/strategy-signals/summary'},
        {'name': 'operator_chat_messages', 'method': 'GET', 'path': '/operator-chat/messages'},
        {'name': 'operator_tasks', 'method': 'GET', 'path': '/operator-chat/workspace/tasks'},
        {'name': 'operator_finance_summary', 'method': 'GET', 'path': '/operator-chat/workspace/finance/summary'},
        {'name': 'audit_events', 'method': 'GET', 'path': '/audit-events'},
    ]


def backend_checks(include_network_market: bool = False) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    checks.append(safe('db_connection', lambda: {'audit_sample': len(db.list_audit_events(limit=5))}))
    checks.append(safe('acceptance_dashboard', lambda: __import__('app.acceptance_center', fromlist=['acceptance_summary']).acceptance_summary('20.3-full-system-selftest')))
    checks.append(safe('ops_workflow_dashboard', lambda: __import__('app.ops_workflow', fromlist=['dashboard']).dashboard()))
    checks.append(safe('ops_automation_dashboard', lambda: __import__('app.ops_automation', fromlist=['dashboard']).dashboard()))
    checks.append(safe('diagnostics_module_health', lambda: __import__('app.diagnostics_center', fromlist=['module_health']).module_health()))
    checks.append(safe('system_map_dashboard', lambda: __import__('app.system_map', fromlist=['dashboard']).dashboard()))
    checks.append(safe('release_gate_summary', lambda: __import__('app.release_gate', fromlist=['gate_summary']).gate_summary('20.3-full-system-selftest')))
    checks.append(safe('portfolio_risk_dashboard', lambda: __import__('app.portfolio_risk', fromlist=['dashboard']).dashboard()))
    checks.append(safe('paper_trading_dashboard', lambda: __import__('app.paper_trading', fromlist=['dashboard']).dashboard()))
    checks.append(safe('trade_readiness_status', lambda: __import__('app.trade_readiness', fromlist=['status']).status()))
    checks.append(safe('trade_lifecycle_dashboard', lambda: __import__('app.trade_lifecycle', fromlist=['dashboard']).dashboard(limit=100)))
    checks.append(safe('strategy_signals_summary', lambda: __import__('app.strategy_signals', fromlist=['summary']).summary()))
    checks.append(safe('operator_chat_messages', lambda: __import__('app.operator_chat', fromlist=['list_messages']).list_messages()))
    checks.append(safe('operator_tasks', lambda: __import__('app.operator_chat', fromlist=['list_tasks']).list_tasks()))
    checks.append(safe('operator_finance_summary', lambda: __import__('app.operator_chat', fromlist=['get_finance_summary']).get_finance_summary()))
    if include_network_market:
        checks.append(safe('exchange_market_matrix_network', lambda: __import__('app.exchange_market', fromlist=['build_matrix', 'MatrixRequest']).build_matrix(__import__('app.exchange_market', fromlist=['MatrixRequest']).MatrixRequest()), required=False))
    return checks


def notify_operator(content: str) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO operator_chat_messages (session_id,role,content,model,status,created_at) VALUES (?, ?, ?, ?, ?, ?)', ('operator-main', 'assistant', content, get_settings().ollama_model, 'system_selftest', now()))
        row = conn.execute('SELECT * FROM operator_chat_messages WHERE id=?', (int(cur.lastrowid),)).fetchone()
    return dict(row)


def run_selftest(req: SelfTestRequest) -> Dict[str, Any]:
    ensure_tables()
    results: List[Dict[str, Any]] = []
    results.append({'name': 'ui_files', **ui_files_check(), 'required': True})
    results.append({'name': 'home_ui_dom', **home_ui_dom_check(), 'required': True})
    results.extend(backend_checks(include_network_market=req.include_network_market))
    failed = [x for x in results if x.get('status') == 'fail']
    warnings = [x for x in results if x.get('status') == 'warn']
    status = 'fail' if failed else 'warning' if warnings else 'pass'
    summary = {
        'status': status,
        'level': req.level,
        'total_checks': len(results),
        'passed': len([x for x in results if x.get('status') == 'pass']),
        'failed': len(failed),
        'warnings': len(warnings),
        'failed_checks': [x.get('name') for x in failed],
        'warning_checks': [x.get('name') for x in warnings],
        'results': results,
        'route_manifest': route_manifest(),
        'time_utc': now(),
        'safety': 'read-only self-test by default; no autonomous live trading',
    }
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO system_selftest_runs (level,status,summary_json,created_at) VALUES (?, ?, ?, ?)', (req.level, status, jd(summary), now()))
        run_id = int(cur.lastrowid)
    db.audit('system_selftest_run', 'system_selftest', str(run_id), {'status': status, 'failed': len(failed), 'warnings': len(warnings)}, 'success' if status != 'fail' else 'failed', 'high', status)
    message = None
    if req.sync_operator:
        message = notify_operator(f'系统自检 #{run_id}: {status}\n检查项: {len(results)}\n失败: {len(failed)}\n警告: {len(warnings)}\n失败项: {", ".join(summary["failed_checks"]) or "无"}')
    return {'status': status, 'run_id': run_id, 'summary': summary, 'message': message}


@router.get('/status')
def status() -> Dict[str, Any]:
    return {'status': 'ok', 'version': '20.3-full-system-selftest', 'features': ['ui asset check', 'home dom reference check', 'backend dashboard direct check', 'route manifest', 'operator notification'], 'safety': 'read-only by default'}


@router.get('/manifest')
def manifest() -> Dict[str, Any]:
    return {'status': 'ok', 'routes': route_manifest(), 'time_utc': now()}


@router.post('/run', dependencies=[Depends(require_key)])
def run(req: SelfTestRequest) -> Dict[str, Any]:
    return run_selftest(req)


@router.get('/runs')
def runs(limit: int = 50) -> Dict[str, Any]:
    ensure_tables()
    with db.connect() as conn:
        data = [dict(r) for r in conn.execute('SELECT * FROM system_selftest_runs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
    for x in data:
        try:
            x['summary'] = json.loads(x.pop('summary_json'))
        except Exception:
            x['summary'] = {}
    return {'status': 'ok', 'runs': data}
