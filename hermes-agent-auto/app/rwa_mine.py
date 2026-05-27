from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings

router = APIRouter(prefix='/rwa-mine', tags=['RWA Mine MVP Command Center'])


def require_key(x_hermes_api_key: str = Header(default='')) -> None:
    settings = get_settings()
    if settings.hermes_agent_api_key and x_hermes_api_key != settings.hermes_agent_api_key:
        raise HTTPException(status_code=401, detail='Invalid API key')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jd(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


MVP_SCOPE = [
    {'module': '钱包连接（多链）', 'included': True, 'priority': 'P0', 'note': '支持 MetaMask / WalletConnect'},
    {'module': '每日激活挖矿', 'included': True, 'priority': 'P0', 'note': '链上激活 + 产出计算'},
    {'module': '锁仓系统', 'included': True, 'priority': 'P0', 'note': '4 个档位，sRWAU 铸造/销毁'},
    {'module': '1 级邀请返佣', 'included': True, 'priority': 'P0', 'note': '仅 L1 现金返佣，L2/3 延后'},
    {'module': '回购销毁看板', 'included': True, 'priority': 'P0', 'note': '链上数据展示'},
    {'module': '节点限量铸造', 'included': True, 'priority': 'P0', 'note': 'Standard + Gold 两级先上'},
    {'module': '个人中心', 'included': True, 'priority': 'P0', 'note': '持仓、锁仓、收益记录'},
    {'module': 'RWA 资产池（真实）', 'included': False, 'priority': 'P1', 'note': '第二版接入真实资产'},
    {'module': 'L2/L3 邀请奖励', 'included': False, 'priority': 'P1', 'note': '第二版开放'},
    {'module': 'Genesis 节点', 'included': False, 'priority': 'P1', 'note': '冷启动结束后开放'},
    {'module': '治理投票', 'included': False, 'priority': 'P2', 'note': '后续 DAO 版本'},
]

FE_TASKS = [
    ('FE-001','frontend','Sprint 1','Next.js 项目初始化，Tailwind + TypeScript 配置','P0',1,'前端'),
    ('FE-002','frontend','Sprint 1','RainbowKit 钱包连接集成，多链配置','P0',2,'前端'),
    ('FE-003','frontend','Sprint 1','wagmi + viem 合约调用封装层','P0',2,'前端'),
    ('FE-004','frontend','Sprint 1','首页布局：顶部导航 + 公告 Banner','P0',1,'前端'),
    ('FE-005','frontend','Sprint 1','首页：TVL/销毁量/算力 KPI 卡片组件','P0',1,'前端'),
    ('FE-006','frontend','Sprint 1','ECharts 全局封装，主题适配','P0',1,'前端'),
    ('FE-007','frontend','Sprint 1','回购销毁折线图组件','P0',2,'前端'),
    ('FE-008','frontend','Sprint 1','节点剩余席位进度条组件','P0',1,'前端'),
    ('FE-009','frontend','Sprint 2','挖矿页：每日激活大按钮 + 状态管理','P0',2,'前端'),
    ('FE-010','frontend','Sprint 2','算力明细展示组件（来源分类）','P0',2,'前端'),
    ('FE-011','frontend','Sprint 2','减产倒计时组件','P0',1,'前端'),
    ('FE-012','frontend','Sprint 2','近 7 日挖矿产出柱状图','P0',1,'前端'),
    ('FE-013','frontend','Sprint 2','个人中心：持仓/锁仓/收益总览','P0',2,'前端'),
    ('FE-014','frontend','Sprint 2','操作记录时间轴组件','P0',2,'前端'),
    ('FE-015','frontend','Sprint 2','资产分布饼图','P1',1,'前端'),
    ('FE-016','frontend','Sprint 3','锁仓页：4 档位选择 + 金额输入 + 确认流','P0',3,'前端'),
    ('FE-017','frontend','Sprint 3','锁仓明细列表 + 解锁按钮（含提前解锁警告）','P0',2,'前端'),
    ('FE-018','frontend','Sprint 3','节点页：等级卡片 + 权益对比表','P0',2,'前端'),
    ('FE-019','frontend','Sprint 3','节点铸造流程（确认销毁 + 钱包签名）','P0',2,'前端'),
    ('FE-020','frontend','Sprint 3','推广页：邀请链接生成 + 复制功能','P0',1,'前端'),
    ('FE-021','frontend','Sprint 3','团队统计 + 直推活跃人数展示','P0',2,'前端'),
    ('FE-022','frontend','Sprint 3','排行榜组件（本月 Top 10）','P1',2,'前端'),
    ('FE-023','frontend','Sprint 3','RWA 资产池展示型列表（静态数据）','P1',2,'前端'),
]

SC_TASKS = [
    ('SC-001','contract','Contracts','RWAUToken — ERC-20 + 销毁 + 买卖税 + 黑名单','P0',3,'合约'),
    ('SC-002','contract','Contracts','sRWAUToken — 不可转让 ERC-20，受 StakingVault 控制','P0',2,'合约'),
    ('SC-003','contract','Contracts','StakingVault — 4 档锁仓，提前解锁罚金，sRWAU mint/burn','P0',4,'合约'),
    ('SC-004','contract','Contracts','RewardDistributor — 每日激活，算力计算，收益分发','P0',5,'合约'),
    ('SC-005','contract','Contracts','ReferralRegistry — 邀请绑定，L1 返佣触发，防女巫基础','P0',3,'合约'),
    ('SC-006','contract','Contracts','NodeNFT — ERC-721，2 级节点，铸造销毁主币','P0',3,'合约'),
    ('SC-007','contract','Contracts','Treasury — 收入接收，回购执行，销毁接口','P0',3,'合约'),
    ('SC-008','contract','Contracts','Proxy 升级结构（OpenZeppelin TransparentProxy）','P0',2,'合约'),
    ('SC-009','contract','Contracts','多签钱包配置（Gnosis Safe 3/5）','P0',1,'合约'),
    ('SC-010','contract','Contracts','测试套件：单元测试覆盖率 > 90%','P0',5,'合约'),
    ('SC-011','contract','Contracts','测试网部署 + 集成测试 30 天','P0',30,'合约'),
    ('SC-012','contract','Contracts','外部安全审计','P0',14,'第三方'),
]

BE_TASKS = [
    ('BE-001','backend','Backend','NestJS 项目初始化，模块划分，CI/CD 配置','P0',2,'后端'),
    ('BE-002','backend','Backend','PostgreSQL 数据库 Schema 建表（12 张核心表）','P0',2,'后端'),
    ('BE-003','backend','Backend','Redis 缓存层配置（算力缓存、排行榜）','P0',1,'后端'),
    ('BE-004','backend','Backend','链上事件监听服务（BullMQ 队列）','P0',4,'后端'),
    ('BE-005','backend','Backend','用户模块 API（钱包绑定、信息查询）','P0',2,'后端'),
    ('BE-006','backend','Backend','挖矿模块 API（激活记录、产出查询）','P0',3,'后端'),
    ('BE-007','backend','Backend','锁仓模块 API（订单管理、收益计算）','P0',3,'后端'),
    ('BE-008','backend','Backend','邀请模块 API（关系链、团队统计）','P0',2,'后端'),
    ('BE-009','backend','Backend','节点模块 API（持有查询、权益计算）','P0',2,'后端'),
    ('BE-010','backend','Backend','看板数据 API（TVL、销毁、算力聚合）','P0',3,'后端'),
    ('BE-011','backend','Backend','WebSocket 实时推送（回购销毁播报）','P1',2,'后端'),
    ('BE-012','backend','Backend','风控规则引擎（异常检测、频率限制）','P0',3,'后端'),
    ('BE-013','backend','Backend','定时任务：每日排放计算、健康度评分','P0',2,'后端'),
]

DB_TABLES = [
    {'table':'users','fields':'id, wallet, created_at, risk_score','purpose':'用户基础信息'},
    {'table':'wallet_bindings','fields':'user_id, chain, address, verified_at','purpose':'多链钱包绑定'},
    {'table':'daily_mining_logs','fields':'user_id, date, hashrate, emission, claimed','purpose':'每日挖矿记录'},
    {'table':'stake_orders','fields':'id, user_id, tier, amount, start, end, penalty_rate','purpose':'锁仓订单'},
    {'table':'reward_claims','fields':'id, user_id, type, amount, tx_hash, claimed_at','purpose':'领取记录'},
    {'table':'referral_relations','fields':'user_id, l1, l2, l3, bound_at','purpose':'邀请关系链'},
    {'table':'team_stats_daily','fields':'user_id, date, direct_count, active_count, team_hashrate','purpose':'团队日统计'},
    {'table':'node_orders','fields':'id, user_id, tier, token_id, cost_burned, minted_at','purpose':'节点铸造记录'},
    {'table':'burn_records','fields':'id, source, amount, tx_hash, burned_at','purpose':'所有销毁记录'},
    {'table':'buyback_records','fields':'id, usdt_amount, rwau_amount, price, burned, injected, at','purpose':'回购记录'},
    {'table':'asset_pools','fields':'id, name, type, size, apy_min, apy_max, term, status','purpose':'资产池信息'},
    {'table':'system_configs','fields':'key, value, updated_at','purpose':'系统参数配置'},
]

TIMELINE = [
    {'weeks':'W1-W2','stage':'基础框架','deliverables':'前端骨架 + 钱包连接 + 合约基础代码','milestone':'—'},
    {'weeks':'W3-W4','stage':'核心功能','deliverables':'挖矿激活 + 个人中心 + 锁仓合约','milestone':'内部演示'},
    {'weeks':'W5-W6','stage':'完整功能','deliverables':'节点铸造 + 邀请 + 后端 API 全联调','milestone':'Alpha 测试'},
    {'weeks':'W7-W8','stage':'测试 + 安全','deliverables':'合约审计 + 压力测试 + Bug 修复','milestone':'—'},
    {'weeks':'W9-W10','stage':'上线准备','deliverables':'测试网 30 天运行 + 创世节点发售准备','milestone':'Beta'},
    {'weeks':'W11-W12','stage':'正式上线','deliverables':'主网部署 + 创世节点开放 + 运营启动','milestone':'Launch'},
]

SECURITY_CHECKS = [
    'RWAUToken 买卖税上限、黑名单权限、销毁逻辑必须可审计',
    'sRWAUToken 必须不可转让，只能由 StakingVault mint/burn',
    'StakingVault 需覆盖 4 档锁仓、提前解锁罚金、重复解锁、防重入',
    'RewardDistributor 需防每日激活刷量、重复领取、算力异常膨胀',
    'ReferralRegistry 需防循环邀请、同地址/同设备异常、L1 返佣刷量',
    'NodeNFT 需防重复铸造、越权铸造、销毁成本错误',
    'Treasury、ProxyAdmin、税率参数、黑名单权限必须归 Gnosis Safe 3/5',
    '测试覆盖率必须 > 90%，测试网集成测试必须 30 天',
    '外部安全审计不可跳过，宁可延期也不降低安全质量',
    'RWA 资产池第一版只能展示静态数据，不能表述为真实保本收益',
]

GATE_CHECKS = [
    'P0 前端任务全部完成', 'P0 合约任务全部完成', 'P0 后端任务全部完成',
    '合约测试覆盖率 > 90%', '测试网运行 30 天', '外部安全审计完成',
    'Gnosis Safe 3/5 多签配置完成', 'Proxy Admin / Treasury / 参数权限归多签',
    'RWA 静态资产展示无误导性收益表述', '上线回滚预案完成',
]

class TaskUpdate(BaseModel):
    status: str = Field(default='todo', pattern='^(todo|doing|blocked|done)$')
    note: str = ''
    evidence: str = ''

class GateUpdate(BaseModel):
    passed: bool = False
    note: str = ''


def ensure_tables() -> None:
    with db.connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_tasks (task_id TEXT PRIMARY KEY,module TEXT NOT NULL,sprint TEXT,description TEXT NOT NULL,priority TEXT NOT NULL,estimate_days INTEGER NOT NULL,owner TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'todo',note TEXT,evidence TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_gate_checks (id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,passed INTEGER NOT NULL DEFAULT 0,note TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS rwa_mine_reports (id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,report_path TEXT NOT NULL,created_at TEXT NOT NULL)''')


def task_rows(query: str, args: tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(query, args).fetchall()]


def one(query: str, args: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    ensure_tables()
    with db.connect() as conn:
        r = conn.execute(query, args).fetchone()
        return dict(r) if r else None


@router.post('/bootstrap', dependencies=[Depends(require_key)])
def bootstrap() -> Dict[str, Any]:
    ensure_tables()
    created = {'tasks': 0, 'gate_checks': 0}
    ts = now()
    with db.connect() as conn:
        for item in FE_TASKS + SC_TASKS + BE_TASKS:
            if not conn.execute('SELECT task_id FROM rwa_mine_tasks WHERE task_id=?', (item[0],)).fetchone():
                conn.execute('INSERT INTO rwa_mine_tasks (task_id,module,sprint,description,priority,estimate_days,owner,status,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (*item, 'todo', ts, ts))
                created['tasks'] += 1
        for name in GATE_CHECKS:
            if not conn.execute('SELECT id FROM rwa_mine_gate_checks WHERE name=?', (name,)).fetchone():
                conn.execute('INSERT INTO rwa_mine_gate_checks (name,passed,note,created_at,updated_at) VALUES (?, ?, ?, ?, ?)', (name, 0, '', ts, ts))
                created['gate_checks'] += 1
    db.audit('rwa_mine_bootstrap', 'rwa_mine_project', None, created, 'success', 'medium', 'not_required')
    return {'status': 'success', 'created': created}


@router.get('/status')
def status() -> Dict[str, Any]:
    ensure_tables()
    total = one('SELECT COUNT(*) c FROM rwa_mine_tasks') or {'c': 0}
    if total['c'] == 0:
        return {'status': 'not_bootstrapped', 'message': 'Run POST /rwa-mine/bootstrap first.', 'version': '10.11-rwa-mine-mvp-command'}
    return overview()


@router.get('/overview')
def overview() -> Dict[str, Any]:
    ensure_tables()
    tasks = task_rows('SELECT * FROM rwa_mine_tasks')
    gates = task_rows('SELECT * FROM rwa_mine_gate_checks')
    def count(where: str) -> int:
        return int((one(f'SELECT COUNT(*) c FROM rwa_mine_tasks {where}') or {'c': 0})['c'])
    modules = {}
    for module in ['frontend','contract','backend']:
        mt = [t for t in tasks if t['module'] == module]
        modules[module] = {'total': len(mt), 'done': len([t for t in mt if t['status'] == 'done']), 'blocked': len([t for t in mt if t['status'] == 'blocked']), 'p0_total': len([t for t in mt if t['priority'] == 'P0']), 'p0_done': len([t for t in mt if t['priority'] == 'P0' and t['status'] == 'done'])}
    return {'status': 'ok', 'version': '10.11-rwa-mine-mvp-command', 'tasks_total': len(tasks), 'tasks_done': len([t for t in tasks if t['status'] == 'done']), 'tasks_blocked': len([t for t in tasks if t['status'] == 'blocked']), 'p0_total': count("WHERE priority='P0'"), 'p0_done': count("WHERE priority='P0' AND status='done'"), 'modules': modules, 'gate_total': len(gates), 'gate_passed': len([g for g in gates if int(g['passed']) == 1]), 'scope': MVP_SCOPE}


@router.get('/tasks')
def list_tasks(module: Optional[str] = None, priority: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    bootstrap_if_empty()
    q = 'SELECT * FROM rwa_mine_tasks WHERE 1=1'
    args: list[Any] = []
    if module:
        q += ' AND module=?'; args.append(module)
    if priority:
        q += ' AND priority=?'; args.append(priority)
    if status:
        q += ' AND status=?'; args.append(status)
    q += ' ORDER BY task_id'
    return task_rows(q, tuple(args))


def bootstrap_if_empty() -> None:
    ensure_tables()
    c = one('SELECT COUNT(*) c FROM rwa_mine_tasks') or {'c': 0}
    if int(c['c']) == 0:
        bootstrap()


@router.post('/tasks/{task_id}/status', dependencies=[Depends(require_key)])
def update_task(task_id: str, req: TaskUpdate) -> Dict[str, Any]:
    bootstrap_if_empty()
    if not one('SELECT task_id FROM rwa_mine_tasks WHERE task_id=?', (task_id,)):
        raise HTTPException(status_code=404, detail='task not found')
    with db.connect() as conn:
        conn.execute('UPDATE rwa_mine_tasks SET status=?, note=?, evidence=?, updated_at=? WHERE task_id=?', (req.status, req.note, req.evidence, now(), task_id))
    db.audit('rwa_mine_task_update', 'rwa_mine_task', task_id, req.model_dump(), 'success', 'low', 'not_required')
    return one('SELECT * FROM rwa_mine_tasks WHERE task_id=?', (task_id,)) or {'task_id': task_id}


@router.get('/timeline')
def timeline() -> Dict[str, Any]:
    return {'status': 'ok', 'timeline': TIMELINE}


@router.get('/db-schema')
def db_schema() -> Dict[str, Any]:
    return {'status': 'ok', 'tables': DB_TABLES}


@router.get('/security-checklist')
def security_checklist() -> Dict[str, Any]:
    return {'status': 'ok', 'checks': SECURITY_CHECKS}


@router.get('/gate-check')
def gate_check() -> Dict[str, Any]:
    bootstrap_if_empty()
    gates = task_rows('SELECT * FROM rwa_mine_gate_checks ORDER BY id')
    p0_incomplete = task_rows("SELECT task_id,description,status FROM rwa_mine_tasks WHERE priority='P0' AND status!='done' ORDER BY task_id")
    gate_failures = [g for g in gates if int(g['passed']) != 1]
    launch_ready = len(p0_incomplete) == 0 and len(gate_failures) == 0
    return {'status': 'ok', 'launch_ready': launch_ready, 'p0_incomplete_count': len(p0_incomplete), 'p0_incomplete': p0_incomplete[:50], 'gate_total': len(gates), 'gate_passed': len(gates) - len(gate_failures), 'gate_failures': gate_failures}


@router.post('/gate-check/{gate_id}', dependencies=[Depends(require_key)])
def update_gate(gate_id: int, req: GateUpdate) -> Dict[str, Any]:
    bootstrap_if_empty()
    if not one('SELECT id FROM rwa_mine_gate_checks WHERE id=?', (gate_id,)):
        raise HTTPException(status_code=404, detail='gate check not found')
    with db.connect() as conn:
        conn.execute('UPDATE rwa_mine_gate_checks SET passed=?, note=?, updated_at=? WHERE id=?', (int(req.passed), req.note, now(), gate_id))
    db.audit('rwa_mine_gate_update', 'rwa_mine_gate', str(gate_id), req.model_dump(), 'success', 'medium', 'not_required')
    return one('SELECT * FROM rwa_mine_gate_checks WHERE id=?', (gate_id,)) or {'id': gate_id}


@router.post('/daily-report', dependencies=[Depends(require_key)])
def daily_report() -> Dict[str, Any]:
    bootstrap_if_empty()
    settings = get_settings()
    report_dir = Path(settings.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    ov = overview()
    gate = gate_check()
    blocked = list_tasks(status='blocked')
    doing = list_tasks(status='doing')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = report_dir / f'rwa_mine_daily_{stamp}.md'
    lines = [
        '# RWA Mine MVP Daily Report', '', f'- created_at_utc: {now()}', f'- version: {ov["version"]}', '', '## Progress',
        f'- tasks_total: {ov["tasks_total"]}', f'- tasks_done: {ov["tasks_done"]}', f'- p0_total: {ov["p0_total"]}', f'- p0_done: {ov["p0_done"]}', f'- launch_ready: {gate["launch_ready"]}', '', '## Module Progress',
    ]
    for k, v in ov['modules'].items():
        lines.append(f'- {k}: {v["done"]}/{v["total"]}; P0 {v["p0_done"]}/{v["p0_total"]}; blocked {v["blocked"]}')
    lines += ['', '## Doing Tasks']
    for t in doing[:20]:
        lines.append(f'- {t["task_id"]}: {t["description"]}')
    lines += ['', '## Blocked Tasks']
    for t in blocked[:20]:
        lines.append(f'- {t["task_id"]}: {t["description"]} — {t.get("note") or ""}')
    lines += ['', '## Gate Check']
    for g in gate['gate_failures'][:30]:
        lines.append(f'- [ ] {g["name"]} — {g.get("note") or ""}')
    lines += ['', '## Security Red Lines']
    for s in SECURITY_CHECKS:
        lines.append(f'- {s}')
    path.write_text('\n'.join(lines), encoding='utf-8')
    with db.connect() as conn:
        conn.execute('INSERT INTO rwa_mine_reports (title, report_path, created_at) VALUES (?, ?, ?)', ('RWA Mine MVP Daily Report', str(path), now()))
    db.audit('rwa_mine_daily_report', 'rwa_mine_report', None, {'report_path': str(path)}, 'success', 'low', 'not_required')
    return {'status': 'success', 'report_path': str(path), 'overview': ov, 'gate': gate}


@router.get('/reports')
def list_reports() -> List[Dict[str, Any]]:
    ensure_tables()
    return task_rows('SELECT * FROM rwa_mine_reports ORDER BY id DESC LIMIT 100')
