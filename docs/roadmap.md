# Roadmap

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
- [ ] POSIX sh lite worker
- [ ] beacon-only 心跳脚本
- [ ] OpenWrt procd 模板
- [ ] OpenRC 模板
- [x] systemd reporter 示例
- [ ] 文档生成模板
- [x] 本地 dry-run 模式

## v0.3：控制面 MVP

- [x] join token 创建
- [x] join token 撤销 / 过期 UX
- [x] node registry
- [x] SQLite 存储
- [ ] SSH executor
- [x] audit log
- [x] Approval 数据模型和 CLI 骨架
- [ ] Telegram approval flow

## v0.4：Worker 模式

- [x] worker pull task
- [x] task signing
- [x] heartbeat
- [x] node token rotation
- [x] worker fingerprint rotation 自动同步
- [x] offline node handling
- [x] runtime profile 自动选择
- [x] service manager adapter 安装器

## v0.5：资产文档自动化

- [x] service docs 生成（dry-run 输出目录）
- [x] service index 生成（dry-run 输出目录）
- [x] domains index 生成（dry-run 输出目录）
- [x] runbooks index 生成（dry-run 输出目录）
- [ ] 生产文档目录写入
- [ ] 文档回写审批链路

## v0.6：插件化组件架构

- [x] Component Bundle 架构文档
- [x] component manifest schema
- [x] component registry / loader
- [x] `hmn component list/show/plan/apply/verify/uninstall/status`
- [x] component audit events
- [x] reverse-proxy 内置组件 MVP
- [x] forwarder 内置组件 MVP
- [x] monitor 内置组件 MVP
- [ ] backup / docs-sync 组件

## v1.1：Useful Ops MVP

当前范围只做本地/测试目录 dry-run，不连接外部系统，不写生产路径。

- [x] `hmn inspect node --local`：本机资产盘点
- [x] `hmn discover services`：从 inventory 生成 service registry
- [x] `hmn docs generate`：从 registry 生成 service 文档与 service/domains/runbooks 索引
- [x] `hmn uptime plan`：生成 Uptime Kuma 同步计划 dry-run JSON
- [x] 文档与输出中的敏感值统一写 `[REDACTED]`
- [ ] 真实 Uptime Kuma sync
- [ ] 生产写入 `/srv/files/...` 或其他正式资产目录
- [ ] token/password/API key 注入与回写
- [ ] 高风险/外部系统写操作审批链路

相关文档：

- [Useful Ops MVP](useful-ops-mvp.md)
- [Architecture Backlog](architecture-backlog.md)
- [架构契约](architecture-contract.md)
