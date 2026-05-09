# 权限模型

## 展示层：A/B/C 托管等级

### A 档：观察托管

允许：

- 查看系统状态
- 查看日志
- 盘点资产
- 写文档
- 发告警

禁止：

- 修改配置
- 重启服务
- 删除文件

### B 档：日常托管

允许低风险动作：

- 重启指定服务
- 重启指定容器
- 验证后 reload Nginx / Caddy
- 清理可确认日志
- 补充 swap
- 更新巡检文档

要求：

```text
变更前备份 -> 执行动作 -> 变更后验证 -> 写入审计
```

### C 档：应急全托管

用于救援、迁移、严重故障。

允许：

- 修改 SSH
- 修改防火墙
- 恢复备份
- 迁移服务
- 处理数据库故障

要求：

- 必须审批
- 必须限时
- 用完自动收权

## 底层：权限包

A/B/C 是展示层，底层建议使用权限包。

```yaml
observe:
  - read.system
  - read.logs
  - read.docker
  - write.docs

maintain:
  - restart.service
  - restart.container
  - reload.proxy
  - clean.safe_logs

emergency:
  - edit.ssh
  - edit.firewall
  - restore.backup
  - migrate.service
```

映射关系：

```text
A = observe
B = observe + maintain
C = observe + maintain + emergency
```

## sudo 白名单

长期不建议：

```sudoers
hermes ALL=(ALL) NOPASSWD: ALL
```

建议按 playbook 允许必要命令。示例见：

- `examples/sudoers-hermes`
