# MCWEB Performance Testing and Profiling

This document describes the added load-testing/profiling framework and how to run it safely.

## What Was Added

### 1) Load/Stress Harness
- Script: `scripts/perf_load_test.py`
- Simulates:
  - high-frequency polling: `/metrics`, `/observed-state`, `/maintenance/api/state`
  - SSE: `/metrics-stream`, `/log-stream/<source>`
  - operation polling: `/operation-status/<op_id>`
  - optional mutation load: `/start`, `/stop`, `/backup`, `/restore-backup`
- Outputs:
  - JSON report (`--report-json`)
  - terminal human summary (avg/p95/p99, throughput, error rate, lifecycle duration)
- Supports isolated fixture generation:
  - `--generate-backup-fixture`
  - creates dummy zip files and snapshot dirs in an explicit target path

### 2) Profiling Instrumentation (Optional)
- Module: `app/core/profiling.py`
- Enabled only when `MCWEB_PROFILE=1`
- Instrumented paths include:
  - SQLite operation calls and related state-store paths
  - operation lifecycle/checkpoint timing
  - observed-state builder sub-steps
  - maintenance preview/state snapshot sub-steps
  - reconciler iteration/per-operation/consistency phases
  - snapshot download zip build time + peak traced memory
- Profiling summary endpoint:
  - `GET /profiling-summary?sudo_password=<admin_password>`
  - returns aggregated timing metrics when profiling is enabled

### 3) Analysis Helper
- Script: `scripts/perf_analyze.py`
- Consumes one load report JSON and optional profiling JSON
- Produces ranked summaries (top endpoint latency and top profiled paths)

## Safety

- By default, load is read-heavy.
- Mutation load is **off** unless `--enable-mutations` is supplied.
- Use mutation load only in a controlled environment.
- For backup fixture generation, always point `--generate-backup-fixture` to an isolated test directory.

## Quick Start

## A. Start app with profiling enabled
```powershell
$env:MCWEB_PROFILE='1'
python -B mcweb.py
```

## B. Medium run
```powershell
python scripts/perf_load_test.py `
  --base-url http://127.0.0.1:80 `
  --clients 50 `
  --duration-s 30 `
  --poll-interval-s 0.2 `
  --metrics-sse-clients 8 `
  --log-sse-clients-per-source 4 `
  --server-pid <PID> `
  --report-json data/perf_report_medium.json
```

## C. High run
```powershell
python scripts/perf_load_test.py `
  --base-url http://127.0.0.1:80 `
  --clients 160 `
  --duration-s 25 `
  --poll-interval-s 0.15 `
  --metrics-sse-clients 24 `
  --log-sse-clients-per-source 8 `
  --server-pid <PID> `
  --pull-profiling `
  --sudo-password <admin_password> `
  --report-json data/perf_report_high_profiled.json
```

## D. Idempotent mutation stress (backup)
```powershell
python scripts/perf_load_test.py `
  --base-url http://127.0.0.1:80 `
  --clients 40 `
  --duration-s 20 `
  --enable-mutations `
  --mutation-ratio 0.6 `
  --mutation-actions backup `
  --idempotency-key-pool-size 3 `
  --metrics-sse-clients 4 `
  --log-sse-clients-per-source 2 `
  --server-pid <PID> `
  --report-json data/perf_report_idempotent_backup_stress.json
```

## E. Maintenance-heavy fixture stress
```powershell
python scripts/perf_load_test.py `
  --base-url http://127.0.0.1:80 `
  --clients 40 `
  --duration-s 30 `
  --poll-interval-s 0.1 `
  --observed-weight 0.0 `
  --maintenance-state-weight 1.0 `
  --generate-backup-fixture o:\webserverbyjp\data\perf_backups `
  --fixture-zip-count 2000 `
  --fixture-snapshot-count 300 `
  --fixture-snapshot-files 10 `
  --server-pid <PID> `
  --report-json data/perf_report_maintenance_heavy.json
```

## F. Post-run analysis
```powershell
python scripts/perf_analyze.py `
  --report-json data/perf_report_high_profiled.json `
  --profiling-json data/profiling_summary_latest.json `
  --out-json data/perf_analysis_summary.json
```

## Notes

- If Node.js is not installed, JS syntax checks are skipped.
- Some routes can legitimately return errors in certain environments (for example, maintenance API internal failures due existing runtime constraints). The harness reports those as measured error-rate signals.
- For reproducible trend analysis, keep the same environment, fixture size, and duration when comparing builds.
