## Summary

- What changed:
- Why:
- Risk level: low / medium / high

## Validation Evidence

- [ ] `python -m compileall -q app tests debug`
- [ ] `pytest -q tests/test_architecture_boundaries.py`
- [ ] `pytest -q tests/test_port_contracts.py`
- [ ] `pytest -q tests/test_boot_smoke.py`
- [ ] `pytest -q tests`

## Priority Acceptance Checklist

Reference: `doc/PR_ACCEPTANCE_CHECKLIST.md`

- [ ] P0 Data Protection satisfied (no data-loss path introduced)
- [ ] P1 Efficiency impact reviewed
- [ ] P2 Stability impact reviewed
- [ ] P3 Reliability impact reviewed
- [ ] P4 Snappiness impact reviewed
- [ ] P5 Information/alerts impact reviewed
- [ ] P6 Maintainability/cleanup impact reviewed
- [ ] P7 Record keeping impact reviewed
- [ ] P8 Architecture boundaries preserved
- [ ] P9 Access control preserved
- [ ] P10 Security posture preserved
- [ ] P11 Delivery hygiene complete

## Rollback Plan

- How to revert safely if production issue occurs:
