from odoo import _, api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Odoo Community prints the company name, address, phone and VAT (the RFC in
    # Mexico) on the POS receipt, but the tax regime lives in the Enterprise
    # localisation. A Mexican ticket is expected to carry it, and to say plainly
    # that it is not a tax document, so the customer knows to ask for an invoice.
    vlux_fiscal_regime = fields.Char(
        string="Régimen fiscal",
        help="Régimen fiscal impreso en el ticket, por ejemplo "
        "'601 - General de Ley Personas Morales'.",
    )
    vlux_receipt_legend = fields.Char(
        string="Leyenda del ticket",
        default=lambda self: _("Este ticket no es un comprobante fiscal"),
        help="Se imprime al pie del ticket. Vacío para no imprimir ninguna leyenda.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "vlux_fiscal_regime",
            "vlux_receipt_legend",
        ]
