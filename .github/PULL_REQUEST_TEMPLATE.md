## Summary

This PR delivers the Hermes Managed Network control-plane MVP: a small, auditable controller for safely onboarding nodes, receiving heartbeats, dispatching low-risk tasks, and closing the worker result loop without enabling shell execution by default.

## MVP Scope

- FastAPI controller with SQLite persistence
- One-time join tokens and node registry
- Pending → managed node lifecycle
- Heartbeat endpoint and worker facts reporting
- Worker bootstrap asset served by controller
- Task queue MVP:
  - create/list tasks from CLI
  - worker polls one pending task
  - worker submits result
- Safe default worker mode:
  - `HMN_ENABLE_EXEC=0`
  - no shell command execution unless explicitly enabled on the node
- Audit trail for token, node, heartbeat, task, and component events
- Component lifecycle state MVP:
  - manifest registry
  - plan/apply/verify/uninstall shape
  - forwarder and monitor bundle examples
- README and deployment docs for local usage and smoke testing

## Security Boundary

- Join tokens are temporary sensitive values and are not documented as durable secrets.
- New nodes join as `pending`; they must be confirmed before becoming `managed`.
- Node task polling and result submission require the registered fingerprint.
- Worker defaults to safe mode (`HMN_ENABLE_EXEC=0`).
- In safe mode, worker proves the queue/result loop by returning:
  - `exit_code=126`
  - `stderr=execution disabled; set HMN_ENABLE_EXEC=1`
- Only `low` / `medium` task risk is accepted by the current CLI.
- Component `apply` currently records desired state/audit only; it does not mutate remote machines.

## Verified Commands

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/python -m compileall -q src
bash -n install.sh scripts/install-master.sh scripts/join.sh scripts/worker.sh src/hermes_managed_network/assets/worker.sh
git diff --check
```

Result:

```text
100 passed
```

End-to-end smoke test also passed locally:

- started local controller with `hmn-server`
- created join token
- simulated node join via `POST /api/v1/join`
- confirmed node with `hmn node confirm`
- created task with `hmn task run 'echo smoke-disabled'`
- ran worker in `HMN_ENABLE_EXEC=0`
- confirmed task result returned as failed/126 with disabled-exec stderr

Smoke assertion:

```text
TASK_STATUS failed
TASK_EXIT 126
TASK_STDERR execution disabled; set HMN_ENABLE_EXEC=1
```

## Follow-up Roadmap

- Token expiration/revocation operator UX
- Worker systemd install hardening and upgrade policy
- Real component lifecycle drivers behind approval/task engine
- Permission bundle refinement
- Multi-node asset/service documentation sync
