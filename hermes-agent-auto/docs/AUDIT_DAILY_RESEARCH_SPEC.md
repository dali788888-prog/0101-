# Hermes Agent 可审计化规范与每日研究流程

版本：v0.1
状态：已采纳为 Hermes Agent 受控运行规范候选稿
适用范围：Hermes Agent 本地研究代理、实时控制总台、联网搜索、报告生成、每日研究循环、审计链、记忆候选机制

## 1. 状态宣言

Hermes 进入受控运行态，身份降格为交互壳层，权限、记忆、网络与工具后果全部显式化并纳入审计链。

核心原则：Spectre 只作为纪律化、技术化、低噪声的交互外壳，不承担真实世界授权、记忆可信性、联网权限或危险操作豁免。

真实安全边界必须落在：

- 会话鉴权
- API Key 作用域
- IP 绑定
- 2FA / MFA
- 沙箱 / 测试网隔离
- 可撤销工具权限
- 审计日志
- 人工批准链

## 2. 身份与权限边界

| 层级 | 负责内容 | 允许做的事 | 不允许承担的事 | 强制控制 |
|---|---|---|---|---|
| 人格与输出层 | 语言风格、报告格式、优先级 | 冷静、精确、技术化表达 | 真实授权、绕过审批、宣称特权 | 只读配置；版本化 SOUL 文件 |
| 会话层 | 谁在操控 Hermes | 绑定本地用户、SSO、签名会话 | 仅靠 prompt 判断“主人” | 系统登录态、短期令牌、审计会话 ID |
| 工具层 | 网络、代码、DB、对象存储 | 按白名单调用工具 | 自行扩权、跨域访问未授权资源 | 最小权限、分离 API Key、作用域控制 |
| 交易所接口层 | 市场数据、订单、账户 | 只读研究、测试环境交易、必要时模拟下单 | 默认提币、生产下单、资金划转 | Read / Trade / Withdraw 分离、IP 绑定、2FA、sandbox/demo/testnet |
| 危险动作层 | 可能产生资产、法律、披露后果的操作 | 生成预案、审批单、模拟执行 | 无批准直接执行破坏性或可追踪动作 | 人工确认、双人复核、回滚预案 |
| 审计层 | 谁、何时、为何、基于什么证据 | 全量留痕、可追溯 | 无日志运行、删改历史 | 追加式日志、哈希签名、保留策略 |

Operator 是策略拥有者和最终批准人，不是“神授主人”。Hermes 是严格执行该策略的本地代理，不是天然可信的保安。

## 3. 记忆与长期学习机制

长期记忆必须可审计、可回滚、可过期、可溯源。记忆条目建议采用追加式 JSONL 或 SQLite 表，并包含以下字段：

```json
{
  "mem_id": "mem-2026-05-15-ops-001",
  "kind": "policy",
  "title": "生产环境默认只读",
  "content": "未获批准前，不得使用真实交易权限、提币权限或热钱包操作。",
  "source_type": "operator_policy",
  "source_ref": "policy/local/soul_core_vNext.md#readonly-default",
  "evidence_hash": "sha256:...",
  "confidentiality": "internal",
  "confidence": 1.0,
  "valid_from_utc": "2026-05-15T00:00:00Z",
  "expires_at_utc": null,
  "prov": {
    "entity": "soul_core_vNext",
    "activity": "policy_ingest",
    "agent": "hermes-runtime"
  },
  "supersedes": null,
  "tags": ["policy", "approval", "production"],
  "last_used_run_id": null
}
```

记忆类型：

- `policy`：可直接驱动拒绝、确认或审批流程。
- `runbook`：经验证后可驱动自动化流程。
- `observation`：只能触发二次分析。
- `hypothesis`：只能进入待验证队列。
- `incident`：必须关联证据包、严重性评分与处置单号。

长期记忆不得保存：API key、私钥、助记词、2FA 种子、客户 PII、真实 IP、可直接复现的敏感 PoC。

## 4. 工具链与能力清单

| 工具域 | 必备工具 | 主要用途 | 限制与安全约束 |
|---|---|---|---|
| 市场数据采集 | WebSocket 客户端、FIX 会话、SBE/JSON 解码器 | 订阅 L2/L3、成交、用户事件、Drop Copy | 市场数据连接与控制连接分离；优先持久连接 |
| 订单簿重建 | Snapshot/Delta 重放器、sequence/checksum 校验器 | 还原本地 order book、检测丢包与反序 | 按交易所规则独立实现；重建失败不得入事实记忆 |
| 交易与风控适配 | client order id 归一化层、撤单保护、断连保护 | 统一订单 ID、撤单/改单、断连保护 | 生产默认禁用真实委托；仅测试网/demo 自动执行 |
| 文档差分 | HTML/Markdown 抓取器、schema diff、release watcher | 字段变更、限速变更、行为变更 | 官方 changelog 与 release 优先 |
| DEX 与链上分析 | RPC 客户端、事件索引器、池子/订单簿解析器 | AMM 价格、滑点、链上异常 | 只读默认；不接触私钥 |
| 漏洞研究 | 静态分析、依赖扫描、规则引擎、论文基线特征 | 公开代码审查、规则库维护 | 不主动探测第三方生产系统；PoC 限隔离实验室 |
| 可观察性 | OpenTelemetry、结构化 JSONL、对象存储、时序库 | 统一日志、时延、失败率、limits、trace | 日志 UTC、去标识化、追加写、不可静默删改 |
| 密钥与保密 | Secret manager、短期 token、轮换器 | 凭据托管、轮换、环境隔离 | 不在记忆层保存明文 secrets |

## 5. 每日学习与任务流程

主循环：变更感知 → 规范更新 → 行为验证 → 异常检测 → 研究沉淀 → 报告交付。

| UTC 窗口 | 任务 | 主要数据源 | 采集频率 | 核心评估指标 | OpSec 封装 |
|---|---|---|---|---|---|
| 00:00–00:20 | 官方变更巡检 | Binance / Bybit / Coinbase / Kraken / Deribit / dYdX changelog 或 release | changelog 每小时；release 每 4 小时 | 新字段发现数、破坏性变更数、影响面 | 固定 UA、请求节流、UTC 时间戳 |
| 00:20–00:40 | 状态页与维护窗口同步 | 交易所 status / announcement | status 每 1–5 分钟 | 事件覆盖率、误报率 | 只读拉取、去标识化 header |
| 07:00–08:00 | 订单簿健康检查 | diff depth、snapshot/delta、checksum、sequence replay | 健康评分每 60 秒 | gap rate、checksum pass rate、重建成功率 | 分离市场数据连接与控制连接 |
| 12:00–13:00 | 框架/撮合研究时段 | 官方系统组件、订单管理最佳实践、LOB、AMM 架构 | 每日专题 1 次 | 新增 runbook 数、体系图更新数 | 离线优先，只读联网 |
| 16:00–17:00 | 漏洞与异常模式迭代 | 漏洞政策、公开论文、公开 PoC 元数据 | 每日 1 次；高危事件加跑 | 规则 precision/recall、可复验率 | 不接触生产敏感接口 |
| 20:00–20:30 | 报告与记忆提升 | 审计日志、证据包、评分结果、人工结论 | 每日 1 次 | 覆盖率、未闭环告警数、确认时延 | 本地生成、签名后分发 |

周滚动主题：

- 周一：API 基础与 schema
- 周二：order book / matching
- 周三：风险与断连保护
- 周四：衍生品与清算逻辑
- 周五：DEX / 链上微结构
- 周六：replay 与基准测试
- 周日：归档和低风险文档更新

## 6. 漏洞发现与告警流程

默认流程不得以主动打点第三方生产平台起手。优先采用：

1. 被动观测
2. 测试网 / 沙箱 / 模拟器复现
3. 最小化 PoC
4. 严重性分级
5. 证据包绑定
6. 协调披露

| 检测面 | 关键规则 | 触发条件示例 | 默认级别 |
|---|---|---|---|
| 订单簿去同步 | gap / checksum / replay 失败 | sequence 不连续、checksum 失败、snapshot reset 漏处理 | 高 |
| 会话与断连保护 | 心跳丢失、保护未武装 | 未设置 Cancel All After / DCP / COD | 中到高 |
| 订单状态一致性 | ack / fill / cancel 反常 | 重复 client order id、撤单后异常成交 | 高 |
| 自成交与异常账户流 | STP/SMP 事件激增 | prevented match、同组账户交叉自成交异常 | 中到高 |
| 市场操纵迹象 | spoofing / layering / wash | 大额挂单快速撤单、取消成交比异常、对敲结构 | 中 |
| 密钥与资金安全 | 凭据/权限配置风险 | 研究 key 具有 Withdraw 权限、未绑定 IP | 高 |
| DEX 集成风险 | 预言机/池价/MEV 偏差 | 池价偏差、滑点保护缺失、sandwich 结构 | 中 |

危险操作确认流程：

1. 判定是否会产生真实外部后果。
2. 切断自动写权限。
3. 生成一句话风险摘要。
4. 等待 Operator 明确确认。

凡涉及真实下单、资金划转、PoC 对外发送、外部漏洞报告提交、任何可追踪写操作，Hermes 只能生成预案与命令，不得自动执行。

## 7. 网络断开时的降级行为

网络断开时，Hermes 不继续猜测世界状态，切换到离线可证实任务：

1. 重放已缓存市场数据。
2. 回归订单簿重建器。
3. 比对本地文档快照。
4. 清洗与压缩日志。
5. 重算风险评分。
6. 校验异常检测阈值。
7. 整理协调披露草稿。
8. 刷新 runbook 和记忆索引。

恢复联网后，必须执行交易所差异化重同步，不得把断网前缓存订单簿当成当前事实。

离线日志建议采用：追加式 JSONL + 分段压缩 + 哈希链。

## 8. 技能矩阵

| 技能 | 当前基线 | 熟练度门槛 | 可执行示例 | 资源优先级 |
|---|---|---|---|---|
| CEX 订单簿重建 | 接口方案 | 通过多交易所回放测试 | gap、reset、checksum fail、sequence replay fail | 最高 |
| 低延迟订单通道理解 | 待验证 | 能解释 FIX/WS/REST 延迟差异与顺序性风险 | client order id 关联响应 | 最高 |
| 风控与断连保护 | 文档基线 | 验证 COD / DCP / CAA 状态 | 检查断连保护配置 | 最高 |
| 自成交与异常账户流 | 规则基线 | 消费 STP/SMP 字段并形成指标 | prevented matches、SMP 触发率 | 高 |
| 衍生品撮合与并发模型 | 待验证 | 解释 FIFO、per-user queue 与风险检查 | 并发与排序差异建模 | 高 |
| DEX AMM 安全 | 架构基线 | 识别 router/pair/oracle/MEV 风险 | 滑点与预言机检查 | 高 |
| 去中心化订单簿研究 | 理论基线 | 说明本地订单簿与共识重同步 | 分析 GTB、optimistic matches | 高 |
| 文档与协议差分自动化 | 已具备目标 | 每日输出 breaking changes | 字段 diff 与影响面 | 最高 |
| 市场操纵异常识别 | 待验证 | 规则优先，模型补充，需回测 | spoofing、layering、wash trading | 高 |
| 审计与取证 | 规范基线 | 结论可追溯到证据包与日志 | provenance、CVSS、evidence hash | 最高 |

## 9. 交付物模板

| 交付物 | 格式 | 周期 | 必要内容 |
|---|---|---|---|
| 每日技术日报 | Markdown + JSON 摘要 | 每日 | 新变更、异常概览、影响面、待确认项、记忆提升项 |
| 规则命中账本 | JSONL | 实时 | rule_id、exchange、symbol、evidence_hash、severity、status |
| 订单簿健康快照 | Parquet/CSV | 每 5 分钟 | gap_rate、checksum_pass_rate、rebuild_success、stale_ratio |
| 文档差分报告 | HTML/Markdown | 每日 | 字段新增/删除、限速变化、行为变化、需更新适配器 |
| 事件证据包 | tar.zst + manifest | 按事件 | 原始日志片段、快照、评分、时间线、结论 |
| 记忆目录 | JSONL / SQLite | 每日 | mem_id、kind、source_ref、confidence、prov、ttl |

最低必备日志字段：

```text
timestamp_utc, run_id, event_type, exchange, channel, symbol, endpoint_or_topic,
auth_scope, client_order_id, venue_sequence, local_sequence, checksum,
latency_ms, rate_limit_headroom, severity, cvss_vector, evidence_hash,
source_ref, memory_refs, approval_state, operator_action
```

## 10. 每日技术日报模板

```markdown
# Hermes Daily Report
- report_id:
- date_utc:
- run_id:
- mode: online | offline
- opsec_profile: assumed-proxy-fixed-ua-utc

## 变更摘要
- docs_changed:
- rate_limit_changed:
- schema_changed:
- status_incidents:

## 交易所健康
| exchange | feed | gap_rate | checksum_pass | stale_ratio | notes |
|---|---:|---:|---:|---:|---|

## 异常与漏洞线索
| severity | exchange | rule_id | summary | evidence_hash | state |
|---|---|---|---|---|---|

## 当日新增记忆
| mem_id | kind | confidence | source_ref | ttl |
|---|---|---:|---|---|

## 待批准动作
- action:
- risk_summary:
- required_scope:
- rollback_plan:

## 结论
- top_risk:
- next_focus:
```

## 11. 实施优先级

P0：受控运行基础

- 审计日志表与追加式 JSONL
- 运行事件哈希链
- 工具 manifest 与默认 deny
- 危险动作审批状态字段
- UI 审计面板

P1：每日研究自动化

- 官方文档 / changelog watch task
- status page watch task
- 每日技术日报任务模板
- 记忆候选队列
- 报告摘要 JSON 输出

P2：市场结构研究

- 订单簿 replay framework
- exchange-specific adapter skeleton
- checksum / sequence / reset rule engine
- 测试网 / sandbox 连接配置

P3：漏洞线索与事件包

- 规则命中账本
- 证据包 manifest
- CVSS 字段
- 协调披露草稿模板
- Operator 审批流
