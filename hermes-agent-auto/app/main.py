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


@app.get('/health')
def health() -> dict:
    return {
        'status': 'ok',
        'app': settings.app_name,
        'search_provider': settings.search_provider,
        'model': settings.ollama_model,
        'version': '10.9-live-gate-prediction-alerts',
    }


def html_file(name: str, fallback: str) -> str:
    path = Path(__file__).with_name(name)
    return path.read_text(encoding='utf-8') if path.exists() else fallback


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    return html_file('home_ui.html', '<h1>Hermes AssetOps OS</h1><p>home_ui.html not found.</p>')


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
