"""Benchmark the API v1 catalog feed: a register syncing the whole catalog.

Runs the feed in-process (the same code the HTTP endpoint calls, as a VLUX
owner so record rules apply) and reports, per page, latency and SQL query
count. The query count must not grow with the page number or the page size
beyond the rows themselves; the latency budget is the acceptance criterion of
phase B (p95 per page under 400 ms for 10 000 products).

A second round edits and deletes a few products and syncs again from the
cursor: the page must contain exactly those changes.

    python tools/perf/bench_catalog_sync.py -c odoo.conf -d vlux_perf_10k --limit 500

With ``--base-url`` the full sync is repeated over HTTP (token issued in
memory and revoked afterwards) to report end-to-end latency as well.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment, percentile  # noqa: E402

BENCH_LOGIN = "vlux-bench-api"
CHANGES = 50
DELETIONS = 5


def _owner(env):
    user = env["res.users"].with_context(active_test=False).search([("login", "=", BENCH_LOGIN)], limit=1)
    if not user:
        user = env["res.users"].with_context(no_reset_password=True).create({
            "name": "VLUX bench API",
            "login": BENCH_LOGIN,
            "company_id": env.company.id,
            "company_ids": [(6, 0, env.company.ids)],
            "group_ids": [(6, 0, [env.ref("base.group_user").id, env.ref("vlux_core.group_vlux_owner").id])],
        })
    return user


def _wait_next_second():
    second = datetime.now().second
    while datetime.now().second == second:
        time.sleep(0.05)


def _sync(env, api_catalog, limit, cursor=None):
    """Page through the feed; return (pages, items, deleted, cursor)."""
    pages, items, deleted = [], 0, []
    while True:
        queries_before = env.cr.sql_log_count
        started = time.perf_counter()
        page = api_catalog._feed(env, "product.product", "vlux_sync_date", cursor, limit, api_catalog._product_payload)
        json.dumps(page, default=str)  # serialisation is part of the cost
        pages.append({
            "ms": (time.perf_counter() - started) * 1000,
            "queries": env.cr.sql_log_count - queries_before,
            "items": len(page["items"]),
        })
        items += len(page["items"])
        deleted += page["deleted"]
        cursor = page["next_cursor"]
        env.invalidate_all()  # a real request starts with an empty cache
        if not page["has_more"]:
            return pages, items, deleted, cursor


def _http_sync(base_url, raw, limit):
    import requests

    session = requests.Session()
    headers = {"Authorization": "Bearer " + raw, "User-Agent": "vlux-bench-catalog-sync/1.0"}
    latencies, cursor, items = [], None, 0
    while True:
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        started = time.perf_counter()
        response = session.get(base_url.rstrip("/") + "/vlux/api/v1/catalog/products", params=params, headers=headers, timeout=60)
        latencies.append((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        data = response.json()["data"]
        items += len(data["items"])
        cursor = data["next_cursor"]
        if not data["has_more"]:
            return latencies, items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--base-url", default="", help="Also measure over HTTP against a running server")
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    odoo = bootstrap(args)

    from odoo.addons.vlux_core.controllers import api_catalog

    api_catalog.SETTLE_SECONDS = 0  # no concurrent writers in a bench
    report = {"label": args.label or "catalog_sync", "limit": args.limit}

    with environment(odoo, args.db) as env:
        owner = _owner(env)
        env = env(user=owner.id, context={"allowed_company_ids": [owner.company_id.id]})
        _wait_next_second()
        pages, items, _deleted, cursor = _sync(env, api_catalog, args.limit)
        latencies = [page["ms"] for page in pages]
        queries = [page["queries"] for page in pages]
        report["full_sync"] = {
            "products": items,
            "pages": len(pages),
            "page_ms": {"p50": round(percentile(latencies, 0.5), 1), "p95": round(percentile(latencies, 0.95), 1), "max": round(max(latencies), 1)},
            "queries_per_page": {"min": min(queries), "max": max(queries)},
            "queries_constant": max(queries[1:-1]) - min(queries[1:-1]) <= 1 if len(queries) > 3 else None,
            "total_ms": round(sum(latencies), 1),
        }

        # Incremental round: a few edits and a few real deletions. The doomed
        # products are created and synced first so their deletion is a real
        # "you knew this product, forget it" for the client.
        doomed = env["product.template"].sudo().create([
            {"name": "bench doomed %d" % index, "available_in_pos": False, "list_price": 1.0}
            for index in range(DELETIONS)
        ])
        env.cr.commit()
        _wait_next_second()
        _pages, _items, _deleted, cursor = _sync(env, api_catalog, args.limit, cursor)
        templates = env["product.template"].sudo().search([("available_in_pos", "=", True)], limit=CHANGES)
        templates.write({"description_sale": "bench %s" % datetime.now().isoformat()})
        doomed_ids = doomed.product_variant_ids.ids
        doomed.unlink()
        env.cr.commit()
        _wait_next_second()
        pages, changed, deleted, cursor = _sync(env, api_catalog, args.limit, cursor)
        report["incremental_sync"] = {
            "expected_changes": len(templates.product_variant_ids),
            "delivered_changes": changed,
            "expected_deletions": DELETIONS,
            "delivered_deletions": len(deleted),
            "deletions_match": sorted(deleted) == sorted(doomed_ids),
            "page_ms": round(sum(page["ms"] for page in pages), 1),
            "queries": sum(page["queries"] for page in pages),
        }
        report["incremental_exact"] = (
            changed == len(templates.product_variant_ids) and report["incremental_sync"]["deletions_match"]
        )
        raw = None
        if args.base_url:
            token, raw = env["vlux.api.token"].sudo().issue("bench_catalog_sync (temporary)", "catalog:read", user=owner)
            env.cr.commit()

    if args.base_url:
        try:
            http_latencies, http_items = _http_sync(args.base_url, raw, args.limit)
        finally:
            del raw
            with environment(odoo, args.db) as env:
                env["vlux.api.token"].browse(token.id).action_revoke()
        report["http_full_sync"] = {
            "products": http_items,
            "pages": len(http_latencies),
            "page_ms": {"p50": round(percentile(http_latencies, 0.5), 1), "p95": round(percentile(http_latencies, 0.95), 1), "max": round(max(http_latencies), 1)},
            "total_ms": round(sum(http_latencies), 1),
        }

    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
