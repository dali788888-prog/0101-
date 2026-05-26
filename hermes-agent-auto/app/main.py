from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from app import db
from app.agent import HermesAgent
from app.config import get_settings
from app.runtime import run_store
from app.scheduler import HermesScheduler
from app.schemas import AgentResult, RunRequest, TaskCreate, TaskOut

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


@app.get('/health')
def health() -> dict:
    return {'status': 'ok', 'app': settings.app_name, 'search_provider': settings.search_provider, 'model': settings.ollama_model}


@app.get('/', response_class=HTMLResponse)
def index() -> str:
    ui_path = Path(__file__).with_name('ui.html')
    if ui_path.exists():
        return ui_path.read_text(encoding='utf-8')
    return '<h1>Hermes Agent Auto Executor</h1><p>UI file not found.</p>'


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
    result = HermesAgent().run(
        req.prompt,
        title=req.title,
        max_results=req.max_results,
        notify=req.notify,
        progress_callback=lambda event_type, message, progress, data: run_store.emit(run_id, event_type, message, progress=progress, data=data),
    )
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
