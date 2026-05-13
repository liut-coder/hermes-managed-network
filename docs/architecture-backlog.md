# Architecture Backlog

HMN 当前架构主体已完成，下一阶段转入 Orchestrator + Provider 化全托管自动化：机器接入后自动发现服务，接入 Coolify / GitHub Actions / Uptime Kuma / restic-borgmatic / Ansible 等成熟系统，由 HMN 统一做任务统筹、service registry、审批、审计、文档中心和跨工具编排。

## 当前完成度

- 总体路线图：89 / 126 项完成，约 70.6%。v1.0 及以前路线图已收尾；v1.1 转为 Orchestrator + Provider 化托管控制面，未完成项主要集中在长期自动统筹、Service Registry、Coolify/GitHub Actions/Uptime Kuma/restic-borgmatic/Ansible Provider、部署编排、文档自动填充、集中备份、恢复和迁移。
- 架构阶段：v1.0 主体闭环已完成，进入 v1.1 Provider 化托管自动化建设；当前进一步细化为 Orchestrator + Provider 化托管控制面。
- 已完成主干：Control Plane、Worker、Headscale Network Provider、Approval、Asset Docs 基础、Component Framework、NAS/IPv6 接入、Production Readiness 基础、monitor 真实闭环、backup 本地归档/verify 基础。

## P0：Orchestrator 全自动托管统筹

目标：把“主控定时检查进度、分发任务给备用机器人/第二 Hermes、合并 worktree、解决冲突、30 分钟汇报”沉淀为 HMN 原生能力，而不是依赖单次聊天会话提示词。

验收标准：

- `hmn orchestrator tick` 可执行单轮统筹，适合 systemd timer / cron 每 30 分钟调用。
- `hmn orchestrator status/enqueue/report` 能查看队列、添加任务、生成 Telegram/API 友好的简短报告。
- 持久化 task queue、worker registry、assignment、lease、run/report 和 audit。
- 默认通过 worker / bridge / webhook / queue 分发任务；raw SSH 仅作 fallback。
- bridge/worker timeout、No route to host、空输出等临时失败至少多轮重试，连续多次无可用状态后才暂停。
- 第二 Hermes 并行改代码必须使用隔离 worktree；主控负责验收、合并和冲突解决。
- 低风险代码、测试、文档和 feature branch 提交可自动推进；生产写入、真实 provider 变更、凭据、生产部署、合并 main 必须走 approval。

## P0：Provider 化托管控制面

目标：HMN 不自研完整 CI/CD 流水线引擎，而是把成熟外部系统接入为 Provider；HMN 只负责统一入口、service registry、plan/approval/audit/docs 和跨 Provider 状态聚合。

验收标准：

- Provider 统一接口覆盖 `discover / plan / apply / verify / status / rollback`。
- Provider 返回可审计 plan 和 sanitized result，不能把 token/password 写入 docs/audit。
- Deployment Provider 优先接入 Coolify。
- CI Provider 优先接入 GitHub Actions。
- Monitor Provider 接入 Uptime Kuma。
- Backup Provider 接入 restic / borgmatic / Kopia。
- Config Provider 接入 Ansible/AWX 或至少支持 Ansible inventory export。
- 高风险 Provider apply/rollback 必须复用 HMN approval/audit。

## P0：Coolify / GitHub Actions 部署编排 MVP

目标：用 Coolify 承担应用部署，用 GitHub Actions 承担 test/build/image，HMN 只做部署请求、审批、状态聚合、验证和文档更新。

验收标准：

- `hmn provider coolify sync` 能把 Coolify apps/domains/repo/deploy target 同步到 service registry。
- `hmn deploy plan <service>` 生成非变更部署计划、风险等级、验证步骤和 rollback hint。
- `hmn deploy apply <service>` 可触发 GitHub Actions / Coolify / SSH fallback，按风险进入 approval。
- `hmn deploy status <service|run_id>` 聚合 GitHub Actions、Coolify、Uptime Kuma 和 HMN verify 状态。
- `hmn deploy rollback <service|run_id>` 优先调用 Coolify rollback，高风险必须 approval。
- 支持 GitHub/Coolify webhook 回写 deployment 状态。
- 明确不实现通用 DAG pipeline runner；复杂流水线交给 CI/CD Provider。

## P0：服务自动发现与服务清单

目标：让每台机器接入 HMN 后，主控能形成可靠的 service registry。后续 Uptime、文档、备份和迁移都以 service registry 为数据源。

验收标准：

- 发现 systemd unit、Docker / Compose、Caddy / Nginx 入口、监听端口和公开 URL。
- 从 Coolify 同步 app、domain、repo、deploy target、env 摘要和运行状态。
- 服务绑定 node、runtime、端口、域名、部署路径、配置文件、env 文件、数据目录、反代入口和健康检查策略。
- 支持 dry-run，不修改节点。
- 支持手工确认/忽略发现项，避免误把临时端口或内部服务纳入托管。
- 所有发现、确认、忽略和变更写 audit。

## P0：Uptime Kuma 自动同步

目标：把 HMN 发现并确认的服务自动推送到 Uptime Kuma，形成公开状态页/内部状态页监控闭环。

验收标准：

- 新增 Uptime Kuma Provider / adapter。
- 根据服务类型生成 HTTP / keyword / TCP / ping monitor。
- 自动 upsert monitor，并绑定状态页分组。
- 支持新增、更新、禁用/下线同步。
- 排除状态页自身和内部-only 服务，避免误公开。
- 同步过程不写入 Uptime token 到 docs/audit。
- 同步结果写 audit，并能从 HMN CLI 查看。

## P0：文档中心自动化增强

目标：服务接入/发现/变更后，自动集中生成部署文档和维护文档，统一落到文档中心，而不是散落在各台机器。

验收标准：

- 机器维度继续写 `/srv/files/docs/server/<host>/`。
- 服务维度继续写 `/srv/files/service/<service>/`。
- 自动填充部署路径、systemd/compose、端口、域名、env、数据目录、依赖、启动/停止命令。
- 自动生成维护文档：巡检、日志、重启、升级、备份、恢复、回滚、常见故障。
- 更新机器索引、服务索引、域名索引和 Runbook 索引。
- 文档中不得写入 token、密码、SSH key 或可公开定位的敏感资产信息。

## P1：集中备份与恢复 MVP

目标：把当前 backup 本地归档能力升级成服务级、集中化、可验证的备份/恢复基础。

验收标准：

- 基于 service registry 生成 include/exclude、数据库 dump 策略、保留周期和校验策略。
- 备份产物、manifest、checksum 和恢复说明集中保存到指定备份目录/文档中心。
- `hmn backup verify` 校验归档、manifest 和 checksum。
- 一键恢复 MVP 必须先 plan，再 approval，再 restore，再 verify。
- 恢复前检查目标路径、权限、端口冲突、依赖和可用空间。
- 失败时保留诊断信息和 rollback hint。

## P1：迁移文档与一键迁移 MVP

目标：从“能备份/能执行任务”推进到“能生成迁移计划，并在审批后执行低风险迁移”。

验收标准：

- 自动生成迁移文档：源节点、目标节点、依赖、数据目录、端口/域名、备份包、恢复步骤、验证步骤和回滚步骤。
- `migration plan` 不修改任何机器。
- `migration apply` 必须走 approval。
- 流程覆盖 plan → backup → transfer → restore → verify → switch traffic → rollback hint。
- 先支持低风险/低停机服务；有状态数据库服务必须明确维护窗口和一致性校验。
- 不能默认宣称所有服务无损迁移；无损/低停机能力按服务类型逐步扩展。

## P1：一次部署与网内机器托管

目标：主控一次部署后，能批量接入和逐步托管同一 Headscale/Tailscale 网络内的机器，并为迁移提供目标选择依据。

验收标准：

- 结合 Headscale/Tailscale preauth key、join token、worker installer 和 approval 批量 onboarding。
- 接入后自动识别 full-worker / lite-worker / beacon-only / proxy-managed，以及 service manager。
- 自动盘点目标节点网络可达性、磁盘空间、运行时、服务管理器和权限能力。
- 跨节点迁移计划能推荐目标节点和迁移策略。
- 迁移后自动更新 Uptime Kuma、服务文档、机器文档、域名索引、Runbook、backup manifest 和 audit。

## 建议执行顺序

1. Orchestrator 数据模型 + `tick/status/enqueue/report` 最小闭环。
2. Worker/bridge adapter + 30 分钟巡检汇报 + 多次失败重试策略。
3. 第二 Hermes/worktree 隔离与主控合并策略。
4. Provider 统一接口契约。
5. Coolify Provider + service registry sync。
6. GitHub Actions Provider + `hmn deploy plan/apply/status`。
7. Uptime Kuma Provider / 状态页同步。
8. docs-sync 真实驱动，集中生成部署/维护文档。
9. restic/borgmatic Backup Provider 与服务级备份策略。
10. Ansible inventory/export 与机器级配置编排。
11. 一键恢复 MVP。
12. 迁移文档与一键迁移 MVP。
13. 一次部署后的网内批量接入和迁移推荐。
