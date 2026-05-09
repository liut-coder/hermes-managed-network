# 平台与运行时扩展架构

HMN 不把所有节点都当成标准 Linux 服务器。

目标是：同一套控制面，按节点能力选择不同运行时，后续能自然扩展到老路由器、OpenWrt、macOS、Windows 和完全非标设备。

## 分层原则

```text
Control Plane
  - token / inventory / task / audit / policy
  - 不关心节点具体 init 系统

Runtime Profile
  - full-worker
  - lite-worker
  - beacon-only
  - proxy-managed

Service Manager Adapter
  - systemd
  - openrc
  - procd
  - launchd
  - windows-task
  - cron
  - loop / none

Node Script Asset
  - bash worker
  - POSIX sh lite worker
  - wget beacon
  - PowerShell worker
```

核心规则：

- 控制面 API 和任务模型保持统一。
- 节点安装、定时、能力探测走平台适配层。
- 老设备默认不执行任意 shell。
- 没有 agent 能力的设备走旁路代理管理。

## Runtime Profile

### full-worker

适合：

- VPS
- 常规 Linux 服务器
- 有 Python / curl / 可写 `/etc` / 可用服务管理器的节点

能力：

- 心跳上报
- 拉取任务
- 上报执行结果
- 可选开启低风险命令执行

限制：

- 命令执行仍必须显式开启，例如 `HMN_ENABLE_EXEC=1`。
- 高风险任务仍需要审批。

### lite-worker

适合：

- OpenWrt
- BusyBox Linux
- 老路由器
- 非标准 Linux 固件

最低要求：

- POSIX `/bin/sh`
- `curl` 或 `wget`
- `crond` / procd / OpenRC / 简单 loop 之一

能力：

- 心跳
- 拉取只读或白名单任务
- 上报基础状态

默认限制：

- 不执行任意 shell。
- 不依赖 Python、bash、jq、GNU coreutils。
- 脚本必须 POSIX sh 兼容。

### beacon-only

适合：

- 资源很小的旧路由器
- 只有 `wget`/`curl` 的设备
- 不适合常驻 worker 的环境

能力：

- 定时打一条心跳
- 上报极少量事实

限制：

- 不拉任务。
- 不执行命令。
- 只用于在线性和资产可见性。

### proxy-managed

适合：

- 完全跑不了 agent 的设备
- 只能 telnet/HTTP 管理的旧设备
- 厂商非标系统

方式：

```text
HMN Master
  ↓
Nearby Managed Proxy Node
  ↓
Legacy Router / Non-standard Device
```

能力：

- 由旁路 Linux 节点代为巡检或管理。
- 非标设备本身只作为资产记录。

限制：

- 不向目标设备安装 HMN agent。
- 所有操作通过 proxy node 的 playbook 和审计链路记录。

## Service Manager Adapter

当前已实现：

- `systemd`：Linux systemd 主控和 worker timer

预留适配：

- `openrc`：Alpine / 部分轻量 Linux
- `procd`：OpenWrt
- `launchd`：macOS
- `windows-task`：Windows Scheduled Task / Service
- `cron`：BusyBox / 老 Linux
- `loop`：没有服务管理器时的临时循环模式
- `none`：beacon-only 或 proxy-managed

适配器只负责：

- 安装文件放哪里
- 如何持久运行
- 如何启动/停止/卸载
- 如何读取节点 env

不负责：

- token 策略
- 权限策略
- 审批策略
- 审计模型

这些仍由控制面统一管理。

## 能力探测

节点接入前先跑极简 POSIX sh 探测，不假设 bash/Python/jq。

探测项包括：

- OS family
- `sh` / `bash`
- `curl` / `wget`
- `python3`
- `busybox`
- `systemctl` / `openrc` / `procd` / `launchctl`
- `powershell`
- `crond`
- `/etc` / `/tmp` 可写性

探测结果映射到 runtime profile：

```text
systemd + python3 + http client + writable /etc
  -> full-worker

BusyBox/OpenWrt/OpenRC/procd + sh + http client
  -> lite-worker

sh + wget/curl only
  -> beacon-only

无法稳定运行脚本
  -> proxy-managed
```

对应代码：

- `src/hermes_managed_network/platforms.py`
- `CapabilityProbe`
- `classify_capabilities()`
- `detect_service_manager()`
- `render_capability_probe()`

## 后续扩展方式

新增一个平台时，不应改控制面核心流程。

推荐步骤：

1. 增加或完善能力探测字段。
2. 增加 service manager adapter。
3. 增加对应脚本 asset。
4. 增加 installer renderer。
5. 增加端到端 fixture 测试。
6. 更新部署文档。

例如 OpenWrt：

```text
CapabilityProbe(has_procd=True, has_busybox=True, has_wget=True)
  -> lite-worker + procd
```

例如 Windows：

```text
CapabilityProbe(os_family="windows", has_powershell=True)
  -> windows-task adapter
```

例如老路由器：

```text
CapabilityProbe(has_sh=True, has_wget=True)
  -> beacon-only
```

## 安全默认值

- 老设备默认只心跳。
- lite-worker 默认不执行任意命令。
- full-worker 的命令执行也需要显式启用。
- proxy-managed 不在目标设备落盘 agent。
- 所有任务仍必须绑定 node id。
- 高风险任务进入审批，不因平台不同而绕过。
