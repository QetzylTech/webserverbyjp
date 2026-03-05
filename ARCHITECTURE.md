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

## Process Rules

- Background loops/threads are started only through `app/services/worker_scheduler.py`.
- Worker lifecycle ownership is centralized in scheduler + worker runtime.

## Data and Config Rules

- Configuration is parsed and validated in bootstrap.
- Runtime receives typed, validated config values.
- Services should use explicit/typed dependencies or context objects, not generic mega mutable state dicts.

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
