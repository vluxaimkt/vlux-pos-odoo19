from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class VluxFiscalConfig(models.Model):
    _name = "vlux.fiscal.config"
    _description = "Configuración fiscal de VLUX"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id"

    name = fields.Char(
        string="Nombre",
        compute="_compute_name",
        store=True,
    )
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Empresa",
        required=True,
        default=lambda self: self.env.company,
        ondelete="cascade",
        tracking=True,
    )
    mode = fields.Selection(
        [
            ("simulation", "Simulación"),
            ("production", "Producción"),
        ],
        string="Modo de operación",
        required=True,
        default="simulation",
        tracking=True,
    )
    auto_process = fields.Boolean(
        string="Procesamiento automático",
        default=True,
        tracking=True,
    )
    pac_provider_id = fields.Many2one(
        "vlux.pac.provider",
        string="Proveedor PAC",
        required=True,
        domain=[("active", "=", True)],
        tracking=True,
    )
    fiscal_name = fields.Char(
        string="Nombre o razón social del emisor",
        tracking=True,
    )
    vat = fields.Char(
        string="RFC del emisor",
        tracking=True,
    )
    fiscal_zip = fields.Char(
        string="Código postal fiscal",
        tracking=True,
    )
    fiscal_regime = fields.Char(
        string="Régimen fiscal",
        tracking=True,
    )
    billing_email = fields.Char(
        string="Correo de facturación",
        tracking=True,
    )
    csd_status = fields.Selection(
        [
            ("not_configured", "No configurado"),
            ("configured", "Configurado"),
            ("expired", "Vencido"),
            ("error", "Error"),
        ],
        string="Estado del CSD",
        default="not_configured",
        required=True,
        readonly=True,
        tracking=True,
    )
    certificate_serial = fields.Char(
        string="Número de serie",
        readonly=True,
    )
    certificate_rfc = fields.Char(
        string="RFC del certificado",
        readonly=True,
    )
    certificate_valid_from = fields.Datetime(
        string="Válido desde",
        readonly=True,
    )
    certificate_valid_to = fields.Datetime(
        string="Válido hasta",
        readonly=True,
    )
    is_validated = fields.Boolean(
        string="Configuración validada",
        default=False,
        readonly=True,
        tracking=True,
    )
    validated_at = fields.Datetime(
        string="Validada el",
        readonly=True,
    )
    last_validation_message = fields.Text(
        string="Resultado de validación",
        readonly=True,
        tracking=True,
    )

    _company_unique = models.Constraint(
        "UNIQUE (company_id)",
        "Solo puede existir una configuración fiscal por empresa.",
    )

    @api.depends("company_id")
    def _compute_name(self):
        for record in self:
            record.name = (
                _("Configuración fiscal - %s")
                % record.company_id.display_name
                if record.company_id
                else _("Configuración fiscal")
            )

    @api.onchange("company_id")
    def _onchange_company_id(self):
        for record in self:
            if record.company_id:
                record.fiscal_name = record.company_id.name
                record.vat = (record.company_id.vat or "").strip().upper()
                record.billing_email = record.company_id.email

    @api.onchange("vat")
    def _onchange_vat(self):
        if self.vat:
            self.vat = self.vat.strip().upper()

    def write(self, vals):
        configuration_fields = {
            "company_id",
            "mode",
            "pac_provider_id",
            "fiscal_name",
            "vat",
            "fiscal_zip",
            "fiscal_regime",
            "billing_email",
        }
        if configuration_fields.intersection(vals):
            vals.setdefault("is_validated", False)
            vals.setdefault("validated_at", False)
        return super().write(vals)

    def action_load_company_data(self):
        for record in self:
            company = record.company_id
            record.write(
                {
                    "fiscal_name": company.name,
                    "vat": (company.vat or "").strip().upper(),
                    "billing_email": company.email,
                    "is_validated": False,
                    "validated_at": False,
                    "last_validation_message": _(
                        "Se cargaron los datos disponibles desde la empresa."
                    ),
                }
            )
        return True

    def action_validate_configuration(self):
        for record in self:
            if record.mode == "simulation":
                required = {
                    "fiscal_name": _("Nombre del emisor para pruebas"),
                    "pac_provider_id": _("Proveedor PAC"),
                }
            else:
                required = {
                    "fiscal_name": _("Nombre o raz?n social del emisor"),
                    "vat": _("RFC del emisor"),
                    "fiscal_zip": _("C?digo postal fiscal"),
                    "fiscal_regime": _("R?gimen fiscal"),
                    "pac_provider_id": _("Proveedor PAC"),
                }

            missing = [
                label
                for field_name, label in required.items()
                if not record[field_name]
            ]
            if missing:
                raise ValidationError(
                    _("Faltan datos de configuración: %s")
                    % ", ".join(missing)
                )

            if (
                record.mode == "production"
                and record.pac_provider_id.provider_type == "simulator"
            ):
                raise ValidationError(
                    _(
                        "El modo producción no puede utilizar "
                        "VLUX PAC Simulador."
                    )
                )

            if (
                record.mode == "production"
                and record.csd_status != "configured"
            ):
                raise ValidationError(
                    _(
                        "El modo producción requiere un CSD configurado "
                        "y validado."
                    )
                )

            result = record.pac_provider_id.validate_credentials()
            message = result.get("message") or _(
                "Configuración validada correctamente."
            )
            record.write(
                {
                    "is_validated": True,
                    "validated_at": fields.Datetime.now(),
                    "last_validation_message": message,
                }
            )
            record.message_post(body=message)
        return True

    def action_test_provider(self):
        self.ensure_one()
        if not self.pac_provider_id:
            raise UserError(_("Seleccione un proveedor PAC."))
        result = self.pac_provider_id.validate_credentials()
        message = result.get("message") or _("Proveedor disponible.")
        self.message_post(body=message)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Prueba de proveedor"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }
