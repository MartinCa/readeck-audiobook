# Readeck Audiobook — Agent Instructions

AI agents should read [`CLAUDE.md`](CLAUDE.md) first: it is the canonical contributor guide for this repository (project overview, layout notes, development workflow). This file only adds the git-hooks conventions.

## Git hooks

Local git hooks come from the shared [`MartinCa/lefthook-configs`](https://github.com/MartinCa/lefthook-configs) fragments pinned at `v2.0.0` in [`lefthook.yml`](lefthook.yml).

Install them (or re-install after a fresh clone):

```sh
uv tool install lefthook@2.1.12   # standalone binary; first install only
lefthook install                  # idempotent, safe to re-run
```

`lefthook` is a standalone binary, not a project dependency, and must be on `PATH` — `uv tool install` puts it in uv's tool bin dir (default `~/.local/bin`, which git needs on `PATH` to run the hooks).

The hooks run:

- **pre-commit** — `langs/python.yml` lints and formats staged `*.py`/`*.pyi` files (`uvx ruff check --fix` + `uvx ruff format`, re-staging fixes via `stage_fixed`); `lefthook-shared.yml` scans the staged diff with `betterleaks` (blocks the commit on a leak) and audits staged `.github/workflows/*` files with `zizmor` (blocks on a finding).
- **commit-msg** — `commit-msg.yml` enforces [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat: ...`, `fix(api): ...`.

Three tools must be on `PATH` for the hooks: `lefthook`, `betterleaks` (secret scan), and `zizmor` (workflow audit). If a tool is missing, `LEFTHOOK=0 git commit` skips the hooks entirely — a pragmatic escape hatch for restricted setups, not a way to dodge the gates.

Hooks vs CI: `ci.yml` runs `ruff check` and `ruff format --check`, `pytest`, Docker builds, plus a `zizmor` job (`uvx zizmor@1.29.0 --format sarif .`) that shows on PRs and uploads a SARIF report to code scanning — SARIF mode exits 0 on findings by design, so that job cannot fail them; findings surface in code scanning. `betterleaks` and the commit-msg validation run **only** as local hooks (no CI equivalent), so do not bypass them.
