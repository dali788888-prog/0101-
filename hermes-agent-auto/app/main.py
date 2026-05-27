from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from app import db
from app.agent import HermesAgent
from app.asset_os import router as asset_os_router
from app.config import get_settings
from app.runtime import run_store
from app.safe_ops import approve_tx, build_safe_tx_payload, create_tx_draft, mark_executed, record_signature, register_safe
from app.scheduler import HermesScheduler
from app.schemas import ApprovalRequest, AgentResult, MultisigPlanRequest, RunRequest, SafeExecutionMark, SafeRegistryCreate, SafeRegistryOut, SafeSignRequest, SafeTxDraftCreate, SafeTxDraftOut, TaskCreate, TaskOut, TronPermissionDraftCreate, TronPermissionDraftOut, TronPermissionExecutionMark, WalletMonitorCreate, WalletMonitorOut, WalletRefreshResult
from app.tron_ops import approve_tron_permission_draft, create_tron_permission_draft, get_tron_permission_payload, list_tron_permission_drafts, mark_tron_permission_executed
from app.wallets import create_multisig_plan, refresh_all_wallet_monitors, refresh_wallet_monitor_by_id

settings = get_settings()
scheduler = HermesScheduler()


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(asset_os_router)


@app.get('/health')
def health() -> dict:
    return {'status': 'ok', 'app': settings.app_name, 'search_provider': settings.search_provider, 'model': settings.ollama_model, 'version': '10.0-assetops-os'}


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    ui_path = Path(__file__).with_name('ui.html')
    if ui_path.exists():
        return ui_path.read_text(encoding='utf-8')
    return '<h1>Hermes Agent Auto Executor</h1><p>UI file not found.</p>'


@app.get('/asset-os-ui', response_class=HTMLResponse)
def asset_os_ui() -> str:
    ui_path = Path(__file__).with_name('asset_os_ui.html')
    if ui_path.exists():
        return ui_path.read_text(encoding='utf-8')
    return '<h1>AssetOps OS UI file not found.</h1>'


@app.get('/tron-ui', response_class=HTMLResponse)
def tron_ui() -> str:
    ui_path = Path(__file__).with_name('tron_ui.html')
    if ui_path.exists():
        return ui_path.read_text(encoding='utf-8')
    return '<h1>TRON UI file not found.</h1>'


def _run_with_events(run_id: str, title: str, prompt: str, max_results: int, notify: bool, task_id: Optional[int] = None) -> None:
    def progress(event_type: str, message: str, progress_value: int, data: dict) -> None:
        run_store.emit(run_id, event_type, message, progress=progress_value, data=data)

    result = HermesAgent().run(prompt, title=title, max_results=max_results, notify=notify, progress_callback=progress)
    db.record_run(task_id, title, prompt, result.status, result.report_path, result.sources, result.error)
    if task_id is not None:
        db.set_task_result(task_id, result.status, result.report_path)
    run_store.finish(run_id, result.status, report_path=result.report_path, error=result.error, sources_count=len(result.sources))


@app.post('/run', response_model=AgentResult, dependencies=[Depends(require_key)])
def run(req: RunRequest) -> AgentResult:
    run_id = run_store.create_run(req.title, req.prompt, kind='manual')
    result = HermesAgent().run(req.prompt, title=req.title, max_results=req.max_results, notify=req.notify, progress_callback=lambda event_type, message, progress, data: run_store.emit(run_id, event_type, message, progress=progress, data=data))
    db.record_run(None, req.title, req.prompt, result.status, result.report_path, result.sources, result.error)
    run_store.finish(run_id, result.status, report_path=result.report_path, error=result.error, sources_count=len(result.sources))
    return result


@app.post('/run_async', dependencies=[Depends(require_key)])
def run_async(req: RunRequest) -> dict:
    run_id = run_store.create_run(req.title, req.prompt, kind='manual')
    thread = threading.Thread(target=_run_with_events, args=(run_id, req.title, req.prompt, req.max_results, req.notify, None), daemon=True)
    thread.start()
    return {'run_id': run_id, 'status': 'queued'}


@app.post('/tasks', response_model=TaskOut, dependencies=[Depends(require_key)])
def create_task(req: TaskCreate) -> TaskOut:
    task = db.create_task(req)
    scheduler.add_task_job(task.id)
    if req.run_now:
        run_id = run_store.create_run(task.title, task.prompt, kind='scheduled', task_id=task.id)
        thread = threading.Thread(target=_run_with_events, args=(run_id, task.title, task.prompt, task.max_results, task.notify, task.id), daemon=True)
        thread.start()
    return task


@app.get('/tasks', response_model=List[TaskOut])
def tasks() -> List[TaskOut]:
    return db.list_tasks()


@app.get('/runs')
def runs(limit: int = 50) -> list[dict]:
    return run_store.list_runs(limit=limit)


@app.get('/runs/{run_id}')
def run_detail(run_id: str) -> dict:
    run = run_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail='Run not found')
    return run


@app.get('/events')
def events(run_id: Optional[str] = None):
    return StreamingResponse(run_store.sse_stream(run_id=run_id), media_type='text/event-stream')


@app.get('/events_snapshot')
def events_snapshot(last_id: int = 0, run_id: Optional[str] = None) -> list[dict]:
    return run_store.events_after(last_id=last_id, run_id=run_id)


@app.post('/multisig/plan', dependencies=[Depends(require_key)])
def multisig_plan(req: MultisigPlanRequest) -> dict:
    try:
        return create_multisig_plan(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/wallet-monitors', response_model=WalletMonitorOut, dependencies=[Depends(require_key)])
def create_wallet_monitor(req: WalletMonitorCreate) -> WalletMonitorOut:
    try:
        monitor = db.create_wallet_monitor(req)
        refresh_wallet_monitor_by_id(monitor.id)
        refreshed = db.get_wallet_monitor(monitor.id)
        return refreshed or monitor
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/wallet-monitors', response_model=List[WalletMonitorOut])
def wallet_monitors() -> List[WalletMonitorOut]:
    return db.list_wallet_monitors()


@app.post('/wallet-monitors/{monitor_id}/refresh', response_model=WalletRefreshResult, dependencies=[Depends(require_key)])
def refresh_wallet_monitor(monitor_id: int) -> WalletRefreshResult:
    return refresh_wallet_monitor_by_id(monitor_id)


@app.post('/wallet-monitors/refresh-all', dependencies=[Depends(require_key)])
def refresh_wallet_monitors() -> list[WalletRefreshResult]:
    return refresh_all_wallet_monitors()


@app.get('/wallet-alerts')
def wallet_alerts(limit: int = 50) -> list[dict]:
    return db.list_wallet_alerts(limit=limit)


@app.post('/safes', response_model=SafeRegistryOut, dependencies=[Depends(require_key)])
def create_safe_registry(req: SafeRegistryCreate) -> SafeRegistryOut:
    try:
        return register_safe(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/safes', response_model=List[SafeRegistryOut])
def safes() -> List[SafeRegistryOut]:
    return db.list_safes()


@app.post('/safe-txs', response_model=SafeTxDraftOut, dependencies=[Depends(require_key)])
def create_safe_tx(req: SafeTxDraftCreate) -> SafeTxDraftOut:
    try:
        return create_tx_draft(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/safe-txs', response_model=List[SafeTxDraftOut])
def safe_txs() -> List[SafeTxDraftOut]:
    return db.list_safe_txs()


@app.get('/safe-txs/{tx_id}/payload')
def safe_tx_payload(tx_id: int) -> dict:
    try:
        return build_safe_tx_payload(tx_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post('/safe-txs/approve', dependencies=[Depends(require_key)])
def approve_safe_tx(req: ApprovalRequest) -> dict:
    try:
        return approve_tx(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/safe-txs/signature', dependencies=[Depends(require_key)])
def add_safe_signature(req: SafeSignRequest) -> dict:
    try:
        return record_signature(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/safe-txs/mark-executed', dependencies=[Depends(require_key)])
def mark_safe_tx_executed(req: SafeExecutionMark) -> dict:
    try:
        return mark_executed(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/tron/permissions', response_model=TronPermissionDraftOut, dependencies=[Depends(require_key)])
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


@app.post('/tron/permissions/{draft_id}/approve', dependencies=[Depends(require_key)])
def approve_tron_permission(draft_id: int, decision: str = 'approved', operator: str = 'local-operator', note: str = '') -> dict:
    try:
        return approve_tron_permission_draft(draft_id, decision, operator, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/tron/permissions/mark-executed', dependencies=[Depends(require_key)])
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
    items = []
    for path in sorted(folder.glob('*.md'), reverse=True):
        items.append({'id': path.name, 'path': str(path), 'size': path.stat().st_size})
    return items


@app.get('/reports/{report_id}', response_class=PlainTextResponse)
def report(report_id: str) -> str:
    if '/' in report_id or '..' in report_id:
        raise HTTPException(status_code=400, detail='Invalid report id')
    path = Path(settings.report_dir) / report_id
    if not path.exists():
        raise HTTPException(status_code=404, detail='Report not found')
    return path.read_text(encoding='utf-8')
