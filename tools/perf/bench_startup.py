"""Measure Odoo start-up time until ``/vlux/health`` answers, plus RSS and
PostgreSQL connections held once idle.

    python tools/perf/bench_startup.py -c odoo.conf -d vlux_perf --odoo-home /opt/odoo --port 8169
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _odoo_env import add_common_arguments, bootstrap, environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_arguments(parser)
    parser.add_argument("--port", type=int, default=8169)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--json", default="")
    parser.add_argument("--label", default="startup")
    args = parser.parse_args()
    if not args.odoo_home:
        raise SystemExit("--odoo-home is required to spawn odoo-bin")

    command = [args.python, str(Path(args.odoo_home) / "odoo-bin")]
    if args.config:
        command += ["-c", args.config]
    if args.addons_path:
        command += ["--addons-path", args.addons_path]
    command += ["-d", args.db, "--http-port", str(args.port), "--logfile=", "--log-level=warn"]
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    health_url = f"http://127.0.0.1:{args.port}/vlux/health"
    ready_at = None
    try:
        deadline = started + args.timeout
        while time.perf_counter() < deadline:
            try:
                response = requests.get(health_url, headers={"X-Odoo-Database": args.db}, timeout=2)
                if response.ok and response.json().get("status") == "ok":
                    ready_at = time.perf_counter()
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        if ready_at is None:
            raise SystemExit("Odoo did not become healthy in time")
        # Warm one request that touches the registry, then sample resources.
        requests.get(f"http://127.0.0.1:{args.port}/web/login", headers={"X-Odoo-Database": args.db}, timeout=30)
        time.sleep(2)
        proc = psutil.Process(process.pid)
        rss = proc.memory_info().rss
        for child in proc.children(recursive=True):
            rss += child.memory_info().rss
        odoo = bootstrap(args)
        with environment(odoo, args.db, readonly=True) as env:
            env.cr.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                [args.db],
            )
            connections = env.cr.fetchone()[0]
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()

    report = {
        "label": args.label,
        "db": args.db,
        "startup_to_health_s": round(ready_at - started, 2),
        "rss_mb_idle": round(rss / (1024 * 1024), 1),
        "pg_connections_idle": connections,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
