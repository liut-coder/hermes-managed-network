# HMN Orchestrator Branch Triage

> Generated from the merge-first cleanup pass. This file tracks branch/worktree debt so future orchestrator runs do not rediscover the same state.

## Base

- Base branch: `feat/v1-1-useful-ops-mvp`
- Goal: classify stale/absorbed worktrees first, then merge or extract one remaining useful slice at a time.

## Status meanings

- `absorbed`: branch HEAD is already ancestor of the base, or its task-specific patch is byte-identical/already present on base; safe to remove worktree after confirming no dirty files.
- `needs-review`: branch has commits/files not in base and needs diff/test review.
- `duplicate-likely`: branch overlaps with already merged features; inspect before merging.
- `merge-candidate`: branch appears to contain still-useful feature work.
- `stale-base`: branch was cut from an old base; never broad-merge it because two-dot diffs may delete newer mainline files.

## Current classification

### absorbed / cleanup candidates

These reported `git merge-base --is-ancestor <branch> feat/v1-1-useful-ops-mvp = true` or had no ahead commits against base:

- `docs/architecture-backlog`
- `feat/p1-headscale-network-smoke`
- `feat/p1-nas-ipv6-lite-worker-smoke`
- `feat/p1-remote-smoke-script`
- `feat/p1-telegram-approval-smoke`
- `feat/p1-telegram-approval-smoke-script`
- `feat/production-readiness-doctor`
- `feat/v1-production-doctor-readiness`
- `hmn-orchestrator-cli`
- `hmn-task13-github-actions`
- `hmn-task14-deploy-cli`
- `hmn-task15-docs-sync`
- `hmn-task16-backup-provider`

Additional task-specific absorption verified in the latest cron pass:

- `hmn-task12-coolify`
  - Status: `absorbed` / cleanup candidate.
  - Reason: current base already has `src/hermes_managed_network/coolify_provider.py` and `tests/test_coolify_provider.py`; task-specific diff is empty for those files.
  - Verification: `python -m pytest -q tests/test_coolify_provider.py` passed via `.venv/bin/python`; compileall, shell syntax, and diff-check passed.
  - Note: branch itself is `stale-base`; broad two-dot diff would remove many newer mainline files, so do not merge the whole branch.

Current absorbed count: 15 branches/worktrees, including `hmn-config-provider-merge-check`.

### needs-review / merge candidates

Process one at a time. Prefer cherry-pick or manual extraction over broad merge when branch base is old.

1. `hmn-config-provider-merge-check`
   - Status: `absorbed` / cleanup candidate.
   - Reason: branch is already ancestor of current HEAD; `config_provider.py` and `tests/test_config_provider.py` blob hashes are identical to HEAD.
   - CLI diff is stale-base only: broad-merging would delete newer restore/migration/onboarding CLI code from HEAD.
   - Verification target: `tests/test_config_provider.py`.

2. `hmn-docs-center-apply`
   - Status: `partially-extracted` / remaining stale-base cleanup candidate.
   - Extracted files/hunks: `docs/docs-center.md`, `src/hermes_managed_network/docs_sync.py`, `tests/test_docs_sync_apply.py`, plus current-main CLI wiring for `hmn docs sync apply --root/--execute/--json`.
   - Preserve current approval-only path when called without `--root`, `--execute`, or `--json`.
   - Verification: `.venv/bin/python -m pytest -q tests/test_docs_sync.py tests/test_docs_sync_apply.py`, compileall, shell syntax, and diff-check passed.
   - Remaining restore/migration/onboarding files are already present or handled by later duplicate checks; do not broad-merge stale branch.

3. `hmn-task17-restore-plan`
   - Status: `absorbed` / cleanup candidate.
   - Reason: `src/hermes_managed_network/restore.py` and `tests/test_restore_plan.py` blob hashes are identical to HEAD.
   - Verification target: `tests/test_restore_plan.py`.
   - Note: branch itself is `stale-base`; do not broad-merge.

4. `hmn-task18-migration-plan`
   - Status: `absorbed-with-mainline-hardening` / cleanup candidate.
   - Reason: tests are identical to HEAD; `restore.py` is identical; `migration.py` differs only because HEAD replaced the older docs redaction helper with shared `providers.redact_sensitive_data`.
   - Verification target: `tests/test_restore_plan.py tests/test_migration_plan.py`.
   - Note: keep HEAD version; do not reintroduce old `docs_generate` helper coupling.

5. `hmn-task19-onboarding-plan`
   - Status: `absorbed-with-mainline-hardening` / cleanup candidate.
   - Reason: tests are identical to HEAD; `restore.py` is identical; `migration.py` and `onboarding.py` differ only because HEAD replaced older docs redaction helpers with shared `providers.redact_sensitive_data`.
   - Verification target: `tests/test_restore_plan.py tests/test_migration_plan.py tests/test_onboarding_plan.py`.
   - Note: keep HEAD version; do not reintroduce old `docs_generate` helper coupling.

6. `feat/monitor-closed-loop`
   - Status: `absorbed-with-mainline-hardening` / cleanup candidate.
   - Reason: task-specific files are already present on HEAD: `tests/test_monitor_cli.py`, backup/docs-sync component manifests, monitor snapshot storage, and monitor/backup CLI surfaces. The branch still reports not-ancestor because it is stale-base and lacks later docs-sync/restore/migration/onboarding hardening.
   - Verification: `.venv/bin/python -m pytest -q tests/test_components.py tests/test_monitor_cli.py` passed; compileall, shell syntax, and diff-check passed in the cron pass that marked it absorbed.
   - Note: do not broad-merge; keep current HEAD implementation.

7. `feat/production-readiness-p0` / `fix/production-p0-readiness`
   - Status: `absorbed-with-mainline-hardening` / cleanup candidate.
   - Reason: both remote branch heads are still not ancestors because they were cut from stale base `b3e7089`, but their production-readiness slice has already been extracted to HEAD in smaller commits.
   - Extracted coverage on HEAD:
     - public raw install URLs now point to `main/install.sh` in README/deployment/headscale docs;
     - `hmn doctor` renders production sections for install/service/API/upgrade rollback readiness;
     - installer upgrade manifest records backup DB/env/config/metadata and `ROLLBACK_COMMAND`;
     - `docs/production-readiness.md`, roadmap checkboxes, and `tests/test_production_readiness_docs.py` are present.
   - Verification: `.venv/bin/python -m pytest -q tests/test_production_readiness_docs.py tests/test_linux_install.py::test_master_installer_detects_existing_version_policy_and_rollback_metadata tests/test_cli.py::test_doctor_command_reports_full_production_readiness tests/test_cli.py::test_doctor_command_reports_installer_readiness` passed.
   - Note: do not broad-merge either branch; two-dot diff would delete newer mainline CLI/provider/docs-sync code.

8. `feat/useful-ops-mvp`
   - Status: `absorbed-with-summary-doc-extracted` / cleanup candidate.
   - Reason: branch is an old v1.1 dry-run integration branch. Its provider/backup/deploy/docs-sync/restore/migration/onboarding code is already represented on HEAD through newer hardened commits; broad merge reports many add/add and docs conflicts.
   - Extracted files/hunks in this pass: `docs/managed-ops-summary-v1.1.md` only.
   - Note: keep current HEAD implementation for code and roadmap files; do not broad-merge this stale branch.

## Latest cron reconciliation — 2026-05-13 11:34 EDT

Native backlog command was rerun after `git fetch --prune origin`:

```bash
.venv/bin/python -m hermes_managed_network.cli orchestrator backlog --repo . --base feat/v1-1-useful-ops-mvp
```

Observed branch state:

- Branch total: 12.
- Local branches: 2 (`feat/v1-1-useful-ops-mvp`, `main`).
- Worktrees: 1 (`/root/hermes-managed-network`).
- WIP: 0/3.
- `generated`: empty.
- `needs-review`: empty.
- `merge-ready`: empty.
- `conflict`: empty.
- `stale`: empty.
- `duplicate`: empty.
- `abandoned`: empty.
- `merged` / cleanup candidates:
  - `docs/architecture-backlog`
  - `feat/monitor-closed-loop`
  - `feat/p1-remote-smoke-script`
  - `feat/p1-telegram-approval-smoke-script`
  - `feat/production-readiness-doctor`
  - `feat/production-readiness-p0`
  - `feat/useful-ops-mvp`
  - `fix/production-p0-readiness`
  - `hmn-task-provider-contract`
  - `hmn-task20-config-provider`

Manual priority branch probes in this cron pass:

- `hmn-task12-coolify`: absent locally/remotely.
- `hmn-config-provider-merge-check`: absent locally/remotely.
- `hmn-docs-center-apply`: absent locally/remotely.
- `hmn-task17-restore-plan`: absent locally/remotely.
- `hmn-task18-migration-plan`: absent locally/remotely.
- `hmn-task19-onboarding-plan`: absent locally/remotely.
- `origin/feat/monitor-closed-loop`: `merge-base --is-ancestor` is false, but the remaining commit is a stale-base backup CLI exposure patch already represented on HEAD. Current HEAD already exposes `hmn backup plan/run/verify/status` plus monitor CLI/menu/help coverage, so this remains cleanup-only.
- `origin/feat/production-readiness-p0`: `merge-base --is-ancestor` is false only because it is stale-base; production readiness slice is already extracted on HEAD.
- `origin/fix/production-p0-readiness`: same production readiness slice is already extracted on HEAD.

Cron state:

- Active merge-first orchestrator: `9b36e7b758d9` (`HMN merge-first 全托管统筹`), next run around 12:02 EDT.
- Older HMN orchestrator crons remain paused: `a664a1c7cc73`, `b2f723639ca7`.
- No active duplicate HMN merge-first cron was observed in the live cron list.

Working tree state:

- No tracked code diff was present at the start of this pass.
- Untracked generated artifacts remain unchanged:
  - `docs/plans/2026-05-13-hmn-web-docs-module.md` — generated implementation plan; outside current P0 merge-first priority, so left uncommitted.
  - `uv.lock` — generated lockfile; left uncommitted until the project explicitly adopts uv lockfile policy.

This pass treated this triage-note refresh and the `feat/monitor-closed-loop` recheck as the bounded file cluster. No new worker development was dispatched because merge-first hygiene still has cleanup-only remote branches that require an explicit delete/abandon policy before opening another implementation slice.

Focused gate for this pass:

```bash
.venv/bin/python -m pytest -q tests/test_orchestrator_cli.py tests/test_monitor_cli.py tests/test_backup_provider.py
.venv/bin/python -m compileall -q src
bash -n install.sh scripts/*.sh src/hermes_managed_network/assets/*.sh
git diff --check
```

Result: orchestrator CLI, monitor CLI, and backup provider focused tests passed; compileall, shell syntax, and diff-check passed.

## Latest cron reconciliation — 2026-05-13 12:09 EDT

Native backlog command was rerun after the mandatory git/worktree/cron inspection:

```bash
.venv/bin/python -m hermes_managed_network.cli orchestrator backlog --repo . --base feat/v1-1-useful-ops-mvp
```

Observed branch state:

- Branch total: 12.
- Local branches: 2 (`feat/v1-1-useful-ops-mvp`, `main`).
- Worktrees: 1 (`/root/hermes-managed-network`).
- WIP: 0/3.
- `generated`: empty.
- `needs-review`: empty.
- `merge-ready`: empty.
- `conflict`: empty.
- `stale`: empty.
- `duplicate`: empty.
- `abandoned`: empty.
- `merged` / cleanup candidates:
  - `docs/architecture-backlog`
  - `feat/monitor-closed-loop`
  - `feat/p1-remote-smoke-script`
  - `feat/p1-telegram-approval-smoke-script`
  - `feat/production-readiness-doctor`
  - `feat/production-readiness-p0`
  - `feat/useful-ops-mvp`
  - `fix/production-p0-readiness`
  - `hmn-task-provider-contract`
  - `hmn-task20-config-provider`

Manual priority branch probes in this cron pass:

- `hmn-task12-coolify`: absent locally/remotely.
- `hmn-config-provider-merge-check`: absent locally/remotely.
- `hmn-docs-center-apply`: absent locally/remotely.
- `hmn-task17-restore-plan`: absent locally/remotely.
- `hmn-task18-migration-plan`: absent locally/remotely.
- `hmn-task19-onboarding-plan`: absent locally/remotely.
- `origin/feat/monitor-closed-loop`: still not a direct ancestor, but backlog classification keeps it as `merged` because task-specific monitor/backup/docs-sync files are already represented on HEAD; do not broad-merge stale-base branch.
- `origin/feat/production-readiness-p0` and `origin/fix/production-p0-readiness`: still not direct ancestors, but production readiness code/docs/tests are already extracted on HEAD; do not broad-merge stale-base branches.

Cron state:

- Active merge-first orchestrator: `9b36e7b758d9` (`HMN merge-first 全托管统筹`), next run around 12:37 EDT.
- No active duplicate HMN merge-first cron was observed in the live cron list.

Working tree state:

- No tracked code diff was present at the start of this pass.
- Untracked generated artifacts remain unchanged and intentionally outside this P0 merge-first cleanup slice:
  - `docs/plans/2026-05-13-hmn-web-docs-module.md` — generated implementation plan.
  - `uv.lock` — generated lockfile; left uncommitted until the project explicitly adopts uv lockfile policy.

This pass treated this triage-note refresh as the bounded file cluster. No new worker development was dispatched because cleanup-only remote branches still need an explicit delete/abandon policy before opening another implementation slice.

Focused gate for this pass:

```bash
.venv/bin/python -m pytest -q tests/test_orchestrator_cli.py
.venv/bin/python -m compileall -q src
bash -n install.sh scripts/*.sh src/hermes_managed_network/assets/*.sh
git diff --check
```

## Next action

Delete the absorbed remote cleanup branches after operator approval, or explicitly mark them abandoned. Only after that should the orchestrator move to the next P0 Worker timeout / heartbeat / cancel / watch implementation slice.

Safe cleanup command after approval:

```bash
git push origin --delete docs/architecture-backlog feat/monitor-closed-loop feat/p1-remote-smoke-script feat/p1-telegram-approval-smoke-script feat/production-readiness-doctor feat/production-readiness-p0 feat/useful-ops-mvp fix/production-p0-readiness hmn-task-provider-contract hmn-task20-config-provider
```
