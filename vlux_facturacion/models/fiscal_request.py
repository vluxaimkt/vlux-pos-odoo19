import base64
import secrets
from xml.sax.saxutils import escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class VluxFiscalRequest(models.Model):
    _name = "vlux.fiscal.request"
    _description = "Solicitud de facturación VLUX"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    def _default_config_id(self):
        return self.env["vlux.fiscal.config"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("active", "=", True),
            ],
            limit=1,
        )

    name = fields.Char(
        string="Folio",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("Nuevo"),
        tracking=True,
    )
    request_date = fields.Datetime(
        string="Fecha de solicitud",
        default=fields.Datetime.now,
        required=True,
        readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("validated", "Datos validados"),
            ("confirmed", "Confirmada"),
            ("ready_to_stamp", "Lista para timbrar"),
            ("stamping", "Timbrando"),
            ("simulation_completed", "Simulación completada"),
            ("stamped", "Timbrada"),
            ("stamp_error", "Error de timbrado"),
            ("cancel_requested", "Cancelación solicitada"),
            ("cancelled", "Cancelada"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    pos_order_id = fields.Many2one(
        "pos.order",
        string="Venta POS",
        required=True,
        ondelete="restrict",
        tracking=True,
        index=True,
    )
    account_move_id = fields.Many2one(
        "account.move",
        string="Factura contable",
        ondelete="set null",
        tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Cliente",
        ondelete="restrict",
        tracking=True,
    )
    company_id = fields.Many2one(
        related="pos_order_id.company_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="pos_order_id.currency_id",
        store=True,
        readonly=True,
    )
    amount_total = fields.Monetary(
        related="pos_order_id.amount_total",
        currency_field="currency_id",
        store=True,
        readonly=True,
    )

    config_id = fields.Many2one(
        "vlux.fiscal.config",
        string="Configuración fiscal",
        default=_default_config_id,
        ondelete="restrict",
        tracking=True,
    )
    automatic_processing = fields.Boolean(
        string="Procesamiento automático",
        default=True,
        tracking=True,
    )

    fiscal_name = fields.Char(
        string="Nombre o razón social",
        tracking=True,
    )
    vat = fields.Char(
        string="RFC",
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
    cfdi_use = fields.Char(
        string="Uso del CFDI",
        tracking=True,
    )
    email = fields.Char(
        string="Correo electrónico",
        tracking=True,
    )
    phone = fields.Char(
        string="Teléfono",
    )

    pac_provider_id = fields.Many2one(
        "vlux.pac.provider",
        string="Proveedor PAC",
        tracking=True,
        domain=[("active", "=", True)],
    )
    simulation_reference = fields.Char(
        string="Referencia de simulación",
        readonly=True,
        copy=False,
        tracking=True,
    )
    fiscal_uuid = fields.Char(
        string="UUID fiscal",
        readonly=True,
        copy=False,
        tracking=True,
    )
    stamped_at = fields.Datetime(
        string="Fecha de timbrado",
        readonly=True,
        copy=False,
    )
    retry_count = fields.Integer(
        string="Reintentos",
        readonly=True,
        copy=False,
    )
    last_error = fields.Text(
        string="Último error",
        readonly=True,
        copy=False,
        tracking=True,
    )
    access_token = fields.Char(
        string="Token público",
        default=lambda self: secrets.token_urlsafe(32),
        required=True,
        readonly=True,
        copy=False,
        index=True,
    )
    token_expires_at = fields.Datetime(
        string="Vencimiento del token",
        copy=False,
    )
    confirmed_at = fields.Datetime(
        string="Fecha de confirmación",
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            "request_pos_order_unique",
            "unique(pos_order_id)",
            "Ya existe una solicitud de facturación para esta venta POS.",
        ),
        (
            "request_token_unique",
            "unique(access_token)",
            "El token público debe ser único.",
        ),
    ]

    def _find_company_config(self, company):
        self.ensure_one()
        return self.env["vlux.fiscal.config"].search(
            [
                ("company_id", "=", company.id),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _apply_configuration(self):
        for record in self:
            if not record.pos_order_id:
                continue

            config = record.config_id or record._find_company_config(
                record.pos_order_id.company_id
            )

            if config:
                record.write(
                    {
                        "config_id": config.id,
                        "pac_provider_id": config.pac_provider_id.id,
                        "automatic_processing": config.auto_process,
                    }
                )

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        config_model = self.env["vlux.fiscal.config"]

        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = (
                    sequence.next_by_code("vlux.fiscal.request")
                    or _("Nuevo")
                )

            order_id = vals.get("pos_order_id")
            if not order_id:
                continue

            order = self.env["pos.order"].sudo().browse(order_id)

            if order.account_move and not vals.get("account_move_id"):
                vals["account_move_id"] = order.account_move.id

            if order.partner_id and not vals.get("partner_id"):
                vals["partner_id"] = order.partner_id.id

            config = False
            if vals.get("config_id"):
                config = config_model.browse(vals["config_id"])
            else:
                config = config_model.search(
                    [
                        ("company_id", "=", order.company_id.id),
                        ("active", "=", True),
                    ],
                    limit=1,
                )
                if config:
                    vals["config_id"] = config.id

            if config:
                vals.setdefault(
                    "pac_provider_id",
                    config.pac_provider_id.id,
                )
                vals.setdefault(
                    "automatic_processing",
                    config.auto_process,
                )

        records = super().create(vals_list)

        for record in records:
            record.message_post(
                body=_(
                    "Solicitud creada. El documento no tiene validez fiscal "
                    "hasta recibir un timbre PAC válido."
                )
            )

        return records

    @api.onchange("pos_order_id")
    def _onchange_pos_order_id(self):
        for record in self:
            if not record.pos_order_id:
                record.partner_id = False
                record.account_move_id = False
                record.config_id = False
                record.pac_provider_id = False
                return

            record.partner_id = record.pos_order_id.partner_id
            record.account_move_id = record.pos_order_id.account_move

            config = self.env["vlux.fiscal.config"].search(
                [
                    (
                        "company_id",
                        "=",
                        record.pos_order_id.company_id.id,
                    ),
                    ("active", "=", True),
                ],
                limit=1,
            )

            record.config_id = config
            record.pac_provider_id = config.pac_provider_id
            record.automatic_processing = config.auto_process

    @api.onchange("config_id")
    def _onchange_config_id(self):
        for record in self:
            if record.config_id:
                record.pac_provider_id = record.config_id.pac_provider_id
                record.automatic_processing = record.config_id.auto_process

    @api.onchange("vat")
    def _onchange_vat_uppercase(self):
        if self.vat:
            self.vat = self.vat.strip().upper()

    def _check_required_fiscal_data(self):
        for record in self:
            config = record.config_id

            if not config and record.pos_order_id:
                config = self.env["vlux.fiscal.config"].search(
                    [
                        (
                            "company_id",
                            "=",
                            record.pos_order_id.company_id.id,
                        ),
                        ("active", "=", True),
                    ],
                    limit=1,
                )

                if config:
                    record.config_id = config
                    record.pac_provider_id = (
                        record.pac_provider_id
                        or config.pac_provider_id
                    )
                    record.automatic_processing = config.auto_process

            if not config:
                raise ValidationError(
                    _(
                        "No existe una configuración fiscal activa "
                        "para esta empresa."
                    )
                )

            if config.mode == "simulation":
                required = {
                    "fiscal_name": _(
                        "Nombre o razón social para la simulación"
                    ),
                    "pac_provider_id": _("Proveedor PAC"),
                }
            else:
                required = {
                    "fiscal_name": _("Nombre o razón social"),
                    "vat": _("RFC"),
                    "fiscal_zip": _("Código postal fiscal"),
                    "fiscal_regime": _("Régimen fiscal"),
                    "cfdi_use": _("Uso del CFDI"),
                    "pac_provider_id": _("Proveedor PAC"),
                }

            missing = [
                label
                for field_name, label in required.items()
                if not record[field_name]
            ]

            if missing:
                raise ValidationError(
                    _("Faltan datos fiscales obligatorios: %s")
                    % ", ".join(missing)
                )
    def action_validate_data(self):
        self._check_required_fiscal_data()

        for record in self:
            if record.state not in ("draft", "stamp_error"):
                raise UserError(
                    _("La solicitud no está en un estado validable.")
                )

            record.write(
                {
                    "state": "validated",
                    "last_error": False,
                }
            )
            record.message_post(body=_("Datos fiscales validados."))

        return True

    def action_confirm(self):
        for record in self:
            if record.state != "validated":
                raise UserError(
                    _("Primero deben validarse los datos fiscales.")
                )

            record.write(
                {
                    "state": "confirmed",
                    "confirmed_at": fields.Datetime.now(),
                }
            )
            record.message_post(body=_("Solicitud confirmada."))

        return True

    def action_mark_ready(self):
        for record in self:
            if record.state != "confirmed":
                raise UserError(
                    _(
                        "Solo una solicitud confirmada puede quedar "
                        "lista para timbrar."
                    )
                )

            record.state = "ready_to_stamp"
            record.message_post(
                body=_(
                    "Solicitud lista para timbrar. "
                    "Todavía no existe un CFDI emitido."
                )
            )

        return True

    def action_send_to_pac(self):
        for record in self:
            if record.state not in ("ready_to_stamp", "stamp_error"):
                raise UserError(
                    _("La solicitud no está lista para enviarse al PAC.")
                )

            if not record.pac_provider_id:
                record._apply_configuration()

            if not record.pac_provider_id:
                raise UserError(
                    _("No existe un proveedor PAC configurado.")
                )

            record.write(
                {
                    "state": "stamping",
                    "last_error": False,
                }
            )

            try:
                record.pac_provider_id.stamp_request(record)
            except Exception as error:
                record.write(
                    {
                        "state": "stamp_error",
                        "last_error": str(error),
                        "retry_count": record.retry_count + 1,
                    }
                )
                record.message_post(
                    body=_("Error al procesar el timbrado: %s")
                    % str(error)
                )
                raise

        return True

    def action_process_automatically(self):
        for record in self:
            record._apply_configuration()

            if not record.config_id:
                raise UserError(
                    _(
                        "No existe una configuración fiscal activa "
                        "para la empresa de esta venta."
                    )
                )

            if not record.config_id.is_validated:
                raise UserError(
                    _(
                        "Primero valide la configuración fiscal "
                        "de la empresa."
                    )
                )

            if record.state in ("draft", "stamp_error"):
                record.action_validate_data()

            if record.state == "validated":
                record.action_confirm()

            if record.state == "confirmed":
                record.action_mark_ready()

            if record.state == "ready_to_stamp":
                record.action_send_to_pac()

        return True

    def _apply_simulation_result(self, provider):
        self.ensure_one()

        reference = "SIMULACION-VLUX-%06d" % self.id

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<vluxSimulation version="1.0">\n'
            '  <notice>DOCUMENTO DE PRUEBA SIN VALIDEZ FISCAL</notice>\n'
            f'  <reference>{escape(reference)}</reference>\n'
            f'  <request>{escape(self.name or "")}</request>\n'
            f'  <posOrder>{escape(self.pos_order_id.pos_reference or "")}</posOrder>\n'
            f'  <rfc>{escape(self.vat or "")}</rfc>\n'
            f'  <name>{escape(self.fiscal_name or "")}</name>\n'
            f'  <amount>{escape(str(self.amount_total or 0.0))}</amount>\n'
            f'  <currency>{escape(self.currency_id.name or "")}</currency>\n'
            f'  <pac>{escape(provider.name or "")}</pac>\n'
            '</vluxSimulation>\n'
        )

        attachment = self.env["ir.attachment"].sudo().create(
            {
                "name": "%s_SIMULACION.xml"
                % self.name.replace("/", "_"),
                "type": "binary",
                "datas": base64.b64encode(xml.encode("utf-8")),
                "mimetype": "application/xml",
                "res_model": self._name,
                "res_id": self.id,
            }
        )

        self.write(
            {
                "state": "simulation_completed",
                "simulation_reference": reference,
                "last_error": False,
            }
        )

        self.message_post(
            body=_(
                "Simulación completada. XML de prueba adjunto, "
                "sin validez fiscal."
            ),
            attachment_ids=attachment.ids,
        )

        return {
            "ok": True,
            "mode": "simulation",
            "reference": reference,
            "attachment_id": attachment.id,
        }

    def action_reset_to_draft(self):
        for record in self:
            if record.state in ("stamped", "cancelled"):
                raise UserError(
                    _(
                        "Un documento timbrado o cancelado "
                        "no puede volver a borrador."
                    )
                )

            record.write(
                {
                    "state": "draft",
                    "last_error": False,
                }
            )

        return True

    def action_request_cancellation(self):
        for record in self:
            if record.state != "stamped":
                raise UserError(
                    _("Solo un CFDI timbrado puede solicitar cancelación.")
                )

            record.state = "cancel_requested"

        return True

    def action_open_pos_order(self):
        self.ensure_one()

        return {
            "type": "ir.actions.act_window",
            "res_model": "pos.order",
            "view_mode": "form",
            "res_id": self.pos_order_id.id,
            "target": "current",
        }

    def action_open_invoice(self):
        self.ensure_one()

        if not self.account_move_id:
            raise UserError(
                _("La solicitud todavía no tiene factura contable.")
            )

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.account_move_id.id,
            "target": "current",
        }