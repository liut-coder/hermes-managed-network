# Useful Ops MVP v1.1

## 结论

Useful Ops v1.1 当前已经补齐一条可验证的 dry-run 运维链路：

```text
inspect/service discovery -> DB service registry -> deploy/docs/uptime/console dry-run -> approval-gated apply/sync
```

它现在适合：

- 本机资产盘点
- 服务清单推导并持久化到 DB service registry
- 从 DB service registry 生成 deploy/docs/uptime 计划
- console API 查看服务摘要
- 文档草稿与 docs-sync 计划生成
- Uptime Kuma 同步计划预演

它现在还不做：

- 不连接真实 Uptime Kuma
- 不写生产资产目录
- 不启用真实 token/password/API key
- 不执行任何未审批的生产写入

敏感值一律写 `[REDACTED]`。

## 当前新增能力

### 1. inspect

命令：

```bash
hmn inspect node --local --output /tmp/inventory.json --json
```

用途：

- 盘点本机端口、容器、systemd 服务、反代域名、路径等。
- 输出 inventory JSON，供下游 discover 使用。

边界：

- `--local` 只盘点本机。
- 当前 MVP 不执行远程 SSH。
- 只读，不改机器。

### 2. discover

命令：

```bash
hmn discover services --inventory /tmp/inventory.json --output /tmp/service-registry.json --json
```

用途：

- 从 inventory 推导 service registry。
- 把容器、systemd 服务、域名、端口整理成统一服务记录。

典型字段：

- `service_id`
- `name`
- `node`
- `kind`
- `domains`
- `ports`
- `runtime`
- `source`
- `docs_path`
- `warnings`

边界：

- 只依赖 inventory 文件。
- 不回写原主机配置。
- 敏感来源字段展示时必须脱敏。

### 3. service registry

service registry 是 Useful Ops 链路的中间标准件。当前支持两种来源：临时 JSON registry，以及由 `SQLiteStore.list_service_records()` 读取的 DB service registry；deploy/docs/uptime 共用同一套 DB service record -> `ServiceRegistry` 适配。

作用：

- 给 `deploy plan/status --db` 提供统一输入
- 给 `docs sync plan/apply --db` 提供统一输入
- 给 `uptime plan/sync --db` 提供统一输入
- 给 console `/api/v1/console/services` 提供服务摘要
- 把 inspect/discover 结果固定成可审计 DB service records；JSON registry 仍可用于离线 dry-run

约束：

- 允许放测试/临时目录
- 不能默认当作生产事实源直接写回外部系统
- 文档中引用时，敏感字段必须先脱敏

### 4. docs generate

命令：

```bash
hmn docs generate --registry /tmp/service-registry.json --output-dir /tmp/hmn-docs
```

用途：

- 从 registry 生成 service 文档
- 生成 service/domains/runbooks 索引
- 输出适合后续人工审核

当前输出定位：

- 应显式指定输出目录，或使用默认仓库内 `docs/` 目录
- 适合 dry-run、测试、PR 审查
- 不直接写 `/srv/files/...` 等生产资产目录

脱敏要求：

- `token`
- `password`
- `passwd`
- `pwd`
- `api_key`
- `access_token`
- `refresh_token`
- `Authorization: Bearer ...`

以上都必须写成 `[REDACTED]`。

示例：

```text
source: docker inspect --format [REDACTED]
monitor.api_key: [REDACTED]
```

### 5. docs sync 与 uptime plan dry-run

命令：

```bash
hmn docs sync plan --db /tmp/hmn.db --json
hmn uptime plan --db /tmp/hmn.db --json
# 离线 JSON registry 仍支持：
hmn uptime plan --service-registry /tmp/service-registry.json --json
```

用途：

- `docs sync plan --db` 从 DB service registry 生成 docs-center dry-run，同步 server/service/domain/runbook 索引目标
- `uptime plan --db` 从同一 DB service registry 推导可创建的监控项
- Uptime 优先生成 HTTP 监控；没有域名时回退到 TCP 监控
- 无域名且无端口时写入 `skip`

当前边界：

- 只输出计划 JSON
- 不发起任何外部 API 请求
- 不对真实 Uptime Kuma 做 create/update

dry-run 输出结构示例：

```json
{
  "create": [
    {
      "service_id": "demo-node:docker:web",
      "name": "web",
      "monitor": {
        "type": "http",
        "name": "web (demo-node)",
        "url": "https://app.example.com"
      }
    },
    {
      "service_id": "node-b:docker:ssh",
      "name": "ssh",
      "monitor": {
        "type": "tcp",
        "name": "ssh (node-b)",
        "host": "node-b",
        "port": 2222
      }
    }
  ],
  "update": [],
  "skip": [
    {
      "service_id": "node-c:unknown:worker",
      "name": "worker",
      "reason": "missing domain and port"
    }
  ]
}
```

## 最短 CLI 链路

```bash
hmn inspect node --local --output /tmp/inventory.json --json
hmn discover services --inventory /tmp/inventory.json --output /tmp/service-registry.json --json
hmn service discover --node-id local --db /tmp/hmn.db --apply --json
hmn deploy plan --db /tmp/hmn.db --json
hmn docs sync plan --db /tmp/hmn.db --json
hmn uptime plan --db /tmp/hmn.db --json
```

## 明确未启用项

以下能力仍未启用，后续必须审批：

- 真实 Uptime Kuma sync
- 生产目录写入
- token/password/API key 的真实注入或回填
- 外部系统 API 调用

输出约束：

- 所有敏感值写 `[REDACTED]`
- 所有生产写入默认关闭
- 所有外部系统同步默认关闭

## 下一步建议

建议后续拆分为两个显式命令：

1. `uptime plan`
   - 永远 dry-run
2. `uptime sync`
   - 读取计划
   - 走审批
   - 再执行真实写入

文档写入也同理：

1. `docs generate`
   - 输出审阅草稿
2. `docs sync`
   - 走审批后写正式资产目录
