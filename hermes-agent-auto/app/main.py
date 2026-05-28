from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from app import db
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
from app.config import get_settings
from app.scheduler import HermesScheduler
from app.schemas import TronPermissionDraftCreate, TronPermissionDraftOut, TronPermissionExecutionMark
from app.tron_ops import approve_tron_permission_draft, create_tron_permission_draft, get_tron_permission_payload, list_tron_permission_drafts, mark_tron_permission_executed

settings = get_settings()
scheduler = HermesScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(asset_os_router)
app.include_router(asset_ext_router)
app.include_router(quant_bot_router)
app.include_router(quant_ext_router)
app.include_router(quant_market_router)
app.include_router(quant_live_predict_router)
app.include_router(quant_emergency_router)
app.include_router(rwa_mine_router)
app.include_router(rwa_scaffold_router)
app.include_router(rwa_codegen_router)
app.include_router(rwa_quality_router)
app.include_router(rwa_fixit_router)
app.include_router(commercial_os_router)
app.include_router(operator_chat_router)
app.include_router(agent_runs_router)
app.include_router(exchange_market_router)
app.include_router(strategy_signals_router)


@app.get('/health')
def health() -> dict:
    return {
        'status': 'ok',
        'app': settings.app_name,
        'search_provider': settings.search_provider,
        'model': settings.ollama_model,
        'version': '16.0-strategy-research-signal-center',
    }


def html_file(name: str, fallback: str) -> str:
    path = Path(__file__).with_name(name)
    return path.read_text(encoding='utf-8') if path.exists() else fallback


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    return html_file('home_ui_v158.html', '<h1>Hermes v15.8 UI file not found.</h1>')


@app.get('/legacy-ui', response_class=HTMLResponse)
def legacy_ui() -> str:
    return '<h1>Legacy UI disabled</h1><p>已取消旧版入口，请使用首页侧边栏。</p>'


@app.get('/asset-os-ui', response_class=HTMLResponse)
def asset_os_ui() -> str:
    return html_file('asset_os_ui.html', '<h1>AssetOps OS UI file not found.</h1>')


@app.get('/tron-ui', response_class=HTMLResponse)
def tron_ui() -> str:
    return html_file('tron_ui.html', '<h1>TRON UI file not found.</h1>')


@app.get('/quant-ui', response_class=HTMLResponse)
def quant_ui() -> str:
    return html_file('quant_ui.html', '<h1>Quant AI Robot UI file not found.</h1>')


@app.get('/quant-risk-ui', response_class=HTMLResponse)
def quant_risk_ui() -> str:
    return html_file('quant_risk_ui.html', '<h1>Quant Risk UI file not found.</h1>')


@app.get('/rwa-mine-ui', response_class=HTMLResponse)
def rwa_mine_ui() -> str:
    return html_file('rwa_mine_ui.html', '<h1>RWA Mine UI file not found.</h1>')


@app.get('/rwa-scaffold-ui', response_class=HTMLResponse)
def rwa_scaffold_ui() -> str:
    return html_file('rwa_scaffold_ui.html', '<h1>RWA Scaffold UI file not found.</h1>')


@app.get('/commercial-os-ui', response_class=HTMLResponse)
def commercial_os_ui() -> str:
    return html_file('commercial_os_ui.html', '<h1>Commercial OS UI file not found.</h1>')


@app.get('/market-ws-ui', response_class=HTMLResponse)
def market_ws_ui() -> str:
    return html_file('market_ws_ui.html', '<h1>Market WebSocket UI file not found.</h1>')


@app.get('/market-matrix-ui', response_class=HTMLResponse)
def market_matrix_ui() -> str:
    return html_file('market_matrix_ui.html', '<h1>Market Matrix UI file not found.</h1>')


@app.get('/strategy-signals-ui', response_class=HTMLResponse)
def strategy_signals_ui() -> str:
    return html_file('strategy_signals_ui.html', '<h1>Strategy Signals UI file not found.</h1>')


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
