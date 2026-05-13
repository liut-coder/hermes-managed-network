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
   - Status: `duplicate-likely`; restore dry-run appears already merged as `a21e6dc`.

4. `hmn-task18-migration-plan`
   - Status: `duplicate-likely`; migration dry-run appears already merged as `11e0aca`.

5. `hmn-task19-onboarding-plan`
   - Status: `duplicate-likely`; onboarding dry-run appears already merged as `11e0aca`.

6. `feat/monitor-closed-loop`
   - Adds monitor closed loop, backup/docs-sync component manifests, backup dry-run CLI.
   - Status: `merge-candidate` but broad diff; handle after smaller provider slices.

7. `feat/production-readiness-p0` / `fix/production-p0-readiness`
   - Production readiness docs/install/CLI tests.
   - Status: `needs-review`; choose one, do not merge both blindly.

## Next action

Next merge-first action: classify `hmn-task17-restore-plan`, `hmn-task18-migration-plan`, and `hmn-task19-onboarding-plan` by blob/hash. They are expected duplicate/absorbed now that docs-center apply was extracted; verify before cleanup.

Suggested gate:

```bash
for b in hmn-task17-restore-plan hmn-task18-migration-plan hmn-task19-onboarding-plan; do git diff --name-status feat/v1-1-useful-ops-mvp...$b; done
.venv/bin/python -m pytest -q tests/test_restore_plan.py tests/test_migration_plan.py tests/test_onboarding_plan.py
python -m compileall -q src
bash -n install.sh scripts/*.sh src/hermes_managed_network/assets/*.sh
git diff --check
```
