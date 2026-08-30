# Repository Instructions

## Git policy

- `main` is the repository's only long-lived and publishable branch.
- Do not create or push another branch unless the user explicitly overrides this policy for a specific task.
- Keep Codex-managed worktrees on detached HEAD. Use Handoff to move completed work to the local `main` checkout instead of using "Create branch here".
- Record important milestones with annotated tags on `main` rather than preservation branches.
- Before removing an existing branch, compare its commits and tree with `main`. Bring over only non-duplicated, still-relevant changes; when conflicts involve superseded architecture, preserve the current `main` design and user working changes.
- Keep Dependabot automated security updates disabled in the GitHub repository settings. `.github/dependabot.yml` disables version-update pull requests only; both settings are required to prevent bot branches.
- Never force-push `main` or rewrite published tags.

## Verification

- Run the checks documented in the root, backend, and client README files before committing integrated changes to `main`.
