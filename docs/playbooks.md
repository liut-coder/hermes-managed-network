# Playbook 规范

Playbook 用来约束 Agent 的运维动作，避免每次自由发挥命令。

## 标准流程

```text
precheck -> backup -> action -> verify -> audit -> rollback_hint
```

## 示例

```yaml
id: restart-container
risk: low
permissions:
  - restart.container
inputs:
  container:
    type: string
    required: true
precheck:
  - docker ps --filter name={{ container }}
action:
  - docker restart {{ container }}
verify:
  - docker ps --filter name={{ container }}
rollback_hint:
  - docker logs --tail=100 {{ container }}
```

## 风险等级

### low

可自动执行。

示例：

- 查看状态
- 查看日志
- 重启白名单服务
- 重启白名单容器

### medium

可执行后汇报，或根据策略审批。

示例：

- reload 反代
- 补 swap
- 重启 Docker
- 清理日志

### high

必须先审批。

示例：

- 删除数据
- 数据库恢复
- 修改 SSH
- 修改防火墙
- 重启整机

## 审计字段

每次 playbook 执行应记录：

```yaml
time: 2026-05-09T00:00:00Z
actor: telegram:user-id
node: node-id
action: restart-container
risk: low
inputs_redacted: {}
status: success
verify_result: passed
rollback_point: null
```
