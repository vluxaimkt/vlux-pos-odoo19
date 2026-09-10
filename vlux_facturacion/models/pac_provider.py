from odoo import fields, models, _
from odoo.exceptions import UserError


class VluxPacProvider(models.Model):
    _name = "vlux.pac.provider"
    _description = "Proveedor PAC de VLUX"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    provider_type = fields.Selection(
        [("simulator", "Simulador VLUX"), ("custom", "Adaptador PAC futuro")],
        required=True,
        default="simulator",
    )
    environment = fields.Selection(
        [("test", "Pruebas"), ("production", "Producción")],
        required=True,
        default="test",
    )
    notes = fields.Text()

    _code_unique = models.Constraint(
        "UNIQUE (code)",
        "El código del proveedor PAC debe ser único.",
    )

    def validate_credentials(self):
        self.ensure_one()
        if self.provider_type == "simulator":
            return {"ok": True, "message": _("Simulador VLUX disponible.")}
        raise UserError(_("El adaptador del PAC todavía no está implementado."))

    def stamp_request(self, fiscal_request):
        self.ensure_one()
        fiscal_request.ensure_one()
        if self.provider_type == "simulator":
            return fiscal_request._apply_simulation_result(self)
        raise UserError(_("No existe un adaptador de timbrado para %s.") % self.display_name)

    def cancel_document(self, fiscal_request, reason=None, replacement_uuid=None):
        raise UserError(_("La cancelación estará disponible cuando se conecte un PAC real."))

    def get_document_status(self, fiscal_request):
        self.ensure_one()
        if self.provider_type == "simulator":
            return {"status": "simulation", "reference": fiscal_request.simulation_reference}
        raise UserError(_("La consulta de estado requiere un adaptador PAC real."))
