# HMN 当前优先级执行清单

> 更新时间：2026-05-13
> 用途：把当前讨论确认过的推进顺序单独留档，避免后续统筹重复发散。
> 口径：`docs/priority-plan.md` 是完整总纲；本文是当前施工顺序。若两者冲突，以最新代码状态 + 本文为当下执行准绳，并回写总纲。

## 长期原则

Hermes 是 HMN 的自动化孵化器，不是最终拐杖。

当前阶段可以依赖 Hermes/AI 做复杂运维探索、异常兜底和经验抽取；但成功路径必须沉淀为 HMN 原生能力：CLI、worker task、provider、playbook、shell script、docs template 和 test case。

最终目标是：普通用户一键接入、一键巡检、一键备份、一键恢复、一键迁移；AI 只保留为解释、规划和异常兜底，而不是核心执行路径的唯一依赖。

## P0 已完成 / 收尾

### 1. 全托管统筹闭环与分支债务清理

状态：第一阶段已完成；剩余为收尾增强。

当前结论：
- 本地分支已收敛到主线与 `main`。
- worktree 已清理到单 worktree。
- 已完成全量测试门禁。
- 后续仍可补 `hmn orchestrator backlog` / `hmn orchestrator merge-queue`，但不再阻塞主线开发。

### 2. Worker watchdog / stuck recovery 第一阶段

状态：已完成。

已落地：
- 提交：`3a5e907 fix(worker): add task watchdog recovery`。
- `Task` / SQLite schema 增加 `claimed_at`、`lease_expires_at`、`attempt_count`、`failure_reason`。
- `/api/v1/nodes/{node_id}/tasks/next` 原子 claim，避免并发 poll 重复领取。
- claim 前自动 expire stuck running worker task。
- `complete_task` 只允许 `running -> succeeded/failed`，迟到 result 不覆盖 terminal 状态。
- stdout/stderr 64KiB server-side cap。
- 新增 `hmn task recover-stuck --older-than SECONDS`。
- 完整门禁：`375 passed`。

## P0 当前第一优先级

### 3. Worker timeout / heartbeat / cancel / watch

目标：从“过期 running 可恢复”升级到“长任务可观测、可取消、可主动告警”。

建议拆分：
1. `feat(worker): add task timeout, heartbeat and cancel`
2. `feat(task): add watch command and queue diagnostics`

必做能力：
- worker 执行命令使用独立 process group，timeout 可整体 kill。
- `timeout_sec` / `idle_timeout_sec` / `heartbeat_at` / `last_output_at`。
- `cancel_requested_at` 和 `hmn task cancel <task_id>`。
- worker running heartbeat / progress API。
- stdout tail / 日志上限 / `output_truncated`。
- `hmn task watch <task_id>`。
- `hmn task list` 显示 duration、started_at、last_output_at、阻塞队列信息。
- `hmn node worker-status` 显示 current_task、queue_depth、timeout 剩余时间。
- worker 重启后清理遗留 running / orphan child process。
- 卡住约 10 分钟主动上报状态、影响、止损命令和下一步。

验收标准：
- `sleep 999` 会被 timeout kill，后续队列继续执行。
- 长任务持续输出不会被 idle timeout 误杀。
- `hmn task cancel <id>` 可取消当前任务。
- `hmn task watch <id>` 能看到 heartbeat / stdout tail / timeout 剩余时间。
- `hmn task list` 能看出哪个 running 任务堵队列。

## P0 后续

### 4. 核心安全闭环

范围：
- `hmn token revoke <TOKEN_OR_ID>`。
- token list 显示 active / used / expired / revoked。
- join API 明确拒绝 expired / revoked / used token。
- revoked node 不能 heartbeat / poll / submit。
- task result 只能由对应 node/fingerprint 提交。
- worker protocol 不兼容时继续允许 heartbeat，但拒绝 task dispatch。

### 5. 审批状态机与高风险动作边界

范围：
- `hmn approval list`
- `hmn approval show <ID>`
- `hmn approval approve <ID>`
- `hmn approval reject <ID>`
- high risk 必须审批。
- critical 默认拒绝。
- SSH / playbook / Headscale ACL/tag 进入 approval。

### 6. Orchestrator merge queue / backlog 收尾

范围：
- `hmn orchestrator backlog`
- `hmn orchestrator merge-queue`
- merge-first / WIP limit 原生命令化。
- 分支 absorbed / stale / conflict 判重自动化。

## P1 主线

### 7. 服务发现与 service registry

目标：让 HMN 自动知道一台机器上跑了什么服务，并驱动监控、文档、备份、迁移。

识别范围：
- systemd units
- Docker containers
- Docker Compose projects
- Caddy / Nginx site config
- listening ports
- data / env / log paths

### 8. Provider 化控制面补齐

范围：
- Backup provider dry-run + plan。
- Config provider 接 Ansible / AWX 结果。
- Deploy provider 补 apply / status / rollback。

### 9. Headscale + Executor 联动

剩余重点：
- 高风险 SSH / playbook 进入 approval。
- Headscale tag / ACL 更新进入 approval。

## P1 / P2

### 10. 安装 / 交付任务 job steps

目标：把黑盒安装命令拆成可观测、可恢复、可清理的 step job。

建议步骤：
- probe
- download
- checksum
- extract
- install
- configure
- verify

每步应有独立 timeout、日志、状态、失败原因和重试策略。

## P2

### 11. NAS / IPv6 接入优化

原则：
- 不开放 NAS 入站。
- 继续使用 worker pull。
- 支持 endpoint fallback。
- 兼容群晖、QNAP、OpenWrt 等无 systemd 环境。

### 12. 组件真实能力

范围：
- monitor
- reverse-proxy
- forwarder
- backup
- docs-sync

## 推荐执行顺序

1. `feat(worker): add task timeout, heartbeat and cancel`
2. `feat(task): add watch command and queue diagnostics`
3. `feat(security): enforce token and revoked node boundaries`
4. `feat(approval): add approval state machine and high-risk boundaries`
5. `feat(orchestrator): add merge queue and branch backlog status`
6. `feat(discovery): build service discovery dry-run`
7. `feat(registry): persist service registry and diff`
8. `feat(provider): complete backup/config/deploy provider apply/status loops`
9. `feat(monitor): generate uptime kuma plan from service registry`
10. `feat(job): add install step runner and artifact cleanup`
11. `feat(backup): add provider dry-run from service registry`
12. `feat(docs): enrich docs-sync from service registry`
13. `feat(migration): generate migration plan from service registry`

## 当前执行结论

下一步不要再开新方向，优先继续 Worker timeout / heartbeat / cancel / watch。

原因：worker stuck recovery 已能避免永久堵队列，但还缺少进程级 timeout、running heartbeat、主动取消和可观测诊断；这是当前最影响全托管可靠性的点。
