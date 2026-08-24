---
name: qmt-safe-commit
description: Safely review, verify, stage, and commit changes in the QMT-export repository. Use when asked to commit QMT strategies, MiniQMT infrastructure, backtest code, tests, analysis, or documentation. Do not use for stash creation or restoration unless explicitly requested.
---

# QMT Safe Commit

Create small, attributable commits without sweeping unrelated user work into them.

## Repository invariants

- Read the root `AGENTS.md` before staging.
- A modified strategy Python file must become a new versioned Python file; never overwrite an existing strategy version merely to prepare a commit.
- Treat QMT-injected globals as valid runtime dependencies. Do not add local stubs just to silence IDE warnings.
- Preserve GBK encoding declarations on QMT strategy files.
- Exclude logs, `__pycache__`, compiled bytecode, backtest output, credentials, local settings, and other generated artifacts.
- Shared infrastructure changes, especially `Stragety/MiniQMT_Stragety/infra/`, can affect multiple strategies. State that scope in the handoff.

## Workflow

1. Inspect `git status --short`, staged and unstaged diffs, untracked files, and recent commit style.
2. Attribute every candidate file to the requested work. Preserve unrelated edits and stage explicit paths instead of `git add -A` when the worktree contains mixed changes.
3. Remove only disposable artifacts created during the current task. Do not delete or revert unknown user files.
4. Verify in proportion to the change:
   - QMT/MiniQMT SDK behavior: use `C:\QMT\bin.x64\python.exe` when the ordinary interpreter lacks `xtquant`.
   - Connector or order-routing changes: run focused fake-trader tests that cannot place live orders.
   - Backtest changes: use the repository `run-qmt-export` smoke-test flow, preferring cached data.
   - Documentation-only changes: perform formatting and link/path checks as applicable.
5. Run `git diff --cached --check` and inspect `git diff --cached --stat` before committing.
6. Split independent themes into separate commits. Use a conventional prefix such as `fix:`, `feat:`, `test:`, `docs:`, or `chore:` and keep the subject concise.
7. Commit only after the user has requested a commit. Do not push unless explicitly authorized.
8. Report commit hashes, subjects, verification results, and any remaining working-tree changes.

If verification cannot run because a dependency is missing, retry with the repository's intended interpreter or environment. Report the limitation rather than installing dependencies without authorization.
