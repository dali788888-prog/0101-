from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from app import db
from app.config import get_settings
from app.scheduler import HermesScheduler
from app.schemas import TronPermissionDraftCreate, TronPermissionDraftOut, TronPermissionExecutionMark
from app.tron_ops import approve_tron_permission_draft, create_tron_permission_draft, get_tron_permission_payload, list_tron_permission_drafts, mark_tron_permission_executed

from app.asset_os import router as asset_os_router
from app.asset_ext import router as asset_ext_router
from app.quant_bot import router as quant_bot_router
from app.quant_ext import router as quant_ext_router
from app.quant_market import router as quant_market_router
from app.quant_live_predict import router as quant_live_predict_router
from app.quant_emergency import router as quant_emergency_router
from app.rwa_mine import router as rwa_mine_router
from app.rwa_scaffold import router as rwa_scaffold_router
from app.rwa_codegen import router as rwa_codegen_router
from app.rwa_quality import router as rwa_quality_router
from app.rwa_fixit import router as rwa_fixit_router
from app.commercial_os import router as commercial_os_router
from app.operator_chat import router as operator_chat_router
from app.agent_runs import router as agent_runs_router
from app.exchange_market import router as exchange_market_router
from app.strategy_signals import router as strategy_signals_router
from app.trade_readiness import router as trade_readiness_router
from app.trade_lifecycle import router as trade_lifecycle_router
from app.paper_trading import router as paper_trading_router
from app.portfolio_risk import router as portfolio_risk_router
from app.release_gate import router as release_gate_router
from app.ops_automation import router as ops_automation_router
from app.ops_workflow import router as ops_workflow_router
from app.diagnostics_center import router as diagnostics_center_router
from app.system_map import router as system_map_router
from app.acceptance_center import router as acceptance_center_router
from app.system_selftest import router as system_selftest_router

settings = get_settings()
scheduler = HermesScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
for router in [
    asset_os_router, asset_ext_router, quant_bot_router, quant_ext_router, quant_market_router,
    quant_live_predict_router, quant_emergency_router, rwa_mine_router, rwa_scaffold_router,
    rwa_codegen_router, rwa_quality_router, rwa_fixit_router, commercial_os_router,
    operator_chat_router, agent_runs_router, exchange_market_router, strategy_signals_router,
    trade_readiness_router, trade_lifecycle_router, paper_trading_router, portfolio_risk_router,
    release_gate_router, ops_automation_router, ops_workflow_router, diagnostics_center_router,
    system_map_router, acceptance_center_router, system_selftest_router,
]:
    app.include_router(router)


@app.get('/health')
def health() -> dict:
    return {
        'status': 'ok',
        'app': settings.app_name,
        'search_provider': settings.search_provider,
        'model': settings.ollama_model,
        'version': '20.3-full-system-selftest',
    }


def html_file(name: str, fallback: str) -> str:
    path = Path(__file__).with_name(name)
    return path.read_text(encoding='utf-8') if path.exists() else fallback


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    return html_file('home_ui_v202.html', '<h1>Hermes v20.2 UI file not found.</h1>')


@app.get('/legacy-ui', response_class=HTMLResponse)
def legacy_ui() -> str:
    return '<h1>Legacy UI disabled</h1><p>已取消旧版入口，请使用首页侧边栏。</p>'


_UI_FILES = {
    '/acceptance-ui': 'acceptance_ui.html',
    '/diagnostics-ui': 'diagnostics_ui.html',
    '/system-map-ui': 'system_map_ui.html',
    '/ops-automation-ui': 'ops_automation_ui.html',
    '/release-gate-ui': 'release_gate_ui.html',
    '/portfolio-risk-ui': 'portfolio_risk_ui.html',
    '/paper-trading-ui': 'paper_trading_ui.html',
    '/asset-os-ui': 'asset_os_ui.html',
    '/tron-ui': 'tron_ui.html',
    '/quant-ui': 'quant_ui.html',
    '/quant-risk-ui': 'quant_risk_ui.html',
    '/rwa-mine-ui': 'rwa_mine_ui.html',
    '/rwa-scaffold-ui': 'rwa_scaffold_ui.html',
    '/commercial-os-ui': 'commercial_os_ui.html',
    '/market-ws-ui': 'market_ws_ui.html',
    '/market-matrix-ui': 'market_matrix_ui.html',
    '/strategy-signals-ui': 'strategy_signals_ui.html',
    '/signal-workspace-ui': 'signal_workspace_ui.html',
    '/trade-readiness-ui': 'trade_readiness_ui_v168.html',
    '/trade-lifecycle-ui': 'trade_lifecycle_ui.html',
    '/trade-readiness-v167-ui': 'trade_readiness_ui_v167.html',
    '/trade-readiness-v165-ui': 'trade_readiness_ui.html',
}


def make_ui_route(file_name: str):
    def route() -> str:
        return html_file(file_name, f'<h1>{file_name} not found.</h1>')
    return route


for path, file_name in _UI_FILES.items():
    app.add_api_route(path, make_ui_route(file_name), methods=['GET'], response_class=HTMLResponse)


@app.post('/tron/permissions', response_model=TronPermissionDraftOut)
def create_tron_permission(req: TronPermissionDraftCreate) -> TronPermissionDraftOut:
    try:
        return create_tron_permission_draft(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/tron/permissions', response_model=List[TronPermissionDraftOut])
def tron_permissions() -> List[TronPermissionDraftOut]:
    return list_tron_permission_drafts()


@app.get('/tron/permissions/{draft_id}/payload')
def tron_permission_payload(draft_id: int) -> dict:
    try:
        return get_tron_permission_payload(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/tron/permissions/{draft_id}/approve')
def approve_tron_permission(draft_id: int, decision: str = 'approved', operator: str = 'local-operator', note: str = '') -> dict:
    try:
        return approve_tron_permission_draft(draft_id, decision, operator, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/tron/permissions/mark-executed')
def mark_tron_permission_execution(req: TronPermissionExecutionMark) -> dict:
    try:
        return mark_tron_permission_executed(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/audit-events')
def audit_events(limit: int = 100) -> list[dict]:
    return db.list_audit_events(limit=limit)


@app.get('/reports')
def reports() -> list[dict]:
    folder = Path(settings.report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    return [{'id': p.name, 'path': str(p), 'size': p.stat().st_size} for p in sorted(folder.glob('*.md'), reverse=True)]


@app.get('/reports/{report_id}', response_class=PlainTextResponse)
def report(report_id: str) -> str:
    if '/' in report_id or '..' in report_id:
        raise HTTPException(status_code=400, detail='Invalid report id')
    path = Path(settings.report_dir) / report_id
    if not path.exists():
        raise HTTPException(status_code=404, detail='Report not found')
    return path.read_text(encoding='utf-8')
