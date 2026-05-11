# Architecture Backlog

## Useful Ops v1.1 当前边界

已完成的 MVP 能力：

- `inspect node --local`：只盘点本机，不走远程 SSH。
- `discover services`：只从 inventory 推导 service registry。
- `docs generate`：只把 registry 渲染到指定输出目录。
- `uptime plan`：只生成监控计划 JSON，不真实写 Uptime Kuma。

明确不做：

- 不连接外部系统。
- 不写生产路径。
- 不启用真实 token/password/API key。
- 不绕过审批做生产写入。

所有示例、文档、生成内容里的敏感值必须写 `[REDACTED]`。

## 已落地的架构增量

### 1. Inspect / Discover / Registry 链路

```text
inspect node --local
  -> inventory.json
  -> discover services
  -> service-registry.json
```

约束：

- `inspect` 只做读操作。
- `discover` 只做推导，不反写源系统。
- service registry 是后续 docs / uptime dry-run 的唯一输入。

### 2. Docs Generate 链路

```text
service-registry.json
  -> docs generate --output-dir <tmp-or-docs>
  -> service docs + service/domains/runbooks indexes
```

约束：

- 输出目录应显式指定，或使用默认仓库内 `docs/` 目录；当前不应指向生产路径。
- 不能默认写 `/srv/files/...`、`/srv/service/...`、`/etc/...`。
- `source`、`monitor` 等字段里的 `token`、`password`、`passwd`、`pwd`、`api_key`、`access_token`、`refresh_token`、`Authorization: Bearer ...` 必须脱敏为 `[REDACTED]`。

### 3. Uptime Plan dry-run 链路

```text
service-registry.json
  -> uptime plan --json
  -> {create, update, skip}
```

约束：

- 当前只生成计划，不发 API 请求。
- `create` / `update` 是意图，不代表真实落库。
- `skip` 必须给出原因，便于后续审批或人工补齐。

## 待补的架构能力

### P1：外部写入审批化

- Uptime Kuma 真正同步前，需要审批对象、目标实例、变更摘要、回滚信息。
- 生产文档写入前，需要审批目标根目录、覆盖范围、冲突策略。
- 任何 API key / token / password 注入都必须走审批，并且日志中只显示 `[REDACTED]`。

### P1：生产资产目录适配

- 将 docs generate 的 dry-run 输出，映射到正式机器级/服务级资产目录。
- 增加“计划路径”和“最终路径”双字段，避免误写。
- 生产路径默认关闭，只有审批通过后才允许启用。

### P2：真实 Uptime Kuma Sync

建议未来拆成两阶段：

1. `uptime plan`
   - 继续只生成 JSON
2. `uptime sync`
   - 读取计划
   - 做 diff
   - 走审批
   - 再写外部系统

要求：

- 支持幂等比对
- 支持 dry-run / apply 双模式
- 支持脱敏审计
- 支持失败回执

### P2：远程 inspect

- 可以增加 SSH/worker 采集器
- 但必须保持 `inspect -> registry -> docs/uptime` 的只读建模边界
- 远程采集拿到的敏感值依然不能原文落文档

## 最短 CLI 示例

```bash
hmn inspect node --local --output /tmp/inventory.json --json
hmn discover services --inventory /tmp/inventory.json --output /tmp/service-registry.json --json
hmn docs generate --registry /tmp/service-registry.json --output-dir /tmp/hmn-docs
hmn uptime plan --service-registry /tmp/service-registry.json --json
```

## Uptime Plan dry-run 输出结构示例

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
    }
  ],
  "update": [],
  "skip": [
    {
      "service_id": "demo-node:systemd:demo.service",
      "name": "demo",
      "reason": "missing domain and port"
    }
  ]
}
```

说明：

- `create`：计划新增的监控项。
- `update`：未来真实 sync 时计划更新的监控项；当前 MVP 通常为空。
- `skip`：信息不足或不适合生成监控项的服务。
