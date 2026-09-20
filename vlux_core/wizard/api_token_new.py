from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.api_token import SCOPES


class VluxApiTokenNew(models.TransientModel):
    """Issue an API token from the backend.

    The plaintext token exists only here, once: it is shown after creating the
    record and never stored, so an operator can copy it into a register without
    it ever landing in the database or in a log.
    """

    _name = "vlux.api.token.new"
    _description = "Nuevo token de la API VLUX"

    name = fields.Char(required=True, help="Para qué es este token, por ejemplo 'Caja 1 — tablet'.")
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, default=lambda self: self.env.user,
        domain="[('share', '=', False)]",
        help="El token nunca puede hacer más que este usuario.",
    )
    expires_at = fields.Datetime(string="Expira", help="Vacío: no expira.")
    allowed_scopes = fields.Char(compute="_compute_allowed_scopes")

    scope_system_read = fields.Boolean(string=SCOPES["system:read"])
    scope_catalog_read = fields.Boolean(string=SCOPES["catalog:read"])
    scope_catalog_write = fields.Boolean(string=SCOPES["catalog:write"])
    scope_orders_write = fields.Boolean(string=SCOPES["orders:write"])
    scope_session_manage = fields.Boolean(string=SCOPES["session:manage"])
    scope_dashboard_read = fields.Boolean(string=SCOPES["dashboard:read"])

    token_id = fields.Many2one("vlux.api.token", readonly=True)
    raw_token = fields.Char(
        string="Token", readonly=True,
        help="Cópialo ahora: no se guarda y no se puede volver a mostrar.",
    )

    @api.depends("user_id")
    def _compute_allowed_scopes(self):
        Token = self.env["vlux.api.token"]
        for wizard in self:
            allowed = Token.allowed_scopes_for(wizard.user_id) if wizard.user_id else set()
            wizard.allowed_scopes = ", ".join(sorted(allowed)) or _("ninguno (el usuario no tiene un rol VLUX)")

    def _selected_scopes(self):
        self.ensure_one()
        return [scope for scope in SCOPES if self[self._scope_field(scope)]]

    @staticmethod
    def _scope_field(scope):
        return "scope_" + scope.replace(":", "_")

    def action_issue(self):
        """Create the token and come back showing its plaintext once."""
        self.ensure_one()
        scopes = self._selected_scopes()
        if not scopes:
            raise UserError(_("Elige al menos un alcance para el token."))
        token, raw_token = self.env["vlux.api.token"].issue(
            self.name, scopes, user=self.user_id, expires_at=self.expires_at or None
        )
        self.write({"token_id": token.id, "raw_token": raw_token})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self.env.context,
        }
