# CI Quality Gates

This document defines required CI gate order and corresponding commands.

## Gate Order (Fail Fast)

1. Format / syntax gate
2. Type contract gate
3. Architecture enforcement gate
4. Port contract gate
5. Boot/integration smoke gate
6. Full test gate
7. Topology artifact export
8. Documentation / delivery gate

Architecture must fail before unit/integration suites run.

## Executable Gates

These are the expected commands in CI:

1. Format / syntax
- `python -m compileall -q app tests debug`

2. Type contracts
- `mypy --config-file mypy.ini app/ports/interfaces.py app/bootstrap/config_loader.py app/state/contexts.py app/infrastructure/adapters.py`

3. Architecture rules (AST/import boundaries)
- `pytest -q tests/test_architecture_boundaries.py`

4. Port contracts (cross-platform adapters)
- `pytest -q tests/test_port_contracts.py`

5. Boot smoke
- `pytest -q tests/test_boot_smoke.py`

6. Full suite
- `pytest -q tests`

7. Dependency topology artifact
- `python scripts/export_import_topology.py`
- upload `doc/import_topology.dot` as CI artifact

8. Documentation / delivery
- verify docs were updated when architecture or client-runtime behavior changed:
  - `README.md`
  - `ARCHITECTURE.md`
  - `doc/PR_ACCEPTANCE_CHECKLIST.md`

## Non-Negotiable Rules Mapped to Gates

- Data protection safety regressions must be blocked by tests before merge.
- Layer violations must fail in architecture gate.
- Port behavior drift across OS adapters must fail in contract gate.
- Boot path regressions must fail in smoke gate.
- Type contract drift in core interfaces must fail in type gate.

## Frontend Shell Migration Note

Frontend shell/runtime changes must update the architecture contract and acceptance checklist when they change navigation lifecycle, SSE ownership, hydration strategy, or route/template responsibilities.
