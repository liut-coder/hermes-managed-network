# 节点生命周期

## 1. 创建 Join Token

控制面生成一次性 join token：

```text
id: j_xxxxx
expires_at: now + 30m
trust_level: A | B | C
labels: [managed, region:hk]
status: pending
```

## 2. 节点执行接入命令

```bash
HERMES_JOIN_TOKEN="j_xxxxx" bash <(curl -fsSL https://sh.example.com/hmi)
```

接入脚本负责：

- 安装基础依赖
- 创建低权限 `hermes` 用户
- 加入私有网络
- 采集机器指纹
- 向控制面提交注册请求

## 3. 临时接入

节点初次接入后只进入 pending 状态。

此时只能：

- 上报基础信息
- 等待控制面确认

不能直接执行运维动作。

## 4. 正式托管

控制面确认后下发：

- 节点 ID
- node token
- 托管等级
- 权限包
- sudo 白名单
- 巡检策略
- 文档模板

## 5. 日常运行

节点周期性上报：

- 心跳
- 系统信息
- 服务状态
- 磁盘 / 内存 / 负载
- 可选资产快照

## 6. 权限升级

A/B/C 等级可以临时调整。

C 档必须：

- 设置过期时间
- 记录审批人
- 记录原因
- 到期自动收权

## 7. 撤销节点

撤销节点时：

- 禁用 node token
- 从 ACL 中移除
- 移除或禁用 SSH key
- 标记 inventory 状态为 revoked
- 保留历史审计日志
