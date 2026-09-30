import hashlib
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .rate_limit import own_transaction

# Scopes are coarse on purpose: a caller either reads the catalog or it does
# not. Finer permissions stay where Odoo already enforces them (the user's VLUX
# role), because the token never grants more than its user has.
SCOPES = {
    "system:read": "Estado del sistema",
    "catalog:read": "Leer catálogo y precios",
    "catalog:write": "Alta y edición de productos",
    "orders:write": "Registrar ventas",
    "session:manage": "Abrir y cerrar caja",
    "dashboard:read": "Indicadores del negocio",
}
# What each VLUX role may hand out. A token can never exceed this set.
ROLE_SCOPES = {
    "vlux_core.group_vlux_owner": set(SCOPES),
    "vlux_core.group_vlux_administrator": set(SCOPES),
    "vlux_core.group_vlux_supervisor": {"system:read", "catalog:read", "orders:write", "session:manage", "dashboard:read"},
    # A cashier opens and closes the register; the closing difference limit is
    # enforced by the server (``pos.session.close_session_from_ui``).
    "vlux_core.group_vlux_cashier": {"system:read", "catalog:read", "orders:write", "session:manage"},
    "vlux_core.group_vlux_inventory_operator": {"system:read", "catalog:read", "catalog:write"},
    "vlux_core.group_vlux_auditor": {"system:read", "dashboard:read"},
    "vlux_core.group_vlux_support": {"system:read"},
}
TOKEN_BYTES = 32
PREFIX_LENGTH = 8
# After a renewal the old token keeps working this long, so a device whose
# answer got lost on the way can still ask again.
ROTATION_GRACE = timedelta(hours=24)


class VluxApiToken(models.Model):
    """Bearer credential for the VLUX API.

    Only the hash is stored, exactly like the mobile scanner pairing: a leaked
    database row cannot be replayed against the API. The plaintext is returned
    once, when the token is created.
    """

    _name = "vlux.api.token"
    _description = "Token de la API VLUX"
    _order = "create_date desc"

    name = fields.Char(required=True, help="Para qué es este token, por ejemplo 'Caja 1 — tablet'.")
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", default=lambda self: self.env.user,
        help="El token nunca puede hacer más que este usuario.",
    )
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    scopes = fields.Char(string="Alcances", required=True, help="Lista separada por espacios.")
    token_prefix = fields.Char(readonly=True, help="Primeros caracteres, para identificarlo sin revelarlo.")
    token_hash = fields.Char(readonly=True, required=True, index=True, groups="base.group_system")
    expires_at = fields.Datetime(help="Vacío: no expira.")
    last_used_at = fields.Datetime(readonly=True)
    active = fields.Boolean(default=True)
    # A register's token works for that register only: a lost device cannot be
    # used to open, sell on or close another one. Deleting the register
    # deletes its tokens rather than widening them to every register.
    pos_config_id = fields.Many2one(
        "pos.config", string="Caja", ondelete="cascade", index="btree_not_null",
        help="Si se indica, el token sólo sirve para esta caja.",
    )
    lifetime_days = fields.Integer(
        string="Renovación (días)", default=0,
        help="Si es mayor que cero, el token caduca a los N días y el dispositivo lo renueva "
             "solo antes de que eso ocurra. Cero: no se renueva.",
    )
    rotated_to_id = fields.Many2one("vlux.api.token", string="Renovado por", readonly=True, ondelete="set null")

    _token_hash_unique = models.Constraint("UNIQUE (token_hash)", "El token ya existe.")
    _lifetime_positive = models.Constraint("CHECK (lifetime_days >= 0)", "La renovación no puede ser negativa.")

    @api.constrains("pos_config_id", "company_id")
    def _check_register_company(self):
        for token in self:
            if token.pos_config_id and token.pos_config_id.company_id != token.company_id:
                raise UserError(_("La caja del token debe ser de la misma compañía que el token."))

    @api.model
    def _hash(self, raw_token):
        return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()

    @api.model
    def allowed_scopes_for(self, user):
        """Scopes ``user`` may be granted, from its VLUX roles."""
        allowed = set()
        for xmlid, scopes in ROLE_SCOPES.items():
            if user.has_group(xmlid):
                allowed |= scopes
        return allowed

    @api.model
    def issue(self, name, scopes, user=None, expires_at=None, pos_config=None, lifetime_days=0):
        """Create a token and return ``(record, raw_token)``; the raw value is not stored.

        ``pos_config`` binds it to one register; ``lifetime_days`` makes it
        expire after that many days and lets the device renew it (``rotate``).
        """
        user = user or self.env.user
        if lifetime_days and lifetime_days < 0:
            raise UserError(_("La renovación no puede ser negativa."))
        if lifetime_days and not expires_at:
            expires_at = fields.Datetime.now() + timedelta(days=lifetime_days)
        requested = set(scopes.split() if isinstance(scopes, str) else scopes or [])
        unknown = requested - set(SCOPES)
        if unknown:
            raise UserError(_("Alcances desconocidos: %s", ", ".join(sorted(unknown))))
        allowed = self.allowed_scopes_for(user)
        if not requested:
            raise UserError(_("Un token necesita al menos un alcance."))
        if requested - allowed:
            raise UserError(
                _("El rol de %(user)s no permite: %(scopes)s",
                  user=user.display_name, scopes=", ".join(sorted(requested - allowed)))
            )
        company = user.company_id
        if pos_config:
            # A register's token works in the register's company.
            company = pos_config.company_id
            if company not in user.company_ids:
                raise UserError(_("%(user)s no tiene acceso a la compañía de la caja %(register)s.",
                                  user=user.display_name, register=pos_config.display_name))
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        record = self.sudo().create({
            "name": name,
            "user_id": user.id,
            "company_id": company.id,
            "scopes": " ".join(sorted(requested)),
            "token_prefix": raw_token[:PREFIX_LENGTH],
            "token_hash": self._hash(raw_token),
            "expires_at": expires_at,
            "pos_config_id": pos_config.id if pos_config else False,
            "lifetime_days": lifetime_days or 0,
        })
        return record, raw_token

    def rotate(self):
        """Replace a renewable token: return ``(new record, new raw token)``.

        The new token has the same name, user, scopes and register, and a
        fresh lifetime; the scopes are checked again against the user's
        current role. The old one keeps working for ``ROTATION_GRACE`` so a
        lost answer does not lock the device out. Asking again within that
        window revokes the replacement that was never used and issues another,
        so retries do not pile up live tokens.
        """
        self.ensure_one()
        token = self.sudo()
        if not token.lifetime_days:
            raise UserError(_("Este token no se renueva: emite uno nuevo en Odoo."))
        if token.rotated_to_id and not token.rotated_to_id.last_used_at:
            token.rotated_to_id.active = False
        new, raw = self.issue(
            token.name, token.scopes, user=token.user_id,
            pos_config=token.pos_config_id, lifetime_days=token.lifetime_days,
        )
        new.sudo().company_id = token.company_id
        grace_end = fields.Datetime.now() + ROTATION_GRACE
        token.write({
            "rotated_to_id": new.id,
            "expires_at": min(token.expires_at, grace_end) if token.expires_at else grace_end,
        })
        return new, raw

    def allows_register(self, config):
        """Whether this token may act on register ``config``."""
        self.ensure_one()
        return not self.pos_config_id or self.pos_config_id == config

    @api.model
    def authenticate(self, raw_token):
        """Return the usable token for ``raw_token``, or an empty recordset."""
        if not raw_token:
            return self.browse()
        token = self.sudo().search([("token_hash", "=", self._hash(raw_token))], limit=1)
        if not token or not token.user_id.active:
            return self.browse()
        if token.expires_at and token.expires_at <= fields.Datetime.now():
            return self.browse()
        return token

    def has_scope(self, scope):
        self.ensure_one()
        return scope in (self.scopes or "").split()

    def action_revoke(self):
        """Stop every device using these tokens, right now."""
        self.write({"active": False})

    def touch(self):
        """Record usage at most once a minute: the hot path stays read-only.

        The write goes through a transaction of its own with the throttle in
        the WHERE clause, so a burst of first requests on a fresh token (or on
        a stale one) updates the row once and never fails on a concurrent
        update; see :func:`own_transaction`.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < 60:
            return
        with own_transaction(self.env) as cr:
            cr.execute(
                f"""
                UPDATE {self._table}
                   SET last_used_at = %s
                 WHERE id = %s
                   AND (last_used_at IS NULL OR last_used_at < %s)
                """,
                [now, self.id, now - timedelta(seconds=60)],
            )
        self.invalidate_recordset(["last_used_at"])
