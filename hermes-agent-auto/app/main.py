from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse

from app import db
from app.agent import HermesAgent
from app.config import get_settings
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
    return '''
<!doctype html>
<html>
<head><title>Hermes Agent</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font-family:Arial;margin:40px;max-width:980px}textarea,input{width:100%;padding:10px;margin:6px 0}button{padding:10px 16px}pre{background:#f4f4f4;padding:14px;white-space:pre-wrap}</style></head>
<body>
<h1>Hermes Agent Auto Executor</h1>
<p>Local-first autonomous public-source research and scheduled reporting.</p>
<h2>Run once</h2>
<input id="title" value="Hermes Agent Report">
<textarea id="prompt" rows="6">搜索公开资料并输出中文报告，标注来源。</textarea>
<input id="max" value="8">
<button onclick="runOnce()">Run</button>
<h2>Create scheduled task</h2>
<input id="taskTitle" value="OKX public research">
<textarea id="taskPrompt" rows="6">每2小时搜索公开资料：OKX Web3 钱包/API/SDK/流动性挖矿项目对接要求，输出中文更新报告，标注来源。</textarea>
<input id="minutes" value="120">
<button onclick="createTask()">Create task</button>
<h2>Output</h2><pre id="out"></pre>
<script>
async function runOnce(){let r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.value,prompt:prompt.value,max_results:parseInt(max.value)})});out.textContent=JSON.stringify(await r.json(),null,2)}
async function createTask(){let r=await fetch('/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:taskTitle.value,prompt:taskPrompt.value,interval_minutes:parseInt(minutes.value),max_results:8,run_now:true})});out.textContent=JSON.stringify(await r.json(),null,2)}
</script>
</body></html>
'''


@app.post('/run', response_model=AgentResult, dependencies=[Depends(require_key)])
def run(req: RunRequest) -> AgentResult:
    result = HermesAgent().run(req.prompt, title=req.title, max_results=req.max_results, notify=req.notify)
    db.record_run(None, req.title, req.prompt, result.status, result.report_path, result.sources, result.error)
    return result


@app.post('/tasks', response_model=TaskOut, dependencies=[Depends(require_key)])
def create_task(req: TaskCreate) -> TaskOut:
    task = db.create_task(req)
    scheduler.add_task_job(task.id)
    if req.run_now:
        scheduler.run_task(task.id)
        refreshed = db.get_task(task.id)
        return refreshed or task
    return task


@app.get('/tasks', response_model=List[TaskOut])
def tasks() -> List[TaskOut]:
    return db.list_tasks()


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
