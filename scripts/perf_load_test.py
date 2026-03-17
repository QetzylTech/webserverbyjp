#!/usr/bin/env python3
"""MC Web Dashboard load/stress harness for polling/SSE/operation flows."""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import re
import statistics
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import http.client
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * float(pct)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.samples = defaultdict(list)
        self.counts = defaultdict(int)
        self.errors = defaultdict(int)
        self.bytes = defaultdict(int)
        self.lifecycle = []
        self.started = time.time()

    def add(self, name, latency_s, ok=True, size=0):
        with self.lock:
            self.samples[name].append(float(max(0.0, latency_s)))
            self.counts[name] += 1
            self.bytes[name] += int(max(0, size))
            if not ok:
                self.errors[name] += 1

    def add_lifecycle(self, op_type, duration_s, terminal_status):
        with self.lock:
            self.lifecycle.append(
                {
                    "op_type": str(op_type),
                    "duration_s": float(max(0.0, duration_s)),
                    "terminal_status": str(terminal_status),
                }
            )

    def summary(self):
        with self.lock:
            counts = dict(self.counts)
            errors = dict(self.errors)
            samples = {k: list(v) for k, v in self.samples.items()}
            lifecycle = list(self.lifecycle)
            byte_counts = dict(self.bytes)
        elapsed = max(0.001, time.time() - self.started)
        endpoints = {}
        for name, values in samples.items():
            total = len(values)
            err = errors.get(name, 0)
            endpoints[name] = {
                "count": total,
                "errors": err,
                "error_rate": (err / total) if total else 0.0,
                "throughput_rps": total / elapsed,
                "avg_ms": (statistics.mean(values) * 1000.0) if values else 0.0,
                "p95_ms": _percentile(values, 0.95) * 1000.0,
                "p99_ms": _percentile(values, 0.99) * 1000.0,
                "max_ms": (max(values) * 1000.0) if values else 0.0,
                "bytes_total": byte_counts.get(name, 0),
            }
        lifecycle_by_type = defaultdict(list)
        for item in lifecycle:
            lifecycle_by_type[item["op_type"]].append(item["duration_s"])
        operation_lifecycle = {}
        for op_type, values in lifecycle_by_type.items():
            operation_lifecycle[op_type] = {
                "count": len(values),
                "avg_s": statistics.mean(values) if values else 0.0,
                "p95_s": _percentile(values, 0.95),
                "p99_s": _percentile(values, 0.99),
                "max_s": max(values) if values else 0.0,
            }
        total_count = sum(counts.values())
        total_errors = sum(errors.values())
        return {
            "started_at_epoch": self.started,
            "ended_at_epoch": time.time(),
            "elapsed_s": elapsed,
            "totals": {
                "requests": total_count,
                "errors": total_errors,
                "error_rate": (total_errors / total_count) if total_count else 0.0,
                "throughput_rps": total_count / elapsed,
            },
            "endpoints": endpoints,
            "operation_lifecycle": operation_lifecycle,
            "operation_lifecycle_samples": lifecycle,
        }


class Session:
    def __init__(self, base_url, timeout_s=10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.cookies = {}
        self.csrf_token = ""

    def _cookie_header(self):
        if not self.cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _capture_set_cookie(self, headers):
        for key, value in headers:
            if key.lower() != "set-cookie":
                continue
            first = value.split(";", 1)[0]
            if "=" not in first:
                continue
            name, raw_val = first.split("=", 1)
            self.cookies[name.strip()] = raw_val.strip()

    def request(self, method, path, *, headers=None, data=None):
        url = f"{self.base_url}{path}"
        req_headers = {
            "User-Agent": "mcweb-perf-harness/1.0",
            "X-Requested-With": "XMLHttpRequest",
        }
        if headers:
            req_headers.update(headers)
        cookie_header = self._cookie_header()
        if cookie_header:
            req_headers["Cookie"] = cookie_header
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, method=method.upper(), headers=req_headers, data=body)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                self._capture_set_cookie(resp.getheaders())
                return {
                    "ok": True,
                    "status": int(resp.status),
                    "latency_s": time.perf_counter() - started,
                    "body": raw,
                    "headers": dict(resp.getheaders()),
                }
        except urllib.error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            self._capture_set_cookie(exc.headers.items() if exc.headers else [])
            return {
                "ok": False,
                "status": int(getattr(exc, "code", 500) or 500),
                "latency_s": time.perf_counter() - started,
                "body": raw,
                "headers": dict(exc.headers.items()) if exc.headers else {},
            }
        except Exception:
            return {
                "ok": False,
                "status": 0,
                "latency_s": time.perf_counter() - started,
                "body": b"",
                "headers": {},
            }

    def ensure_csrf(self):
        resp = self.request("GET", "/")
        if resp["status"] <= 0:
            return ""
        text = resp["body"].decode("utf-8", errors="ignore")
        m = re.search(r'csrfToken\s*:\s*"([^"]+)"', text)
        self.csrf_token = m.group(1) if m else ""
        return self.csrf_token


def poll_operation_until_terminal(session, op_id, timeout_s=120.0, interval_s=0.5):
    started = time.time()
    last_status = ""
    while (time.time() - started) <= timeout_s:
        resp = session.request("GET", f"/operation-status/{urllib.parse.quote(str(op_id))}")
        if resp["status"] == 200:
            try:
                payload = json.loads(resp["body"].decode("utf-8", errors="ignore"))
            except Exception:
                payload = {}
            op = payload.get("operation") if isinstance(payload, dict) else {}
            status = str((op or {}).get("status", "")).strip().lower()
            if status in {"observed", "failed"}:
                return True, status, (time.time() - started)
            last_status = status
        time.sleep(interval_s)
    return False, last_status or "timeout", (time.time() - started)


def sse_worker(base_url, path, stop_event, stats, name, heartbeat_timeout_s=20.0):
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    use_ssl = parsed.scheme == "https"
    started = time.perf_counter()
    events = 0
    errors = 0
    conn = None
    try:
        conn_cls = http.client.HTTPSConnection if use_ssl else http.client.HTTPConnection
        conn = conn_cls(host, port, timeout=heartbeat_timeout_s)
        conn.request("GET", path, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
        resp = conn.getresponse()
        if resp.status != 200:
            stats.add(name, time.perf_counter() - started, ok=False)
            return
        last_event_at = time.time()
        while not stop_event.is_set():
            line = resp.readline()
            if not line:
                if (time.time() - last_event_at) > heartbeat_timeout_s:
                    break
                continue
            text = line.decode("utf-8", errors="ignore").strip()
            if text.startswith("data:"):
                events += 1
                last_event_at = time.time()
            elif text.startswith(":"):
                last_event_at = time.time()
    except Exception:
        errors += 1
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        stats.add(name, time.perf_counter() - started, ok=(errors == 0), size=events)


def random_op_name(prefix):
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}-{suffix}"


@dataclass
class ResourceSample:
    at_epoch: float
    cpu_percent: float
    rss_bytes: int
    threads: int
    open_files: int


def resource_sampler(stop_event, out_samples, target_pid):
    try:
        import psutil  # type: ignore
    except Exception:
        psutil = None
    proc = None
    if psutil is not None and target_pid > 0:
        try:
            proc = psutil.Process(target_pid)
            proc.cpu_percent(interval=None)
        except Exception:
            proc = None
    while not stop_event.is_set():
        sample = ResourceSample(time.time(), 0.0, 0, 0, 0)
        if proc is not None:
            try:
                sample = ResourceSample(
                    at_epoch=time.time(),
                    cpu_percent=float(proc.cpu_percent(interval=None)),
                    rss_bytes=int(proc.memory_info().rss),
                    threads=int(proc.num_threads()),
                    open_files=int(len(proc.open_files()) if hasattr(proc, "open_files") else 0),
                )
            except Exception:
                pass
        out_samples.append(sample)
        stop_event.wait(1.0)


def generate_backup_fixture(root: Path, zip_count: int, snapshot_count: int, snapshot_files: int):
    root.mkdir(parents=True, exist_ok=True)
    snapshots = root / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    for i in range(zip_count):
        (root / f"perf_dummy_{i:05d}.zip").write_bytes(b"PK\x03\x04" + os.urandom(64))
    for i in range(snapshot_count):
        snap_dir = snapshots / f"snapshot_{i:05d}"
        snap_dir.mkdir(parents=True, exist_ok=True)
        for j in range(snapshot_files):
            (snap_dir / f"chunk_{j:03d}.bin").write_bytes(os.urandom(256))


def run_load(args):
    stats = Stats()
    op_queue = queue.Queue()
    stop_event = threading.Event()
    end_time = 0.0

    if args.generate_backup_fixture:
        fixture_root = Path(args.generate_backup_fixture).resolve()
        generate_backup_fixture(fixture_root, args.fixture_zip_count, args.fixture_snapshot_count, args.fixture_snapshot_files)
    end_time = time.time() + float(args.duration_s)

    resource_samples = []
    resource_thread = None
    if args.server_pid > 0:
        resource_thread = threading.Thread(
            target=resource_sampler,
            args=(stop_event, resource_samples, args.server_pid),
            daemon=True,
        )
        resource_thread.start()

    sse_threads = []
    for _ in range(args.metrics_sse_clients):
        t = threading.Thread(
            target=sse_worker,
            args=(args.base_url, "/metrics-stream", stop_event, stats, "sse:/metrics-stream"),
            daemon=True,
        )
        t.start()
        sse_threads.append(t)
    for source in args.log_sources:
        for _ in range(args.log_sse_clients_per_source):
            t = threading.Thread(
                target=sse_worker,
                args=(args.base_url, f"/log-stream/{source}", stop_event, stats, f"sse:/log-stream/{source}"),
                daemon=True,
            )
            t.start()
            sse_threads.append(t)

    def worker_loop(worker_id):
        session = Session(args.base_url, timeout_s=args.timeout_s)
        if args.enable_mutations:
            session.ensure_csrf()
        idempotency_pool = [f"{worker_id}-{i}" for i in range(max(0, args.idempotency_key_pool_size))]
        while time.time() < end_time and not stop_event.is_set():
            coin = random.random()
            path = "/metrics"
            method = "GET"
            data = None
            name = "GET /metrics"
            if coin < args.observed_weight:
                path = "/observed-state"
                name = "GET /observed-state"
            elif coin < (args.observed_weight + args.maintenance_state_weight):
                path = "/maintenance/api/state"
                name = "GET /maintenance/api/state"

            do_mutation = args.enable_mutations and (random.random() < args.mutation_ratio)
            if do_mutation:
                if not session.csrf_token:
                    session.ensure_csrf()
                choice = random.choice(args.mutation_actions)
                if idempotency_pool:
                    key = f"{choice}-idemp-{random.choice(idempotency_pool)}"
                else:
                    key = random_op_name(f"{choice}-idemp")
                method = "POST"
                if choice == "start":
                    path = "/start"
                    name = "POST /start"
                    data = {"csrf_token": session.csrf_token, "idempotency_key": key}
                elif choice == "backup":
                    path = "/backup"
                    name = "POST /backup"
                    data = {"csrf_token": session.csrf_token, "idempotency_key": key}
                elif choice == "stop":
                    path = "/stop"
                    name = "POST /stop"
                    data = {"csrf_token": session.csrf_token, "sudo_password": args.sudo_password, "idempotency_key": key}
                elif choice == "restore":
                    path = "/restore-backup"
                    name = "POST /restore-backup"
                    data = {
                        "csrf_token": session.csrf_token,
                        "sudo_password": args.sudo_password,
                        "idempotency_key": key,
                        "filename": args.restore_filename,
                    }
            resp = session.request(method, path, data=data)
            ok = bool(resp["status"] and resp["status"] < 500)
            stats.add(name, resp["latency_s"], ok=ok, size=len(resp.get("body", b"")))
            if method == "POST" and resp["status"] == 202:
                try:
                    payload = json.loads(resp["body"].decode("utf-8", errors="ignore"))
                except Exception:
                    payload = {}
                op_id = str(payload.get("op_id", "") or "").strip()
                if op_id:
                    op_queue.put((choice if do_mutation else "unknown", op_id, session))
            time.sleep(args.poll_interval_s)

    def op_poller():
        while time.time() < end_time and not stop_event.is_set():
            try:
                op_type, op_id, session = op_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            ok, terminal, duration = poll_operation_until_terminal(
                session,
                op_id,
                timeout_s=args.operation_timeout_s,
                interval_s=args.operation_poll_interval_s,
            )
            stats.add("GET /operation-status/<op_id>", duration, ok=ok)
            stats.add_lifecycle(op_type, duration, terminal)

    workers = []
    for i in range(args.clients):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()
        workers.append(t)
    op_threads = []
    for _ in range(max(1, args.operation_pollers)):
        t = threading.Thread(target=op_poller, daemon=True)
        t.start()
        op_threads.append(t)

    for t in workers:
        t.join()
    grace_until = time.time() + max(1.0, args.operation_timeout_s)
    while (time.time() < grace_until) and (not op_queue.empty()):
        time.sleep(0.2)
    stop_event.set()
    for t in op_threads:
        t.join(timeout=2.0)
    for t in sse_threads:
        t.join(timeout=2.0)
    if resource_thread is not None:
        resource_thread.join(timeout=2.0)

    result = stats.summary()
    if resource_samples:
        result["resource_samples"] = [
            {
                "at_epoch": s.at_epoch,
                "cpu_percent": s.cpu_percent,
                "rss_bytes": s.rss_bytes,
                "threads": s.threads,
                "open_files": s.open_files,
            }
            for s in resource_samples
        ]
        result["resource_summary"] = {
            "cpu_percent_max": max((s.cpu_percent for s in resource_samples), default=0.0),
            "rss_bytes_max": max((s.rss_bytes for s in resource_samples), default=0),
            "threads_max": max((s.threads for s in resource_samples), default=0),
            "open_files_max": max((s.open_files for s in resource_samples), default=0),
        }

    if args.pull_profiling and args.sudo_password:
        session = Session(args.base_url, timeout_s=args.timeout_s)
        resp = session.request("GET", f"/profiling-summary?sudo_password={urllib.parse.quote(args.sudo_password)}")
        if resp["status"] == 200:
            try:
                result["profiling_summary"] = json.loads(resp["body"].decode("utf-8", errors="ignore"))
            except Exception:
                result["profiling_summary_error"] = "invalid_json"
        else:
            result["profiling_summary_error"] = f"status_{resp['status']}"
    return result


def print_human_summary(report):
    totals = report.get("totals", {})
    print("=== MCWEB Load Test Summary ===")
    print(f"Elapsed: {report.get('elapsed_s', 0.0):.2f}s")
    print(f"Requests: {totals.get('requests', 0)}")
    print(f"Errors: {totals.get('errors', 0)} ({totals.get('error_rate', 0.0) * 100:.2f}%)")
    print(f"Throughput: {totals.get('throughput_rps', 0.0):.2f} rps")
    print("")
    print("Top endpoints by p95:")
    endpoints = report.get("endpoints", {})
    ranked = sorted(endpoints.items(), key=lambda kv: kv[1].get("p95_ms", 0.0), reverse=True)
    for name, item in ranked[:10]:
        print(
            f"- {name}: count={item.get('count', 0)} "
            f"avg={item.get('avg_ms', 0.0):.2f}ms p95={item.get('p95_ms', 0.0):.2f}ms "
            f"p99={item.get('p99_ms', 0.0):.2f}ms err={item.get('error_rate', 0.0) * 100:.2f}%"
        )
    lifecycle = report.get("operation_lifecycle", {})
    if lifecycle:
        print("")
        print("Operation lifecycle durations:")
        for op_type, item in sorted(lifecycle.items()):
            print(
                f"- {op_type}: count={item.get('count', 0)} avg={item.get('avg_s', 0.0):.2f}s "
                f"p95={item.get('p95_s', 0.0):.2f}s p99={item.get('p99_s', 0.0):.2f}s"
            )
    resource = report.get("resource_summary")
    if resource:
        print("")
        print("Server resource peaks (sampled):")
        print(
            f"- CPU max: {resource.get('cpu_percent_max', 0.0):.2f}% | "
            f"RSS max: {resource.get('rss_bytes_max', 0)} bytes | "
            f"Threads max: {resource.get('threads_max', 0)} | "
            f"Open files max: {resource.get('open_files_max', 0)}"
        )


def parse_args():
    p = argparse.ArgumentParser(description="MC Web Dashboard load/stress harness")
    p.add_argument("--base-url", default="http://127.0.0.1:5000", help="Target MCWEB base URL")
    p.add_argument("--clients", type=int, default=50, help="Concurrent polling workers")
    p.add_argument("--duration-s", type=int, default=60, help="Test duration in seconds")
    p.add_argument("--poll-interval-s", type=float, default=0.25, help="Polling interval per worker")
    p.add_argument("--timeout-s", type=float, default=10.0, help="HTTP timeout seconds")
    p.add_argument("--observed-weight", type=float, default=0.35, help="Weight for /observed-state")
    p.add_argument("--maintenance-state-weight", type=float, default=0.20, help="Weight for /maintenance/api/state")
    p.add_argument("--enable-mutations", action="store_true", help="Enable safe mutation load")
    p.add_argument("--mutation-ratio", type=float, default=0.05, help="Mutation probability per request")
    p.add_argument("--mutation-actions", nargs="+", default=["start", "backup"], choices=["start", "stop", "backup", "restore"])
    p.add_argument("--sudo-password", default="", help="Admin password (required for stop/restore/profiling summary)")
    p.add_argument("--restore-filename", default="", help="Restore filename used in mutation mode")
    p.add_argument("--operation-pollers", type=int, default=4, help="Concurrent operation-status poller threads")
    p.add_argument("--operation-poll-interval-s", type=float, default=0.5, help="Polling interval for operation status")
    p.add_argument("--operation-timeout-s", type=float, default=120.0, help="Max wait for op terminal state")
    p.add_argument("--idempotency-key-pool-size", type=int, default=0, help="If >0, reuse idempotency keys from a fixed per-worker pool")
    p.add_argument("--metrics-sse-clients", type=int, default=5, help="Concurrent /metrics-stream clients")
    p.add_argument("--log-sse-clients-per-source", type=int, default=2, help="Concurrent /log-stream clients per source")
    p.add_argument("--log-sources", nargs="+", default=["minecraft", "backup"], help="Log stream sources")
    p.add_argument("--server-pid", type=int, default=0, help="Optional server PID for CPU/memory/thread sampling")
    p.add_argument("--pull-profiling", action="store_true", help="Fetch /profiling-summary at end (requires password)")
    p.add_argument("--generate-backup-fixture", default="", help="Optional isolated directory to generate backup/snapshot fixtures")
    p.add_argument("--fixture-zip-count", type=int, default=1000, help="Dummy zip files to generate")
    p.add_argument("--fixture-snapshot-count", type=int, default=200, help="Dummy snapshot dirs to generate")
    p.add_argument("--fixture-snapshot-files", type=int, default=20, help="Files per dummy snapshot dir")
    p.add_argument("--report-json", default="", help="Output path for full JSON report")
    return p.parse_args()


def main():
    args = parse_args()
    report = run_load(args)
    print_human_summary(report)
    if args.report_json:
        out_path = Path(args.report_json).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("")
        print(f"JSON report written: {out_path}")


if __name__ == "__main__":
    main()
