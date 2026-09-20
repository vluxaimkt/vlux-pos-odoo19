"""Burst the VLUX API v1 with concurrent requests and count what comes back.

A rate limiter is only worth something when many requests arrive at once, so
this tool fires ``--requests`` calls to ``GET /vlux/api/v1/me`` with
``--concurrency`` threads in flight and reports the status histogram, the
latency percentiles and whether the 600/min budget held: every request must
answer 200 or 429, never 500, and the number of 200s must not exceed the
limit.

The bench issues its own token through the ORM (a synthetic Owner user), keeps
it in memory only and revokes it at the end.

    python tools/perf/bench_api_burst.py -c odoo.conf -d vlux_perf \
        --base-url http://127.0.0.1:8069 --requests 620 --concurrency 20
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment, percentile  # noqa: E402

BENCH_LOGIN = "vlux-bench-api"
API_LIMIT_PER_MINUTE = 600


def _issue_token(env):
    user = env["res.users"].with_context(active_test=False).search([("login", "=", BENCH_LOGIN)], limit=1)
    if not user:
        user = env["res.users"].with_context(no_reset_password=True).create({
            "name": "VLUX bench API",
            "login": BENCH_LOGIN,
            "company_id": env.company.id,
            "company_ids": [(6, 0, env.company.ids)],
            "group_ids": [(6, 0, [env.ref("base.group_user").id, env.ref("vlux_core.group_vlux_owner").id])],
        })
    token, raw = env["vlux.api.token"].issue("bench_api_burst (temporary)", "system:read", user=user)
    return token.id, raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--base-url", default="http://127.0.0.1:8069")
    parser.add_argument("--requests", type=int, default=620)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--limit", type=int, default=API_LIMIT_PER_MINUTE, help="Budget the server enforces per minute")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    odoo = bootstrap(args)

    with environment(odoo, args.db) as env:
        token_id, raw = _issue_token(env)

    url = args.base_url.rstrip("/") + "/vlux/api/v1/me"
    headers = {"Authorization": "Bearer " + raw, "User-Agent": "vlux-bench-api-burst/1.0"}
    session = requests.Session()

    def call(_index):
        started = time.perf_counter()
        try:
            response = session.get(url, headers=headers, timeout=args.timeout)
            status = response.status_code
            try:
                error = response.json().get("error")
            except ValueError:
                error = "NOT_JSON"
        except requests.RequestException:
            status, error = 0, "TRANSPORT"
        return status, error, (time.perf_counter() - started) * 1000

    try:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            outcomes = list(pool.map(call, range(args.requests)))
        elapsed = time.perf_counter() - started
    finally:
        del raw, headers
        with environment(odoo, args.db) as env:
            env["vlux.api.token"].browse(token_id).action_revoke()

    statuses: dict[str, int] = {}
    errors: dict[str, int] = {}
    for status, error, _latency in outcomes:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
        if error:
            errors[error] = errors.get(error, 0) + 1
    latencies = [latency for _s, _e, latency in outcomes]
    ok = statuses.get("200", 0)
    limited = statuses.get("429", 0)
    failed = args.requests - ok - limited
    expected_limited = max(0, args.requests - args.limit)
    report = {
        "label": args.label or "api_burst",
        "requests": args.requests,
        "concurrency": args.concurrency,
        "limit_per_minute": args.limit,
        "elapsed_s": round(elapsed, 2),
        "throughput_rps": round(args.requests / elapsed, 1) if elapsed else None,
        "status_counts": statuses,
        "error_counts": errors,
        "accepted": ok,
        "rate_limited": limited,
        "failed": failed,
        "latency_ms": {
            "p50": round(percentile(latencies, 0.5), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "max": round(max(latencies), 1) if latencies else None,
        },
        # The contract: nothing fails, nothing above the budget gets through.
        "budget_held": failed == 0 and ok <= args.limit and limited >= expected_limited,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
