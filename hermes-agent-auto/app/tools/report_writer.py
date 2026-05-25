from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings


class ReportWriter:
    def __init__(self) -> None:
        self.settings = get_settings()
        Path(self.settings.report_dir).mkdir(parents=True, exist_ok=True)

    def write(self, title: str, markdown: str) -> str:
        safe = re.sub(r'[^a-zA-Z0-9._-]+', '-', title).strip('-')[:80] or 'report'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        path = Path(self.settings.report_dir) / f'{stamp}-{safe}.md'
        path.write_text(markdown, encoding='utf-8')
        return str(path)
