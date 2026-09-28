import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError } from "@web/core/network/rpc";
import { usePos } from "@point_of_sale/app/hooks/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { VluxAbonoReceipt } from "./abono_receipt";

/**
 * A customer pays (part of) what they owe. Any cashier may receive it; the
 * server registers it in the open session, so cash enters the closing count.
 * Needs the server: the balance must be the real one.
 */
export class VluxAbonoDialog extends Component {
    static template = "vlux_pos_credit.AbonoDialog";
    static components = { Dialog, VluxAbonoReceipt };
    static props = { close: Function, partner: Object };

    setup() {
        this.pos = usePos();
        this.printer = useService("printer");
        this.methods = this.pos.config.payment_method_ids.filter((method) => method.type !== "pay_later");
        this.state = useState({
            amount: "",
            methodId: this.methods.find((method) => method.is_cash_count)?.id || this.methods[0]?.id,
            saving: false,
            error: "",
            ticket: null,
        });
        // One request id per abono: a retry after a dropped connection gets
        // the same abono back instead of a second one.
        this.uuid = crypto.randomUUID();
    }

    get debt() {
        return this.props.partner.vlux_credit_balance || 0;
    }

    formatCurrency(value) {
        return this.env.utils.formatCurrency(value);
    }

    settleAll() {
        this.state.amount = String(this.debt);
    }

    async register() {
        const amount = Number(String(this.state.amount || "").replace(",", "."));
        if (!Number.isFinite(amount) || amount <= 0) {
            this.state.error = _t("Escribe cuánto paga el cliente.");
            return;
        }
        this.state.saving = true;
        this.state.error = "";
        try {
            const ticket = await this.pos.data.call("pos.session", "vlux_pos_register_abono", [
                this.pos.session.id,
                this.props.partner.id,
                amount,
                this.state.methodId,
                this.uuid,
                this.pos.getCashier()?.id || false,
            ]);
            this.props.partner.vlux_credit_balance = ticket.new_balance;
            this.state.ticket = ticket;
        } catch (error) {
            this.state.error =
                error instanceof ConnectionLostError
                    ? _t("Sin conexión: el abono no se registró. Intenta de nuevo cuando vuelva el internet.")
                    : error?.data?.message || _t("No se pudo registrar el abono.");
        } finally {
            this.state.saving = false;
        }
    }

    async print() {
        await this.printer.print(VluxAbonoReceipt, { ticket: this.state.ticket }, this.pos.printOptions);
    }
}
