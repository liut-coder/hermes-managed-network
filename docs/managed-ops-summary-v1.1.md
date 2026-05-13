# Managed Ops Summary v1.1

## 结论

HMN v1.1 当前交付的是 managed ops dry-run MVP。

它已经具备：

- inspect / discover / service registry
- docs generate
- provider contract
- Uptime Kuma provider approval gate skeleton
- Coolify provider sync skeleton
- GitHub Actions provider status / dispatch plan skeleton
- deploy plan/status CLI MVP
- docs-sync dry-run plan
- backup provider 服务级 dry-run
- restore plan MVP
- migration plan dry-run MVP
- onboarding / capacity plan MVP

## 当前真实边界

以上能力当前全部属于以下范围之一：

- dry-run
- plan
- status summary
- approval gate skeleton
- fixture-based skeleton

它们的目标是：

- 先把输入/输出结构固定下来
- 先把审阅面和审批前置条件补齐
- 先让 roadmap/PR/测试可以收敛

不是：

- 直接接真实外部系统
- 直接做生产写入
- 直接承诺自动化闭环

## 明确不做

当前版本明确不做以下真实动作：

- 不真实写 Uptime Kuma
- 不真实写 Coolify
- 不真实 dispatch GitHub Actions workflow
- 不真实写 backup repo
- 不真实执行 restore
- 不真实执行 migration / 自动切流
- 不真实写 DNS
- 不真实写反代配置
- 不真实写 `/srv/files/...` 或其他正式资产目录
- 不真实注入 token/password/API key

## 审批边界

当前 approval gate 的定位是：

- 记录高风险意图
- 固定 plan fingerprint / provider / target 等元数据
- 为后续真实 apply/sync 留出审批接口

当前 approval gate 不是：

- 审批后自动真实执行
- 审批后自动连接外部系统
- 审批后自动放开生产写入

也就是说：

审批存在 ≠ 真实 apply 已实现。

## 迁移边界

当前 migration / restore / backup 只提供计划层能力：

- 说明应该备哪些数据
- 说明应该按什么策略恢复
- 说明可能的前置条件、验证步骤、风险提示

当前不承诺：

- 一键迁移
- 无损迁移
- 零停机切流
- 自动回滚

这些都要等后续真实实现和授权。

## 文档与脱敏边界

所有文档、计划输出、示例 JSON、provider summary 都必须：

- 保持 dry-run 语义清晰
- 不夸大为“已接通生产”
- 对敏感字段统一输出 `[REDACTED]`

## 后续真实能力清单

后续若要进入可执行 managed ops，需要继续补：

1. 真实 provider apply/sync/dispatch
2. 真实 docs sync 到正式资产目录
3. 真实 backup repo/snapshot/verify
4. 真实 restore 执行链路
5. 真实 migration 切流与回滚
6. token/password/API key 安全注入与回写
7. DNS / 反代 / 外部系统写入审批闭环
8. 审批通过后的最小可验证执行流
