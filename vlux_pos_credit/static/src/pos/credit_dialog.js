import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useService } from "@web/core/utils/hooks";

/**
 * Authorise a customer for credit and set the limit, at the register.
 * Only offered to the encargado and the owner; the server checks it again.
 */
export class VluxCreditDialog extends Component {
    static template = "vlux_pos_credit.CreditDialog";
    static components = { Dialog };
    static props = { close: Function, partner: Object };

    setup() {
        this.pos = usePos();
        this.notification = useService("notification");
        const partner = this.props.partner;
        this.state = useState({
            allowed: Boolean(partner.vlux_credit_allowed),
            limit: partner.vlux_credit_limit ? String(partner.vlux_credit_limit) : "",
            saving: false,
            error: "",
        });
    }

    get balance() {
        return this.env.utils.formatCurrency(this.props.partner.vlux_credit_balance || 0);
    }

    async save() {
        const limit = Number(String(this.state.limit || "0").replace(",", "."));
        if (!Number.isFinite(limit) || limit < 0) {
            this.state.error = _t("Escribe el límite en pesos, por ejemplo 1000. 0 = sin límite.");
            return;
        }
        this.state.saving = true;
        this.state.error = "";
        try {
            await this.pos.data.call("res.partner", "vlux_pos_set_credit", [
                this.props.partner.id,
                this.state.allowed,
                limit,
                this.pos.config.id,
                this.pos.getCashier()?.id || false,
            ]);
            // Refresh the customer in the register (flags, limit, balance).
            await this.pos.data.read("res.partner", [this.props.partner.id]);
            this.notification.add(
                this.state.allowed
                    ? _t("Crédito autorizado a %s.", this.props.partner.name)
                    : _t("Se retiró el crédito a %s.", this.props.partner.name),
                { type: "success" }
            );
            this.props.close();
        } catch (error) {
            this.state.error =
                error?.data?.message || _t("No se pudo guardar. Revisa la conexión e intenta de nuevo.");
        } finally {
            this.state.saving = false;
        }
    }
}
