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

> 当前仓库处于架构设计与 MVP 沉淀阶段。

## 核心架构

```text
Telegram / Web Console
        ↓
Hermes Master
  - 指令理解
  - 审批策略
  - 节点清单
  - Playbook 调度
  - 审计日志
  - 文档同步
        ↓
Task Engine
  - SSH Executor
  - Worker Queue
  - Cron 巡检
        ↓
Headscale / Tailscale
  - 节点身份
  - ACL
  - 临时授权
        ↓
Managed Nodes
  - hermes 用户
  - sudo 白名单
  - node reporter
  - optional worker
        ↓
Docs / Backup / Monitor
```

## MVP 范围

### v0.1：文档型 MVP

- 架构总览
- 节点生命周期
- 安全模型
- 权限等级与权限包
- Playbook 规范
- Headscale ACL 示例
- sudoers 示例

### v0.2：脚本型 MVP

- `join.sh`
- 节点盘点脚本
- sudoers 模板
- systemd reporter 示例
- 文档生成模板

### v0.3：可运行控制面

- join token 创建 / 撤销
- node registry
- SSH executor
- audit log
- Telegram approval flow

## 文档

- [架构总览](docs/architecture.md)
- [安全模型](docs/security-model.md)
- [节点生命周期](docs/node-lifecycle.md)
- [权限模型](docs/permission-model.md)
- [Playbook 规范](docs/playbooks.md)
- [Roadmap](docs/roadmap.md)

## CLI

- `hmn wake`：交互式输入机器信息，生成一次性 token 和安全接入命令
- `hmn token create`：创建一次性 join token
- `hmn token join-command`：生成节点接入命令
- `hmn node list`：查看节点
- `hmn audit list`：查看审计日志
- `hmn playbook run`：本地演练 playbook

## 设计原则

1. 全托管不等于全权限
2. 能连上不等于全网互信
3. 高风险动作必须审批
4. 所有动作必须审计
5. 所有资产必须可反查
6. 节点接入凭证必须短期、一次性、可撤销

## 快速开始

### 启动 master

```bash
sudo bash scripts/install-master.sh
```

### 唤醒一台新节点

主控安装完成后，推荐用交互式唤醒向导生成接入命令：

```bash
hmn wake
```

向导会自动给出默认值：

- hostname 默认是 `node-serverN`，N 为当前主控已接入节点数 + 1
- 主控 URL 默认读取 `HMN_PUBLIC_URL`；没有则读取 `/etc/hermes-managed-network/master.env` 的 `HMN_HOST/HMN_PORT`
- 如果主控监听 `0.0.0.0`，会尝试使用本机第一个非 `127.0.0.1` 的 IPv4
- 不会默认写入任何真实机器 hostname/IP

确认默认值可以直接回车。随后输出一条可复制到目标机器执行的安全接入命令。

### 创建 join token

```bash
hmn token create --trust B --label managed
```

### 生成节点接入命令

```bash
hmn token join-command <TOKEN> --master-url https://your-master.example
```

### 本地演练 playbook

```bash
hmn playbook run playbooks/demo.yml --message hello
```

## License

MIT
