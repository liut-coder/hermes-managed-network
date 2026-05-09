# 安全模型

## 基本原则

> 全托管不等于全权限。

Hermes Managed Network 的安全模型围绕四件事设计：

1. 最小权限
2. 短期凭证
3. 可审计操作
4. 可撤销节点

## Token 类型

### Join Token

用于新节点首次接入。

要求：

- 一次性
- 默认 30 分钟过期
- 用完立即失效
- 可手动撤销
- 绑定托管等级
- 绑定节点标签
- 不写入长期文档

### Node Token

用于节点上报状态或 Worker 拉取任务。

权限：

- 上报节点状态
- 拉取属于自己的任务
- 汇报执行结果

禁止：

- 获取其他节点配置
- 管理其他节点
- 执行未授权任务

### Admin Session / Admin Token

只存在于控制面或 Web 管理端。

禁止下发到被托管节点。

## 网络边界

推荐 Headscale / Tailscale ACL 默认收紧：

```text
admin          -> master:管理端口
master         -> managed:22
worker         -> master:任务 API
managed        -> infra:必要服务端口
managed        -> managed:拒绝
worker         -> managed:拒绝
```

## 高风险动作

以下动作必须审批：

- 删除数据
- 数据库恢复
- 修改 SSH
- 修改防火墙
- 大规模迁移
- 重启整机
- 升级到 C 档托管

## 审计要求

每次执行记录：

- 时间
- 用户来源
- 目标节点
- 动作类型
- 权限等级
- 命令摘要
- 执行结果
- 验证结果
- 回滚点
- 关联文档

敏感内容必须打码：

- token
- password
- cookie
- private key
- database url
