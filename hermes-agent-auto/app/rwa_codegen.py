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

router = APIRouter(prefix='/rwa-mine', tags=['RWA Mine Automatic Code Progressor'])


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
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_codegen_runs (id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,project_name TEXT NOT NULL,root_path TEXT NOT NULL,module TEXT NOT NULL,status TEXT NOT NULL,files_json TEXT NOT NULL,note TEXT,created_at TEXT NOT NULL)''')


class CodegenTaskRequest(BaseModel):
    project_name: Optional[str] = None
    mark_task_done: bool = False
    overwrite: bool = True


class CodegenBatchRequest(BaseModel):
    project_name: Optional[str] = None
    module: Optional[str] = Field(default=None, pattern='^(frontend|contract|backend)$')
    priority: Optional[str] = Field(default='P0', pattern='^(P0|P1|P2)$')
    status_filter: Optional[str] = Field(default=None, pattern='^(todo|doing|blocked|done)$')
    limit: int = Field(default=10, ge=1, le=100)
    mark_task_done: bool = False
    overwrite: bool = True


def qrows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def qrow(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


def safe_slug(value: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]+', '-', value.strip()).strip('-').lower()[:80] or 'rwa-mine-mvp'


def latest_project_root(project_name: Optional[str] = None) -> tuple[str, Path]:
    if project_name:
        name = safe_slug(project_name)
        root = Path('/app/storage/rwa_mine_projects') / name
        if not root.exists():
            raise HTTPException(status_code=404, detail=f'project scaffold not found: {name}. Generate scaffold first.')
        return name, root
    rec = qrow('SELECT project_name, root_path FROM rwa_mine_scaffolds ORDER BY id DESC LIMIT 1')
    if not rec:
        raise HTTPException(status_code=404, detail='no scaffold found. Generate RWA scaffold first.')
    return rec['project_name'], Path(rec['root_path'])


def task_by_id(task_id: str) -> Dict[str, Any]:
    task = qrow('SELECT * FROM rwa_mine_tasks WHERE task_id=?', (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail=f'task not found: {task_id}. Run POST /rwa-mine/bootstrap first.')
    return task


def write_file(path: Path, content: str, overwrite: bool = True) -> Dict[str, Any]:
    if path.exists() and not overwrite:
        return {'path': str(path), 'status': 'skipped_exists', 'size': path.stat().st_size}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + '\n', encoding='utf-8')
    return {'path': str(path), 'status': 'written', 'size': path.stat().st_size}


def pascal(name: str) -> str:
    return ''.join(x.capitalize() for x in re.split(r'[^a-zA-Z0-9]+', name) if x)


def frontend_page(title: str, task_id: str, desc: str) -> str:
    return f"""
export default function Page() {{
  return (
    <main className=\"min-h-screen p-8\">
      <section className=\"rounded-2xl border p-6\">
        <p className=\"text-sm text-gray-500\">{task_id}</p>
        <h1 className=\"text-3xl font-bold\">{title}</h1>
        <p className=\"mt-3 text-gray-500\">{desc}</p>
      </section>
    </main>
  )
}}
"""


def frontend_component(name: str, task_id: str, desc: str) -> str:
    comp = pascal(name)
    return f"""
export function {comp}() {{
  return (
    <div className=\"rounded-xl border p-4\">
      <p className=\"text-xs text-gray-500\">{task_id}</p>
      <h3 className=\"font-semibold\">{name}</h3>
      <p className=\"text-sm text-gray-500\">{desc}</p>
    </div>
  )
}}
"""


def nest_module(module_name: str, task_id: str, desc: str) -> Dict[str, str]:
    base = pascal(module_name)
    kebab = safe_slug(module_name)
    return {
        f'backend/src/modules/{kebab}/{kebab}.module.ts': f"""
import {{ Module }} from '@nestjs/common'
import {{ {base}Controller }} from './{kebab}.controller'
import {{ {base}Service }} from './{kebab}.service'

@Module({{ controllers: [{base}Controller], providers: [{base}Service], exports: [{base}Service] }})
export class {base}Module {{}}
""",
        f'backend/src/modules/{kebab}/{kebab}.controller.ts': f"""
import {{ Controller, Get }} from '@nestjs/common'
import {{ {base}Service }} from './{kebab}.service'

@Controller('{kebab}')
export class {base}Controller {{
  constructor(private readonly service: {base}Service) {{}}

  @Get('status')
  status() {{ return this.service.status() }}
}}
""",
        f'backend/src/modules/{kebab}/{kebab}.service.ts': f"""
import {{ Injectable }} from '@nestjs/common'

@Injectable()
export class {base}Service {{
  status() {{
    return {{ module: '{module_name}', task_id: '{task_id}', description: '{desc}', status: 'stub-ready' }}
  }}
}}
""",
    }


def solidity_contract(contract_name: str, task_id: str, desc: str) -> str:
    cname = pascal(contract_name)
    return f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title {cname}
/// @notice Generated from {task_id}: {desc}
/// @dev Replace placeholder logic with audited implementation before testnet/mainnet.
contract {cname} {{
    address public immutable deployer;

    event PlaceholderAction(address indexed operator, string taskId);

    constructor() {{ deployer = msg.sender; }}

    function status() external pure returns (string memory) {{
        return '{task_id}: stub-ready';
    }}

    function placeholderAction() external {{
        emit PlaceholderAction(msg.sender, '{task_id}');
    }}
}}
"""


FE_MAP: Dict[str, List[tuple[str, str]]] = {
    'FE-001': [('frontend/tsconfig.json', '{"compilerOptions":{"target":"es2020","jsx":"preserve","strict":true,"moduleResolution":"bundler"}}'), ('frontend/tailwind.config.ts', 'export default { content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"] }')],
    'FE-002': [('frontend/components/WalletConnectPanel.tsx', frontend_component('WalletConnectPanel', 'FE-002', 'RainbowKit wallet connect panel placeholder'))],
    'FE-003': [('frontend/lib/contracts.ts', 'export const CONTRACT_ADDRESSES = {} as const\nexport const rwauAbi = [] as const')],
    'FE-004': [('frontend/components/AppHeader.tsx', frontend_component('AppHeader', 'FE-004', 'Top navigation and announcement banner'))],
    'FE-005': [('frontend/components/KpiCards.tsx', frontend_component('KpiCards', 'FE-005', 'TVL / burned / hashrate KPI cards'))],
    'FE-006': [('frontend/components/ChartShell.tsx', frontend_component('ChartShell', 'FE-006', 'ECharts wrapper and theme adapter'))],
    'FE-007': [('frontend/components/BurnLineChart.tsx', frontend_component('BurnLineChart', 'FE-007', 'Buyback and burn line chart'))],
    'FE-008': [('frontend/components/NodeSeatProgress.tsx', frontend_component('NodeSeatProgress', 'FE-008', 'Node remaining seats progress bar'))],
    'FE-009': [('frontend/app/mine/page.tsx', frontend_page('Daily Activation Mining', 'FE-009', 'Daily activation button and state management'))],
    'FE-010': [('frontend/components/HashrateBreakdown.tsx', frontend_component('HashrateBreakdown', 'FE-010', 'Hashrate source breakdown'))],
    'FE-011': [('frontend/components/EmissionCountdown.tsx', frontend_component('EmissionCountdown', 'FE-011', 'Emission halving countdown'))],
    'FE-012': [('frontend/components/MiningBarChart.tsx', frontend_component('MiningBarChart', 'FE-012', 'Last 7 days mining output chart'))],
    'FE-013': [('frontend/app/profile/page.tsx', frontend_page('Profile Center', 'FE-013', 'Holdings, staking and reward overview'))],
    'FE-014': [('frontend/components/ActivityTimeline.tsx', frontend_component('ActivityTimeline', 'FE-014', 'User operation timeline'))],
    'FE-015': [('frontend/components/AssetAllocationPie.tsx', frontend_component('AssetAllocationPie', 'FE-015', 'Asset allocation pie chart'))],
    'FE-016': [('frontend/app/stake/page.tsx', frontend_page('Staking Vault', 'FE-016', 'Four staking tiers, amount input and confirmation flow'))],
    'FE-017': [('frontend/components/StakeOrderList.tsx', frontend_component('StakeOrderList', 'FE-017', 'Stake order list and early unlock warning'))],
    'FE-018': [('frontend/app/node/page.tsx', frontend_page('Node NFT', 'FE-018', 'Node level cards and benefits table'))],
    'FE-019': [('frontend/components/NodeMintFlow.tsx', frontend_component('NodeMintFlow', 'FE-019', 'Node mint flow with burn confirmation and wallet signature'))],
    'FE-020': [('frontend/app/referral/page.tsx', frontend_page('Referral Center', 'FE-020', 'Invite link generation and copy'))],
    'FE-021': [('frontend/components/TeamStats.tsx', frontend_component('TeamStats', 'FE-021', 'Team stats and direct active users'))],
    'FE-022': [('frontend/components/Leaderboard.tsx', frontend_component('Leaderboard', 'FE-022', 'Monthly Top 10 leaderboard'))],
    'FE-023': [('frontend/app/assets/page.tsx', frontend_page('Static RWA Asset Showcase', 'FE-023', 'Static display only, no guaranteed yield claims'))],
}

SC_MAP: Dict[str, str] = {
    'SC-001': 'RWAUToken', 'SC-002': 'sRWAUToken', 'SC-003': 'StakingVault', 'SC-004': 'RewardDistributor',
    'SC-005': 'ReferralRegistry', 'SC-006': 'NodeNFT', 'SC-007': 'Treasury', 'SC-008': 'ProxyDeploymentPlan',
    'SC-009': 'GnosisSafeConfig', 'SC-010': 'ContractCoveragePlan', 'SC-011': 'TestnetRunbook', 'SC-012': 'ExternalAuditRunbook'
}

BE_MAP: Dict[str, str] = {
    'BE-001': 'core', 'BE-002': 'database', 'BE-003': 'cache', 'BE-004': 'indexer', 'BE-005': 'users',
    'BE-006': 'mining', 'BE-007': 'staking', 'BE-008': 'referral', 'BE-009': 'node', 'BE-010': 'dashboard',
    'BE-011': 'realtime', 'BE-012': 'risk', 'BE-013': 'scheduler'
}


def files_for_task(task: Dict[str, Any]) -> Dict[str, str]:
    tid = task['task_id']
    desc = task['description']
    module = task['module']
    if module == 'frontend':
        pairs = FE_MAP.get(tid, [(f'frontend/components/{pascal(tid)}.tsx', frontend_component(tid, tid, desc))])
        return {path: content for path, content in pairs}
    if module == 'contract':
        name = SC_MAP.get(tid, tid)
        if tid in {'SC-008','SC-009','SC-010','SC-011','SC-012'}:
            return {f'contracts/docs/{name}.md': f'# {name}\n\nGenerated from {tid}: {desc}\n\n- Status: stub-ready\n- Must be reviewed before testnet/mainnet.'}
        return {f'contracts/contracts/{name}.sol': solidity_contract(name, tid, desc)}
    if module == 'backend':
        name = BE_MAP.get(tid, tid.lower())
        if tid == 'BE-002':
            return {'backend/prisma/schema.prisma': PRISMA_SCHEMA}
        if tid == 'BE-003':
            return {'backend/src/modules/cache/cache.module.ts': "import { Module } from '@nestjs/common'\n@Module({})\nexport class CacheModule {}\n", 'backend/src/modules/cache/cache.service.ts': "export class CacheService { status(){ return { cache: 'redis-stub-ready' } } }\n"}
        return nest_module(name, tid, desc)
    return {f'docs/tasks/{tid}.md': f'# {tid}\n\n{desc}\n'}


PRISMA_SCHEMA = r'''
generator client { provider = "prisma-client-js" }
datasource db { provider = "postgresql" url = env("DATABASE_URL") }

model User { id String @id @default(cuid()) wallet String @unique riskScore Int @default(0) createdAt DateTime @default(now()) }
model WalletBinding { id String @id @default(cuid()) userId String chain String address String verifiedAt DateTime? }
model DailyMiningLog { id String @id @default(cuid()) userId String date DateTime hashrate Decimal emission Decimal claimed Boolean @default(false) }
model StakeOrder { id String @id @default(cuid()) userId String tier String amount Decimal start DateTime end DateTime penaltyRate Int }
model RewardClaim { id String @id @default(cuid()) userId String type String amount Decimal txHash String? claimedAt DateTime @default(now()) }
model ReferralRelation { id String @id @default(cuid()) userId String l1 String? l2 String? l3 String? boundAt DateTime @default(now()) }
model TeamStatsDaily { id String @id @default(cuid()) userId String date DateTime directCount Int activeCount Int teamHashrate Decimal }
model NodeOrder { id String @id @default(cuid()) userId String tier String tokenId String? costBurned Decimal mintedAt DateTime @default(now()) }
model BurnRecord { id String @id @default(cuid()) source String amount Decimal txHash String burnedAt DateTime @default(now()) }
model BuybackRecord { id String @id @default(cuid()) usdtAmount Decimal rwauAmount Decimal price Decimal burned Decimal injected Decimal at DateTime @default(now()) }
model AssetPool { id String @id @default(cuid()) name String type String size Decimal apyMin Decimal apyMax Decimal term String status String }
model SystemConfig { key String @id value String updatedAt DateTime @updatedAt }
'''


@router.get('/codegen/templates')
def templates() -> Dict[str, Any]:
    return {'status': 'ok', 'frontend_tasks': list(FE_MAP.keys()), 'contract_tasks': list(SC_MAP.keys()), 'backend_tasks': list(BE_MAP.keys())}


@router.post('/codegen/task/{task_id}', dependencies=[Depends(require_key)])
def codegen_task(task_id: str, req: CodegenTaskRequest) -> Dict[str, Any]:
    ensure_tables()
    task = task_by_id(task_id)
    project, root = latest_project_root(req.project_name)
    file_map = files_for_task(task)
    results = [write_file(root / rel, content, req.overwrite) for rel, content in file_map.items()]
    if req.mark_task_done:
        with db.connect() as conn:
            conn.execute('UPDATE rwa_mine_tasks SET status=?, note=?, evidence=?, updated_at=? WHERE task_id=?', ('done', 'auto codegen completed', jd(results), now(), task_id))
    with db.connect() as conn:
        conn.execute('INSERT INTO rwa_mine_codegen_runs (task_id, project_name, root_path, module, status, files_json, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (task_id, project, str(root), task['module'], 'success', jd(results), task['description'], now()))
    db.audit('rwa_mine_codegen_task', 'rwa_mine_task', task_id, {'project': project, 'files': results}, 'success', 'medium', 'not_required')
    return {'status': 'success', 'task': task, 'project_name': project, 'root_path': str(root), 'files': results}


@router.post('/codegen/batch', dependencies=[Depends(require_key)])
def codegen_batch(req: CodegenBatchRequest) -> Dict[str, Any]:
    ensure_tables()
    clauses = ['1=1']
    args: list[Any] = []
    if req.module:
        clauses.append('module=?'); args.append(req.module)
    if req.priority:
        clauses.append('priority=?'); args.append(req.priority)
    if req.status_filter:
        clauses.append('status=?'); args.append(req.status_filter)
    sql = f"SELECT task_id FROM rwa_mine_tasks WHERE {' AND '.join(clauses)} ORDER BY task_id LIMIT ?"
    args.append(req.limit)
    tasks = qrows(sql, tuple(args))
    results = []
    for t in tasks:
        results.append(codegen_task(t['task_id'], CodegenTaskRequest(project_name=req.project_name, mark_task_done=req.mark_task_done, overwrite=req.overwrite)))
    return {'status': 'success', 'count': len(results), 'results': results}


@router.get('/codegen/runs')
def codegen_runs(limit: int = 100) -> List[Dict[str, Any]]:
    ensure_tables()
    return qrows('SELECT * FROM rwa_mine_codegen_runs ORDER BY id DESC LIMIT ?', (limit,))
