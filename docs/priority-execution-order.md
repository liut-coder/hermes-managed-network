# HMN 当前优先级执行清单

> 更新时间：2026-05-12
> 用途：把当前讨论确认过的推进顺序单独留档，避免后续统筹重复发散。

## 长期原则

Hermes 是 HMN 的自动化孵化器，不是最终拐杖。

当前阶段可以依赖 Hermes/AI 做复杂运维探索、异常兜底和经验抽取；但成功路径必须沉淀为 HMN 原生能力：CLI、worker task、provider、playbook、shell script、docs template 和 test case。

最终目标是：普通用户一键接入、一键巡检、一键备份、一键恢复、一键迁移；AI 只保留为解释、规划和异常兜底，而不是核心执行路径的唯一依赖。

## P0 已完成 / 收尾

### 1. 全托管统筹闭环与分支债务清理

状态：已基本完成。

当前结论：
- 本地分支已收敛到主线与 `main`。
- worktree 已清理到单 worktree。
- 已完成全量测试门禁。
- 后续仍可补 `hmn orchestrator backlog` / `hmn orchestrator merge-queue`，但不再阻塞主线开发。

## P0 当前第一优先级

### 2. Worker watchdog / stuck recovery

目标：避免 worker 长任务静默卡住队列。

必做能力：
- task timeout / idle timeout
- worker heartbeat
- `last_output_at`
- cancel task
- running / stalled / timed_out / cancelled 状态
- stdout tail / 日志上限
- 并发 poll 原子领取
- worker 重启后清理遗留 running
- 卡住约 10 分钟主动上报

建议拆分：
1. `feat(worker): add task timeout heartbeat and cancel`
2. `feat(task): add watch command and queue diagnostics`

验收标准：
- `sleep 999` 会被 timeout kill。
- 长任务持续输出不会被 idle timeout 误杀。
- `hmn task cancel <id>` 可取消当前任务。
- `hmn task list` / `hmn task watch` 能看出哪个 running 任务堵队列。

## P0 后续

### 3. 核心安全闭环

范围：
- token revoke / expired / used 校验
- revoked node 不能 heartbeat / poll / submit
- task result 只能由对应 node 提交
- worker protocol 不兼容时拒绝派发任务

### 4. 审批状态机与高风险动作边界

范围：
- `hmn approval list`
- `hmn approval show <ID>`
- `hmn approval approve <ID>`
- `hmn approval reject <ID>`
- high risk 必须审批
- critical 默认拒绝
- SSH / playbook / Headscale ACL/tag 进入 approval

## P1 主线

### 5. 服务发现与 service registry

目标：让 HMN 自动知道一台机器上跑了什么服务，并驱动监控、文档、备份、迁移。

识别范围：
- systemd units
- Docker containers
- Docker Compose projects
- Caddy / Nginx site config
- listening ports
- data / env / log paths

### 6. Provider 化控制面补齐

范围：
- Backup provider dry-run + plan
- Config provider 接 Ansible / AWX 结果
- Deploy provider 补 apply / status / rollback

### 7. Headscale + Executor 联动

剩余重点：
- 高风险 SSH / playbook 进入 approval
- Headscale tag / ACL 更新进入 approval

## P1 / P2

### 8. 安装 / 交付任务 job steps

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

### 9. NAS / IPv6 接入优化

原则：
- 不开放 NAS 入站。
- 继续使用 worker pull。
- 支持 endpoint fallback。
- 兼容群晖、QNAP、OpenWrt 等无 systemd 环境。

### 10. 组件真实能力

范围：
- monitor
- reverse-proxy
- forwarder
- backup
- docs-sync

## 推荐执行顺序

1. `feat(worker): add task timeout heartbeat and cancel`
2. `feat(task): add watch command and queue diagnostics`
3. `feat(security): enforce token and revoked node boundaries`
4. `feat(approval): add approval state machine`
5. `feat(discovery): build service discovery dry-run`
6. `feat(registry): persist service registry and diff`
7. `feat(monitor): generate uptime kuma plan from service registry`
8. `feat(job): add install step runner and artifact cleanup`
9. `feat(backup): add provider dry-run from service registry`
10. `feat(docs): enrich docs-sync from service registry`
11. `feat(migration): generate migration plan from service registry`

## 当前执行结论

下一步不要再开新方向，优先实现 Worker watchdog。
这是当前最影响全托管可靠性的点。
