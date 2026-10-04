# AGENTS.md

## Before writing any UI code

Read `DESIGN.md` in this repository in full. It is binding. If a rule there
conflicts with a habit, a tutorial, or a suggestion from a model, the file wins.

`DESIGN.md` is distributed from `MartinCa/frontend-kit` and is **not edited here**.
To change a convention, change it upstream and reinstall:

```sh
pnpm dlx shadcn@latest add MartinCa/frontend-kit/conventions --overwrite
```

The project-specific section at the bottom of `DESIGN.md` is the exception — that
part is owned by this repo.

## Mandatory verification before opening PRs

Git hooks (`lefthook`) format and lint on `git commit`, but agents often work
where hooks are not initialized (ephemeral cloud VMs, web/mobile sessions, Docker
containers). Before creating commits and opening a pull request, you **MUST**
run all verification commands explicitly:

1. `pnpm run lint` — ESLint flat config with `--max-warnings 0`.
2. `pnpm run format-check` — Prettier verification (`prettier --check .`).
3. `tsc --noEmit` (or `pnpm exec tsc --noEmit`) — full project type-checking.
4. `pnpm test` — automated test suite.

Fix any reported violations or warnings rather than disabling rules or skipping checks.

## Shortcuts

- `shadcn info` — what is installed, which base, where the docs are.
- `shadcn docs <component>` — current API for a primitive. Use this instead of
  recalling props from memory; the Base UI and Radix APIs differ.
- `shadcn add <name> --dry-run` / `--view` / `--diff` — inspect before writing files.
- `shadcn preset resolve` — which preset the project is really on (style/baseColor); check alignment.

## Dependency versions

Install packages with the package manager (`pnpm add <pkg>`, no version pin) and
let it resolve the current release; `pnpm add` writes a range and Renovate keeps
it current. Do not hand-write a version into `package.json` from memory — training
data lags, and a remembered version is routinely a major or two behind. If a
specific version genuinely matters (a peer dependency constraint, a known-bad
release), say so and name the reason in the commit.

## House rules that are linted

`pnpm lint` enforces the mechanical parts of `DESIGN.md` — strict TypeScript, no
deep relative imports, no direct primitive imports outside `components/ui/`, no
inline `style` props, no Zustand fetches, TanStack Query best practices; the rules
themselves live in `DESIGN.md`. A few are warnings rather than errors, so CI runs
with `--max-warnings 0`: a warning is a thing to fix, not a pass. Fix the code
rather than disabling the rule; if a rule is genuinely wrong, change it upstream
in `@martinrun/frontend-config`.

## Do not

- Add a state, data-fetching, or UI library. The stack is decided in `DESIGN.md`.
- Hand-edit `src/components/ui/**` or generated files (`src/lib/api-types.ts`,
  `src/routeTree.gen.ts` where present). All are vendored.
- Refactor files unrelated to the task in hand.
- Write a response interface by hand. Regenerate from the OpenAPI spec.
