"""An employee at a register, proven by a PIN the server checked.

Until now the register checked the PIN on the device and the server trusted
the ``employee_id`` it sent: anyone holding a register's token could claim
to be the encargado and sell on credit, close short or authorise credit.
Now the register sends the PIN once, the server compares it with the
employee's, and answers a short-lived session token. Actions that need to
know who is at the register for real require it.

Sales are different: the register may have checked the PIN offline, so a
sale without a valid session is still recorded (never lost, D7) but marked
unverified, and rules that depend on who sold (credit) treat it as a sale by
a cashier.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from odoo import api, fields, models

# A shift. The register asks for the PIN again after it (and after 5 idle minutes).
SESSION_HOURS = 12
# An authorization (someone else's PIN for one module) lasts this long at most.
AUTHORIZATION_MINUTES = 15
SESSION_BYTES = 32
# Wrong PINs per employee and register before a lockout of LOCK_MINUTES.
PIN_ATTEMPTS = 5
LOCK_MINUTES = 15


class VluxPosEmployeeSession(models.Model):
    _name = "vlux.pos.employee.session"
    _description = "Sesión de empleado en una caja VLUX"
    _order = "create_date desc"

    employee_id = fields.Many2one("hr.employee", required=True, ondelete="cascade", index=True)
    pos_config_id = fields.Many2one("pos.config", required=True, ondelete="cascade", index=True)
    api_token_id = fields.Many2one("vlux.api.token", required=True, ondelete="cascade", index=True)
    token_hash = fields.Char(required=True, index=True, readonly=True)
    expires_at = fields.Datetime(required=True, readonly=True)
    active = fields.Boolean(default=True)
    # Set when the session authorizes one module for someone else at the register.
    purpose = fields.Char(readonly=True)
    requested_by_id = fields.Many2one("hr.employee", readonly=True, ondelete="set null")

    _token_hash_unique = models.Constraint("UNIQUE (token_hash)", "La sesión ya existe.")

    @api.model
    def _hash(self, raw):
        return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()

    @api.model
    def _pin_matches(self, employee, pin):
        """Constant-time comparison with the employee's PIN (an employee without one needs none)."""
        stored = employee.sudo().pin or ""
        return hmac.compare_digest(stored.encode("utf-8"), str(pin or "").encode("utf-8"))

    @api.model
    def _issue(self, config, employee, api_token, purpose=None, requested_by=None):
        """A session for ``employee``; with ``purpose`` it only authorizes that module, briefly,
        and the authorization is logged (who authorized whom, for what)."""
        raw = secrets.token_urlsafe(SESSION_BYTES)
        lifetime = timedelta(minutes=AUTHORIZATION_MINUTES) if purpose else timedelta(hours=SESSION_HOURS)
        session = self.sudo().create({
            "employee_id": employee.id,
            "pos_config_id": config.id,
            "api_token_id": api_token.id,
            "token_hash": self._hash(raw),
            "expires_at": fields.Datetime.now() + lifetime,
            "purpose": purpose or False,
            "requested_by_id": requested_by.id if requested_by else False,
        })
        if purpose:
            self.env["vlux.pos.authorization"].sudo().create({
                "pos_config_id": config.id,
                "employee_id": employee.id,
                "requested_by_id": requested_by.id if requested_by else False,
                "purpose": purpose,
            })
        return session, raw

    @api.model
    def _resolve(self, raw, config, api_token):
        """The employee behind ``raw`` on this register and device, or an empty recordset."""
        if not raw:
            return self.env["hr.employee"]
        session = self.sudo().search([("token_hash", "=", self._hash(raw))], limit=1)
        if (
            not session
            or session.pos_config_id != config
            or session.api_token_id != api_token
            or session.expires_at <= fields.Datetime.now()
        ):
            return self.env["hr.employee"]
        return session.employee_id.sudo(False)

    @api.autovacuum
    def _gc_expired(self):
        self.sudo().with_context(active_test=False).search(
            [("expires_at", "<", fields.Datetime.now() - timedelta(days=7))]
        ).unlink()


class VluxPosAuthorization(models.Model):
    """Someone with the right entered their PIN so a module could be used by another person."""
    _name = "vlux.pos.authorization"
    _description = "Autorización en una caja VLUX"
    _order = "create_date desc, id desc"

    pos_config_id = fields.Many2one("pos.config", required=True, ondelete="cascade", index=True, readonly=True)
    employee_id = fields.Many2one("hr.employee", string="Autorizó", required=True, ondelete="cascade", readonly=True)
    requested_by_id = fields.Many2one("hr.employee", string="Para", ondelete="set null", readonly=True)
    purpose = fields.Char(string="Módulo", required=True, readonly=True)
