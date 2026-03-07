# PR Acceptance Checklist (Priority Ordered, Client-Spec Aligned)

Use this checklist in every PR review. `P0` is a hard stop: if any `P0` item fails, do not merge.

## P0 Data Protection (Hard Gate)

- [ ] Boot-time world source selection remains correct: debug mode uses debug source/config world behavior; non-debug mode uses real world path behavior.
- [ ] Backup paths remain valid: manual/session/auto/pre-restore/emergency behavior still works as designed.
- [ ] Restore flow preserves integrity: pre-restore archive is created, live world is moved to `data/old_worlds`, archive extraction is validated.
- [ ] Rollback path remains functional and audited after restore.
- [ ] Destructive cleanup remains guarded by hard guards and blast-radius caps.
- [ ] Manual/rule cleanup dry-run path remains available and accurate.
- [ ] No change can delete world/backup data without explicit guarded path and audit trail.

## P1 Compute / Power / I/O Efficiency

- [ ] No unnecessary loop/polling cadence increases were introduced.
- [ ] Background loops remain centralized under scheduler/worker lifecycle.
- [ ] Heavy filesystem/process operations remain cached, indexed, bounded, or justified.
- [ ] Live dissemination implementation avoids avoidable duplicate work per client.

## P2 System Stability

- [ ] Boot-time discovery remains stable: env/config, root paths, server.properties, logs/crash paths, backup/snapshot paths, data/db bootstrap.
- [ ] Web/worker/debug boot paths still pass smoke tests.
- [ ] No redirect-loop, crash-loop, or thread-leak risk introduced.
- [ ] Failure paths degrade safely without corrupting runtime state.

## P3 Reliability

- [ ] Control actions remain deterministic: start/stop/backup/restore/rcon outcomes are explicit.
- [ ] Idempotency and operation status behavior remain preserved.
- [ ] Timeout and error mapping behavior remains explicit and test-covered.
- [ ] Restore-pane and maintenance event triggers remain reliable under concurrent clients.

## P4 Client-Side Snappiness

- [ ] Home, backups, logs, crash, maintenance, instructions views remain responsive.
- [ ] UI updates for stats/logs/actions are timely and do not block request threads.
- [ ] Route handlers avoid new long blocking work in request path.
- [ ] Shared shell behavior stays centralized (theme/nav/client identity/shared SSE ownership are not re-duplicated per page).
- [ ] New client-side navigation/hydration work does not introduce duplicate timers, duplicate SSE owners, or hidden-page background churn.

## P5 Information Dissemination

- [ ] Live server time, server stats, Minecraft stats, backup stats, and maintenance stats remain available and fresh.
- [ ] Live logs remain available for server console, backup activity, control activity, and control errors.
- [ ] Nav alert signaling still works:
- [ ] Home flashes red/yellow for crash/transition status.
- [ ] Backups flashes when restore pane is open by another client.
- [ ] Maintenance flashes for missed cleanup conditions.
- [ ] Modals/notifications remain coherent for success, error, confirmation, and automatic warnings.

## P6 Maintainability

- [ ] New behavior is mapped to the correct layer and module.
- [ ] Page/pane structure expectations remain represented in routes/templates/static behavior.
- [ ] Cleanup and maintenance logic remains understandable and bounded.
- [ ] Documentation/checklists remain updated when behavior changes.

## P7 Record Keeping

- [ ] Boot and runtime critical events are still logged.
- [ ] User actions include success/failure/password rejection telemetry.
- [ ] Restore and cleanup actions preserve auditability (history, versions, last changed by, missed runs).

## P8 Architecture and App Structure

- [ ] Architecture tests pass with no exceptions.
- [ ] `main.py` remains composition-only.
- [ ] No OS logic outside platform/infrastructure adapters.
- [ ] Services do not regress to mega mutable state dict parameters.
- [ ] Worker lifecycle ownership remains centralized.
- [ ] Shell-first rendering contract remains clear: lightweight route shells, client hydration, and page-specific data endpoints are still separated cleanly.
- [ ] If a persistent-shell/client-router step was added, mount/unmount ownership and shared runtime ownership are explicit and bounded.

## P9 Access Control

- [ ] Password-required actions still enforce password checks:
- [ ] Stop service, privileged RCON submit, restore, maintenance rule edits/runs/manual delete, and protected debug actions.
- [ ] Non-privileged actions remain intentionally no-password where specified (for example selected downloads).
- [ ] Access checks cannot be bypassed through alternate routes or payload variants.

## P10 Security

- [ ] CSRF protections remain active on protected mutation paths.
- [ ] Password handling remains unchanged or improved (no plaintext leaks).
- [ ] IP-to-device display rule is preserved: prefer device name, show IP only when mapping unavailable.
- [ ] Sensitive operational details are not over-exposed in client responses/logs.

## P11 Version Control / Delivery Hygiene

- [ ] Change is scoped, reviewable, and reversible.
- [ ] CI evidence is attached in PR (`architecture`, `contracts`, `smoke`, `full tests`).
- [ ] Rollback plan is documented for risky behavior changes.
- [ ] If client-spec behavior changed, release note includes exact user-visible deltas.
