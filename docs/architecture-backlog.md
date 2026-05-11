# Architecture Backlog

HMN 当前架构主体已完成，剩余工作集中在真实环境 smoke、设备兼容验证和真实组件驱动闭环。

## 当前完成度

- 总体路线图：83 / 89 项完成，约 93.3%。
- 架构阶段：从 MVP 建设进入生产试点与真实环境收口。
- 已完成主干：Control Plane、Worker、Headscale Network Provider、Approval、Asset Docs、Component Framework、NAS/IPv6 设计、Production Readiness 基础。

## P0：真实部署稳定化收口

### 1. Telegram approval gateway 真实 bot smoke

目标：验证真实 Telegram Bot 审批链路，而不是只停留在单元测试或模拟客户端。

验收标准：

- 创建一条新的 high-risk approval。
- approval card 能发送到真实 Telegram chat。
- inline button approve/reject 能被 gateway 轮询到。
- gateway 调用 HMN approval callback API 成功。
- 成功后调用 `answerCallbackQuery`。
- 成功后清理原消息 inline buttons，避免 stale button 重复点击。
- notification / audit 状态可追踪。
- smoke 过程不打印、不写入 Bot token。

### 2. Headscale bundled / external 真实网络 smoke

目标：验证 Headscale/Tailscale overlay network 真实接入和执行路由。

验收标准：

- bundled / external 至少完成一种真实 smoke。
- preauth-key 创建可用。
- worker 节点可加入 Headscale/Tailscale overlay。
- `hmn network sync` 能把 provider node 同步到 HMN node。
- node record 保存 `network_provider`、`network_node_id`、`network_ip`、`network_tags`、`network_online`。
- SSH executor 优先使用 `network_ip`。
- `hmn component verify` 只读检查使用 `network_ip`。
- network tag / ACL 写操作仍走 approval，不允许绕过 gate。

### 3. 远程 E2E smoke 脚本化

目标：把已经做过的真实部署验证沉淀成可重复运行的 gate。

验收标准：

- 新增或完善 `scripts/smoke-remote-e2e.sh`。
- 通过环境变量接收远程 master / worker 连接信息。
- 覆盖 master install、worker join、confirm、heartbeat、worker-status。
- 覆盖 disabled-exec task 安全拒绝。
- 覆盖 docs generate。
- 覆盖 `hmn doctor` 基础生产检查。
- 失败时输出足够诊断信息，但不输出 secret。

## P1：设备兼容真实验证

### 4. NAS / OpenWrt / IPv6-only lite-worker 真实设备 smoke

目标：验证非标准 Linux / 无 systemd / IPv6-only 场景不是纸面设计。

验收标准：

- Synology、QNAP、OpenWrt 或 IPv6-only 节点至少完成一个真实 smoke。
- lite-worker 能按 `HMN_MASTER_URLS` 顺序 fallback。
- service manager adapter 至少验证 cron / procd / openrc / loop 中的一种。
- heartbeat 能稳定上报。
- worker/task 语义保持安全默认，不自动执行 shell。
- docs 中记录设备限制和推荐运行模式。

## P1：真实组件驱动闭环

### 5. 真实组件驱动先落一个

建议优先级：monitor > docs-sync > backup。

目标：把一个组件从 manifest MVP 推进到真实 lifecycle 闭环。

验收标准：

- 完整覆盖 `discover -> plan -> approve -> apply -> verify -> monitor/status -> rollback_hint`。
- `plan` 非变更。
- state-changing action 进入 approval。
- apply 通过 Task Engine / Executor，不在 CLI 内直接散落执行。
- verify 独立可重复运行。
- audit 记录 run/action/outcome/target/risk。
- roadmap 更新对应完成状态。

## P2：路线图清理

### 6. 文档生成模板状态收口

目标：处理 v0.2 中遗留的 `文档生成模板` 未勾选项。

验收标准：

- 如果已被 v0.6 asset docs 覆盖，则更新 roadmap 文案说明吸收关系。
- 如果未覆盖，则补最小模板和测试。
- 避免 roadmap 中旧项误导后续优先级判断。

## 建议执行顺序

1. Telegram approval gateway 真实 bot smoke。
2. Headscale 真实网络 smoke。
3. `scripts/smoke-remote-e2e.sh` 可重复远程 gate。
4. NAS / OpenWrt / IPv6-only 真实设备 smoke。
5. monitor 真实组件驱动闭环。
6. 文档生成模板状态收口。
