# Roadmap

## v0.1：文档型 MVP

- [x] README
- [x] 架构总览
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

- [ ] join token 创建 / 撤销
- [ ] node registry
- [ ] SQLite 存储
- [ ] SSH executor
- [ ] audit log
- [ ] Telegram approval flow

## v0.4：Worker 模式

- [x] worker pull task
- [ ] task signing
- [x] heartbeat
- [ ] node token rotation
- [ ] offline node handling
- [ ] runtime profile 自动选择
- [ ] service manager adapter 安装器

## v0.5：资产文档自动化

- [ ] server docs 生成
- [ ] service docs 生成
- [ ] domain index 生成
- [ ] runbook index 生成

## v0.6：插件化组件架构

- [x] Component Bundle 架构文档
- [x] component manifest schema
- [x] component registry / loader
- [x] `hmn component list/show/plan/apply/verify/uninstall/status`
- [x] component audit events
- [x] reverse-proxy 内置组件 MVP
- [ ] forwarder 内置组件 MVP
- [ ] monitor / backup / docs-sync 组件
