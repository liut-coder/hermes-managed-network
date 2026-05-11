# Headscale 集成方案

HMN 支持两种 Headscale 使用模式：

- `bundled`：默认模式。HMN 安装器写入内置 Headscale 配置骨架，后续由 `headscale-server` 组件负责安装、配置、verify 和备份 Headscale 本体。
- `external`：对接已有 Headscale。HMN 不安装 Headscale，只写入 Network Provider 配置并通过 API 同步节点、创建 preauth key、管理 tags。
- `disabled`：不启用 Headscale，节点可继续使用公网 SSH 或 worker pull 模式。

## 架构边界

```text
HMN Core
  - node identity / approval / audit / task / component lifecycle

Network Provider: headscale
  - status / sync / preauth-key / tags
  - 读取 /etc/hermes-managed-network/config.yaml
  - API key 来自 HMN_HEADSCALE_API_KEY 环境变量

Component: headscale-server
  - 安装与管理 Headscale 服务本体
  - 生成 Headscale 配置
  - 创建 API key / namespace
  - verify / backup / uninstall
```

Headscale 不进入 HMN Core。Core 只依赖 Network Provider seam，未来可以替换为 Netbird、Nebula、ZeroTier 或其他组网实现。

## 一键安装参数

非交互安装示例：

```bash
sudo env \
  HMN_PUBLIC_URL='https://hmn.example.com' \
  HMN_ENABLE_TELEGRAM=1 \
  HMN_TELEGRAM_CHAT_ID='<chat-id>' \
  HMN_TELEGRAM_BOT_TOKEN='<bot-token>' \
  HMN_HEADSCALE_MODE='bundled' \
  HMN_HEADSCALE_URL='https://hs.example.com' \
  HMN_HEADSCALE_NAMESPACE='misk' \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh)"
```

外部 Headscale：

```bash
sudo env \
  HMN_HEADSCALE_MODE='external' \
  HMN_HEADSCALE_URL='https://hs.example.com' \
  HMN_HEADSCALE_API_KEY='<headscale-api-key>' \
  HMN_HEADSCALE_NAMESPACE='misk' \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/liut-coder/hermes-managed-network/feat/control-plane-mvp/install.sh)"
```

敏感值只写入 root/服务用户可读的 env/config 文件，不进入审计和公开文档。

## 操作流

安装后检查：

```bash
hmn network status
hmn wake --network headscale
hmn network sync
hmn node status
hmn node doctor
```

节点有 `network_ip` 后，SSH executor 和 component verify 会优先使用 overlay IP。

## 当前状态

当前已完成：

- Headscale Network Provider MVP
- installer 中 `bundled/external/disabled` 配置入口
- Telegram Gateway 一键 systemd 集成
- `headscale-server` 内置组件 manifest

下一步：补齐 `headscale-server` 组件的真实 install/configure/verify/backup playbook。
