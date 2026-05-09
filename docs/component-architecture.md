# 插件化组件架构

HMN 的长期形态应是 **核心控制面 + 按需加载组件**。

核心控制面只负责身份、授权、审计、调度、状态与回滚边界；反代、转发、负载均衡、集群、备份、监控等能力都以 Component Bundle 形式接入。

这样可以避免把 nginx、frp、Headscale、备份脚本等细节硬编码进 master，也方便不同节点按能力和风险启用不同组件。

## 总体分层

```text
Hermes Master
  - 指令理解
  - 策略 / 审批
  - 节点清单
  - 组件注册表
  - Component Planner
  - Task / Playbook 调度
  - 审计日志
        ↓
Task Engine
  - Worker Queue
  - SSH Executor（规划中）
  - Cron 巡检（规划中）
        ↓
Network / Identity
  - Headscale / Tailscale（规划中）
  - ACL / tag / 临时授权（规划中）
        ↓
Managed Nodes
  - hermes 用户
  - sudo 白名单
  - runtime profile
  - service-manager adapter
  - node reporter / optional worker
        ↓
Components
  - reverse-proxy
  - forwarder
  - load-balancer
  - cluster
  - monitor
  - backup
  - docs-sync
```

## Core 与 Component 的边界

### HMN Core 负责

- 节点身份和状态
- 节点 capability / runtime profile
- 权限包与审批策略
- 组件注册表和版本兼容
- 组件计划生成与风险分级
- task / playbook 下发
- 审计日志
- 状态查询
- 回滚入口

### Component 负责

- 声明自己需要什么能力
- 声明自己会提供什么服务
- 声明风险等级
- 生成 install / configure / verify / uninstall 计划
- 提供配置模板
- 提供健康检查规则
- 上报当前状态

Core 不应该理解 nginx、Caddy、frp、gost、HAProxy、Headscale 的内部细节；这些细节属于组件。

## Component Bundle 目录建议

```text
components/
  reverse-proxy/
    component.yaml
    README.md
    templates/
      caddyfile.j2
      nginx-site.conf.j2
    playbooks/
      install.yaml
      configure.yaml
      verify.yaml
      uninstall.yaml
    probes/
      detect.sh
    tests/
      test_manifest.py
```

包内组件也可以放在：

```text
src/hermes_managed_network/components/<component_id>/
```

外部组件以后可以通过 Git repo、OCI artifact、压缩包或本地目录加载，但 MVP 先只支持内置组件即可。

## Component Manifest

每个组件必须有 `component.yaml`。

示例：

```yaml
id: reverse-proxy
name: Reverse Proxy
version: 0.1.0
api_version: 1
summary: HTTP/HTTPS reverse proxy component
risk: medium

requires:
  runtime_profiles:
    - full-worker
    - proxy-managed
  service_managers:
    - systemd
  capabilities:
    - network.bind
    - file.write
    - service.manage
  ports:
    - 80
    - 443

provides:
  services:
    - reverse-proxy
  commands:
    - proxy.site.create
    - proxy.site.remove
    - proxy.reload

permissions:
  observe:
    - component.status
    - component.verify
  operate:
    - component.configure
    - component.restart
  admin:
    - component.install
    - component.uninstall

config_schema:
  type: object
  required:
    - domain
    - upstream
  properties:
    domain:
      type: string
    upstream:
      type: string
    tls:
      type: boolean
      default: true

drivers:
  default: caddy
  options:
    - caddy
    - nginx

playbooks:
  install: playbooks/install.yaml
  configure: playbooks/configure.yaml
  verify: playbooks/verify.yaml
  uninstall: playbooks/uninstall.yaml

health:
  checks:
    - type: command
      command: systemctl is-active caddy
    - type: http
      url: https://{{ domain }}/healthz

audit:
  category: component.reverse-proxy
```

## 组件生命周期

统一生命周期：

```text
discover
  ↓
plan
  ↓
approve
  ↓
apply
  ↓
verify
  ↓
monitor
  ↓
upgrade / rollback / uninstall
```

含义：

- `discover`：加载组件 manifest，检查节点能力是否满足
- `plan`：生成执行计划，不改机器
- `approve`：按风险等级进入自动通过或审批
- `apply`：下发 task / playbook
- `verify`：验证服务状态和端口
- `monitor`：进入巡检
- `rollback`：按组件声明的回滚入口执行
- `uninstall`：卸载组件并清理状态

## CLI 目标形态

```bash
hmn component list
hmn component show reverse-proxy
hmn component plan reverse-proxy --node node1 --config site.yaml
hmn component apply reverse-proxy --node node1 --config site.yaml
hmn component status --node node1
hmn component verify reverse-proxy --node node1
hmn component uninstall reverse-proxy --node node1
```

后续也可以提供更贴近场景的快捷命令：

```bash
hmn proxy add example.com http://127.0.0.1:3000 --node node1
hmn forward add --listen 0.0.0.0:8443 --target 10.0.0.2:443 --node node1
hmn lb create app-lb --backend node1:3000 --backend node2:3000
```

快捷命令本质上仍应转成 component plan/apply。

## 数据模型建议

### components

记录可用组件。

```text
component_id
name
version
api_version
source
status
enabled_at
manifest_json
```

### node_components

记录某个节点上组件的期望状态和当前状态。

```text
node_id
component_id
desired_state
current_state
config_json
installed_version
driver
last_plan_id
last_run_id
last_verified_at
```

### component_runs

记录组件一次 plan/apply/verify/uninstall。

```text
run_id
component_id
node_id
action
risk
status
plan_json
result_json
created_by
created_at
completed_at
```

### component_artifacts

记录配置文件、生成的 systemd unit、反代站点配置等工件。

```text
artifact_id
component_id
node_id
path
kind
checksum
content_preview
created_at
```

## 风险与审批

组件动作必须有风险等级。

建议默认：

```text
low
  - status
  - verify
  - dry-run plan

medium
  - 写普通服务配置
  - reload/restart 单个服务
  - 新增反代站点

high
  - 开防火墙端口
  - 修改 SSH / sudoers
  - 安装 Headscale / Tailscale
  - 删除组件
  - 影响多节点负载均衡
  - 集群变更 / leader 切换
```

执行策略：

```text
low       自动执行 + 审计
medium    默认汇报，可配置自动执行
high      默认必须审批
critical  默认拒绝，除非显式 break-glass
```

## 组件与 runtime profile

组件不能假设所有节点都是 systemd Linux。

组件必须声明支持的 runtime profile：

```text
full-worker
  可运行完整 worker，可执行 task/playbook

lite-worker
  只有 POSIX sh / curl / wget，能力有限

beacon-only
  只能心跳和上报状态，不能执行变更

proxy-managed
  不能跑 worker，但 master 可通过 SSH/API 代管
```

组件也要声明 service manager：

```text
systemd
openrc
procd
launchd
windows-task
cron
loop
none
```

安装逻辑必须通过 service-manager adapter，不要在组件里直接假设 systemd。

## 第一批组件建议

### reverse-proxy

用途：网站反代、TLS、路由。

候选 driver：

- Caddy：默认推荐，配置简单，自动 TLS
- Nginx：通用，但模板复杂
- Traefik：适合容器场景

### forwarder

用途：端口转发、内网服务暴露、临时通道。

候选 driver：

- gost
- frp
- socat
- nftables / iptables

### load-balancer

用途：多后端负载均衡、健康检查。

候选 driver：

- HAProxy
- Nginx upstream
- Caddy reverse_proxy 多 upstream

### monitor

用途：巡检、心跳、磁盘、内存、服务状态。

MVP 先复用 node heartbeat facts，再逐步增加指标。

### backup

用途：目录备份、数据库 dump、推送文件中心。

要求：

- 明确 include/exclude
- 先 dry-run 估算
- 记录 checksum
- 备份结果写服务文档

### docs-sync

用途：同步机器文档、服务文档、域名索引、变更记录。

要求：

- 机器维度文档
- 服务维度文档
- 不写敏感 token
- 支持 Git/raw 或文件中心输出

## 推荐实现顺序

### Phase A：架构冻结

- manifest schema
- component registry
- component lifecycle
- risk model
- 数据表
- CLI 命令形态

### Phase B：组件框架 MVP

- `hmn component list`
- `hmn component show`
- `hmn component plan`
- `hmn component apply`：MVP 只记录 desired/current 状态与审计，不真实改机器
- `hmn component verify`：独立于 apply，可在没有 apply 记录时运行并写审计
- `hmn component uninstall`：一等动作，记录 absent/planned 状态与审计
- `hmn component status`
- 内置 manifest loader
- SQLite 表迁移
- audit 事件

### Phase C：第一个真实组件

优先做 `reverse-proxy`。

原因：

- 场景直观
- 验证模板、配置、reload、verify
- 和后续 docs-sync / monitor / backup 都能联动

### Phase D：网络与多节点组件

- `forwarder`
- `load-balancer`
- Headscale/Tailscale identity bridge

### Phase E：托管闭环组件

- `monitor`
- `backup`
- `docs-sync`

## 重要原则

- Core 不直接写 nginx/frp/headscale 配置
- 所有组件动作必须先 plan
- plan 必须可审计、可展示、可审批
- apply 只能通过 Task Engine / Executor 执行
- verify 必须独立于 apply
- uninstall 必须是组件一等能力
- 组件状态分 desired/current
- 高风险默认不自动执行
- 组件不能存储明文长期凭证
- 文档同步不能写 token / 密钥

## MVP 不做什么

短期不要做：

- 插件市场
- 任意第三方 Python 插件执行
- 自动安装任意来源组件
- 多租户隔离
- 复杂 Kubernetes 式调度

MVP 先做 **内置组件 + manifest + playbook/template + audit**，等安全边界稳定后再开放外部组件。
