from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=3)
    title: str = 'Hermes Agent Report'
    max_results: int = Field(default=8, ge=0, le=20)
    notify: bool = False


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=2)
    prompt: str = Field(..., min_length=3)
    interval_minutes: int = Field(default=120, ge=5)
    max_results: int = Field(default=8, ge=0, le=20)
    notify: bool = False
    enabled: bool = True
    run_now: bool = False


class TaskOut(BaseModel):
    id: int
    title: str
    prompt: str
    interval_minutes: int
    max_results: int
    notify: bool
    enabled: bool
    created_at: str
    updated_at: str
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None
    last_report_path: Optional[str] = None


class AgentResult(BaseModel):
    title: str
    prompt: str
    status: str
    report_markdown: str
    report_path: Optional[str] = None
    sources: List[Dict[str, Any]] = []
    error: Optional[str] = None
