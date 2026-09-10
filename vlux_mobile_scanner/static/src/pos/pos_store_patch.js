import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": odoo.csrf_token,
            ...(options.headers || {}),
        },
        ...options,
    });
    let data = {};
    try {
        data = await response.json();
    } catch {
        data = { ok: false, message: `HTTP ${response.status}` };
    }
    if (!response.ok || data.ok === false) {
        throw new Error(data.message || `HTTP ${response.status}`);
    }
    return data;
}

patch(PosStore.prototype, {
    async setup() {
        await super.setup(...arguments);
        this.vluxMobileProcessedRequests = new Set();

        // PosData.connectWebSocket es el mecanismo nativo del POS 19.
        this.data.connectWebSocket(
            "VLUX_MOBILE_BARCODE",
            this._vluxOnMobileBarcode.bind(this)
        );
    },

    async vluxCreateMobilePairing() {
        const response = await jsonRequest("/vlux/pos/pairing/create", {
            method: "POST",
            body: JSON.stringify({
                pos_config_id: this.config.id,
                pos_session_id: this.session.id,
                device_identifier: this.device.identifier,
            }),
        });
        return response.pairing;
    },

    async vluxPairingStatus(pairingId) {
        return await jsonRequest("/vlux/pos/pairing/status", {
            method: "POST",
            body: JSON.stringify({
                pairing_id: pairingId,
                pos_session_id: this.session.id,
                device_identifier: this.device.identifier,
            }),
        });
    },

    async vluxRevokePairing(pairingId) {
        return await jsonRequest("/vlux/pos/pairing/revoke", {
            method: "POST",
            body: JSON.stringify({
                pairing_id: pairingId,
                pos_session_id: this.session.id,
                device_identifier: this.device.identifier,
            }),
        });
    },

    async _vluxAck(payload, values) {
        try {
            await jsonRequest("/vlux/pos/ack", {
                method: "POST",
                body: JSON.stringify({
                    request_id: payload.request_id,
                    pos_session_id: this.session.id,
                    device_identifier: this.device.identifier,
                    ...values,
                }),
            });
        } catch (error) {
            console.error("VLUX Mobile Scanner ACK error", error);
        }
    },

    async _vluxOnMobileBarcode(payload) {
        if (
            !payload ||
            payload.pos_config_id !== this.config.id ||
            payload.pos_session_id !== this.session.id ||
            payload.device_identifier !== this.device.identifier
        ) {
            return;
        }

        if (this.vluxMobileProcessedRequests.has(payload.request_id)) {
            return;
        }
        this.vluxMobileProcessedRequests.add(payload.request_id);
        window.setTimeout(
            () => this.vluxMobileProcessedRequests.delete(payload.request_id),
            5 * 60 * 1000
        );

        if (this.router.state.current !== "ProductScreen" || !this.getOrder()) {
            await this._vluxAck(payload, {
                status: "failed",
                result_code: "POS_NOT_READY",
                message: "La caja no esta en la pantalla de productos.",
            });
            return;
        }

        const order = this.getOrder();
        const beforeQuantity = Number(order.totalQuantity || 0);

        try {
            // Reutiliza el flujo estándar de Odoo: nomenclatura -> callbacks -> ProductScreen.
            await this.barcodeReader.scan(payload.barcode);

            const afterQuantity = Number(order.totalQuantity || 0);
            if (Math.abs(afterQuantity - beforeQuantity) < 1e-9) {
                await this._vluxAck(payload, {
                    status: "not_found",
                    result_code: "NO_ORDER_CHANGE",
                    message: "Odoo no encontro un producto compatible con ese codigo.",
                });
                return;
            }

            const line = order.getSelectedOrderline();
            const product = line?.product_id;
            await this._vluxAck(payload, {
                status: "delivered",
                result_code: "ADDED_TO_CART",
                message: "Producto agregado al carrito.",
                product_id: product?.id || false,
                product_name: product?.display_name || product?.name || "",
                unit_price: Number(line?.price_unit || 0),
            });
        } catch (error) {
            console.error("VLUX Mobile Scanner processing error", error);
            await this._vluxAck(payload, {
                status: "failed",
                result_code: "POS_SCAN_ERROR",
                message: error?.message || "Error al procesar el codigo en Odoo POS.",
            });
        }
    },
});
