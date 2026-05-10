# HMN 优先级推进计划

> 本文用于统一后续推进顺序：先补齐 HMN 核心控制面，再接入 Headscale 网络层，最后推进 NAS/IPv6、备份与文档同步。

## 当前判断

HMN 不重复造底层组网轮子。

- **Headscale/Tailscale** 负责机器之间的网络连通、虚拟网络身份、ACL、tag、NAT 穿透。
- **HMN Core** 负责节点登记、授权、审批、审计、任务、组件生命周期和资产文档。
- **Worker Pull** 负责无公网节点/NAS 主动出站接入，不要求节点开放入站端口。

当前 PR 已经具备控制面 MVP 基础，并已补齐 Headscale Network Provider MVP：

- FastAPI controller
- SQLite persistence
- join token / node registry
- pending → managed 节点生命周期
- heartbeat
- worker pull task
- task result submit
- audit log
- component registry / lifecycle MVP
- reverse-proxy / forwarder / monitor manifest MVP
- network provider 抽象与 Headscale adapter
- network status / sync / preauth-key CLI
- wake --network headscale 接入命令

但要变成稳定组网平台，还需要继续补齐 SSH executor、Headscale 写操作审批和真实组件执行。

---

## 总体架构边界

```text
Telegram / CLI / API
        ↓
HMN Core
  - node registry
  - token lifecycle
  - policy / approval
  - task queue
  - component lifecycle
  - audit
  - docs state
        ↓
Network Provider Adapter
  - headscale
  - tailscale SaaS（后续）
  - none / direct IPv6
        ↓
Execution Layer
  - worker pull
  - SSH executor over Tailscale IP（后续）
  - playbook runner
        ↓
Managed Nodes / NAS
  - full-worker
  - lite-worker
  - beacon-only
  - proxy-managed
```

核心原则：

1. HMN 不实现 VPN / NAT 穿透。
2. HMN 不把 Headscale 细节硬编码进 Core。
3. 所有高风险动作必须可审批、可审计、可撤销。
4. 无公网节点优先使用主动 Pull，不开放入站。
5. NAS/IPv6 接入先记录，等核心安全闭环后再优化。

---

## Phase 0：PR 收尾与状态同步

**目标：** 让当前 control-plane MVP PR 状态准确、可审查、可继续拆任务。

**优先级：P0**

### 要做

- 同步 `docs/roadmap.md` 当前真实状态。
- 把已实现但未勾选的项标为完成或拆成子项：
  - join token 创建已完成；撤销/过期 UX 待补。
  - node registry 已完成。
  - SQLite 存储已完成。
  - audit log 已完成。
  - SSH executor 未完成。
  - Telegram approval flow 未完成。
- 补充本文档到 README 或 Roadmap 的链接。

### 验证

```bash
pytest -q
python -m compileall -q src
bash -n install.sh scripts/install-master.sh scripts/join.sh scripts/worker.sh src/hermes_managed_network/assets/worker.sh
git diff --check
```

### 预计时间

半天内。

---

## Phase 1：核心安全闭环

**目标：** 在接 Headscale 前，先保证 HMN 自身的节点、token、worker、任务边界可靠。

**优先级：P0**

### 1.1 Token lifecycle 加固

当前：

- join token 创建/消费已有。

待补：

- `hmn token revoke <TOKEN_OR_ID>` 的 operator UX。
- token 到期展示。
- token list 显示状态：active / used / expired / revoked。
- revoke 写 audit。
- join API 明确拒绝 expired/revoked/used token。

验收：

- revoked token 无法 join。
- expired token 无法 join。
- token 列表不显示明文长期 secret，必要时只显示短 ID/前缀。

### 1.2 Node revoke enforcement

当前：

- node registry / confirm / revoke 基础已有。

待补：

- revoked node 不能 heartbeat。
- revoked node 不能 poll task。
- revoked node 不能 submit result。
- revoke 写 audit。
- CLI 明确显示 revoked 状态。

验收：

- API 对 revoked node 返回 403。
- worker-status 对 revoked node 输出清晰 warning。

### 1.3 Offline node handling

待补：

- 根据最新 heartbeat 时间判断 online/offline/warn。
- `hmn node status` 展示 last heartbeat 和 offline 原因。
- `hmn node worker-status` 对 heartbeat 超时返回非 0。
- audit 可记录 worker-status 检查结果。

验收：

- 没有 heartbeat：warn。
- heartbeat 超时：offline/warn。
- heartbeat 正常：online/ok。

### 1.4 Worker auth / task safety

待补：

- task dispatch 前检查 node status 必须是 managed。
- worker protocol 不兼容时继续允许 heartbeat，但拒绝 task dispatch。
- task result 只能由对应 node/fingerprint 提交。
- 为后续 task signing 预留字段。

验收：

- wrong fingerprint 不能 poll/submit。
- incompatible worker protocol 不能拿任务。
- revoked/offline node 不应被分配新任务。

### 下一步

- Headscale node expire / tag update 这类写操作进入 approval。
- SSH executor 使用 `network_ip`。
- `hmn node status` 融合 Headscale 状态。

---

## Phase 2：审批状态机雏形

**目标：** 先把 approval 数据模型和 CLI 流程做出来，再接 Telegram。

**优先级：P0 / P1**

### 要做

- 新增 approval 数据表：
  - approval_id
  - action_type
  - subject_id
  - risk
  - status: pending / approved / rejected / expired
  - request_json
  - result_json
  - created_by
  - decided_by
  - created_at
  - decided_at
- CLI：
  - `hmn approval list`
  - `hmn approval show <ID>`
  - `hmn approval approve <ID>`
  - `hmn approval reject <ID>`
- 风险策略：
  - low：可自动执行并审计。
  - medium：默认汇报，可配置自动执行。
  - high：必须 approval。
  - critical：默认拒绝。
- component apply / high-risk task 先接入 approval pending，不直接执行。

### 验收

- high risk action 会生成 pending approval。
- approve 后才能进入 task/component run。
- reject 后不会执行。
- 所有 decision 写 audit。

### 预计时间

2～4 天。

---

## Phase 3：Headscale Network Provider MVP

**目标：** 接入成熟组网层，不重复造轮子。HMN 只做管理、审计和策略。

**状态：已完成 MVP；后续进入 Headscale + Executor 联动。**

### 放置位置

当前以轻量模块落地：

```text
src/hermes_managed_network/network_base.py
src/hermes_managed_network/headscale.py
src/hermes_managed_network/network.py
```

### Core 抽象

Core 只依赖 Provider 接口：

```python
class NetworkProvider:
    def create_preauth_key(...): ...
    def list_nodes(...): ...
    def expire_node(...): ...
    def apply_tags(...): ...
    def get_node_status(...): ...
```

### 配置

不保存明文 token 到仓库或文档。

示例：

```yaml
network:
  provider: headscale
  headscale:
    url: https://headscale.example.com
    api_key_env: HMN_HEADSCALE_API_KEY
```

### MVP 功能

- [x] `hmn network status`
- [x] `hmn network sync`
- [x] `hmn network preauth-key create --node <node_id> --tag tag:hmn-managed`
- [x] `hmn wake --network headscale` 可生成 Tailscale/Headscale 接入命令。
- [x] node record 关联：
  - headscale node id
  - tailnet ip
  - tags
  - online state

### 审计事件

- [x] network preauth_key/create
- [x] network preauth_key/create_for_wake
- [x] network sync
- [ ] network/node/expire
- [ ] network/tags/update

### 不在 MVP 做

- 复杂 ACL 生成器。
- 多 tailnet / 多租户。
- exit node 自动化。
- subnet router 自动化。
- 自动让所有节点互通。

### 预计时间

2～4 天。

---

## Phase 4：Headscale + Executor 联动

**目标：** 利用 Headscale 提供的稳定内网身份，补齐 SSH executor 和组件真实 verify/apply 的网络基础。

**优先级：P1**

### 要做

- SSH executor 使用 Tailscale IP。
- `hmn node status` 展示 HMN 状态 + Headscale 状态。
- component verify 可以通过 Tailscale IP 做只读探测。
- 高风险 SSH/playbook action 进入 approval。
- Headscale tag/ACL 更新走 approval。

### 验收

- Master 可以通过 Headscale IP 访问被管节点。
- SSH executor 默认只允许 low/medium 且可审计。
- high risk 操作必须 approval。

### 预计时间

3～7 天。

---

## Phase 5：NAS / IPv6 接入优化

**目标：** 针对无公网 IPv4、但有 IPv6 或可出站的 NAS，提供安全接入路径。

**优先级：P2，先记录，核心补齐后做。**

### 结论

NAS 有 IPv6 时，不必先做 relay。

推荐：

```text
NAS Worker 主动出站
  → HTTPS / IPv6
HMN Master / Headscale
```

原则：

- 即使 NAS 有公网 IPv6，也不开放 NAS 入站。
- 仍然使用 Worker Pull。
- Master URL 优先用域名 AAAA 记录。
- IPv6 字面量 URL 必须用中括号：`http://[240x:...]:8765`。

### 后续要做

- `hmn wake` / `join-command` 增强 IPv6 URL 测试。
- docs 增加 NAS IPv6 示例。
- worker endpoint 支持 fallback：
  - master IPv6
  - Headscale 内网域名/IP
  - relay fallback（可选）
- lite-worker / cron installer：适配群晖、QNAP、OpenWrt 等无 systemd 环境。

### 预计时间

- 普通 Linux NAS：1 天内可接入 MVP。
- 受限 NAS / 无 systemd：2～4 天。
- NAS monitor + backup + docs-sync：1～2 周。

---

## Phase 6：组件真实能力

**目标：** 在 Core 和 Headscale 稳定后，把组件从 manifest/status MVP 推到真实操作。

**优先级：P2**

### 6.1 monitor

- 展示 uptime/load/memory/disk/mount/docker facts。
- 支持服务状态 probe。
- 支持 Headscale online 状态融合。

### 6.2 reverse-proxy

- Caddy driver MVP。
- plan 生成配置 diff。
- apply 通过 task/executor 写入配置。
- verify 独立检查 HTTP/TLS。

### 6.3 forwarder

- gost/frp/socat driver MVP。
- 先 plan/dry-run，再 approval，再 apply。

### 6.4 backup

- include/exclude。
- dry-run 估算。
- checksum。
- 备份结果写 service docs。

### 6.5 docs-sync

- 机器维度文档。
- 服务维度文档。
- 域名索引。
- 不写 token/secret。

### 预计时间

1～3 周，按组件拆 PR。

---

## 推荐实际推进顺序

1. `docs: sync roadmap and add priority plan`
2. `feat: harden token lifecycle`
3. `feat: enforce revoked node access boundaries`
4. `feat: add offline node status`
5. `feat: add approval state machine MVP`
6. `feat: add headscale network provider MVP`（已完成 MVP）
7. `feat: use headscale identity for SSH executor`
8. `feat: improve NAS IPv6/lite-worker onboarding`
9. `feat: add monitor facts dashboard`
10. `feat: add backup and docs-sync components`

---

## 时间预估

- 核心安全闭环：2～4 天。
- 审批状态机雏形：2～4 天。
- Headscale MVP：已完成基础闭环；后续写操作审批 + executor 联动。
- Headscale + SSH executor：3～7 天。
- NAS/IPv6 优化：1～4 天，视 NAS 环境而定。
- backup/docs-sync/真实组件：1～3 周。

较现实的里程碑：

- **一周内：** 可用、安全边界较清楚的 HMN Core。
- **两周内：** Headscale 接入 + 初步内网执行能力。
- **一个月内：** NAS、monitor、backup、docs-sync 形成较完整托管闭环。
