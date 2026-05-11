# Production readiness checklist

HMN 已完成控制面 MVP 与真实双节点 smoke。进入长期代管前，每次部署或升级都要按本清单收口。

## 安装前

- [ ] 目标机器是 systemd Linux，且具备 root/sudo 权限。
- [ ] 已确认 `python3`、`python3-venv`、`python3-pip`、`curl` 可安装。
- [ ] 已确定主控监听地址、端口、数据库路径和公网 URL。
- [ ] 已决定 Headscale 模式：`bundled`、`external` 或 `disabled`。
- [ ] 如启用 Telegram 审批网关，已准备 approval gateway 专用 bot token 和目标 chat id。

## 升级前

- [ ] 执行 `hmn doctor`，确认 master env、数据库、systemd unit、approval gateway、Headscale 配置处于 OK/WARN 可解释状态。
- [ ] 确认 `/var/backups/hermes-managed-network` 可写。
- [ ] 使用 `HMN_UPGRADE_POLICY=auto` 或交互确认升级。
- [ ] 不把 Telegram/GitHub/Headscale token 写入文档或提交记录。

## 部署/升级后

- [ ] installer 已生成 `/etc/hermes-managed-network/upgrade-manifest.env`。
- [ ] `systemctl is-active hermes-managed-network.service` 通过。
- [ ] `curl -fsS http://127.0.0.1:${HMN_PORT}/healthz` 通过。
- [ ] `curl -fsS http://127.0.0.1:${HMN_PORT}/api/v1/version` 通过。
- [ ] `hmn version` 与预期包版本一致。
- [ ] `hmn doctor` 输出包含升级备份路径与修复建议。
- [ ] 如启用审批网关，`hermes-managed-network-approval-gateway.service` 已启动。

## 回滚

- installer 会在升级时备份：
  - control-plane DB
  - master env
  - approval-gateway env
  - legacy telegram-gateway env
  - headscale env
- 回滚原则：先停服务，再恢复 DB/env，再重启服务，最后重新跑 `hmn doctor`。
- 具体备份位置以 `/etc/hermes-managed-network/upgrade-manifest.env` 中的 `HMN_BACKUP_DIR` 和 `HMN_LAST_BACKUP_STAMP` 为准。
