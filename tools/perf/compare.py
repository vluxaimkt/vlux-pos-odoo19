"""Print a BEFORE/AFTER table from benchmark JSON reports and enforce budgets.

    python tools/perf/compare.py before/owner.json after/owner.json \
        --budget latency_ms.p95:<=:0.7   # AFTER must be <= 70 % of BEFORE
        --budget scans_lost:==:0          # absolute value check on AFTER

Budget syntax: ``<dotted.key>:<op>:<value>``. Relative operators (``<=``,
``<``) compare AFTER / BEFORE ratios when both reports contain the key; when
the value is prefixed with ``abs:`` or BEFORE lacks the key, the AFTER value is
compared directly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def dig(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def flatten(data: dict, prefix: str = "") -> dict[str, object]:
    rows: dict[str, object] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            rows.update(flatten(value, name + "."))
        elif isinstance(value, (int, float, str)) or value is None:
            rows[name] = value
    return rows


def compare(op: str, left: float, right: float) -> bool:
    return {
        "<=": left <= right,
        "<": left < right,
        ">=": left >= right,
        ">": left > right,
        "==": left == right,
    }[op]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--budget", action="append", default=[])
    args = parser.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))

    rows_before = flatten(before)
    rows_after = flatten(after)
    keys = sorted(set(rows_before) | set(rows_after))
    width = max(len(k) for k in keys)
    print(f"{'metric'.ljust(width)}  {'BEFORE':>14}  {'AFTER':>14}  {'ratio':>7}")
    for key in keys:
        left, right = rows_before.get(key), rows_after.get(key)
        ratio = ""
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) and left:
            ratio = f"{right / left:.2f}x"
        print(f"{key.ljust(width)}  {str(left):>14}  {str(right):>14}  {ratio:>7}")

    failures = 0
    for budget in args.budget:
        key, op, raw = budget.split(":", 2)
        absolute = raw.startswith("abs:")
        target = float(raw[4:] if absolute else raw)
        value = dig(after, key)
        if value is None:
            print(f"BUDGET FAIL {budget}: missing key in AFTER")
            failures += 1
            continue
        base = dig(before, key)
        if absolute or not isinstance(base, (int, float)) or base == 0:
            ok = compare(op, float(value), target)
            print(f"BUDGET {'PASS' if ok else 'FAIL'} {key} {value} {op} {target}")
        else:
            ratio = float(value) / float(base)
            ok = compare(op, ratio, target)
            print(f"BUDGET {'PASS' if ok else 'FAIL'} {key} ratio {ratio:.2f} {op} {target}")
        failures += 0 if ok else 1
    if failures:
        print(f"{failures} budget(s) failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
