from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

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
        self.add_system_jobs()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload_jobs(self) -> None:
        for job in self.scheduler.get_jobs():
            job.remove()
        for task in db.list_tasks():
            if task.enabled:
                self.add_task_job(task.id)
        self.add_system_jobs()

    def add_task_job(self, task_id: int) -> None:
        task = db.get_task(task_id)
        if not task or not task.enabled:
            return
        self.scheduler.add_job(self.run_task, trigger=IntervalTrigger(minutes=task.interval_minutes), args=[task.id], id=f'task-{task.id}', replace_existing=True, max_instances=1, coalesce=True)

    def add_system_jobs(self) -> None:
        self.scheduler.add_job(self.run_signal_analysis_job, trigger=IntervalTrigger(minutes=30), id='system-signal-analysis-30m', replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.add_job(self.run_signal_workspace_sync_job, trigger=CronTrigger(hour=23, minute=55), id='system-signal-workspace-sync-daily', replace_existing=True, max_instances=1, coalesce=True)

    def run_task(self, task_id: int) -> None:
        task = db.get_task(task_id)
        if not task or not task.enabled:
            return
        result = self.agent.run(task.prompt, title=task.title, max_results=task.max_results, notify=task.notify)
        db.set_task_result(task.id, result.status, result.report_path)
        db.record_run(task.id, task.title, task.prompt, result.status, result.report_path, result.sources, result.error)

    def run_signal_analysis_job(self) -> None:
        try:
            from app.strategy_signals import SignalAnalyzeRequest, analyze

            result = analyze(SignalAnalyzeRequest(persist=True))
            db.audit('system_signal_analysis_job', 'scheduler', 'system-signal-analysis-30m', {'signals': result.get('signal_count', 0), 'persisted': result.get('persisted_count', 0)}, 'success', 'low', 'not_required')
        except Exception as exc:
            db.audit('system_signal_analysis_job', 'scheduler', 'system-signal-analysis-30m', {'error': str(exc)}, 'failed', 'medium', 'not_required')

    def run_signal_workspace_sync_job(self) -> None:
        try:
            from app.strategy_signals import SignalWorkspaceSyncRequest, sync_signal_workspace

            result = sync_signal_workspace(SignalWorkspaceSyncRequest(period='daily', limit=100, create_high_notes=True, create_report=True, notify_operator=True, force=False, operator='scheduler'))
            db.audit('system_signal_workspace_sync_job', 'scheduler', 'system-signal-workspace-sync-daily', result.get('metrics', {}), 'success', 'low', 'not_required')
        except Exception as exc:
            db.audit('system_signal_workspace_sync_job', 'scheduler', 'system-signal-workspace-sync-daily', {'error': str(exc)}, 'failed', 'medium', 'not_required')
