# Roadmap

架构剩余待办清单见：[Architecture Backlog](architecture-backlog.md)。

## v0.1：文档型 MVP

- [x] README
- [x] 架构总览
- [x] 架构契约
- [x] 安全模型
- [x] 节点生命周期
- [x] 权限模型
- [x] Playbook 规范
- [x] Headscale ACL 示例
- [x] sudoers 示例

## v0.2：脚本型 MVP

- [x] `scripts/join.sh`
- [x] 节点能力探测抽象
- [x] POSIX sh lite worker
- [x] beacon-only 心跳脚本
- [x] OpenWrt procd 模板
- [x] OpenRC 模板
- [x] systemd reporter 示例
- [x] 文档生成模板（已由 v0.6 资产文档自动化吸收：`hmn docs server/service/generate`、机器/服务/域名/Runbook 索引）
- [x] 本地 dry-run 模式

## v0.3：控制面 MVP

- [x] join token 创建
- [x] join token 撤销 / 过期 UX
- [x] node registry
- [x] SQLite 存储
- [x] SSH executor（阶段性完成：显式 `executor=ssh` 路由、worker poll 隔离、`ssh-run-next`、网络 IP 目标解析与审计已闭环；后续生产增强另列）
- [x] audit log
- [x] Approval 数据模型和 CLI 骨架
- [x] Approval -> Task dispatch 闭环
- [x] Approval API approve/reject 入口（Telegram bridge 可复用）
- [x] Telegram approval 消息卡片和回调 payload
- [x] Telegram approval flow 核心处理器
- [x] Telegram gateway 发送/回调接入 API
- [x] Telegram gateway 实际 Bot 轮询发送器核心
- [x] Telegram gateway systemd/CLI 运维入口
- [x] installer 支持 Telegram Gateway 一键 systemd 集成
- [x] revoked node 心跳 / poll / result 拒绝闭环
- [x] worker protocol mismatch 禁止取任务

## v0.4：Worker 模式

- [x] worker pull task
- [x] task signing
- [x] heartbeat
- [x] node token rotation
- [x] worker fingerprint rotation 自动同步
- [x] offline node handling
- [x] stale / offline liveness CLI 展示与审计
- [x] runtime profile 自动选择
- [x] service manager adapter 安装器

## v0.5：Headscale Network Provider MVP

- [x] Network Provider 抽象与 Headscale API adapter
- [x] `hmn network status`
- [x] `hmn network sync` 同步 provider node 到 HMN node
- [x] `hmn network preauth-key create`
- [x] `hmn wake --network headscale` 生成 Headscale/Tailscale 接入命令
- [x] node record 保存 `network_provider` / `network_node_id` / `network_ip` / `network_tags` / `network_online`
- [x] network sync / preauth-key audit
- [x] Headscale tag 写操作审批化
- [x] installer 支持 Headscale bundled / external / disabled 配置入口
- [x] Headscale ACL 文件级更新审批化（`hmn network acl plan` 生成 diff + critical approval，审批后 apply/reload/verify/audit）
- [x] SSH executor 使用 Tailscale IP
- [x] component verify 使用 Tailscale/Headscale `network_ip` 作为只读探测目标

## v0.6：资产文档自动化

- [x] server docs 生成：`hmn docs server <node_id>`、`hmn docs index`
- [x] service docs 生成：`hmn docs service <service_id>`、`hmn docs service-index`
- [x] 一键刷新资产入口：`hmn docs generate` 同步机器文档、机器索引、服务索引、域名索引和 Runbook 索引
- [x] domain index 生成：`hmn docs domain-index`
- [x] runbook index 生成：`hmn docs runbook-index`

## v0.7：插件化组件架构

- [x] Component Bundle 架构文档
- [x] component manifest schema
- [x] component registry / loader
- [x] `hmn component list/show/plan/apply/verify/uninstall/status`
- [x] component audit events
- [x] reverse-proxy 内置组件 MVP
- [x] forwarder 内置组件 MVP
- [x] monitor 内置组件 MVP（facts 收集、threshold 判定）
- [x] monitor heartbeat 闭环：`hmn monitor health / scan / snapshot` 基于心跳事件评估节点健康，自动记录 MonitorSnapshot，支持 stale/timeout/stale_heartbeat/missing/critical 分级
- [x] headscale-server 内置组件 manifest（安装/verify playbook 后续补齐）
- [x] backup / docs-sync 组件 manifest（先完成组件注册、配置 schema、driver 边界和健康检查声明；真实驱动闭环后续补齐）

## v0.8：NAS / IPv6 接入优化

- [x] `hmn token join-command` 覆盖 IPv6 literal URL（中括号格式）
- [x] `hmn node install-heartbeat --runtime lite-worker --endpoint ...` 支持 master IPv6 / Headscale 内网 / relay fallback
- [x] POSIX lite-worker 按 `HMN_MASTER_URLS` 顺序尝试 endpoint fallback
- [x] lite-worker / cron installer 覆盖群晖、QNAP、OpenWrt 常见无 systemd 环境
- [x] docs 增加 NAS IPv6 接入示例

## v0.9：真实部署稳定化

- [x] 本地 E2E smoke：controller 启动、join、confirm、worker disabled task、heartbeat、worker-status、docs generate
- [x] 双 Debian VPS 真实部署 smoke：Master systemd、Worker join/confirm、full-worker timer、disabled-exec 安全拒绝、资产文档生成
- [x] Telegram approval gateway 真实 bot 轮询与回调 smoke：`scripts/smoke-telegram-approval.sh`
- [x] Headscale bundled/external 接入真实网络 smoke：`scripts/smoke-headscale-network.sh`
- [x] NAS / OpenWrt / IPv6-only lite-worker fallback 真实设备 smoke：`scripts/smoke-nas-ipv6-lite-worker.sh`
- [x] 将真实试点脚本化为可重复运行的 `scripts/smoke-remote-e2e.sh`

## v1.0：生产就绪

- [x] `hmn doctor` 覆盖安装、升级、回滚和服务状态检查
- [x] installer 写入 upgrade manifest / backup metadata
- [x] installer upgrade / rollback 说明闭环
- [x] 默认更新入口指向 main 分支 raw 安装脚本
- [x] 生产 readiness checklist 文档化：`docs/production-readiness.md`
- [x] 真实组件驱动闭环：monitor 已先落地（heartbeat facts → component apply/verify/status → MonitorSnapshot / node_component / audit 闭环）

## v1.1：全托管自动化规划

目标：让机器接入 HMN 后，逐步形成“Orchestrator 统筹 → 服务发现 → 外部部署系统 → 监控同步 → 文档中心 → 备份 → 迁移”的集中托管闭环。HMN 不自研完整 CI/CD 流水线引擎，而是接入成熟工具；HMN 负责统一入口、长期任务统筹、service registry、provider 编排、approval、audit 和文档中心。所有高风险变更仍必须走 approval / audit，不允许因为自动化而绕过安全边界。

长期原则：Hermes/AI 是早期全托管探索层和自动化孵化器，不是 HMN 的最终拐杖。复杂、低频、非结构化运维可以先由 Hermes 代管；但每次成功操作都要沉淀为 HMN 原生能力，包括 CLI、worker task、provider、playbook、shell script、docs template 和 test case。高频、稳定、可验证流程必须逐步脱离 AI，会收敛成用户可一键执行的接入、巡检、备份、恢复、迁移和文档刷新能力。AI 后期只作为解释、规划、异常兜底和复杂场景助手，不能成为核心自动化路径的唯一依赖。

### Hermes 经验沉淀到 HMN 原生自动化

- [ ] 建立“AI 运维 → HMN 自动化”沉淀规则：每次 Hermes 处理完机器接入、排障、部署、备份、迁移，都必须判断是否转成 CLI / provider / playbook / worker task / 测试。
- [ ] 为常见操作建立可复用任务模板：接入、巡检、服务发现、文档刷新、备份计划、恢复计划、迁移计划。
- [ ] 把高频成功 runbook 固化为 `hmn ... plan/apply/verify` 命令，避免长期依赖会话上下文和提示词。
- [ ] 为一键自动化保留安全边界：plan 可自动生成，apply 按风险进入 approval，执行结果必须写 audit，并提供 verify / rollback hint。
- [ ] Orchestrator 定期扫描“仍依赖 Hermes 手工判断”的流程，生成自动化候选 backlog。

### Orchestrator 全自动托管统筹

- [x] Orchestrator 数据模型：维护 task queue、worker registry、assignment、lease、progress report 和 audit 事件。（已落地 SQLite 持久化、snapshot/report、attempt 计数和 audit）
- [x] `hmn orchestrator tick/status/enqueue/report`：支持巡检调度、自动分发、状态查询和简短进度汇报。
- [x] Worker/bridge adapter：默认通过 worker / bridge / webhook / queue 分发任务，raw SSH 仅作 fallback；连续多次失败后才暂停。（已支持 bridge adapter、retry 计数、lease timeout 回收；webhook/queue 为后续 transport 扩展）
- [ ] 第二 Hermes / 多 Agent worktree 隔离：并行任务必须隔离工作区，Orchestrator 负责验收、合并和冲突解决。
- [x] Orchestrator approval gate：低风险代码/测试/文档自动推进；生产写入、真实 provider 变更、凭据、生产部署和合并 main 必须审批。（已在 provider apply、Uptime sync、docs-sync apply 等真实写入口保持审批边界）

### Provider 化托管控制面

- [x] Provider 接口契约：统一 `discover / plan / apply / verify / status / rollback`，所有 Provider 必须返回可审计 plan 和 sanitized result。
- [x] Deployment Provider：优先接入 Coolify，支持同步 apps、读取部署状态、触发 deploy、触发 rollback，并映射到 HMN service registry。（阶段性完成：`hmn deploy plan/status` 聚合 dry-run/fixture 状态；真实 deploy/rollback 继续受 approval 约束）
- [x] CI Provider：优先接入 GitHub Actions，支持读取 workflow/check 状态、触发 `workflow_dispatch`，HMN 不直接承担 build runner。（阶段性完成：部署状态聚合预留 CI provider 输出入口）
- [x] Monitor Provider：接入 Uptime Kuma，基于 service registry upsert monitor、状态页分组和服务健康状态。（已完成 `hmn uptime plan/sync`：sync 只创建 approval，审批前不写 Uptime Kuma）
- [ ] Backup Provider：接入 restic / borgmatic / Kopia，HMN 管策略、审批、审计、verify 和恢复文档，底层备份交给成熟工具。
- [ ] Config Provider：接入 Ansible/AWX，HMN 导出 inventory、审批 playbook、记录执行结果和 audit。
- [x] Docs Provider：保持文档中心落地，统一生成机器、服务、域名、Runbook、部署/恢复/迁移文档。（已完成 docs generate / docs-sync plan/apply，apply 只创建 approval，审批前不写 docs-center）

### 服务自动发现与状态页同步

- [ ] 节点服务自动发现：识别 systemd unit、Docker / Compose、Caddy / Nginx 入口、监听端口、公开 URL 和本地健康检查路径。
- [ ] 建立 service registry：服务绑定 node、runtime、端口、域名、部署路径、配置文件、env 文件、数据目录、反代入口和健康检查策略。
- [ ] 从 Coolify 同步 service registry：把 Coolify app、domain、repo、deploy target、env 摘要和运行状态映射成 HMN service/service_instance。
- [x] Uptime Kuma Provider：把已发现服务自动 upsert 到 Uptime Kuma，并绑定状态页分组；新增、变更、下线都写 audit。（阶段性完成：`hmn uptime plan` 生成 upsert 计划，`hmn uptime sync` 创建高风险审批且审批前不触达 provider）
- [ ] 监控策略自动生成：根据服务类型选择 HTTP / keyword / TCP / ping 检查，避免把状态页自身或内部-only 服务错误公开。

### 部署与流水线编排

- [ ] `hmn deploy plan <service>`：读取 service registry 和 Deployment/CI Provider，生成非变更部署计划、风险等级、验证步骤和 rollback hint。
- [ ] `hmn deploy apply <service>`：按风险走 approval，触发 GitHub Actions / Coolify / SSH fallback，并写 deployment record 与 audit。
- [ ] `hmn deploy status <service|run_id>`：聚合 GitHub Actions、Coolify、Uptime Kuma 和 HMN verify 结果。
- [ ] `hmn deploy rollback <service|run_id>`：优先调用 Coolify rollback；高风险必须 approval，并在 rollback 后 verify/monitor/docs sync。
- [ ] Webhook 接入：支持 GitHub/Coolify webhook 回写 deployment 状态，避免 HMN 长轮询成为唯一状态来源。
- [ ] 不实现通用 DAG pipeline runner；如后续确需复杂 stage/dependency，再以外部 CI/CD Provider 或轻量 orchestrator adapter 方式接入。

### 文档中心自动化

- [ ] 服务部署文档自动填充：从 service registry 生成部署路径、systemd / compose、端口、域名、env、数据目录、依赖和启动/停止命令。
- [ ] 服务维护文档自动生成：生成巡检、日志、重启、升级、备份、恢复、回滚、常见故障处理步骤。
- [ ] 文档集中落地到文档中心：机器维度继续写 `/srv/files/docs/server/<host>/`，服务维度继续写 `/srv/files/service/<service>/`，不得分散写在各节点本地。
- [x] docs-sync 真实驱动：支持按节点、按服务和全量刷新，更新索引、域名索引、Runbook 索引，并记录同步结果。（阶段性完成：`hmn docs sync plan/apply`，apply 先创建 approval，审批前不写 `/srv/files`）

### 备份、恢复与迁移

- [ ] 服务级备份策略：基于 service registry 自动生成 include/exclude、停机/热备要求、数据库 dump 策略、保留周期和校验策略。
- [ ] 集中备份归档：备份产物、manifest、checksum、恢复说明集中保存到指定备份目录/文档中心，不散落在业务节点。
- [ ] 自动生成迁移文档：为每个服务生成源节点、目标节点、依赖、数据目录、端口/域名、备份包、恢复步骤、验证步骤和回滚步骤。
- [ ] 一键恢复 MVP：先支持显式审批后的单服务恢复；恢复前校验 checksum、目标路径、权限、端口冲突和运行时依赖。
- [ ] 一键迁移 MVP：支持低风险/低停机服务迁移，流程为 plan → backup → transfer → restore → verify → switch traffic → rollback hint。
- [ ] 低停机/无损迁移增强：对数据库和有状态服务引入维护窗口、增量同步、最终一致性校验、流量切换和失败回滚；不能默认承诺所有服务无损。

### 一次部署与网内机器托管

- [ ] 主控一次部署后批量接入网内机器：结合 Headscale/Tailscale preauth key、join token、worker installer 和 approval，完成批量 onboarding。
- [ ] 网内节点能力盘点：接入后自动识别 full-worker / lite-worker / beacon-only / proxy-managed，以及可用的 service manager。
- [ ] 跨节点迁移计划：根据目标节点能力、网络可达性、磁盘空间、服务依赖和已有备份，推荐迁移目标与迁移策略。
- [ ] 迁移后自动收口：更新 Uptime Kuma、服务文档、机器文档、域名索引、Runbook、backup manifest 和 audit。
