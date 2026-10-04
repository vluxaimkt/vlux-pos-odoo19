import hashlib
from contextlib import contextmanager
from datetime import timedelta

from psycopg2.extensions import ISOLATION_LEVEL_READ_COMMITTED

from odoo import api, fields, models
from odoo.sql_db import Cursor


@contextmanager
def own_transaction(env):
    """Yield a cursor on a short-lived READ COMMITTED transaction of its own.

    Odoo opens every request transaction in REPEATABLE READ. Two requests that
    increment the same counter row at the same time then cannot both succeed:
    the second one fails with ``could not serialize access due to concurrent
    update`` and the request ends in a 500. A hot counter therefore lives in
    its own READ COMMITTED transaction, where concurrent updates simply queue
    on the row lock and re-evaluate. The transaction is committed on exit, so
    the count survives even when the surrounding request fails.

    Inside a test the registry hands out a ``TestCursor`` that shares the test
    connection; the isolation level is left alone because the transaction has
    already started and nothing runs concurrently there anyway.
    """
    with env.registry.cursor() as cr:
        if isinstance(cr, Cursor):
            # Must happen before the first statement; the pool resets the
            # connection to REPEATABLE READ when it is borrowed again.
            cr.connection.set_isolation_level(ISOLATION_LEVEL_READ_COMMITTED)
        yield cr
        cr.commit()


class VluxRateLimit(models.Model):
    """Per-identity request budget shared by every VLUX surface.

    The counter is updated with a single upsert in a transaction of its own,
    so two workers hitting the same key at once cannot both pass a full window
    and neither of them fails because of the other.
    """

    _name = "vlux.rate.limit"
    _description = "Límite de solicitudes VLUX"

    key = fields.Char(required=True, index=True, readonly=True)
    window_start = fields.Datetime(required=True, readonly=True)
    request_count = fields.Integer(required=True, readonly=True)

    _key_unique = models.Constraint(
        "UNIQUE (key)",
        "La clave del límite de solicitudes debe ser única.",
    )

    @api.model
    def _key(self, scope, identity):
        identity = str(identity or "unknown")
        return hashlib.sha256(f"{scope}:{identity}".encode("utf-8")).hexdigest()

    @api.model
    def exhausted(self, scope, identity, limit, window_seconds):
        """Whether ``identity`` already used its ``limit`` in the current window, without counting.

        With ``consume`` called only on failures and ``reset`` on success,
        this is a lockout after repeated failures (wrong PINs).
        """
        cutoff = fields.Datetime.now() - timedelta(seconds=window_seconds)
        self.env.cr.execute(
            f"SELECT request_count FROM {self._table} WHERE key = %s AND window_start > %s",
            [self._key(scope, identity), cutoff],
        )
        row = self.env.cr.fetchone()
        return bool(row) and row[0] >= limit

    @api.model
    def reset(self, scope, identity):
        """Forget ``identity``'s count (after a successful attempt)."""
        with own_transaction(self.env) as cr:
            cr.execute(f"DELETE FROM {self._table} WHERE key = %s", [self._key(scope, identity)])

    @api.model
    def consume(self, scope, identity, limit, window_seconds):
        """True while ``identity`` stays inside ``limit`` per ``window_seconds``."""
        key = self._key(scope, identity)
        now = fields.Datetime.now()
        cutoff = now - timedelta(seconds=window_seconds)
        with own_transaction(self.env) as cr:
            cr.execute(
                f"""
                INSERT INTO {self._table}
                            (key, window_start, request_count, create_date, write_date)
                     VALUES (%s, %s, 1, %s, %s)
                ON CONFLICT (key) DO UPDATE
                        SET window_start = CASE
                                WHEN {self._table}.window_start <= %s THEN %s
                                ELSE {self._table}.window_start
                            END,
                            request_count = CASE
                                WHEN {self._table}.window_start <= %s THEN 1
                                ELSE {self._table}.request_count + 1
                            END,
                            write_date = %s
                RETURNING request_count
                """,
                [key, now, now, now, cutoff, now, cutoff, now],
            )
            count = cr.fetchone()[0]
        return count <= limit

    @api.autovacuum
    def _gc_old_windows(self):
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.sudo().search([("window_start", "<", cutoff)]).unlink()
