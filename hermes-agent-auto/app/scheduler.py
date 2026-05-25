from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import db
from app.agent import HermesAgent


class HermesScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone='UTC')
        self.agent = HermesAgent()

    def start(self) -> None:
        db.init_db()
        self.scheduler.start()
        self.reload_jobs()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            job.remove()
        for task in db.list_tasks():
            if task.enabled:
                self.add_task_job(task.id)

    def add_task_job(self, task_id: int) -> None:
        task = db.get_task(task_id)
        if not task or not task.enabled:
            return
        self.scheduler.add_job(self.run_task, trigger=IntervalTrigger(minutes=task.interval_minutes), args=[task.id], id=f'task-{task.id}', replace_existing=True, max_instances=1, coalesce=True)

    def run_task(self, task_id: int) -> None:
        task = db.get_task(task_id)
        if not task or not task.enabled:
            return
        result = self.agent.run(task.prompt, title=task.title, max_results=task.max_results, notify=task.notify)
        db.set_task_result(task.id, result.status, result.report_path)
        db.record_run(task.id, task.title, task.prompt, result.status, result.report_path, result.sources, result.error)
