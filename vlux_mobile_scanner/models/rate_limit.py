import hashlib
from datetime import timedelta

from odoo import api, fields, models


class VluxMobileScannerRateLimit(models.Model):
    _name = "vlux.mobile.scanner.rate.limit"
    _description = "Límite de solicitudes del escáner móvil"

    key = fields.Char(required=True, index=True, readonly=True)
    window_start = fields.Datetime(required=True, readonly=True)
    request_count = fields.Integer(required=True, readonly=True)

    _key_unique = models.Constraint(
        "UNIQUE (key)",
        "La clave del límite de solicitudes debe ser única.",
    )

    @api.model
    def consume(self, scope, identity, limit, window_seconds):
        identity = str(identity or "unknown")
        key = hashlib.sha256(f"{scope}:{identity}".encode("utf-8")).hexdigest()
        now = fields.Datetime.now()
        cutoff = now - timedelta(seconds=window_seconds)
        self.env.cr.execute(
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
        return self.env.cr.fetchone()[0] <= limit

    @api.autovacuum
    def _gc_old_windows(self):
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.sudo().search([("window_start", "<", cutoff)]).unlink()
