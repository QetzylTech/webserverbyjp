# PR Acceptance Checklist (Aligned With `doc/project requirements.txt` And `ARCHITECTURE.md`)

Use this checklist for every PR review. Treat `doc/project requirements.txt` as the client-behavior source of truth and `ARCHITECTURE.md` as the implementation contract. Treat any failed hard-gate item as a merge blocker.

## P0 Hard Gates

- [ ] No change breaks world safety, backup safety, restore safety, or cleanup safety.
- [ ] No change weakens password checks, CSRF protection, or password hashing behavior.
- [ ] No change breaks the shell-owned client model, shared SSE ownership, or multi-tab single-owner behavior.
- [ ] No change breaks the required fallback rule for device identification in UI: show mapped device name when found, otherwise show IP address.
- [ ] No change violates explicit client-spec behavior in `doc/project requirements.txt` without also updating the requirements and PR notes.
- [ ] No change violates `ARCHITECTURE.md` layer, runtime, or shell ownership rules without an intentional contract update.

## P1 Live Metrics And Client Dissemination

- [ ] All required live data still exists and is available to connected clients through server-driven JSON/SSE updates.
- [ ] Home metrics still include RAM, CPU per core, CPU frequency, storage, server status, players online, tick time, auto-stop timer, backup status, last backup, next backup, backups folder, backup/stale-world counts, cleanup run metadata, cleanup versions, rule last changed by, next cleanup run, and missed-run count.
- [ ] Clients do not introduce individual polling for data that is supposed to come from shared server broadcasts.
- [ ] File lists, maintenance state, restore progress, and operation progress consume server streams in normal runtime flow instead of browser polling loops.
- [ ] Cadence-by-state still matches the rules:
- [ ] Server off + 0 clients: metrics/logs paused.
- [ ] Server on + 0 clients: metrics paused, logs/storage refreshed only at the specified idle cadence.
- [ ] 1+ clients connected: shared data updates at 1 Hz, live logs stream as they arrive.
- [ ] File lists, counts, and storage-related data remain cached and refreshed by boot/interval/action-trigger rules.
- [ ] CSS/JS offline shell behavior still works: UI loads without server data, shows disconnected state, and rehydrates when the server comes back.
- [ ] Multi-tab behavior still acts like a single active tab from the server perspective, with one tab owning live data and forwarding to the others.
- [ ] Entire client still behaves as an app shell where shared metrics/processes are shell-owned and pages do not own duplicate long-lived runtime state.

## P2 Server Runs And Status

- [ ] Start still requires no password and is only triggered from the Start action.
- [ ] Start still moves status through queued -> starting -> running according to the log/status rules.
- [ ] Server still resolves to running correctly when already up at Flask app boot.
- [ ] Stop still requires password confirmation and shows shutting down state.
- [ ] Off/running/shutting down/crashed states still follow the documented requirements.
- [ ] Start cooldown still prevents duplicate start requests for 10 seconds.
- [ ] Start/Stop/Backup button enablement rules still match the spec.
- [ ] Low-storage and estimated-storage emergency shutdown rules still work.
- [ ] Idle auto-stop after zero players for 3 minutes still works.

## P3 Backup And Restore

- [ ] All four backup triggers still exist: session end, manual backup button, backup interval, and restore initiation.
- [ ] Backup running-state file semantics remain correct.
- [ ] Backup types and filenames still reflect purpose and timestamp.
- [ ] Auto backups still use snapshots and keep only the latest 3 snapshots.
- [ ] Non-auto backups still produce downloadable zip artifacts in the backups directory.
- [ ] Download still requires password where specified.
- [ ] Backup still blocks concurrent backup execution and honors low-storage protection.
- [ ] Backup still performs save/off autosave/on autosave flow correctly.
- [ ] Player-facing RCON backup announcements still exist.
- [ ] Restore list still shows backups/snapshots with restore controls.
- [ ] Restore pane still opens and exposes live restore logs.
- [ ] Restore still creates pre-restore backup, moves old world to old-worlds storage, installs restored world, updates `server.properties`, and records the operation.
- [ ] Restore rollback behavior for invalid/corrupt restore sources still works.
- [ ] Restore remains disabled while the server is running or while backup is running.

## P4 Cleanup

- [ ] Separate backup and stale-world cleanup rules still exist.
- [ ] Age, space, count, and time-based cleanup rules still match the configured per-scope requirements.
- [ ] Hard guards still prevent deleting files newer than 3 days where required and still protect the last backup/newest protected items.
- [ ] Manual cleanup and manual rule-trigger flows still exist.
- [ ] Dry run remains the default for manual cleanup actions.
- [ ] Wet runs still require password plus explicit danger acknowledgement.
- [ ] Cleanup history, last changed by, versions, next run, and missed runs still display correctly.
- [ ] If actor name mapping is missing, cleanup UI still shows the IP instead of `-`.

## P5 Log Files And Live Logs

- [ ] Log file lists still load from the correct directories.
- [ ] Log files still provide both View and Download actions.
- [ ] Viewer pane still opens and displays file content correctly.
- [ ] Log categories still include Minecraft Logs, Crash Reports, Backup and Restore Logs, Control Panel Activity Logs, and Control Panel Errors/System Logs.
- [ ] Live log views still include server console, backup activity, control panel activity, and control panel system/error logs.
- [ ] Log cache behavior still follows the documented requirements, including pause/idle behavior and bounded retained line counts.

## P6 Instructions / Documentation View

- [ ] Instructions content still loads from the configured markdown in the docs folder.
- [ ] Markdown is still rendered into styled HTML correctly.
- [ ] Side ToC is still autogenerated.
- [ ] Built-in ToC hiding behavior in desktop mode still works when applicable.

## P7 Global UI Rules

- [ ] App shell/page/pane structure still follows the rules: nav pane, header/content/action panes, pane title rows, pane content cards.
- [ ] Shared visual rules remain intact where applicable: pane/title consistency, spacing, button sizing, dropdown styling, nav/view switcher styling.
- [ ] Global spacing between elements remains aligned with the 12px rule unless explicitly justified and documented.
- [ ] Selected/opened files still highlight correctly.
- [ ] Nav pane remains left-aligned, sticky, fixed-width, and present across pages as required.

## P8 Local UI Rules

- [ ] Home page still contains the required stat groups and live log pane structure.
- [ ] Backup and Restore page still contains file list, sort/filter controls, and restore pane behavior.
- [ ] Cleanup page still preserves its rules/history/manual split and expected controls.
- [ ] Panel Settings still behaves as a protected page with the required settings panes and controls.

## P9 Initial Setup And Env Behavior

- [ ] First-run missing-config behavior still routes to setup as required.
- [ ] Setup still captures password, Minecraft root, backup directory, and timezone.
- [ ] Setup still generates env config with defaults correctly.
- [ ] Env file still stores runtime config, paths, refresh values, secret keys, and password hashes.
- [ ] Typed passwords still do not remain in forms after submission.
- [ ] Password throttling after three failed attempts still works and still notifies/logs as required.

## P10 Security

- [ ] Passwords remain hashed and salted.
- [ ] Access control assumptions in the rules are still respected; no new bypass path is introduced.
- [ ] CSRF protection remains enabled on protected mutation paths.
- [ ] RCON password regeneration and server.properties enforcement still work.
- [ ] Sensitive data is not exposed unnecessarily in responses, logs, or UI.

## P11 Logging, Errors, And Notifications

- [ ] Minecraft logs and backup logs still come from the correct external/runtime sources.
- [ ] App actions are still logged with enough detail, including rejections, errors, and exceptions.
- [ ] Error handling still logs failures and notifies clients through the correct modal/popup flows.
- [ ] Crash behavior still logs, notifies clients, and moves server status to crashed.
- [ ] Missed-run flows for cleanup and backups still notify clients and do not silently auto-catch-up when the rules say not to.
- [ ] Reconnection still rehydrates clients with the latest data.
- [ ] Nav alert behavior still matches the rules for Home, Backup & Restore, and Cleanup.
- [ ] Alert modal types and audio cues still match the expected variants and triggers.

## P12 Conflict Resolution

- [ ] Mutual exclusion rules among backup, restore, and cleanup still hold.
- [ ] Priority rules still hold: backup > restore > cleanup.
- [ ] Queue/reject semantics for simultaneous jobs still match the documented requirements.
- [ ] Conflicting file actions still resolve in favor of restore where specified.

## P13 Future Plan Guardrail

- [ ] PR does not accidentally claim future-plan items are complete unless they actually are.
- [ ] If a PR touches future-plan areas, the current documented behavior remains clear and unchanged unless intentionally updated.

## P14 Implemented Behavior Not Explicitly Listed Above

- [ ] Client registry and heartbeat tracking still work.
- [ ] Idle log buffering and drain-on-client-connect still work.
- [ ] Dedicated restore SSE stream still works.
- [ ] Cleanup scheduler tick persistence and missed-run metadata still work.
- [ ] Cleanup history persistence remains intact.
- [ ] Cleanup blast-radius caps remain enforced.
- [ ] Server-side priority/guard enforcement for cleanup still works.
- [ ] Storage guard still blocks risky start/backup/restore actions and still supports emergency shutdown.

## PR Evidence

- [ ] Reviewer checked code paths against `doc/project requirements.txt`.
- [ ] Reviewer checked touched areas against `ARCHITECTURE.md` where applicable.
- [ ] Reviewer verified user-visible behavior changes are reflected in docs/rules if needed.
- [ ] Reviewer attached or referenced relevant smoke/unit/integration/manual verification evidence.
- [ ] Reviewer noted any intentional deviations from `doc/project requirements.txt` or `ARCHITECTURE.md`.
