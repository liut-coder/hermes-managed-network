# HMN 优先级推进计划

> 本文用于统一 HMN v1.1 后续推进顺序。当前判断：先关掉未合并分支/全托管统筹债务，再补 worker 可观测性与服务发现，最后推进 backup/docs/migration 的真实执行闭环。

## 当前判断

HMN 不重复造底层组网轮子。

- **Headscale/Tailscale** 负责机器之间的网络连通、虚拟网络身份、ACL、tag、NAT 穿透。
- **HMN Core** 负责节点登记、授权、审批、审计、任务、组件生命周期和资产文档。
- **Worker Pull** 负责无公网节点/NAS 主动出站接入，不要求节点开放入站端口。
- **Orchestrator** 负责全托管开发/运维统筹：发现待办、分配 worker、验收、合并、沉淀经验。

当前 v1.1 已从“架构规划”推进到“可试点的全托管控制面雏形”，但还没到“所有机器/服务自动托管”的完整态。实时优先级要以可交付闭环为准：

1. 先清理当前 worktree / 分支债务，避免 worker 空闲但成果不落地。
2. 立刻补 worker watchdog / task watch / cancel，避免 BeroNas 这类长任务静默堵队列。
3. 建立服务发现 → service registry → monitor/docs/backup/migration 的主线。
4. 将每次使用 HMN 的经验沉淀成测试、文档、provider 或 orchestrator 策略；不好用的体验必须转成 backlog 并排优先级。

---

## 总体架构边界

```text
Telegram / CLI / API
        ↓
HMN Core
  - node registry
  - token lifecycle
  - policy / approval
  - task queue
  - component lifecycle
  - audit
  - docs state
        ↓
Network Provider Adapter
  - headscale
  - tailscale SaaS（后续）
  - none / direct IPv6
        ↓
Execution Layer
  - worker pull
  - SSH executor over Tailscale IP
  - playbook runner
        ↓
Managed Nodes / NAS
  - full-worker
  - lite-worker
  - beacon-only
  - proxy-managed
```

核心原则：

1. HMN 不实现 VPN / NAT 穿透。
2. HMN 不把 Headscale 细节硬编码进 Core。
3. 所有高风险动作必须可审批、可审计、可撤销。
4. 无公网节点优先使用主动 Pull，不开放入站。
5. 全托管自动化必须有验收和落地，不允许只“生成分支”而不合并。

---

## P0：全托管统筹闭环与分支债务清理

**目标：** 解决“worker 空了，但待合并分支还堆着”的统筹失效问题。先把已有成果落地，再继续派新任务。

### 当前实测状态

- 当前主线：`feat/v1-1-useful-ops-mvp`
- 许多 worktree 分支其实已被主线吸收，属于“未清理的完成态分支”。
- 仍有未吸收/需判重的分支：
  - `hmn-task12-coolify`：Coolify provider skeleton
  - `hmn-config-provider-merge-check`：config provider，可能与主线重复，需判重
  - `hmn-docs-center-apply`：docs center apply + restore/migration/onboarding 合集，部分已吸收
  - `hmn-task17-restore-plan` / `hmn-task18-migration-plan` / `hmn-task19-onboarding-plan`：大概率部分已吸收，需按文件/commit 判重
  - `feat/monitor-closed-loop`：monitor/backup/docs-sync manifests + CLI，仍有价值但 diff 较大
  - `feat/production-readiness-p0` / `fix/production-p0-readiness`：production readiness 分支，可能与已有修复重叠

### 根因

- Orchestrator/cron 更偏“推进任务”，缺少“分支收割/验收/合并”的硬性状态机。
- worker 完成任务后，没有自动进入 merge queue。
- 没有统一 dashboard 显示：pending / running / review / merge-ready / merged / stale。
- 合并遇到旧基线/冲突时，没有升级成必须处理的 blocking item。
- cron 任务 `a664a1c7cc73` 已暂停；仍运行的 `b2f723639ca7` 和 `9b36e7b758d9` 没有强制 WIP limit 和 merge-first 策略。

### 要做

- 建立 `hmn orchestrator backlog` / `hmn orchestrator merge-queue`：扫描 worktree、分支、PR、cron job、orchestrator assignment。
- 每个分支记录状态：
  - `generated`
  - `needs-review`
  - `merge-ready`
  - `merged`
  - `duplicate`
  - `conflict`
  - `stale`
  - `abandoned`
- cron 统筹规则改为 **merge-first**：只要有 merge-ready / needs-review 分支，禁止继续派发新开发任务。
- 设置 WIP limit：同一 repo 同时未合并 feature 分支 ≤ 3。
- 每轮 cron 必须输出：
  - 本轮发现了哪些分支
  - 合并了哪些
  - 哪些被判重/废弃
  - 哪个分支阻塞，阻塞原因是什么
  - 下一轮只做哪一件事
- 自动判重规则：若 `git merge-base --is-ancestor <branch> <base>` 为真，标记 merged/absorbed，并提示清理 worktree。
- 对未吸收分支执行：`git diff --name-status base...branch`、窄测试、cherry-pick 或人工抽取。
- 合并后跑轻量门禁：
  - `pytest -q` 或相关窄测试
  - `python -m compileall -q src`
  - `bash -n install.sh scripts/*.sh src/hermes_managed_network/assets/*.sh`
  - `git diff --check`
- 分支合并/判重结果写入 `docs/roadmap.md` 或 `docs/priority-plan.md`，避免下轮重复处理。

### 验收

- `hmn orchestrator status` 能看见未合并分支数量和最老分支年龄。
- cron 连续两轮不能只汇报“ok”却没有 merge/判重/阻塞结论。
- worker 空闲时，orchestrator 自动转向 review/merge，而不是继续等待。
- 被吸收的 worktree 有清理清单，未吸收分支有明确 owner 和下一步。

---

## P0：Worker watchdog / stuck recovery

**目标：** 避免 BeroNas 这类长任务占住 worker 队列 1～2 小时且无主动上报。

### 背景

- BeroNas 实测中，长耗时 `pip install` 任务占住 worker 约 1 小时 49 分钟。
- worker 当前偏串行：一个 running 任务未结束时，后续任务只能 pending。
- 控制面只能看到任务仍在 running，缺少实时进度、idle 判断、自动取消和主动告警。

### 要做

- worker 执行命令时使用独立 process group，超时可整体 kill。
- task 增加/预留：
  - `timeout_sec`
  - `idle_timeout_sec`
  - `heartbeat_at`
  - `last_output_at`
  - `cancel_requested_at`
  - `claimed_by`
  - `worker_pid`
  - `attempt`
  - `max_attempts`
  - `log_path`
  - `output_truncated`
- `tasks/next` claim 要原子化：同一节点并发 poll 不应重复领取同一任务。
- `TaskResponse` 下发 timeout / idle timeout / attempt / cancel token，worker 不靠硬编码默认值。
- 新增任务进度/日志 API：worker 可增量 PATCH stdout/stderr tail、heartbeat、last_output_at。
- 对 stdout/stderr 做 ring buffer 或大小上限，完整日志写节点本地 `log_path`，避免控制面 DB 被大输出撑爆。
- worker 定期上报 running heartbeat，不依赖任务结束后一次性回传。
- 控制面 stuck detector：running 超过阈值且无输出/heartbeat 时标记 stalled。
- 支持 `hmn task cancel <task_id>`，取消后 worker kill 当前任务并继续处理队列。
- `hmn task list` 显示 duration、started_at、last_output_at、当前 running 阻塞队列数量。
- `hmn node worker-status` 显示 current_task、duration、queue_depth、last_output_at、timeout 剩余时间。
- worker 启动/重启时清理 orphan child process，并把遗留 running 任务标记为 interrupted/stalled。
- worker 失败上报要包含 failure_reason：timeout、idle_timeout、cancelled、signal、exec_disabled、signature_mismatch、network_error。
- worker poll / submit / progress 回传失败时区分网络错误与任务执行错误；执行结果先落本地 spool，网络恢复后补交。
- 多 master endpoint fallback 覆盖 heartbeat / task poll / result submit / progress submit。
- Telegram/全托管上报：任务卡住约 10 分钟或队列阻塞时主动报告状态、影响、止损命令和下一步。

### 建议状态

```text
pending
claimed
running
stalled
timed_out
cancel_requested
cancelled
interrupted
retrying
succeeded
failed
```

### 验收

- 一个 `sleep 999` 或无输出长任务会在 timeout 后被 kill，后续队列继续执行。
- 一个持续输出的长任务不会被 idle timeout 误杀，但会更新 `last_output_at`。
- 两个 worker tick 并发 poll 也不会重复领取同一 pending task。
- stdout 超大任务不会撑爆 SQLite；CLI 能看到 tail，并标记 `output_truncated`。
- master 短暂不可达时，worker 先本地 spool 结果，恢复后补交。
- `hmn task watch <task_id>` 能看到 running heartbeat / stdout tail / timeout 剩余时间。
- `hmn task cancel <task_id>` 能取消当前 worker 任务。
- `hmn task list` 能直接看出哪个 running 任务堵住了队列。
- `hmn node worker-status` 能显示当前任务和队列深度。
- worker 重启后不会永久遗留 running 任务占队列。
- 卡住超过阈值会产生清晰告警，不需要用户追问。

---

## P0：核心安全闭环

**目标：** 保证 HMN 自身节点、token、worker、任务边界可靠。

### Token lifecycle 加固

- `hmn token revoke <TOKEN_OR_ID>` operator UX。
- token 到期展示。
- token list 显示 active / used / expired / revoked。
- revoke 写 audit。
- join API 明确拒绝 expired/revoked/used token。
- token 列表不显示明文长期 secret，必要时只显示短 ID/前缀。

### Node revoke enforcement

- revoked node 不能 heartbeat。
- revoked node 不能 poll task。
- revoked node 不能 submit result。
- revoke 写 audit。
- CLI 明确显示 revoked 状态。

### Worker auth / task safety

- task dispatch 前检查 node status 必须是 managed。
- worker protocol 不兼容时继续允许 heartbeat，但拒绝 task dispatch。
- task result 只能由对应 node/fingerprint 提交。
- 为后续 task signing 预留字段。

---

## P0/P1：审批状态机与高风险动作边界

**目标：** 让 high risk / critical 动作默认不可直接执行。

### 要做

- approval 数据模型和 CLI：
  - `hmn approval list`
  - `hmn approval show <ID>`
  - `hmn approval approve <ID>`
  - `hmn approval reject <ID>`
- 风险策略：
  - low：可自动执行并审计。
  - medium：默认汇报，可配置自动执行。
  - high：必须 approval。
  - critical：默认拒绝。
- component apply / high-risk task / Headscale tag/ACL / SSH playbook action 进入 approval。

### 验收

- high risk action 会生成 pending approval。
- approve 后才能进入 task/component run。
- reject 后不会执行。
- 所有 decision 写 audit。

---

## P1：服务发现与 service registry 主线

**目标：** 这是 v1.1 真正变“有用”的入口。让 HMN 自动知道一台机器上跑了什么服务，再驱动监控、文档、备份、迁移。

### 要做

- 自动识别：
  - systemd units
  - Docker containers
  - Docker Compose projects
  - Caddy / Nginx site config
  - listening ports
  - data directories / volume mounts
- 建立 service registry：
  - service_id
  - node_id
  - runtime: systemd/docker/compose/coolify/static
  - ports/domains
  - deploy_path
  - data_paths
  - env_paths
  - log_paths
  - backup_policy
  - monitor_policy
- 从 Coolify provider 同步 service registry。
- 生成 Uptime Kuma plan，并在 approval 后 apply。
- docs/backup/migration/onboarding 都从 service registry 读输入，不再各自猜。

### 验收

- 对一个真实节点执行 discovery dry-run，能列出服务、端口、路径、候选监控项。
- registry 写入前展示 diff。
- Uptime Kuma / docs-sync / backup plan 能消费 registry。

---

## P1：Provider 化控制面补齐

**状态：** 已完成统一 provider 契约和部分 dry-run provider；需要从 skeleton 走向闭环。

### 已有/待判重分支

- Coolify provider skeleton：待合并或抽取。
- GitHub Actions provider：主线大概率已吸收，需确认清理。
- Deploy dry-run/status：主线大概率已吸收，需确认清理。
- Config Provider inventory export：可能主线已吸收，需判重。
- Docs Provider / docs-sync plan/apply：docs-center apply 文件簇已抽取到主线，下一轮只需判重旧分支剩余 restore/migration/onboarding。
- Restore / migration / onboarding dry-run：主线已部分吸收，需清理旧 worktree。

### 要做

- Provider status/apply/rollback 接口对齐。
- Backup Provider 接 restic/borgmatic/Kopia 之一的 dry-run + plan。
- Config Provider 接 Ansible/AWX 执行结果读取。
- Deploy Provider 补 apply/status/rollback 的真实闭环。

---

## P1：Headscale + Executor 联动

**目标：** 利用 Headscale 提供稳定内网身份，补齐 SSH executor 和组件真实 verify/apply 的网络基础。

### 当前状态

- [x] SSH executor 使用 Tailscale/Headscale `network_ip`，优先级为 `ssh_host` > `ssh-host` label > `network_ip` > addresses。
- [x] `hmn node status` 展示 HMN 状态 + Headscale 状态。
- [x] `hmn node doctor` 的 SSH 探测可使用 `network_ip` 并记录 `target_source`。
- [x] component verify 可以通过 Tailscale IP 做只读探测。
- [ ] 高风险 SSH/playbook action 进入 approval。
- [ ] Headscale tag/ACL 更新走 approval。

---

## P1/P2：安装/交付任务 job steps

**目标：** 安装类任务可观测、可恢复、可清理。

### 要做

- 安装类任务不要默认使用单条 `curl install.sh | bash` 黑盒命令。
- 抽象为 step job：probe → download → checksum → extract → install → configure → verify。
- 每个 step 独立 timeout、日志、状态、失败原因和重试策略。
- 支持 step 幂等：已完成步骤可跳过，失败后从失败 step 继续。
- 临时 HTTP artifact 由 HMN 管生命周期：一次性随机路径、过期时间、自动停止 server、自动删除目录。
- 临时 artifact 不能包含长期 secret；配置写入应走节点本地 env/config，敏感字段不进日志。

### 验收

- BeroNas 这类 Hermes 安装可以看到每一步进度。
- 不再由一个 pip 任务长时间占住队列且无上报。
- 临时 HTTP server 不会遗留。

---

## P2：NAS / IPv6 接入优化

**目标：** 针对无公网 IPv4、但有 IPv6 或可出站的 NAS，提供安全接入路径。

原则：

- 即使 NAS 有公网 IPv6，也不开放 NAS 入站。
- 仍然使用 Worker Pull。
- Master URL 优先用域名 AAAA 记录。
- IPv6 字面量 URL 必须用中括号：`http://[240x:...]:8765`。

后续：

- `hmn wake` / `join-command` 增强 IPv6 URL 测试。
- docs 增加 NAS IPv6 示例。
- worker endpoint fallback：master IPv6、Headscale 内网域名/IP、relay fallback。
- lite-worker / cron installer：适配群晖、QNAP、OpenWrt 等无 systemd 环境。

---

## P2：组件真实能力

### monitor

- 展示 uptime/load/memory/disk/mount/docker facts。
- 支持服务状态 probe。
- 支持 Headscale online 状态融合。

### reverse-proxy

- Caddy driver MVP。
- plan 生成配置 diff。
- apply 通过 task/executor 写入配置。
- verify 独立检查 HTTP/TLS。

### forwarder

- gost/frp/socat driver MVP。
- 先 plan/dry-run，再 approval，再 apply。

### backup

- include/exclude。
- dry-run 估算。
- checksum。
- 备份结果写 service docs。

### docs-sync

- 机器维度文档。
- 服务维度文档。
- 域名索引。
- 不写 token/secret。

---

## 自动化迭代纪律

每次使用 HMN 自己管理 HMN 或用户机器，都必须完成这个闭环：

1. **记录体验**：哪些命令好用，哪些卡住，哪些输出不够。
2. **归因**：是 worker、API、CLI、provider、docs、orchestrator 哪层的问题。
3. **沉淀**：
   - 好用：写进 docs/roadmap/usage 或 skill/reference。
   - 不好用：写进 priority-plan/backlog，并配验收标准。
   - 可自动化：转成 CLI/provider/orchestrator 任务。
   - 可回归：补测试。
4. **禁止只绕过**：不能只靠人工经验把问题绕过去，必须把经验推进成系统能力。

---

## 推荐实际推进顺序

1. `chore(orchestrator): classify stale and absorbed HMN worktrees`
2. `feat(orchestrator): add merge queue and branch backlog status`
3. `feat(worker): add task timeout, heartbeat and cancel`
4. `feat(task): add watch command and queue diagnostics`
5. `feat(discovery): build service discovery dry-run`
6. `feat(registry): persist service registry and diff`
7. `feat(provider): merge coolify/config/docs-center remaining slices`
8. `feat(monitor): generate uptime kuma plan from service registry`
9. `feat(job): add install step runner and artifact cleanup`
10. `feat(backup): add provider dry-run from service registry`
11. `feat(docs): enrich docs-sync from service registry`
12. `feat(migration): generate migration plan from service registry`

---

## 时间预估

- 分支债务清理 / merge queue MVP：0.5～1 天。
- Worker watchdog / task cancel / diagnostics：1～3 天。
- 服务发现 + service registry MVP：2～5 天。
- Provider 剩余分支合并/判重：0.5～2 天。
- Uptime Kuma 从 registry 同步：1～2 天。
- job-step runner / artifact cleanup：2～4 天。
- backup/docs-sync/migration 真实组件：1～3 周。

较现实的里程碑：

- **1～2 天内：** 分支债务清楚、worker 不再静默卡队列。
- **一周内：** 服务发现 + registry + 监控计划闭环。
- **两周内：** docs/backup/migration 从 registry 自动生成。
- **一个月内：** NAS、monitor、backup、docs-sync 形成较完整托管闭环。
