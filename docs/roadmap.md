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
- [ ] 文档生成模板
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

- [ ] `hmn doctor` 覆盖安装、升级、回滚和服务状态检查
- [ ] installer 写入 upgrade manifest / backup metadata
- [ ] installer upgrade / rollback 说明闭环
- [ ] 默认更新入口指向 main 分支 raw 安装脚本
- [x] 生产 readiness checklist 文档化：`docs/production-readiness.md`
- [ ] 真实组件驱动闭环：monitor / backup / docs-sync 之一先落地
