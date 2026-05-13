# HMN Web 控制台开发计划

> 草案 v0.2。目标：让 `hmn-web` 成为 HMN 后端的浏览器控制台，而不是只读展示页。

## 1. 目标

实现一个手机友好的 HMN Web 控制台，用来观察、控制和审计 HMN 后端能力：

- 节点管理
- 任务下发
- 审批中心
- 服务注册表
- 文档中心
- 组件中心
- 网络 / ACL
- 备份 / 恢复
- 审计日志

默认入口建议：

```text
https://hmn.misk.cc
```

## 2. 产品定位

`hmn-web` 是 HMN 控制平面的 Web UI。

它应该做：

- 把 HMN 后端能力可视化
- 让低风险操作可以直接执行
- 让高风险操作进入审批流
- 让所有操作可审计、可追踪、可回滚

它不应该做：

- 不做普通文件分享站
- 不展示明文 token / password / secret
- 不绕过 HMN 后端直接改系统
- 不让危险操作裸奔一键执行

## 3. 总体架构

MVP 先用 FastAPI 服务端渲染 HTML，不急着做复杂 SPA。

```text
浏览器
  -> /
  -> hermes-managed-network FastAPI
  -> SQLiteStore / Inventory / Task Queue / Approval / Audit
  -> HMN workers
```

推荐目录：

```text
src/hermes_managed_network/
  api.py                  # app 创建和路由挂载
  console_api.py          # Web 控制台 JSON API
  web.py                  # 页面路由
  web_templates.py        # HTML 渲染工具
  web_security.py         # 登录、脱敏、路径安全
  docs_web.py             # 文档浏览模块
```

MVP 可以先少量写在 `api.py`，但页面超过 5 个后应拆模块。

## 4. 权限和安全模型

### 4.1 登录

建议给 hmn-web 加管理密码。

环境变量候选：

```text
HMN_WEB_ADMIN_TOKEN
HMN_WEB_PASSWORD_HASH
```

浏览器侧使用 Cookie session。
API 自动化可使用 Bearer token。

### 4.2 操作风险分级

- 只读：登录后可直接查看
- 低风险：可直接执行，但必须写 audit
- 中风险：建议进入审批流
- 高风险：必须进入审批流

低风险示例：

```text
uptime
df -h
systemctl status <allowlist service>
HMN health probe
查看 worker 状态
查看服务状态
```

高风险示例：

```text
reboot
rm / 修改文件
改 Caddy / systemd / firewall
恢复备份
网络 ACL apply
撤销节点
执行自由 shell 命令
```

### 4.3 脱敏规则

所有 Web 页面和 API 返回都要脱敏：

```text
token
password
secret
api_key
authorization
private_key
cookie
session
```

## 5. 页面模块

## 5.1 总览 Dashboard

路由：

```text
GET /
GET /api/v1/console/summary
```

展示：

- managed / pending / revoked 节点数量
- online / stale / offline 节点数量
- pending approvals 数量
- running / failed tasks 数量
- 已发现服务数量
- 最近心跳
- 最近失败任务
- 关键告警

操作：

- 刷新
- 跳转节点详情
- 跳转任务详情
- 跳转审批中心

MVP 验收：

- 手机打开首页能看清 HMN 当前状态
- 首页不执行任何修改操作

## 5.2 节点管理 Nodes

路由：

```text
GET /nodes
GET /nodes/{node_id}
GET /api/v1/console/nodes
GET /api/v1/console/nodes/{node_id}
```

列表展示：

- node_id
- hostname
- IP / HMN IP
- 状态：pending / managed / revoked
- 在线状态：online / stale / offline
- trust_level
- labels
- permission_bundles
- 最近心跳

详情展示：

- OS / kernel
- CPU / memory / disk
- uptime / load
- worker 版本
- exec 是否启用
- 最近任务
- 相关服务
- 相关文档

操作：

- approve pending node
- revoke node
- 触发健康检查
- 跳转任务下发页
- 跳转服务列表
- 跳转文档

安全：

- approve/revoke 必须写 audit
- revoke 默认走审批流

## 5.3 任务中心 Tasks

路由：

```text
GET  /tasks
GET  /tasks/new?node_id=...
GET  /tasks/{task_id}
GET  /api/v1/console/tasks
POST /api/v1/console/tasks
GET  /api/v1/console/tasks/{task_id}
```

能力：

- 对单节点下发任务
- 查看任务状态
- 查看 stdout / stderr / exit_code
- 查看任务创建人、风险等级、审批状态

MVP 支持命令：

```text
uptime
df -h
systemctl status <allowlist service>
hmn health probe
```

后续支持：

- 多节点 fan-out
- 失败重试
- 定时任务
- 自由 shell 命令，但必须风险识别和审批

安全：

- allowlist 命令可直接跑
- 非 allowlist 命令先创建 approval
- 所有任务写 audit

## 5.4 审批中心 Approvals

路由：

```text
GET  /approvals
GET  /approvals/{approval_id}
POST /api/v1/approvals/{approval_id}/approve
POST /api/v1/approvals/{approval_id}/reject
```

展示：

- 审批标题
- 风险等级
- 请求来源
- 目标节点
- 操作摘要
- 脱敏后的 details
- 创建时间
- 过期时间

按钮：

```text
允许
取消
```

风格：

- 简洁卡片
- 类 iOS 确认弹窗
- 手机上可快速判断

安全：

- 点击允许后才执行高风险动作
- 决策结果写 audit
- details 中不显示秘密

## 5.5 服务注册表 Services

路由：

```text
GET /services
GET /services/{service_id}
GET /api/v1/console/services
GET /api/v1/console/services/{service_id}
```

展示：

- service_id
- 服务名
- 所属节点
- runtime：systemd / docker / compose / binary
- 域名
- 端口
- 状态
- monitor_enabled
- docs_path
- source

操作：

- 查看服务详情
- 打开关联文档
- 对服务执行 health check
- 对 allowlist 服务执行 systemctl status
- 生成监控计划
- 生成部署/迁移计划

MVP 验收：

- 能从 Web 看见 HMN 已知服务
- 能跳转到节点和文档
- 能跑低风险 health check

## 5.6 文档中心 Docs

路由：

```text
GET /docs
GET /docs/view/{relative_path}
GET /docs/raw/{relative_path}
GET /api/v1/console/docs/index
```

读取范围：

```text
/srv/files/docs/server
/srv/files/service
```

能力：

- 浏览机器文档
- 浏览服务文档
- 从 node/service 页面跳转文档
- 展示 Markdown 文本

安全：

- 禁止路径穿越
- 只允许白名单文本扩展
- 不展示敏感文件

## 5.7 组件中心 Components

路由：

```text
GET  /components
GET  /components/{component}
POST /api/v1/console/components/{component}/plan
POST /api/v1/console/components/{component}/run
```

组件候选：

- backup
- restore
- docs-sync
- forwarder
- monitor
- headscale-server
- service-discovery

能力：

- 查看组件说明
- 查看组件状态
- dry-run 生成计划
- 发起执行请求
- 查看执行历史

安全：

- 默认先 dry-run
- 涉及修改系统的 run 必须审批

## 5.8 网络 / Headscale / ACL

路由：

```text
GET  /network
GET  /network/acls
POST /api/v1/console/network/acl/plan
POST /api/v1/console/network/acl/apply
```

展示：

- Headscale 状态
- 节点路由
- tags
- ACL 当前版本
- ACL diff
- 待应用变更

操作：

- 生成 ACL 计划
- 查看 diff
- 发起 apply 审批

安全：

- ACL apply 永远审批
- 必须展示 diff
- 必须可回滚或保留旧版本

## 5.9 备份 / 恢复 Backups

路由：

```text
GET  /backups
GET  /backups/{backup_id}
POST /api/v1/console/backups/plan
POST /api/v1/console/backups/run
POST /api/v1/console/restore/plan
POST /api/v1/console/restore/run
```

展示：

- 备份目标
- 最近备份时间
- 备份状态
- manifest
- 校验和
- 可用恢复点

操作：

- dry-run backup
- 执行 backup
- dry-run restore
- 申请 restore

安全：

- restore 永远审批
- restore 前必须显示影响服务
- restore 前必须显示停启服务计划

## 5.10 审计日志 Audit

路由：

```text
GET /audit
GET /api/v1/console/audit
```

展示：

- join 事件
- 节点确认 / 撤销
- 任务创建 / 执行 / 失败
- 审批创建 / 允许 / 拒绝
- 组件执行
- 网络变更
- 备份恢复

筛选：

- node_id
- action
- risk_level
- outcome
- created_at

要求：

- details 脱敏
- 可复制 JSON
- 不泄漏 token

## 6. 推荐开发顺序

## Milestone A：只读控制台

目标：先安全地看见 HMN 状态。

任务：

1. 增加 `/` 首页
2. 增加 `/api/v1/console/nodes`
3. 增加 `/nodes`
4. 增加 `/services`
5. 增加 `/docs`
6. 增加 `/audit`

验收：

- 手机能打开控制台
- 能看 nodes/services/docs/audit
- 没有任何修改型操作
- 页面不泄漏 secrets

## Milestone B：低风险操作

目标：让 Web 能真正控制 HMN，但只开放低风险动作。

任务：

1. 增加 allowlist task API
2. 增加任务创建页面
3. 增加任务详情页面
4. 服务详情页增加 health check
5. 所有操作写 audit

验收：

- 可从 Web 跑 `uptime`
- 可从 Web 跑 `df -h`
- 可从 Web 查看 systemd allowlist 服务状态
- 可查看 stdout/stderr/exit_code

## Milestone C：审批流

目标：Web 和 Telegram 共用同一套 approval flow。

任务：

1. 增加审批中心页面
2. 增加 approve/reject 表单
3. 高风险任务创建 approval，不直接执行
4. approval 通过后再 dispatch task
5. 审批结果写 audit

验收：

- 高风险命令不会直接执行
- Web 能审批
- Telegram 审批仍可用
- 审批详情已脱敏

## Milestone D：组件化运维

目标：把 HMN 的运维能力放到 Web。

任务：

1. 组件中心
2. 备份中心
3. 恢复 dry-run
4. 网络 ACL 页面
5. docs-sync 页面
6. monitor/uptime 页面

验收：

- 组件可 dry-run
- 高风险组件 run 走审批
- 网络 ACL 展示 diff 后审批
- 备份恢复流程可追踪

## Milestone E：体验优化

目标：变成可长期使用的控制台。

任务：

1. 抽公共 layout
2. 增加卡片式 CSS
3. 增加状态 badge
4. 增加移动端导航
5. 增加任务轮询刷新
6. 可选：再抽小型前端 bundle

验收：

- 手机上好用
- 状态清楚
- 操作反馈明确
- 页面刷新不会丢状态

## 7. API 草案

```text
GET  /api/v1/console/summary
GET  /api/v1/console/nodes
GET  /api/v1/console/nodes/{node_id}
GET  /api/v1/console/services
GET  /api/v1/console/services/{service_id}
GET  /api/v1/console/tasks
POST /api/v1/console/tasks
GET  /api/v1/console/tasks/{task_id}
GET  /api/v1/console/audit
GET  /api/v1/console/docs/index
GET  /api/v1/console/docs/view/{relative_path}
```

## 8. 页面路由草案

```text
GET /
GET /nodes
GET /nodes/{node_id}
GET /services
GET /services/{service_id}
GET /tasks
GET /tasks/new
GET /tasks/{task_id}
GET /approvals
GET /approvals/{approval_id}
GET /docs
GET /audit
GET /components
GET /network
GET /backups
```

## 9. 测试计划

建议测试文件：

```text
tests/test_hmn_web.py
tests/test_console_api.py
tests/test_docs_web.py
```

重点测试：

```text
test_hmn_web_dashboard_page_renders_summary
test_console_nodes_endpoint_lists_nodes
test_hmn_web_nodes_page_links_to_node_detail
test_hmn_web_services_page_uses_service_registry
test_hmn_web_docs_rejects_path_traversal
test_console_task_create_low_risk_dispatches_directly
test_console_task_create_high_risk_creates_approval
test_hmn_web_approval_page_can_approve_pending_request
test_console_audit_endpoint_redacts_secrets
```

命令：

```bash
pytest tests/test_hmn_web.py tests/test_console_api.py tests/test_docs_web.py -q
pytest -q
```

## 10. 第一刀实现建议

第一刀不要直接做全部。

建议先做这个闭环：

1. `/` 总览页
2. `/nodes` 节点列表
3. `/services` 服务列表
4. `/docs` 文档入口
5. `/audit` 审计列表

这一刀全部只读，风险最低，但马上可用。

第二刀再做：

1. 低风险 task allowlist
2. task 创建页
3. task 详情页
4. health check 快捷按钮

第三刀做：

1. approval center
2. 高风险任务审批
3. 组件 dry-run / run

## 11. v1 验收标准

v1 至少做到：

- 可以打开 `https://hmn.misk.cc`
- 可以查看 nodes
- 可以查看 services
- 可以查看 docs
- 可以查看 audit
- 可以创建低风险任务
- 可以查看任务结果
- 高风险任务进入审批流
- Web 审批可用
- 所有修改动作写 audit
- 页面不泄漏 secrets
- 全量测试通过
