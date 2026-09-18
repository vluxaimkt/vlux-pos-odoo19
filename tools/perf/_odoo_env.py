"""Shared bootstrap for VLUX performance tools.

Each tool runs outside the Odoo HTTP server and talks to the ORM directly,
so the numbers reflect server-side cost without browser noise.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--odoo-home", default=os.environ.get("ODOO_HOME", ""),
                        help="Odoo checkout (defaults to ODOO_HOME or an odoo import on sys.path)")
    parser.add_argument("-c", "--config", default=os.environ.get("ODOO_RC", ""),
                        help="odoo.conf to load (database credentials never come from the repo)")
    parser.add_argument("--addons-path", default="", help="Extra addons path override")
    parser.add_argument("-d", "--db", required=True, help="Database name (synthetic data only)")


def bootstrap(args: argparse.Namespace):
    if args.odoo_home:
        sys.path.insert(0, str(Path(args.odoo_home).resolve()))
    import odoo  # noqa: WPS433
    import odoo.tools  # noqa: WPS433
    import odoo.netsvc  # noqa: WPS433
    import odoo.modules.registry  # noqa: WPS433

    argv = []
    if args.config:
        argv += ["-c", args.config]
    if args.addons_path:
        argv += ["--addons-path", args.addons_path]
    argv += ["-d", args.db, "--logfile=", "--log-level=warn"]
    odoo.tools.config.parse_config(argv)
    odoo.netsvc.init_logger()
    return odoo


@contextlib.contextmanager
def environment(odoo, db: str, readonly: bool = False):
    from odoo import SUPERUSER_ID, api

    registry = odoo.modules.registry.Registry(db)
    with registry.cursor() as cr:
        env = api.Environment(cr, SUPERUSER_ID, {})
        try:
            yield env
            if readonly:
                cr.rollback()
            else:
                cr.commit()
        except Exception:
            cr.rollback()
            raise


def percentile(values, pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[index]
