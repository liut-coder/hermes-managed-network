# HMN 架构契约

本文把 HMN 的长期边界固定成可执行契约，避免后续为了快速加功能，把网络、组件、执行器或服务细节塞进 Core。

## Layer ownership

```text
User Entry
  - Telegram / CLI / API
  - 只负责入口、展示、交互，不保存核心状态

HMN Core
  - identity / token / node registry
  - capability / runtime profile
  - policy / approval / audit
  - component registry / lifecycle state
  - task scheduling / result state

Network Provider Adapter
  - Headscale / Tailscale / direct IPv6 / none
  - 只提供连通性、节点网络身份、ACL/tag 同步能力

Task Engine
  - Worker Pull
  - SSH Executor
  - Playbook Runner
  - 只执行经过 Core 授权和审计的计划

Runtime Layer
  - full-worker
  - lite-worker
  - beacon-only
  - proxy-managed
  - 基于能力探测选择，不由组件硬猜

Component Bundle
  - reverse-proxy / forwarder / monitor / backup / docs-sync
  - 提供 manifest、drivers、templates、playbooks、probes、health checks
```

## Core invariants

- Core MUST own identity, node lifecycle, trust tier, permission bundles, policy, approval, task state, audit, rollback entry point, and component state.
- Core MUST NOT hard-code nginx/frp/Headscale service details.
- Core MUST NOT assume every node is systemd Linux.
- Core MUST NOT grant implicit network trust just because Headscale/Tailscale can reach a node.
- Core MUST treat all high-risk actions as approval-gated and audit-required.
- Core MUST keep secrets out of docs, audit details, and user-facing summaries.

## Extension seams

新增能力必须优先接入以下 seam，而不是改 Core 主流程：

- **Network Provider Adapter**：网络发现、ACL/tag、虚拟 IP、连接状态。
- **Runtime Profile**：节点执行能力分层。
- **Service Manager Adapter**：安装、启动、停止、卸载 worker 或 beacon。
- **Component Bundle**：服务能力、模板、playbook、verify、monitor。
- **Task Engine**：worker pull、SSH executor、playbook runner。
- **Approval Provider**：CLI / Telegram / API 审批通道。
- **Docs Provider**：server/service/domain/runbook 文档落地。

## Component lifecycle contract

所有组件必须遵循统一生命周期：

```text
discover -> plan -> approve -> apply -> verify -> monitor -> upgrade/rollback/uninstall
```

契约：

- `discover`：只读取 manifest 和节点能力，不修改目标节点。
- `plan`：生成非破坏性计划，列出目标、driver、风险、预期变更和 rollback hint。
- `approve`：根据 risk、trust tier、permission bundle 决定自动通过、等待审批或拒绝。
- `apply`：只能通过 Task Engine / Executor 执行，不能绕过 audit。
- `verify`：必须独立验证结果，不能只相信 apply 输出。
- `monitor`：把健康检查结果写入状态和 audit。
- `upgrade/rollback/uninstall`：必须作为一等动作，不能只靠手工文档。

## Runtime profile contract

Runtime Profile 是节点能力的保守分类：

- `full-worker`：可运行完整 worker，可 heartbeat、pull task、submit result；命令执行仍需显式启用。
- `lite-worker`：POSIX sh 优先，适合 BusyBox/OpenWrt/NAS；默认只执行只读或白名单动作。
- `beacon-only`：只上报心跳和极少 facts，不拉任务、不执行命令。
- `proxy-managed`：目标设备不装 agent，由附近 managed proxy node 代管。

规则：

- profile 来自 capability probe / heartbeat facts，不来自主观标签。
- service manager adapter 只负责持久化方式，不改变权限、审批、审计语义。
- 组件必须声明 runtime/service-manager/capability 需求，Core 负责匹配。

## Network provider contract

Network Provider Adapter 负责连通性，不拥有运维语义。

允许：

- 查询节点网络在线状态。
- 映射 node id 到虚拟 IP / hostname。
- 同步 ACL / tag / route proposal。
- 为 SSH executor 提供可达地址。

禁止：

- 在 Core 中写死 Headscale API 细节。
- 用网络可达性替代 node trust / permission bundle。
- 绕过 approval 直接改 ACL 或开放高风险 route。

## Approval and audit contract

- low：可自动执行，但仍记录 audit。
- medium：默认汇报，可配置自动执行。
- high：必须 approval。
- critical：默认拒绝，除非显式策略开启。

所有 mutation 都必须记录：

- actor / source
- subject type / id
- action
- risk
- outcome
- plan id / task id / component run id
- sanitized details

不得记录：

- join token 原文
- node fingerprint 原文以外的长期 secret
- SSH key / API token / password
- 可定位真实资产的公开文档信息

## Implementation guardrails

- 新组件优先增加 manifest 和 tests，不把服务细节写进 CLI/storage/api 主流程。
- 新平台优先增加 capability probe、runtime classifier、service manager renderer。
- 新执行方式优先接入 Task Engine，并复用 policy/approval/audit。
- 新文档自动化必须同时考虑 server 维度和 service 维度。
