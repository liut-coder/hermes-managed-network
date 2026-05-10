# Hermes Managed Network

一个面向个人和小团队服务器的安全 Agent 运维托管控制面。

它的目标不是“给 AI 一个 root 密码”，而是把 Agent 放进一套有权限边界、有审计、有撤销机制的运维系统里：

- 一条命令接入服务器
- 私有组网连接节点
- A/B/C 分级托管
- Playbook 化执行低风险运维动作
- Telegram / API 审批高风险动作
- 自动巡检与有限自愈
- 自动沉淀机器、服务、域名文档

> 当前分支提供可运行的控制面 MVP：join token、节点登记、心跳/worker、任务队列、审计、组件状态框架。

## 核心架构

```text
Telegram / Web Console
        ↓
Hermes Master
  - 指令理解
  - 审批策略
  - 节点清单
  - Task / Component 调度
  - 审计日志
  - 文档同步
        ↓
Task Engine
  - Worker Queue
  - Cron 巡检
  - Component Lifecycle
        ↓
Managed Nodes
  - hermes 用户
  - node.env 指纹
  - heartbeat / optional worker
        ↓
Docs / Backup / Monitor
```

## MVP 范围

已打通：

- 本地 FastAPI controller
- SQLite 存储
- 一次性 join token
- 节点 join / confirm / revoke
- 节点 heartbeat
- 安全模式 worker
- task 下发、轮询、result 回传
- audit log
- 内置组件 manifest / lifecycle 状态闭环

暂不包含：

- 默认远程 shell 执行
- 高风险自动执行
- token 过期/撤销完整运维策略
- 多平台 worker 安装器
- 真实组件 apply 驱动

## 快速开始

### 1. 本地开发安装

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

查看版本：

```bash
hmn version
```

### 2. 本地启动 controller

```bash
HMN_DB=/tmp/hmn-demo.db \
uvicorn 'hermes_managed_network.api:create_app' \
  --factory \
  --host 127.0.0.1 \
  --port 8765
```

健康检查：

```bash
curl -sS http://127.0.0.1:8765/healthz
curl -sS http://127.0.0.1:8765/api/v1/version
```

### 3. 生成 join token

```bash
HMN_DB=/tmp/hmn-demo.db hmn token create --trust B --label worker
```

也可以用交互式接入向导：

```bash
HMN_DB=/tmp/hmn-demo.db \
HMN_PUBLIC_URL=http://127.0.0.1:8765 \
hmn wake
```

`hmn wake` 会生成一个一次性 token，并输出可复制到节点执行的安全接入命令。

### 4. 生成节点接入命令

```bash
HMN_DB=/tmp/hmn-demo.db \
hmn token join-command <TOKEN> \
  --master-url http://127.0.0.1:8765 \
  --safe
```

输出命令会下载 controller 暴露的 `/scripts/join.sh`，并要求节点侧提供：

- `HERMES_JOIN_TOKEN`
- `HERMES_MASTER_URL`
- `HERMES_USER`，默认 `hermes`

接入成功后节点进入 `pending`。

### 5. 确认节点

```bash
HMN_DB=/tmp/hmn-demo.db hmn node list
HMN_DB=/tmp/hmn-demo.db hmn node confirm
HMN_DB=/tmp/hmn-demo.db hmn node status
```

如果只有一个 pending 节点，`hmn node confirm` 会自动选择。

### 6. 安装 worker / 心跳

```bash
HMN_DB=/tmp/hmn-demo.db \
hmn node install-heartbeat --master-url http://127.0.0.1:8765
```

把输出的一条命令复制到目标节点执行。它会：

- 写入 `/etc/hermes-managed-network/node.env`
- 下载 `/scripts/worker.sh` 到 `/usr/local/bin/hmn-worker`
- 安装 `hermes-managed-network-heartbeat.timer`
- 默认保持安全模式：`HMN_ENABLE_EXEC=0`

## worker 安全模式

默认 worker 只做两件事：

- 上报 heartbeat / worker facts
- 拉取 pending task 并回传结果

默认不会执行下发的 shell 命令。

当 `HMN_ENABLE_EXEC!=1` 时，worker 收到任务后会回传：

- `exit_code=126`
- `stderr=execution disabled; set HMN_ENABLE_EXEC=1`
- task 状态进入 `failed`

只有在节点侧显式设置：

```bash
HMN_ENABLE_EXEC=1
```

worker 才会执行任务命令。

## task 下发示例

向唯一 managed 节点下发一条低风险任务：

```bash
HMN_DB=/tmp/hmn-demo.db hmn task run 'uptime'
HMN_DB=/tmp/hmn-demo.db hmn task list
```

节点 worker 下一次轮询会请求：

```bash
POST /api/v1/nodes/{node_id}/tasks/next
```

执行或安全拒绝后回传：

```bash
POST /api/v1/tasks/{task_id}/result
```

查看审计：

```bash
HMN_DB=/tmp/hmn-demo.db hmn audit list
```

## 一键安装主控

在 Linux/systemd 主控机上：

```bash
curl -fsSL https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh | sudo bash
```

更新已安装主控同样执行上面的命令。

## CLI 速查

- `hmn`：打开交互式控制台
- `hmn menu --plain`：打印快捷菜单
- `hmn wake`：接入新机器
- `hmn token create`：创建一次性 token
- `hmn token join-command <TOKEN> --master-url <URL> --safe`：生成节点接入命令
- `hmn node list`：查看节点
- `hmn node confirm`：确认 pending 节点
- `hmn node status`：查看节点详情
- `hmn node doctor`：本地控制面检查
- `hmn node heartbeat-command`：生成心跳 curl
- `hmn node install-heartbeat`：生成 worker 安装命令
- `hmn node worker-status`：查看 worker 上报状态
- `hmn task run 'uptime'`：下发低风险任务
- `hmn task list`：查看任务状态
- `hmn component list`：查看组件
- `hmn component plan/apply/verify/uninstall`：组件生命周期 MVP
- `hmn audit list`：查看审计
- `hmn version`：查看版本
- `hmn update`：输出更新命令
- `hmn uninstall`：输出/执行卸载命令

## 文档

- [架构总览](docs/architecture.md)
- [插件化组件架构](docs/component-architecture.md)
- [平台与运行时扩展架构](docs/platform-architecture.md)
- [安全模型](docs/security-model.md)
- [节点生命周期](docs/node-lifecycle.md)
- [权限模型](docs/permission-model.md)
- [Playbook 规范](docs/playbooks.md)
- [部署指南](docs/deployment.md)
- [Roadmap](docs/roadmap.md)

## 设计原则

1. 全托管不等于全权限
2. 能连上不等于全网互信
3. 高风险动作必须审批
4. 所有动作必须审计
5. 所有资产必须可反查
6. 节点接入凭证必须短期、一次性、可撤销

## License

MIT
