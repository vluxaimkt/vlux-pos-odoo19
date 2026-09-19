"""Benchmark the server-side cost of opening a POS session.

`pos.session.load_data` is the payload the browser downloads when the POS
starts (products, categories, taxes, pricelists, ...). Measuring it directly
isolates catalog-size effects from browser rendering.

    python tools/perf/bench_pos_load.py -c odoo.conf -d vlux_perf --iterations 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment, percentile  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--config-name", default="", help="pos.config name (default: first config)")
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="pos_load")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Measure a warm re-open: the browser already has the IndexedDB cache and only asks "
        "for records changed since its last sync (context pos_last_server_date)",
    )
    args = parser.parse_args()
    odoo = bootstrap(args)

    timings: list[float] = []
    queries: list[int] = []
    report: dict = {}
    with environment(odoo, args.db, readonly=False) as env:
        domain = [("company_id", "=", env.company.id)]
        if args.config_name:
            domain.append(("name", "=", args.config_name))
        config = env["pos.config"].search(domain, limit=1)
        if not config:
            raise SystemExit("No pos.config found")
        session = env["pos.session"].search(
            [("config_id", "=", config.id), ("state", "in", ("opening_control", "opened"))], limit=1
        )
        if not session:
            session = env["pos.session"].create({"config_id": config.id, "user_id": env.uid})
        if args.incremental:
            # Same context the POS data service sends when its IndexedDB cache is valid.
            env.cr.execute("SELECT (now() AT TIME ZONE 'UTC')::timestamp(0)::text")
            session = session.with_context(pos_last_server_date=env.cr.fetchone()[0])
        for _ in range(args.iterations):
            env.invalidate_all()
            before = env.cr.sql_log_count
            started = time.perf_counter()
            data = session.load_data([])
            timings.append((time.perf_counter() - started) * 1000.0)
            queries.append(env.cr.sql_log_count - before)
        payload = json.dumps(data, default=str)
        product_rows = len(data.get("product.template", []))
        product_bytes = len(json.dumps(data.get("product.template", []), default=str))
        image_bytes = sum(
            len(str(row.get("image_128") or "")) for row in data.get("product.template", [])
        )
        report = {
            "label": args.label,
            "db": args.db,
            "pos_config": config.name,
            "iterations": args.iterations,
            "mode": "incremental" if args.incremental else "full",
            "load_data_ms": {
                "p50": round(percentile(timings, 0.5), 1),
                "p95": round(percentile(timings, 0.95), 1),
                "max": round(max(timings), 1),
            },
            "sql_queries_per_call": {"min": min(queries), "max": max(queries)},
            "payload_bytes": len(payload),
            "product_template_rows": product_rows,
            "product_template_bytes": product_bytes,
            "product_image_field_bytes": image_bytes,
            "limited_product_count": config.get_limited_product_count(),
            "models": sorted(data.keys()),
        }
        env.cr.rollback()
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
