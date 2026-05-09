# 架构总览

Hermes Managed Network 是一个安全 Agent 运维托管控制面。

它把“用户指令 → Agent 推理 → 运维执行”拆成可审计、可授权、可撤销的多层系统。

## 分层

```text
用户入口层
Telegram / Web Console / API

控制面
Hermes Master / Approval / Audit / Inventory / Scheduler

网络与身份层
Headscale / Tailscale / ACL / Node Identity

执行面
SSH Executor / Worker Agent / sudo allowlist / Playbook Runner

资产层
Docs / Backup / Monitor / Domain Index
```

## 控制面职责

Hermes Master 负责：

- 接收用户指令
- 识别动作风险等级
- 选择执行器
- 发起审批
- 写入审计日志
- 维护节点清单
- 同步机器 / 服务 / 域名文档

## 执行模式

### SSH 模式

适合公网 VPS 或 Tailscale 可达节点。

```text
Hermes Master -> Headscale/Tailscale -> SSH -> Managed Node
```

优点：

- 简单
- 不需要复杂常驻 Agent
- 易审计

### Worker 模式

适合 NAT 后节点、不能开放 SSH 的环境，或需要本地长任务的场景。

```text
Managed Worker -> Pull Task -> Execute Local Playbook -> Report Result
```

约束：

- Worker 只能拉取自己的任务
- Worker token 可撤销
- 任务必须绑定 node id
- 高风险任务仍需审批

## 默认数据流

1. 用户通过 Telegram 发起指令
2. Hermes Master 解析意图与目标节点
3. Inventory 反查机器 / 服务 / 域名
4. Policy Engine 判定风险等级
5. 低风险自动执行，中高风险进入汇报或审批
6. Executor 执行 Playbook
7. Verifier 验证结果
8. Audit 写入操作记录
9. Docs 同步资产文档

## 核心边界

- Telegram 只是入口，不保存核心状态
- Headscale 只负责连接，不代表全网互信
- 节点只拿最小权限
- 所有高风险动作必须进入审批链路
