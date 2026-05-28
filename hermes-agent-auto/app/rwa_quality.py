from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/rwa-mine', tags=['RWA Mine Code Quality Checker'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class QualityScanRequest(BaseModel):
    project_name: Optional[str] = None
    include_frontend: bool = True
    include_contracts: bool = True
    include_backend: bool = True
    include_docs: bool = True
    write_report: bool = True


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_quality_scans (id INTEGER PRIMARY KEY AUTOINCREMENT,project_name TEXT NOT NULL,root_path TEXT NOT NULL,status TEXT NOT NULL,score INTEGER NOT NULL,blocking_count INTEGER NOT NULL,warning_count INTEGER NOT NULL,info_count INTEGER NOT NULL,findings_json TEXT NOT NULL,report_path TEXT,created_at TEXT NOT NULL)''')


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def safe_slug(value: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]+', '-', value.strip()).strip('-').lower()[:80] or 'rwa-mine-mvp'


def project_root(project_name: Optional[str]) -> tuple[str, Path]:
    if project_name:
        name = safe_slug(project_name)
        root = Path('/app/storage/rwa_mine_projects') / name
        if not root.exists():
            raise HTTPException(status_code=404, detail=f'project scaffold not found: {name}')
        return name, root
    rec = qrow('SELECT project_name, root_path FROM rwa_mine_scaffolds ORDER BY id DESC LIMIT 1')
    if not rec:
        raise HTTPException(status_code=404, detail='no scaffold found. Generate RWA scaffold first.')
    return rec['project_name'], Path(rec['root_path'])


def finding(severity: str, area: str, code: str, message: str, path: str = '', recommendation: str = '') -> Dict[str, str]:
    return {'severity': severity, 'area': area, 'code': code, 'message': message, 'path': path, 'recommendation': recommendation}


def exists(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def read(root: Path, rel: str) -> str:
    p = root / rel
    if not p.exists() or not p.is_file():
        return ''
    try:
        return p.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''


REQUIRED_FRONTEND = [
    'frontend/package.json', 'frontend/app/page.tsx', 'frontend/app/mine/page.tsx', 'frontend/app/stake/page.tsx',
    'frontend/app/node/page.tsx', 'frontend/app/referral/page.tsx', 'frontend/app/profile/page.tsx', 'frontend/app/assets/page.tsx',
    'frontend/lib/wallet.ts', 'frontend/.env.example',
]
REQUIRED_CONTRACTS = [
    'contracts/package.json', 'contracts/hardhat.config.ts', 'contracts/contracts/RWAUToken.sol', 'contracts/contracts/sRWAUToken.sol',
    'contracts/contracts/StakingVault.sol', 'contracts/contracts/RewardDistributor.sol', 'contracts/contracts/ReferralRegistry.sol',
    'contracts/contracts/NodeNFT.sol', 'contracts/contracts/Treasury.sol', 'contracts/scripts/deploy.ts',
]
REQUIRED_BACKEND = [
    'backend/package.json', 'backend/src/main.ts', 'backend/src/app.module.ts', 'backend/prisma/schema.prisma', 'backend/.env.example',
]
REQUIRED_DOCS = ['docs/SECURITY.md', 'docs/LAUNCH_GATE_CHECK.md', 'docs/API_MODULES.md', 'docs/COMPLIANCE_LANGUAGE.md']


def check_required(root: Path, files: List[str], area: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for rel in files:
        if not exists(root, rel):
            out.append(finding('blocking', area, 'MISSING_REQUIRED_FILE', f'Missing required file: {rel}', rel, 'Generate scaffold/codegen for this module before continuing.'))
    return out


def check_frontend(root: Path) -> List[Dict[str, str]]:
    out = check_required(root, REQUIRED_FRONTEND, 'frontend')
    wallet = read(root, 'frontend/lib/wallet.ts')
    if wallet and 'projectId' not in wallet:
        out.append(finding('warning', 'frontend', 'WALLETCONNECT_PROJECT_ID_NOT_CONFIGURED', 'WalletConnect project id config not found.', 'frontend/lib/wallet.ts', 'Add NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID handling.'))
    assets = read(root, 'frontend/app/assets/page.tsx')
    risky_words = ['guaranteed', '保本', '固定收益', '真实收益', 'principal protection']
    if any(w.lower() in assets.lower() for w in risky_words):
        out.append(finding('blocking', 'frontend', 'MISLEADING_RWA_LANGUAGE', 'Asset page contains potentially misleading yield/protection language.', 'frontend/app/assets/page.tsx', 'MVP v1 must describe static showcase only; remove guaranteed-yield wording.'))
    return out


def check_contracts(root: Path) -> List[Dict[str, str]]:
    out = check_required(root, REQUIRED_CONTRACTS, 'contracts')
    for rel in (root / 'contracts/contracts').glob('*.sol') if (root / 'contracts/contracts').exists() else []:
        text = rel.read_text(encoding='utf-8', errors='ignore')
        relp = str(rel.relative_to(root))
        if 'pragma solidity ^0.8.24' not in text:
            out.append(finding('warning', 'contracts', 'SOLIDITY_VERSION_NOT_PINNED_TO_TEMPLATE', 'Solidity pragma differs from expected ^0.8.24.', relp, 'Review compiler version and audit assumptions.'))
        if 'placeholder' in text.lower() or 'stub-ready' in text.lower():
            out.append(finding('blocking', 'contracts', 'PLACEHOLDER_CONTRACT', 'Contract still contains placeholder/stub logic.', relp, 'Replace placeholder with audited implementation before testnet/mainnet.'))
        if 'external' in text and 'nonReentrant' not in text and rel.name in {'StakingVault.sol', 'Treasury.sol', 'RewardDistributor.sol'}:
            out.append(finding('warning', 'contracts', 'REENTRANCY_GUARD_MISSING_REVIEW', 'External state-changing contract should be reviewed for reentrancy guard.', relp, 'Use ReentrancyGuard where token transfers or state transitions can reenter.'))
        if rel.name in {'Treasury.sol', 'RWAUToken.sol'} and ('AccessControl' not in text and 'Ownable' not in text):
            out.append(finding('blocking', 'contracts', 'ACCESS_CONTROL_MISSING', 'Critical contract lacks obvious access control import/usage.', relp, 'Critical permissions must use explicit role control and Safe ownership.'))
    sec = read(root, 'docs/SECURITY.md')
    if 'Gnosis Safe 3/5' not in sec:
        out.append(finding('blocking', 'contracts', 'SAFE_3_OF_5_NOT_DOCUMENTED', 'Gnosis Safe 3/5 ownership is not documented.', 'docs/SECURITY.md', 'Document Safe owners and controlled roles before launch.'))
    gate = read(root, 'docs/LAUNCH_GATE_CHECK.md')
    if 'External audit' not in gate and '外部' not in gate:
        out.append(finding('blocking', 'contracts', 'EXTERNAL_AUDIT_GATE_MISSING', 'External audit launch gate is missing.', 'docs/LAUNCH_GATE_CHECK.md', 'Add explicit external audit gate.'))
    return out


def check_backend(root: Path) -> List[Dict[str, str]]:
    out = check_required(root, REQUIRED_BACKEND, 'backend')
    schema = read(root, 'backend/prisma/schema.prisma')
    required_models = ['User', 'WalletBinding', 'DailyMiningLog', 'StakeOrder', 'RewardClaim', 'ReferralRelation', 'TeamStatsDaily', 'NodeOrder', 'BurnRecord', 'BuybackRecord', 'AssetPool', 'SystemConfig']
    for model in required_models:
        if schema and f'model {model}' not in schema:
            out.append(finding('blocking', 'backend', 'PRISMA_MODEL_MISSING', f'Prisma model missing: {model}', 'backend/prisma/schema.prisma', 'Regenerate backend database schema.'))
    env = read(root, 'backend/.env.example')
    if env and 'DATABASE_URL' not in env:
        out.append(finding('blocking', 'backend', 'DATABASE_URL_MISSING', 'DATABASE_URL missing in backend env example.', 'backend/.env.example', 'Add DATABASE_URL example.'))
    if env and 'SECRET' in env.upper() and 'example' not in env.lower():
        out.append(finding('warning', 'backend', 'SECRET_ENV_REVIEW', 'Env example contains secret-like keys; verify no real secret is committed.', 'backend/.env.example', 'Use placeholder values only.'))
    return out


def check_docs(root: Path) -> List[Dict[str, str]]:
    out = check_required(root, REQUIRED_DOCS, 'docs')
    compliance = read(root, 'docs/COMPLIANCE_LANGUAGE.md')
    if compliance and 'static RWA asset showcase' not in compliance and '静态' not in compliance:
        out.append(finding('blocking', 'docs', 'RWA_STATIC_DISCLOSURE_MISSING', 'MVP static RWA asset disclosure is missing.', 'docs/COMPLIANCE_LANGUAGE.md', 'State clearly that MVP v1 is static showcase only.'))
    return out


def score_findings(findings: List[Dict[str, str]]) -> Dict[str, Any]:
    blocking = len([f for f in findings if f['severity'] == 'blocking'])
    warning = len([f for f in findings if f['severity'] == 'warning'])
    info = len([f for f in findings if f['severity'] == 'info'])
    score = max(0, 100 - blocking * 12 - warning * 3 - info)
    status = 'blocked' if blocking else 'warning' if warning else 'pass'
    return {'score': score, 'status': status, 'blocking_count': blocking, 'warning_count': warning, 'info_count': info}


def write_markdown_report(project: str, root: Path, findings: List[Dict[str, str]], summary: Dict[str, Any]) -> str:
    settings = get_settings()
    folder = Path(settings.report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = folder / f'rwa_quality_{project}_{stamp}.md'
    lines = [
        '# RWA Mine Code Quality Report', '', f'- project: {project}', f'- root_path: {root}', f'- created_at_utc: {now()}', f'- status: {summary["status"]}', f'- score: {summary["score"]}', f'- blocking: {summary["blocking_count"]}', f'- warning: {summary["warning_count"]}', '', '## Findings', ''
    ]
    if not findings:
        lines.append('- No findings.')
    for f in findings:
        lines.append(f'- **{f["severity"].upper()}** `{f["code"]}` [{f["area"]}] `{f.get("path", "")}` — {f["message"]}')
        if f.get('recommendation'):
            lines.append(f'  - Recommendation: {f["recommendation"]}')
    lines += ['', '## Launch Rule', '', 'Any blocking finding prevents testnet/mainnet promotion until resolved.']
    path.write_text('\n'.join(lines), encoding='utf-8')
    return str(path)


@router.post('/quality/scan', dependencies=[Depends(require_key)])
def quality_scan(req: QualityScanRequest) -> Dict[str, Any]:
    ensure_tables()
    project, root = project_root(req.project_name)
    findings: List[Dict[str, str]] = []
    if req.include_frontend:
        findings += check_frontend(root)
    if req.include_contracts:
        findings += check_contracts(root)
    if req.include_backend:
        findings += check_backend(root)
    if req.include_docs:
        findings += check_docs(root)
    summary = score_findings(findings)
    report_path = write_markdown_report(project, root, findings, summary) if req.write_report else None
    with db.connect() as conn:
        cur = conn.execute('INSERT INTO rwa_mine_quality_scans (project_name, root_path, status, score, blocking_count, warning_count, info_count, findings_json, report_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (project, str(root), summary['status'], summary['score'], summary['blocking_count'], summary['warning_count'], summary['info_count'], jd(findings), report_path, now()))
        scan_id = int(cur.lastrowid)
    db.audit('rwa_mine_quality_scan', 'rwa_mine_project', project, {'scan_id': scan_id, **summary}, summary['status'], 'high' if summary['blocking_count'] else 'medium', 'not_required')
    return {'status': summary['status'], 'scan_id': scan_id, 'project_name': project, 'root_path': str(root), 'summary': summary, 'findings': findings, 'report_path': report_path}


@router.get('/quality/latest')
def quality_latest() -> Dict[str, Any]:
    ensure_tables()
    rec = qrow('SELECT * FROM rwa_mine_quality_scans ORDER BY id DESC LIMIT 1')
    if not rec:
        raise HTTPException(status_code=404, detail='no quality scan found')
    rec['findings'] = json.loads(rec.pop('findings_json'))
    return rec


@router.get('/quality/scans')
def quality_scans(limit: int = 100) -> List[Dict[str, Any]]:
    ensure_tables()
    return qrows('SELECT id, project_name, root_path, status, score, blocking_count, warning_count, info_count, report_path, created_at FROM rwa_mine_quality_scans ORDER BY id DESC LIMIT ?', (limit,))
