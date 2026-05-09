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
