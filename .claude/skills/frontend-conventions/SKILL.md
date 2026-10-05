---
name: frontend-conventions
description: House conventions for React frontends. Use whenever writing, reviewing, or scaffolding React, TypeScript, shadcn/ui, or Tailwind code in a personal project — including choosing a state library, adding a component, wiring an API call, or starting a new frontend. Also use when asked which stack or library to pick for a UI.
---

# Frontend conventions

These rules apply to every personal React project. They exist so that a dozen
small projects feel like one codebase, and so an agent picking up any of them
finds the same shapes in the same places.

**First: check for a local `DESIGN.md`.** If the project has one, it is
authoritative and its project-specific section overrides anything here. This
skill is the fallback and the summary; the file is the contract.

## The stack, decided

- TypeScript, `strict: true`. No `any`, no untyped API responses.
- React function components and hooks. No class components.
- Vite + TanStack Router by default (with `src/routeTree.gen.ts` committed as a
  vendored contract). Next.js only when SSR or SEO is a stated requirement —
  most of these projects are internal tools behind auth.
- shadcn/ui on Base UI primitives, pinned in `components.json`. Never mix bases.
- Tailwind with semantic theme tokens. Never a raw hex or `bg-blue-500`.
- TanStack Query for anything from a server. Zustand only for cross-cutting
  client state. `useState` for everything else.
- react-hook-form + zod for forms. TanStack Table for tables. date-fns for dates.
- lucide-react for icons. Toasts via the kit's `toast` item (`notifications.*`), never `sonner`.
- Async actions with a visible outcome use `ActionButton` (icon, or icon + text when given children; spinner + tinted result) with the
  result text in a toast — see DESIGN.md section 5.1.

- Anything that goes to a URL is a link. Use `LinkButton` (`link-button` item, never
  `<Button nativeButton={false} render={<Link/>}>`, which adds `role="button"`) or `render={<Link/>}`
  on menu items (and tabs, with `nativeButton={false}`) — never an
  `onClick`/`onSelect`/`onValueChange` that calls `navigate()`, which breaks middle-click and "Open
  in new tab". See DESIGN.md section 3.

Do not introduce a library outside this list without asking first.

## The rule that matters most

Most of what feels like global state is server state. Ask where the data comes
from before choosing a tool:

| Source | Tool |
|---|---|
| An API | TanStack Query. Never copy results into another store. |
| One component | `useState` |
| Parent plus a child or two | Lift it, pass props |
| No common ancestor | Zustand, one store per concern, always with a selector |

React Context is for dependency injection, not for values that change often.

## shadcn components and generated files are vendored

Files in `src/components/ui/` are installed by the CLI and treated as
read-only. To change appearance, override theme CSS variables or target
`[data-slot="..."]`. To change behaviour, wrap the component in
`src/components/`. Hand-editing vendor files makes future `--overwrite` updates
a merge conflict.

Generated files (`src/routeTree.gen.ts`, `src/lib/api-types.ts`) are also
committed vendored contracts — never hand-edit them, always regenerate.
Committing `routeTree.gen.ts` ensures fresh clones have complete route types for
IDEs and type-aware linting without an upfront build.

Use `shadcn docs <component>` to get the current API rather than recalling
props. Use `--dry-run`, `--view`, or `--diff` before writing files.

## Shared pieces come from the kit

Do not reimplement these; install them:

```sh
pnpm dlx shadcn@latest add MartinCa/frontend-kit/conventions   # DESIGN.md + AGENTS.md
pnpm dlx shadcn@latest add MartinCa/frontend-kit/api-client    # typed fetch + ApiError
pnpm dlx shadcn@latest add MartinCa/frontend-kit/query-setup   # QueryClient defaults
pnpm dlx shadcn@latest add MartinCa/frontend-kit/theme         # theme tokens
pnpm dlx shadcn@latest add MartinCa/frontend-kit/theme-provider # theme provider & dark mode support
pnpm dlx shadcn@latest add MartinCa/frontend-kit/link-button   # Button that renders as a real link
```

`theme-provider` does nothing on its own — wrap the app in `<ThemeProvider>`
from `@/components/theme-provider` in `main.tsx`, or dark mode silently never
activates (the app renders light no matter the system preference, and
nothing errors to tell you why).

Lint, TypeScript, Prettier, and TanStack Query ESLint rules come from `@martinrun/frontend-config`.
Pre-commit checks run through `lefthook` (replaced Husky + lint-staged — Husky has had
no release since November 2024).
All CI pipelines and agent pre-PR validations enforce four checks:
`pnpm run lint`, `pnpm run format-check`, `tsc --noEmit`, and `pnpm test`.

## Talking to the backend

Backends vary (ASP.NET Core, FastAPI, Flask). The contract does not:

- OpenAPI spec is the source of truth; generate `src/lib/api-types.ts` from it.
  Never hand-write a response interface.
- Everything goes through `src/lib/api.ts`. No bare `fetch` in components.
- camelCase JSON, ISO 8601 timestamps with offset in UTC, decimals as strings,
  string enums, RFC 9457 `problem+json` errors.
- Same-origin `/api`, proxied by the dev server. No CORS config, no base URL in
  the bundle.

## Quality floor

Not optional, and not worth debating in review:

- Keyboard reachable with a visible focus ring.
- Real semantics. A `<div onClick>` is a defect; so is a button, menu item or tab whose handler
  only calls `navigate()` — render a link instead.
- `prefers-reduced-motion` respected.
- Every async surface defines loading, empty, and error states.
- Error text says what happened and what to do next. It does not apologize.
- Button labels are verbs, consistent through a flow: "Publish" → "Published".
- Works at 375px wide with zero horizontal scrollbars or modal clipping:
  - Modals and overlays must use `w-[calc(100vw-2rem)] sm:w-full max-w-lg max-h-[90dvh] overflow-y-auto`.
  - Dialog footers must use `flex flex-col-reverse sm:flex-row sm:justify-end gap-2` with `className="w-full sm:w-auto"` on buttons for touch targets.
  - Flex text containers beside fixed items (badges, icons) must have `min-w-0 flex-1` for `truncate` / `break-words`.
  - Monospace, diff, path, and URL blocks must include `break-all whitespace-pre-wrap` to avoid horizontal overflow.

## When unsure

Prefer less indirection. Prefer deleting an option to adding one. These are
hobby projects with one user; there is no backwards-compatibility burden.
