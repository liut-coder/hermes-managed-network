# Architecture Backlog

HMN 当前架构主体已完成，下一阶段转入全托管自动化：机器接入后自动发现服务、同步状态页、集中生成文档、集中备份，并逐步实现可审批的一键恢复/迁移。

## 当前完成度

- 总体路线图：89 / 107 项完成，约 83.2%。v1.0 及以前路线图已收尾；新增 v1.1 全托管自动化规划后，未完成项主要集中在服务发现、Uptime Kuma 同步、文档自动填充、集中备份、恢复和迁移。
- 架构阶段：v1.0 主体闭环已完成，进入 v1.1 托管自动化建设。
- 已完成主干：Control Plane、Worker、Headscale Network Provider、Approval、Asset Docs 基础、Component Framework、NAS/IPv6 接入、Production Readiness 基础、monitor 真实闭环、backup 本地归档/verify 基础。

## P0：服务自动发现与服务清单

目标：让每台机器接入 HMN 后，主控能形成可靠的 service registry。后续 Uptime、文档、备份和迁移都以 service registry 为数据源。

验收标准：

- 发现 systemd unit、Docker / Compose、Caddy / Nginx 入口、监听端口和公开 URL。
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

1. 服务自动发现与 service registry。
2. Uptime Kuma Provider / 状态页同步。
3. docs-sync 真实驱动，集中生成部署/维护文档。
4. 服务级集中备份策略与 verify。
5. 一键恢复 MVP。
6. 迁移文档与一键迁移 MVP。
7. 一次部署后的网内批量接入和迁移推荐。
