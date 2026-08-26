import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class VluxMobilePairingDialog extends Component {
    static template = "vlux_mobile_scanner.MobilePairingDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        pairing: Object,
        pos: Object,
    };

    setup() {
        this.state = useState({
            status: this.props.pairing.status,
            lastSeenAt: null,
            copied: false,
        });
        this.pollTimer = null;

        onMounted(() => {
            this.refreshStatus();
            this.pollTimer = window.setInterval(() => this.refreshStatus(), 2000);
        });

        onWillUnmount(() => {
            if (this.pollTimer) {
                window.clearInterval(this.pollTimer);
            }
        });
    }

    get statusText() {
        return {
            waiting: _t("Esperando dispositivo..."),
            paired: _t("Telefono conectado"),
            expired: _t("Codigo expirado"),
            revoked: _t("Conexion revocada"),
        }[this.state.status] || this.state.status;
    }

    get statusClass() {
        return this.state.status === "paired" ? "is-connected" : "is-waiting";
    }

    async refreshStatus() {
        try {
            const result = await this.props.pos.vluxPairingStatus(this.props.pairing.id);
            this.state.status = result.status;
            this.state.lastSeenAt = result.last_seen_at;
        } catch {
            // El dialogo puede cerrarse mientras una solicitud esta en curso.
        }
    }

    async copyUrl() {
        try {
            await navigator.clipboard.writeText(this.props.pairing.scanner_url);
            this.state.copied = true;
            window.setTimeout(() => (this.state.copied = false), 1800);
        } catch {
            this.state.copied = false;
        }
    }

    async revoke() {
        await this.props.pos.vluxRevokePairing(this.props.pairing.id);
        this.state.status = "revoked";
    }
}
