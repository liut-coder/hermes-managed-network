# 部署指南

本页记录 HMN 主控和节点接入的推荐部署方式。

## 安装主控

使用仓库提供的短安装脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh | sudo bash
```

安装完成后执行：

```bash
hmn wake
```

`hmn wake` 会交互生成一条节点接入命令。把输出命令复制到目标节点执行即可。

## hmn wake 默认值

`hmn wake` 适合在主控机上执行。它会自动给出默认值：

- hostname：默认 `node-serverN`，N 为当前主控已接入节点数 + 1
- 机器地址：默认留空，不写死任何真实机器 IP
- 主控 URL：优先读取 `HMN_PUBLIC_URL`
- 如果未设置 `HMN_PUBLIC_URL`，读取 `/etc/hermes-managed-network/master.env` 的 `HMN_HOST/HMN_PORT`
- 如果主控监听 `0.0.0.0`，尝试使用本机第一个非 `127.0.0.1` 的 IPv4
- 信任级别：默认 `B`
- 标签：默认 `worker`
- 节点系统用户：默认 `hermes`
- token 有效期：默认 30 分钟

如需明确指定主控对外地址：

```bash
HMN_PUBLIC_URL='http://<主控可被节点访问的地址>:8765' hmn wake
```

## 节点接入流程

1. 在主控执行：

```bash
hmn wake
```

2. 按提示填写节点信息，或直接接受默认值。

3. 将输出的一键命令复制到目标节点执行。

4. 回到主控查看 pending 节点：

```bash
hmn node list
```

5. 确认节点并授予权限包：

```bash
hmn node confirm
```

如果只有一个 pending 节点，HMN 会自动选择，不需要手填 node_id。

6. 安装节点心跳/worker：

```bash
hmn node install-heartbeat
```

把输出的一条命令复制到目标节点执行。它会安装 systemd timer，节点会定时向主控上报心跳并拉取任务。

7. 查看节点状态和审计：

```bash
hmn node status
hmn node doctor
hmn audit list
```

## 下发低风险任务

节点安装心跳/worker 后，可在主控下发低风险命令：

```bash
hmn task run 'uptime'
hmn task list
```

默认 worker 只轮询和上报心跳。真正执行任务需要在节点侧显式开启 `HMN_ENABLE_EXEC=1`，避免误执行。

安全模式行为：

- `HMN_ENABLE_EXEC=0` 是默认值
- worker 收到 task 后不会执行 shell
- result 会回传 `exit_code=126`
- `stderr` 会说明 `execution disabled; set HMN_ENABLE_EXEC=1`
- 任务状态会变成 `failed`，用于证明队列和 result 回传闭环可用

## 审批网关

高风险任务会进入 approval outbox。主控机优先运行通用 `approval-gateway`，当前客户端实现为 Telegram；旧的 `telegram-gateway` 命令和 systemd 单元仍保留兼容。

一次性发送 pending 审批通知：

```bash
HMN_API_URL='http://127.0.0.1:8765' \
HMN_APPROVAL_GATEWAY_TARGET='<chat-id>' \
HMN_APPROVAL_GATEWAY_TOKEN='<bot-token>' \
hmn approval-gateway poll-once --client telegram
```

持续运行：

```bash
HMN_API_URL='http://127.0.0.1:8765' \
HMN_APPROVAL_GATEWAY_TARGET='<chat-id>' \
HMN_APPROVAL_GATEWAY_TOKEN='<bot-token>' \
hmn approval-gateway run --client telegram --interval 10
```

兼容旧命令：

```bash
HMN_TELEGRAM_CHAT_ID='<chat-id>' \
HMN_TELEGRAM_BOT_TOKEN='<bot-token>' \
hmn telegram-gateway poll-once
```

systemd 示例（token 建议写入 root-only env 文件，不要写入命令行历史）：

```ini
# /etc/hermes-managed-network/approval-gateway.env
HMN_API_URL=http://127.0.0.1:8765
HMN_APPROVAL_GATEWAY_CLIENT=telegram
HMN_APPROVAL_GATEWAY_TARGET=<chat-id>
HMN_APPROVAL_GATEWAY_TOKEN=<bot-token>
# 兼容旧 telegram-gateway 命令/脚本
HMN_TELEGRAM_CHAT_ID=<chat-id>
HMN_TELEGRAM_BOT_TOKEN=<bot-token>
```

```ini
# /etc/systemd/system/hermes-managed-network-approval-gateway.service
[Unit]
Description=Hermes Managed Network approval gateway
After=network-online.target hermes-managed-network.service
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/hermes-managed-network/approval-gateway.env
ExecStart=/usr/local/bin/hmn approval-gateway run --client telegram --target <chat-id> --interval 10
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo install -d -m 700 /etc/hermes-managed-network
sudo chmod 600 /etc/hermes-managed-network/approval-gateway.env
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-managed-network-approval-gateway.service
```

## 本地端到端 smoke test

仓库内置一条 v0.9 本地部署闭环脚本。它会使用临时数据库和临时文档目录，本机启动 controller，模拟节点 join/confirm，运行 worker，验证 disabled-exec 安全拒绝、heartbeat/worker-status，并刷新资产文档：

```bash
./scripts/smoke-local-e2e.sh
```

覆盖内容：

- `hmn-server` 使用显式 `HMN_DB` / `HMN_HOST` / `HMN_PORT` 启动
- `/healthz` 和 `/api/v1/version` 就绪检查
- join token 创建与 `/api/v1/join`
- `hmn node confirm`
- worker heartbeat + task poll + disabled-exec result
- `hmn node worker-status`
- `hmn docs service` 与 `hmn docs generate`

预期结果：

- join 返回 `pending` 节点
- `hmn node confirm` 把节点变成 `managed`
- `hmn node worker-status` 显示 heartbeat / worker / protocol OK
- `hmn task list` 里任务状态为 `failed`
- result stderr 为 disabled-exec 提示，证明 worker 安全拒绝并完成回传
- 机器文档、服务索引、域名索引、Runbook 索引均生成成功

默认会删除临时目录；排障时可保留现场：

```bash
HMN_SMOKE_KEEP=1 ./scripts/smoke-local-e2e.sh
```

## 真实双机部署 smoke

2026-05-11 已用两台 Debian 12 VPS 跑通一轮真实部署闭环。仓库内置可重复脚本，用于在两台一次性 Linux/systemd 主机上重跑同类 P1 gate：

```bash
HMN_MASTER_HOST=master.example.invalid \
HMN_WORKER_HOST=worker.example.invalid \
HMN_SSH_KEY=/path/to/ssh_key \
HMN_PUBLIC_URL=http://master.example.invalid:8765 \
./scripts/smoke-remote-e2e.sh
```

可选参数：

- `HMN_REMOTE_USER=root`：SSH 用户
- `HMN_REMOTE_PORT=8765`：Master 监听端口
- `HMN_REMOTE_BRANCH=main`：远端安装分支
- `HMN_SKIP_INSTALL=1`：复用已安装 Master，仅跑验证链
- `HMN_SKIP_SSH_EXECUTOR=1`：目标未配置 SSH executor 密钥时跳过 SSH executor 验证

覆盖内容：

- Master 使用 `scripts/install-master.sh` 安装并以 systemd 启动 `hermes-managed-network`
- `hmn doctor`、`/healthz`、`/api/v1/version` 均通过
- Worker 通过 join token 接入，Master 侧 `hmn node confirm` 后进入 `managed`
- Worker 安装 `full-worker` systemd timer，`HMN_ENABLE_EXEC=0` 保持安全默认值
- `hmn node worker-status` 显示心跳、worker、协议均 OK
- Master 下发低风险 worker 任务后，Worker 安全拒绝执行并回传 result：`failed` / `exit_code=126` / `execution disabled`
- 可选验证 `hmn task ssh-run-next` 的 SSH executor 路由
- `hmn docs generate` 生成机器文档、服务索引、域名索引和 Runbook 索引

注意事项：

- join token、SSH 密码、私钥、Bot token 都是敏感信息，试点记录只写占位，不落明文
- 当前真实 smoke 默认使用公网 HTTP `8765`，生产化应补 HTTPS / 反代 / 防火墙白名单
- Worker 不需要公网入站，只需要能主动访问 Master
- 默认 disabled-exec 是预期行为；只有明确需要时才在 Worker 侧设置 `HMN_ENABLE_EXEC=1`

## 更新已安装主控

再次执行仓库短安装脚本即可：

```bash
curl -fsSL https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh | sudo bash
```

验证：

```bash
hmn --help
curl -sS http://127.0.0.1:8765/healthz
systemctl status hermes-managed-network --no-pager
```

## 敏感信息规则

- join token 是临时敏感值，不写入文档
- 密码、API key、provider key 不写入文档
- 节点接入后先处于 `pending`，必须由主控确认后才进入托管
- 所有 token 创建、节点确认/撤销动作都应写入审计
