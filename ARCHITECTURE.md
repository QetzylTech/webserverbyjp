# Architecture Contract

These rules are non-negotiable. CI is the enforcement source of truth.

## Layer Dependency Rules

Allowed direction:
`routes -> commands/queries -> services -> ports -> infrastructure -> platform`

Disallowed:
- Backward imports across layers.
- Direct `platform` imports outside `app/platform` and `app/infrastructure`.
- Direct OS detection or OS path primitives outside adapters.

## Runtime Rules

- `app/main.py` is composition only (launcher/bootstrap wiring).
- Route layer is HTTP translation only.
- Service layer contains business/use-case logic only.
- Web process should not own business side effects beyond request handling.

## Frontend Runtime Rules

- Shared shell behavior (theme, nav shell wiring, persistent client identity, shared metrics SSE ownership) must live in shared shell/bootstrap modules, not duplicated per page.
- Page scripts should own page-specific mount/unmount logic only.
- Live dissemination should prefer one shared client runtime owner per page shell, not duplicated SSE or polling owners for the same topic.
- Shell-first hydration is the current contract: full page loads render `app_shell.html`, shell navigation fetches fragment responses, and page modules mount/unmount inside the persistent shell.
- Theme/nav boot, metrics SSE ownership, and other cross-page runtime concerns stay in the shell; page modules must not duplicate them.

## Process Rules

- Background loops/threads are started only through `app/services/worker_scheduler.py`.
- Worker lifecycle ownership is centralized in scheduler + worker runtime.

## Data and Config Rules

- Configuration is parsed and validated in bootstrap.
- Runtime receives typed, validated config values.
- Services should use explicit/typed dependencies or context objects, not generic mega mutable state dicts.

## Persistent Shell Guardrails

- Keep one persistent browser shell, one shared client state/cache surface, and one shared metrics SSE owner across navigation.
- Refactor page code into explicit `mount()` / `unmount()` modules; new work should not reintroduce full-page boot ownership into page scripts.
- Add lightweight fragment/data endpoints where needed, but keep backend business logic in the existing layers.
- Avoid keeping hidden timers, fetchers, or duplicate DOM/runtime owners mounted after a page unmounts.

## Enforcement Layers

1. CI hard failures (first-class gate).
2. AST/import architecture tests.
3. Port contract tests across platform adapters.
4. Typed contracts (mypy in CI).
5. Human-readable contract (this file).

## CI Gate Order

1. Format check (if configured).
2. Type check.
3. Architecture tests.
4. Unit tests.
5. Integration/smoke tests.
6. Performance smoke tests.
