from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/commercial-os', tags=['v15 AssetOps Commercial OS Foundation'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_users (id INTEGER PRIMARY KEY AUTOINCREMENT,wallet TEXT, email TEXT, display_name TEXT, status TEXT NOT NULL DEFAULT 'active', risk_level TEXT NOT NULL DEFAULT 'normal', kyc_status TEXT NOT NULL DEFAULT 'not_started', note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_roles (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE, permissions_json TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_announcements (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL, body TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'global', status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT,user_ref TEXT, title TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'general', priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open', body TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_proof_assets (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL, asset_type TEXT NOT NULL, jurisdiction TEXT, proof_uri TEXT, custodian TEXT, valuation_usd REAL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_audit_files (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL, file_uri TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'security', auditor TEXT, status TEXT NOT NULL DEFAULT 'pending', hash TEXT, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_finance_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL, period TEXT NOT NULL, revenue_usd REAL DEFAULT 0, expense_usd REAL DEFAULT 0, treasury_usd REAL DEFAULT 0, report_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_multisig_records (id INTEGER PRIMARY KEY AUTOINCREMENT,safe_address TEXT NOT NULL, chain TEXT NOT NULL, action TEXT NOT NULL, tx_hash TEXT, signers_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_kyc_cases (id INTEGER PRIMARY KEY AUTOINCREMENT,user_ref TEXT NOT NULL, provider TEXT, level TEXT NOT NULL DEFAULT 'basic', status TEXT NOT NULL DEFAULT 'not_started', reference_id TEXT, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_risk_users (id INTEGER PRIMARY KEY AUTOINCREMENT,user_ref TEXT NOT NULL, reason TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'medium', action TEXT NOT NULL DEFAULT 'monitor', status TEXT NOT NULL DEFAULT 'open', evidence_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_deploy_envs (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE, env_type TEXT NOT NULL DEFAULT 'testnet', domain TEXT, chain TEXT, db_profile TEXT, status TEXT NOT NULL DEFAULT 'draft', note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_launch_gate_items (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE, category TEXT NOT NULL, required INTEGER NOT NULL DEFAULT 1, passed INTEGER NOT NULL DEFAULT 0, evidence TEXT, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS commercial_release_packages (id INTEGER PRIMARY KEY AUTOINCREMENT,version TEXT NOT NULL, title TEXT NOT NULL, manifest_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', report_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')


class UserCreate(BaseModel):
    wallet: str = ''
    email: str = ''
    display_name: str = ''
    status: str = 'active'
    risk_level: str = 'normal'
    kyc_status: str = 'not_started'
    note: str = ''


class RoleCreate(BaseModel):
    name: str
    permissions: List[str] = Field(default_factory=list)
    note: str = ''


class AnnouncementCreate(BaseModel):
    title: str
    body: str
    channel: str = 'global'
    status: str = 'draft'


class TicketCreate(BaseModel):
    user_ref: str = ''
    title: str
    category: str = 'general'
    priority: str = 'normal'
    body: str = ''


class ProofAssetCreate(BaseModel):
    name: str
    asset_type: str = 'static_showcase'
    jurisdiction: str = ''
    proof_uri: str = ''
    custodian: str = ''
    valuation_usd: float = 0
    status: str = 'draft'
    note: str = ''


class AuditFileCreate(BaseModel):
    title: str
    file_uri: str
    category: str = 'security'
    auditor: str = ''
    status: str = 'pending'
    hash: str = ''
    note: str = ''


class FinanceReportCreate(BaseModel):
    title: str
    period: str
    revenue_usd: float = 0
    expense_usd: float = 0
    treasury_usd: float = 0
    report: Dict[str, Any] = Field(default_factory=dict)
    status: str = 'draft'


class MultisigRecordCreate(BaseModel):
    safe_address: str
    chain: str
    action: str
    tx_hash: str = ''
    signers: List[str] = Field(default_factory=list)
    status: str = 'pending'
    note: str = ''


class KycCaseCreate(BaseModel):
    user_ref: str
    provider: str = ''
    level: str = 'basic'
    status: str = 'not_started'
    reference_id: str = ''
    note: str = ''


class RiskUserCreate(BaseModel):
    user_ref: str
    reason: str
    severity: str = 'medium'
    action: str = 'monitor'
    evidence: Dict[str, Any] = Field(default_factory=dict)


class DeployEnvCreate(BaseModel):
    name: str
    env_type: str = 'testnet'
    domain: str = ''
    chain: str = ''
    db_profile: str = ''
    status: str = 'draft'
    note: str = ''


class LaunchGateUpdate(BaseModel):
    passed: bool = False
    evidence: str = ''
    note: str = ''


DEFAULT_ROLES = [
    ('super_admin', ['*'], '最高管理员，仅限本地/受控环境'),
    ('ops_admin', ['announcements:*', 'tickets:*', 'users:read'], '运营管理员'),
    ('risk_admin', ['risk:*', 'kyc:*', 'launch_gate:read'], '风控管理员'),
    ('finance_admin', ['finance:*', 'multisig:read'], '财务管理员'),
    ('auditor', ['audit:read', 'launch_gate:read', 'reports:read'], '只读审计角色'),
]

DEFAULT_GATE_ITEMS = [
    ('RWA Mine P0 tasks complete', 'project', 1, 0, '所有 P0 任务必须 done'),
    ('Quality scan blocking = 0', 'quality', 1, 0, '质量扫描不得存在 blocking'),
    ('FixIt blocking tasks closed', 'quality', 1, 0, 'FixIt blocking 修复任务必须全部完成'),
    ('Contract stubs removed', 'contracts', 1, 0, '合约不得仍为 placeholder/stub'),
    ('External audit completed', 'audit', 1, 0, '外部审计报告必须登记'),
    ('Gnosis Safe 3/5 configured', 'multisig', 1, 0, 'Treasury / ProxyAdmin / 参数权限必须归 Safe'),
    ('30-day testnet run completed', 'deployment', 1, 0, '测试网运行记录必须满足 30 天'),
    ('RWA proof center reviewed', 'proof', 1, 0, '资产证明中心资料必须完成审核'),
    ('No misleading RWA yield language', 'compliance', 1, 0, '不得出现保本/固定收益/真实收益误导表述'),
    ('Rollback plan ready', 'release', 1, 0, '生产回滚方案必须完成'),
]


def rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def insert_and_return(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    ensure_tables()
    keys = list(data.keys())
    placeholders = ','.join(['?'] * len(keys))
    ts = now()
    data['created_at'] = ts
    data['updated_at'] = ts
    keys = list(data.keys())
    placeholders = ','.join(['?'] * len(keys))
    with db.connect() as conn:
        cur = conn.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({placeholders})', tuple(data[k] for k in keys))
        item_id = int(cur.lastrowid)
    return row(f'SELECT * FROM {table} WHERE id=?', (item_id,)) or {'id': item_id}


@router.post('/bootstrap', dependencies=[Depends(require_key)])
def bootstrap() -> Dict[str, Any]:
    ensure_tables()
    created = {'roles': 0, 'gate_items': 0, 'announcements': 0, 'deploy_envs': 0}
    ts = now()
    with db.connect() as conn:
        for name, perms, note in DEFAULT_ROLES:
            if not conn.execute('SELECT id FROM commercial_roles WHERE name=?', (name,)).fetchone():
                conn.execute('INSERT INTO commercial_roles (name,permissions_json,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (name, jd(perms), note, ts, ts))
                created['roles'] += 1
        for name, cat, req, passed, note in DEFAULT_GATE_ITEMS:
            if not conn.execute('SELECT id FROM commercial_launch_gate_items WHERE name=?', (name,)).fetchone():
                conn.execute('INSERT INTO commercial_launch_gate_items (name,category,required,passed,evidence,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (name, cat, req, passed, '', note, ts, ts))
                created['gate_items'] += 1
        if not conn.execute('SELECT id FROM commercial_announcements LIMIT 1').fetchone():
            conn.execute('INSERT INTO commercial_announcements (title,body,channel,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)', ('v15.0 Commercial OS Foundation 已启用', '商业化资产运营 OS 基础版已部署，真实上线仍需完成 Gate、审计、多签和合规检查。', 'global', 'published', ts, ts))
            created['announcements'] += 1
        for env_name, env_type in [('local-dev', 'local'), ('testnet', 'testnet'), ('production', 'mainnet')]:
            if not conn.execute('SELECT id FROM commercial_deploy_envs WHERE name=?', (env_name,)).fetchone():
                conn.execute('INSERT INTO commercial_deploy_envs (name,env_type,domain,chain,db_profile,status,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', (env_name, env_type, '', '', '', 'draft', 'auto bootstrap', ts, ts))
                created['deploy_envs'] += 1
    db.audit('commercial_os_bootstrap', 'commercial_os', None, created, 'success', 'medium', 'not_required')
    return {'status': 'success', 'version': '15.0-assetops-commercial-os-foundation', 'created': created}


@router.get('/overview')
def overview() -> Dict[str, Any]:
    ensure_tables()
    def c(table: str, where: str = '') -> int:
        return int((row(f'SELECT COUNT(*) c FROM {table} {where}') or {'c': 0})['c'])
    latest_quality = row('SELECT status,score,blocking_count,warning_count,report_path,created_at FROM rwa_mine_quality_scans ORDER BY id DESC LIMIT 1') if True else None
    fix_summary = None
    try:
        fix_summary = {
            'total': c('rwa_mine_fix_tasks'),
            'blocking_open': int((row("SELECT COUNT(*) c FROM rwa_mine_fix_tasks WHERE severity='blocking' AND status!='done'") or {'c': 0})['c']),
        }
    except Exception:
        fix_summary = {'total': 0, 'blocking_open': 0}
    gate = evaluate_launch_gate(write_package=False)
    return {
        'status': 'ok',
        'version': '15.0-assetops-commercial-os-foundation',
        'counts': {
            'users': c('commercial_users'),
            'roles': c('commercial_roles'),
            'announcements': c('commercial_announcements'),
            'tickets_open': c('commercial_tickets', "WHERE status!='closed'"),
            'proof_assets': c('commercial_proof_assets'),
            'audit_files': c('commercial_audit_files'),
            'finance_reports': c('commercial_finance_reports'),
            'multisig_records': c('commercial_multisig_records'),
            'kyc_cases': c('commercial_kyc_cases'),
            'risk_users_open': c('commercial_risk_users', "WHERE status!='closed'"),
            'deploy_envs': c('commercial_deploy_envs'),
            'release_packages': c('commercial_release_packages'),
        },
        'latest_quality': latest_quality,
        'fix_summary': fix_summary,
        'launch_gate': gate,
    }


@router.get('/users')
def list_users() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_users ORDER BY id DESC LIMIT 500')


@router.post('/users', dependencies=[Depends(require_key)])
def create_user(req: UserCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_users', req.model_dump())


@router.get('/rbac/roles')
def list_roles() -> List[Dict[str, Any]]:
    out = rows('SELECT * FROM commercial_roles ORDER BY id')
    for r in out:
        r['permissions'] = json.loads(r.pop('permissions_json'))
    return out


@router.post('/rbac/roles', dependencies=[Depends(require_key)])
def create_role(req: RoleCreate) -> Dict[str, Any]:
    item = insert_and_return('commercial_roles', {'name': req.name, 'permissions_json': jd(req.permissions), 'note': req.note})
    item['permissions'] = json.loads(item.pop('permissions_json'))
    return item


@router.get('/announcements')
def list_announcements() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_announcements ORDER BY id DESC LIMIT 200')


@router.post('/announcements', dependencies=[Depends(require_key)])
def create_announcement(req: AnnouncementCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_announcements', req.model_dump())


@router.get('/tickets')
def list_tickets(status: Optional[str] = None) -> List[Dict[str, Any]]:
    if status:
        return rows('SELECT * FROM commercial_tickets WHERE status=? ORDER BY id DESC LIMIT 300', (status,))
    return rows('SELECT * FROM commercial_tickets ORDER BY id DESC LIMIT 300')


@router.post('/tickets', dependencies=[Depends(require_key)])
def create_ticket(req: TicketCreate) -> Dict[str, Any]:
    data = req.model_dump()
    data['status'] = 'open'
    return insert_and_return('commercial_tickets', data)


@router.get('/proof-center/assets')
def list_proof_assets() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_proof_assets ORDER BY id DESC LIMIT 300')


@router.post('/proof-center/assets', dependencies=[Depends(require_key)])
def create_proof_asset(req: ProofAssetCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_proof_assets', req.model_dump())


@router.get('/audit-files')
def list_audit_files() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_audit_files ORDER BY id DESC LIMIT 300')


@router.post('/audit-files', dependencies=[Depends(require_key)])
def create_audit_file(req: AuditFileCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_audit_files', req.model_dump())


@router.get('/finance/reports')
def list_finance_reports() -> List[Dict[str, Any]]:
    out = rows('SELECT * FROM commercial_finance_reports ORDER BY id DESC LIMIT 200')
    for r in out:
        r['report'] = json.loads(r.pop('report_json'))
    return out


@router.post('/finance/reports', dependencies=[Depends(require_key)])
def create_finance_report(req: FinanceReportCreate) -> Dict[str, Any]:
    data = req.model_dump()
    report = data.pop('report')
    data['report_json'] = jd(report)
    item = insert_and_return('commercial_finance_reports', data)
    item['report'] = json.loads(item.pop('report_json'))
    return item


@router.get('/multisig/records')
def list_multisig_records() -> List[Dict[str, Any]]:
    out = rows('SELECT * FROM commercial_multisig_records ORDER BY id DESC LIMIT 300')
    for r in out:
        r['signers'] = json.loads(r.pop('signers_json'))
    return out


@router.post('/multisig/records', dependencies=[Depends(require_key)])
def create_multisig_record(req: MultisigRecordCreate) -> Dict[str, Any]:
    data = req.model_dump()
    signers = data.pop('signers')
    data['signers_json'] = jd(signers)
    item = insert_and_return('commercial_multisig_records', data)
    item['signers'] = json.loads(item.pop('signers_json'))
    return item


@router.get('/kyc/cases')
def list_kyc_cases() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_kyc_cases ORDER BY id DESC LIMIT 300')


@router.post('/kyc/cases', dependencies=[Depends(require_key)])
def create_kyc_case(req: KycCaseCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_kyc_cases', req.model_dump())


@router.get('/risk/users')
def list_risk_users() -> List[Dict[str, Any]]:
    out = rows('SELECT * FROM commercial_risk_users ORDER BY id DESC LIMIT 300')
    for r in out:
        r['evidence'] = json.loads(r.pop('evidence_json'))
    return out


@router.post('/risk/users', dependencies=[Depends(require_key)])
def create_risk_user(req: RiskUserCreate) -> Dict[str, Any]:
    data = req.model_dump()
    evidence = data.pop('evidence')
    data['status'] = 'open'
    data['evidence_json'] = jd(evidence)
    item = insert_and_return('commercial_risk_users', data)
    item['evidence'] = json.loads(item.pop('evidence_json'))
    return item


@router.get('/deploy/envs')
def list_deploy_envs() -> List[Dict[str, Any]]:
    return rows('SELECT * FROM commercial_deploy_envs ORDER BY id DESC LIMIT 100')


@router.post('/deploy/envs', dependencies=[Depends(require_key)])
def create_deploy_env(req: DeployEnvCreate) -> Dict[str, Any]:
    return insert_and_return('commercial_deploy_envs', req.model_dump())


@router.get('/launch-gate')
def launch_gate() -> Dict[str, Any]:
    return evaluate_launch_gate(write_package=False)


@router.post('/launch-gate/evaluate', dependencies=[Depends(require_key)])
def launch_gate_evaluate() -> Dict[str, Any]:
    result = evaluate_launch_gate(write_package=True)
    db.audit('commercial_launch_gate_evaluate', 'commercial_os', None, result, 'success' if result['launch_ready'] else 'blocked', 'high', 'not_required')
    return result


@router.post('/launch-gate/items/{item_id}', dependencies=[Depends(require_key)])
def update_launch_gate_item(item_id: int, req: LaunchGateUpdate) -> Dict[str, Any]:
    ensure_tables()
    if not row('SELECT id FROM commercial_launch_gate_items WHERE id=?', (item_id,)):
        raise HTTPException(status_code=404, detail='launch gate item not found')
    with db.connect() as conn:
        conn.execute('UPDATE commercial_launch_gate_items SET passed=?, evidence=?, note=?, updated_at=? WHERE id=?', (int(req.passed), req.evidence, req.note, now(), item_id))
    return row('SELECT * FROM commercial_launch_gate_items WHERE id=?', (item_id,)) or {'id': item_id}


def evaluate_launch_gate(write_package: bool) -> Dict[str, Any]:
    ensure_tables()
    items = rows('SELECT * FROM commercial_launch_gate_items ORDER BY id')
    failed = [i for i in items if int(i['required']) == 1 and int(i['passed']) != 1]
    latest_quality = row('SELECT status,score,blocking_count,warning_count,report_path,created_at FROM rwa_mine_quality_scans ORDER BY id DESC LIMIT 1')
    fix_blocking_open = 0
    try:
        fix_blocking_open = int((row("SELECT COUNT(*) c FROM rwa_mine_fix_tasks WHERE severity='blocking' AND status!='done'") or {'c': 0})['c'])
    except Exception:
        fix_blocking_open = 0
    dynamic_failures: List[Dict[str, Any]] = []
    if latest_quality and int(latest_quality.get('blocking_count') or 0) > 0:
        dynamic_failures.append({'name': 'Dynamic: quality blocking > 0', 'source': 'quality', 'blocking_count': latest_quality['blocking_count']})
    if fix_blocking_open > 0:
        dynamic_failures.append({'name': 'Dynamic: FixIt blocking open > 0', 'source': 'fixit', 'blocking_open': fix_blocking_open})
    proof_approved = int((row("SELECT COUNT(*) c FROM commercial_proof_assets WHERE status IN ('approved','verified')") or {'c': 0})['c'])
    audit_approved = int((row("SELECT COUNT(*) c FROM commercial_audit_files WHERE status IN ('approved','completed')") or {'c': 0})['c'])
    multisig_done = int((row("SELECT COUNT(*) c FROM commercial_multisig_records WHERE status IN ('executed','completed')") or {'c': 0})['c'])
    launch_ready = len(failed) == 0 and len(dynamic_failures) == 0
    result = {
        'status': 'ok',
        'launch_ready': launch_ready,
        'manual_gate_total': len(items),
        'manual_gate_passed': len(items) - len(failed),
        'manual_failures': failed,
        'dynamic_failures': dynamic_failures,
        'latest_quality': latest_quality,
        'fix_blocking_open': fix_blocking_open,
        'proof_assets_approved': proof_approved,
        'audit_files_approved': audit_approved,
        'multisig_records_completed': multisig_done,
        'redline': 'Any manual or dynamic failure blocks production launch.',
    }
    if write_package:
        result['release_package'] = create_release_package_record(result)
    return result


def create_release_package_record(gate_result: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    folder = Path(settings.report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    title = f'v15 Launch Gate Evaluation {stamp}'
    report_path = folder / f'commercial_os_launch_gate_{stamp}.md'
    lines = [
        '# v15 Commercial OS Launch Gate Evaluation', '', f'- created_at_utc: {now()}', f'- launch_ready: {gate_result["launch_ready"]}', f'- manual_gate_passed: {gate_result["manual_gate_passed"]}/{gate_result["manual_gate_total"]}', f'- dynamic_failures: {len(gate_result["dynamic_failures"])}', '', '## Manual Failures'
    ]
    for f in gate_result['manual_failures']:
        lines.append(f'- {f["name"]} [{f["category"]}] — {f.get("note") or ""}')
    lines += ['', '## Dynamic Failures']
    for f in gate_result['dynamic_failures']:
        lines.append(f'- {f["name"]}: {json.dumps(f, ensure_ascii=False)}')
    lines += ['', '## Redline', gate_result['redline']]
    report_path.write_text('\n'.join(lines), encoding='utf-8')
    data = {'gate_result': gate_result, 'created_at': now(), 'report_path': str(report_path)}
    item = insert_and_return('commercial_release_packages', {'version': '15.0', 'title': title, 'manifest_json': jd(data), 'status': 'blocked' if not gate_result['launch_ready'] else 'ready', 'report_path': str(report_path)})
    item['manifest'] = json.loads(item.pop('manifest_json'))
    return item


@router.get('/release/packages')
def list_release_packages() -> List[Dict[str, Any]]:
    out = rows('SELECT * FROM commercial_release_packages ORDER BY id DESC LIMIT 100')
    for r in out:
        r['manifest'] = json.loads(r.pop('manifest_json'))
    return out
