"""Benchmark the VLUX Owner dashboard service (server-side, no browser).

Reports wall time percentiles, SQL query count per call and the amount of
ORM records pulled into memory, so BEFORE/AFTER comparisons are about the
aggregation strategy rather than network noise.

    python tools/perf/bench_owner.py -c odoo.conf -d vlux_perf --iterations 20 --json out.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment, percentile  # noqa: E402


def cache_records(env) -> int:
    """Approximate number of records materialised in the ORM cache."""
    total = 0
    for field, field_cache in env.transaction.field_data.items():
        if field in env.registry.field_depends_context:
            total += sum(len(bucket) for bucket in field_cache.values())
        else:
            total += len(field_cache)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--json", default="", help="Write the report to this file")
    parser.add_argument("--label", default="owner_dashboard")
    parser.add_argument("--date", default="", help="Local day to measure (YYYY-MM-DD); default: today")
    parser.add_argument(
        "--cache",
        choices=("cold", "warm"),
        default="cold",
        help="cold: clear the dashboard's in-process cache before every call (measures the "
        "real computation); warm: leave it enabled (measures cache hits)",
    )
    args = parser.parse_args()
    odoo = bootstrap(args)

    timings: list[float] = []
    queries: list[int] = []
    cache_sizes: list[int] = []
    context = {}
    with environment(odoo, args.db, readonly=True) as env:
        service = env["vlux.owner.dashboard.service"]
        today = service._local_date(args.date or None)
        start_utc, end_utc = service._utc_bounds(today)
        context = {
            "orders_today": env["pos.order"].search_count([
                ("date_order", ">=", start_utc), ("date_order", "<", end_utc),
                ("state", "in", ("paid", "done", "invoiced")),
                ("company_id", "=", env.company.id),
            ]),
            "orders_total": env["pos.order"].search_count([("company_id", "=", env.company.id)]),
            "products_pos": env["product.product"].search_count([("available_in_pos", "=", True)]),
        }
        for index in range(args.warmup + args.iterations):
            env.invalidate_all()
            if args.cache == "cold" and hasattr(service, "_clear_dashboard_cache"):
                service._clear_dashboard_cache()
            before_queries = env.cr.sql_log_count
            started = time.perf_counter()
            payload = service.get_dashboard(date=args.date or None)
            elapsed = time.perf_counter() - started
            if index >= args.warmup:
                timings.append(elapsed * 1000.0)
                queries.append(env.cr.sql_log_count - before_queries)
                cache_sizes.append(cache_records(env))
        sample = {key: payload[key] for key in ("summary",)}

    report = {
        "label": args.label,
        "db": args.db,
        "context": context,
        "iterations": args.iterations,
        "cache": args.cache,
        "latency_ms": {
            "p50": round(percentile(timings, 0.50), 1),
            "p95": round(percentile(timings, 0.95), 1),
            "mean": round(statistics.fmean(timings), 1),
            "max": round(max(timings), 1),
        },
        "sql_queries_per_call": {
            "min": min(queries),
            "max": max(queries),
        },
        "orm_cache_records_after_call": max(cache_sizes),
        "sample_summary": sample["summary"],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
