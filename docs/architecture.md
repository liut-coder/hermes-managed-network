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
SSH Executor / Worker Agent / Runtime Profile / Service Adapter / sudo allowlist / Playbook Runner

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


## Network Provider

HMN Core 通过 Network Provider seam 接入组网层，当前 MVP 支持 Headscale。
Core 只保存可审计的节点映射状态，不保存明文 API key：

- `network_provider`：例如 `headscale`
- `network_node_id`：provider 侧 node id
- `network_ip`：Tailnet IP
- `network_tags`：Headscale ACL tags
- `network_online`：provider 侧在线状态

当前命令闭环：

- `hmn network status`：读取 provider 状态。
- `hmn network sync`：按 hostname / node_id 匹配 Headscale nodes，更新 HMN node network 字段并写 audit。
- `hmn network preauth-key create`：生成 Headscale 接入 key 并写 audit。
- `hmn wake --network headscale`：同时输出 Tailscale/Headscale 接入命令和 HMN join 命令。

配置只引用环境变量名，例如 `api_key_env: HMN_HEADSCALE_API_KEY`；明文 token 不进入仓库、文档或审计。

## 执行模式

HMN 的执行面分成两层：

- Runtime Profile：决定节点能跑到什么程度。
- Service Manager Adapter：决定如何在该平台上持久运行。

不要假设所有节点都是 systemd Linux。老路由器和非标固件应先能力探测，再选择 `full-worker`、`lite-worker`、`beacon-only` 或 `proxy-managed`。

详见：[平台与运行时扩展架构](platform-architecture.md)

## 架构契约

HMN 后续扩展必须遵守 [架构契约](architecture-contract.md)：Core 不硬编码具体服务细节，网络、运行时、服务管理器、组件和执行器都通过明确 seam 接入。

## 插件化组件

HMN 的扩展能力采用 **核心控制面 + Component Bundle** 模型。

控制面只负责身份、授权、审计、调度和状态；反代、转发、负载均衡、集群、备份、监控、文档同步等能力都作为按需加载组件接入。

```text
HMN Core
  - component registry
  - plan / approve / apply / verify
  - task / playbook dispatch
  - audit / status

Components
  - reverse-proxy
  - forwarder
  - load-balancer
  - cluster
  - monitor
  - backup
  - docs-sync
```

组件必须先声明 manifest、能力需求、风险等级、配置 schema、playbook 和 verify 规则。所有组件动作都应先生成 plan，再按风险进入审批或执行。

详见：[插件化组件架构](component-architecture.md)

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
- revoked 节点不得继续 heartbeat / poll task / submit result
- worker protocol 不兼容时允许 heartbeat，但禁止领取任务

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
- Network Provider 只通过抽象 seam 接入；Core 不硬编码 Headscale 业务细节
- 节点只拿最小权限
- 所有高风险动作必须进入审批链路
- 审批流已通过 API / Telegram callback / gateway poller 闭环到任务派发
- 节点在线性由 heartbeat 审计派生，统一分为 online / stale / offline
