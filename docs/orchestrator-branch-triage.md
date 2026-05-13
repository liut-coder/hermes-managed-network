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
   - Production readiness docs/install/CLI tests.
   - Status: `needs-review`; choose one, do not merge both blindly.

## Next action

Next merge-first action: inspect `fix/production-p0-readiness` against `feat/production-readiness-p0`, choose the fresher/smaller production-readiness slice, and extract only missing docs/install/test hunks if HEAD does not already contain them. Do not merge both blindly.

Suggested gate:

```bash
git diff --name-status feat/v1-1-useful-ops-mvp...fix/production-p0-readiness
.venv/bin/python -m pytest -q tests/test_linux_install.py tests/test_production_readiness_docs.py tests/test_cli.py
.venv/bin/python -m compileall -q src
bash -n install.sh scripts/*.sh src/hermes_managed_network/assets/*.sh
git diff --check
```
