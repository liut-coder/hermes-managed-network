# Production Readiness Checklist

HMN 进入长期运行前，用本清单做主控、审批、网络和 worker 的生产巡检。

## 部署前

- [ ] 使用官方入口安装：`https://raw.githubusercontent.com/liut-coder/hermes-managed-network/main/install.sh`。
- [ ] 主控前面配置 HTTPS / 反代，例如 Caddy、nginx 或隧道网关。
- [ ] 防火墙只开放必要端口；worker 默认主动出站，不要求入站。
- [ ] systemd 服务已启用并能自动重启：`hermes-managed-network.service`。
- [ ] DB、env、`config.yaml` 不进入公开仓库；权限保持 `0640` 或更严格。

## 升级前

- [ ] 执行 `hmn doctor --skip-systemd` 查看离线 readiness。
- [ ] 确认 `/etc/hermes-managed-network/upgrade-manifest.env` 会在安装器升级后写入。
- [ ] 备份包含 DB、env、`config.yaml` 和 metadata 文件。
- [ ] `upgrade-manifest.env` 中保留 `rollback command` 操作提示。

## 部署后巡检

```bash
hmn doctor
curl -fsS http://127.0.0.1:8765/healthz
curl -fsS http://127.0.0.1:8765/api/v1/version
systemctl status hermes-managed-network.service --no-pager
```

检查项：

- [ ] `/healthz` 返回成功。
- [ ] `/api/v1/version` 返回成功。
- [ ] 最近日志无持续 Traceback / permission denied / token conflict。
- [ ] Worker protocol 兼容；旧 worker 不应取到不兼容任务。
- [ ] Telegram approval gateway 使用独立 bot token，不和主 Telegram gateway 抢 `getUpdates`。
- [ ] Headscale provider 通过 `api_key_env` 读取 API key，不在文档中写明文 token。

## 回滚

1. 查看 manifest：

```bash
sudo cat /etc/hermes-managed-network/upgrade-manifest.env
```

2. 按 `HMN_BACKUP_DIR`、`HMN_LAST_BACKUP_STAMP`、`BACKUP_DB`、`BACKUP_ENV`、`BACKUP_CONFIG`、`BACKUP_METADATA` 确认备份存在。
3. 执行 manifest 中的 `ROLLBACK_COMMAND` / rollback command 操作提示。
4. 恢复 DB/env/config 后重启 systemd：

```bash
sudo systemctl restart hermes-managed-network.service
hmn doctor
```

## 组件与网络

- Telegram approval gateway：高风险动作必须审批，按钮回调后应清理 stale buttons。
- Headscale provider：preauth key、network sync、tag/ACL 写操作必须进入 audit / approval。
- Worker protocol：默认 `HMN_ENABLE_EXEC=0`，安全拒绝要回传 `execution disabled`。
