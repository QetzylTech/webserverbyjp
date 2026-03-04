#!/usr/bin/env python3
"""Analyze MCWEB load-test and profiling JSON outputs into ranked bottlenecks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize_report(report):
    endpoints = report.get("endpoints", {})
    ranked_latency = sorted(
        (
            (name, item.get("p95_ms", 0.0), item.get("count", 0), item.get("error_rate", 0.0))
            for name, item in endpoints.items()
            if not str(name).startswith("sse:")
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    return {
        "elapsed_s": report.get("elapsed_s", 0.0),
        "totals": report.get("totals", {}),
        "top_endpoint_p95": ranked_latency[:10],
        "resource_summary": report.get("resource_summary", {}),
        "operation_lifecycle": report.get("operation_lifecycle", {}),
    }


def summarize_profiling(profile_payload):
    profiling = profile_payload.get("profiling", profile_payload).get("profiling", profile_payload.get("profiling", {}))
    metrics = profiling.get("metrics", {}) if isinstance(profiling, dict) else {}
    ranked = sorted(
        (
            (name, item.get("total_ms", 0.0), item.get("avg_ms", 0.0), item.get("p95_ms", 0.0), item.get("count", 0))
            for name, item in metrics.items()
        ),
        key=lambda row: row[1],
        reverse=True,
    )
    return {
        "top_profiled_paths_by_total_ms": ranked[:20],
        "errors": profiling.get("errors", {}) if isinstance(profiling, dict) else {},
        "gauges": profiling.get("gauges", {}) if isinstance(profiling, dict) else {},
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze perf_load_test and profiling outputs")
    parser.add_argument("--report-json", required=True, help="Path to perf_load_test JSON report")
    parser.add_argument("--profiling-json", default="", help="Path to profiling summary JSON")
    parser.add_argument("--out-json", default="", help="Optional output summary JSON path")
    args = parser.parse_args()

    report = load_json(args.report_json)
    output = {"report_summary": summarize_report(report)}
    if args.profiling_json:
        profile_json = load_json(args.profiling_json)
        output["profiling_summary"] = summarize_profiling(profile_json)

    print(json.dumps(output, indent=2))
    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
