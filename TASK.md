# Task: 服务自动发现与状态页同步 backlog item 3

Implement roadmap section "服务自动发现与状态页同步" for HMN.

Repo: /tmp/hmn-service-discovery-status-sync
Branch: feat/service-discovery-status-sync

Scope from docs/roadmap.md:
- 节点服务自动发现：识别 systemd unit、Docker / Compose、Caddy / Nginx 入口、监听端口、公开 URL 和本地健康检查路径。
- 建立 service registry：服务绑定 node、runtime、端口、域名、部署路径、配置文件、env 文件、数据目录、反代入口和健康检查策略。
- 从 Coolify 同步 service registry：把 Coolify app、domain、repo、deploy target、env 摘要和运行状态映射成 HMN service/service_instance。
- 监控策略自动生成：根据服务类型选择 HTTP / keyword / TCP / ping 检查，避免把状态页自身或内部-only 服务错误公开。

Requirements:
1. Follow TDD. Add failing tests first, then implement.
2. Keep safety boundaries: discovery/sync must be dry-run friendly and not publish monitors by default.
3. Prefer small HMN-native modules/CLI commands consistent with existing patterns.
4. If apply/sync writes DB or external systems, record audit; do not call real Coolify/Uptime in tests.
5. Update docs/roadmap.md to mark the four 服务自动发现与状态页同步 items done only if implemented and tested.
6. Run focused tests and full pytest if feasible.
7. Commit your changes with message: feat(discovery): sync service registry and monitor plans

Important repository conventions:
- Python source under src/hermes_managed_network/
- CLI in src/hermes_managed_network/cli.py uses Typer.
- Tests under tests/.
- Do not touch unrelated untracked files from main worktree.
- Do not push.

Deliverable:
- Commit on feat/service-discovery-status-sync.
- Final output should include commit hash, tests run, and summary.
