import hashlib
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

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
    "vlux_core.group_vlux_cashier": {"system:read", "catalog:read", "orders:write"},
    "vlux_core.group_vlux_inventory_operator": {"system:read", "catalog:read", "catalog:write"},
    "vlux_core.group_vlux_auditor": {"system:read", "dashboard:read"},
    "vlux_core.group_vlux_support": {"system:read"},
}
TOKEN_BYTES = 32
PREFIX_LENGTH = 8


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

    _token_hash_unique = models.Constraint("UNIQUE (token_hash)", "El token ya existe.")

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
    def issue(self, name, scopes, user=None, expires_at=None):
        """Create a token and return ``(record, raw_token)``; the raw value is not stored."""
        user = user or self.env.user
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
        raw_token = secrets.token_urlsafe(TOKEN_BYTES)
        record = self.sudo().create({
            "name": name,
            "user_id": user.id,
            "company_id": user.company_id.id,
            "scopes": " ".join(sorted(requested)),
            "token_prefix": raw_token[:PREFIX_LENGTH],
            "token_hash": self._hash(raw_token),
            "expires_at": expires_at,
        })
        return record, raw_token

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
        """Record usage at most once a minute: the hot path stays read-only."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < 60:
            return
        self.sudo().with_context(tracking_disable=True).write({"last_used_at": now})
